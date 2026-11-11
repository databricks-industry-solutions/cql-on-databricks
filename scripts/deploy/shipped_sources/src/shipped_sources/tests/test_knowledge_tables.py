"""DEPLOY-DEFAULT-ENVIRONMENT, CONTENT-FHIR-VARIANT — the deploy's writer of the knowledge tables; no Spark, no network."""
from __future__ import annotations

import pytest

from shipped_sources.knowledge_merge import knowledge_merge_sql
from shipped_sources.knowledge_tables import (
    _KNOWLEDGE_TABLE_DDL,
    DeltaFeaturesError,
    StaleKnowledgeTableError,
    _assert_variant_schema,
    _enable_delta_features,
)


class _SqlSpark:
    """Records the SQL a table is sent. ``fail`` makes the table refuse it, as a table that is not Delta does."""
    def __init__(self, fail=None):
        self.statements, self._fail = [], fail

    def sql(self, text):
        self.statements.append(text)
        if self._fail:
            raise RuntimeError(self._fail)


class _FakeField:
    def __init__(self, name, data_type):
        self.name, self.dataType = name, data_type


class _FakeSpark:
    """Just enough of a SparkSession for the schema guard."""
    def __init__(self, fields):
        self._fields = fields

    def table(self, _fqtn):
        return self

    @property
    def schema(self):
        return self

    @property
    def fields(self):
        return [_FakeField(n, t) for n, t in self._fields.items()]


def test_variant_schema_accepted():
    spark = _FakeSpark({"url": "StringType()", "version": "StringType()",
                        "resource": "VariantType()"})
    _assert_variant_schema(spark, "acme.quality_measures.measure")  # does not raise


def test_pre_variant_table_fails_loudly():
    """CORE-NO-SILENT-MISLOWER — CREATE TABLE IF NOT EXISTS is a no-op against an existing
    table, so without this guard the MERGE fails later with DELTA_MERGE_UNRESOLVED_EXPRESSION,
    which names a missing column but not the cause."""
    spark = _FakeSpark({"url": "StringType()", "version": "StringType()",
                        "name": "StringType()", "title": "StringType()",
                        "resource_json": "StringType()"})
    with pytest.raises(StaleKnowledgeTableError) as excinfo:
        _assert_variant_schema(spark, "acme.quality_measures.measure")
    message = str(excinfo.value)
    assert "DROP TABLE acme.quality_measures.measure" in message   # actionable
    assert "resource_json" in message                   # shows what it found
    assert "re-ingest" in message


def test_struct_resource_column_is_not_accepted():
    """A STRUCT `resource` is the never-shipped design; only VARIANT is valid."""
    spark = _FakeSpark({"url": "StringType()", "version": "StringType()",
                        "resource": "StructType([StructField('url', StringType())])"})
    with pytest.raises(StaleKnowledgeTableError):
        _assert_variant_schema(spark, "acme.quality_measures.library")


# ── The published shape ──────────────────────────────────────────────────────

def test_table_ddl_is_resource_identity_and_provenance_only():
    """CONTENT-NO-DERIVED-COLUMNS — nothing derived is persisted."""
    assert "`resource` VARIANT NOT NULL" in _KNOWLEDGE_TABLE_DDL
    for derived in ("title", "name", "status", "content_cql", "library_refs",
                    "valueset_refs", "resource_json", "cql_name"):
        assert derived not in _KNOWLEDGE_TABLE_DDL


def test_identity_and_payload_come_from_one_parse():
    """The columns cannot describe a payload they disagree with if all three are projected
    from the same parse_json — which is what stands in for GENERATED ALWAYS AS, since
    Databricks does not support column generation over VARIANT."""
    sql = knowledge_merge_sql("k.m.library", "stg")
    assert sql.count("parse_json(") == 1
    assert "variant_get(parsed, '$.url', 'string')" in sql
    assert "variant_get(parsed, '$.version', 'string')" in sql


def test_variant_navigation_is_portable():
    """`resource:path` is Databricks-only and fails to parse on OSS Spark."""
    sql = knowledge_merge_sql("k.m.library", "stg")
    assert "variant_get(" in sql
    assert "parsed:" not in sql


def test_a_version_already_there_is_replaced():
    """CONTENT-INCREMENTAL — the deploy loads the same (url, version) again by replacing it."""
    sql = knowledge_merge_sql("k.m.library", "stg")
    assert "WHEN MATCHED THEN UPDATE" in sql and "WHEN NOT MATCHED THEN INSERT" in sql


# ── Delta features on a new table ────────────────────────────────────────────

def test_a_new_table_gets_the_delta_features_its_merge_reads():
    """CONTENT-INCREMENTAL — the incremental merge reads row tracking and the change feed."""
    spark = _SqlSpark()
    _enable_delta_features(spark, "acme.quality_measures.valueset")
    [sql] = spark.statements
    assert sql.startswith("ALTER TABLE acme.quality_measures.valueset SET TBLPROPERTIES (")
    for prop in ("'delta.enableRowTracking' = 'true'", "'delta.enableChangeDataFeed' = 'true'",
                 "'delta.enableDeletionVectors' = 'true'"):
        assert prop in sql


def test_a_table_that_refuses_the_delta_features_fails_loudly():
    """CORE-NO-SILENT-MISLOWER — a table without the features is an error that names it, not a debug line."""
    spark = _SqlSpark(fail="[DELTA_NOT_A_DELTA_TABLE] not a Delta table")
    with pytest.raises(DeltaFeaturesError) as excinfo:
        _enable_delta_features(spark, "acme.quality_measures.valueset")
    message = str(excinfo.value)
    assert "acme.quality_measures.valueset" in message
    assert "DELTA_NOT_A_DELTA_TABLE" in message


# ── The deploy's load of the example knowledge ───────────────────────────────

class _CatalogSpark:
    """Tables that exist, with the rows a ``SELECT DISTINCT`` returns."""
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.catalog = self

    def tableExists(self, fqtn):
        return fqtn in self.tables

    def sql(self, statement):
        fqtn = statement.rsplit(" FROM ", 1)[-1]
        return type("Rows", (), {"collect": lambda _self: [(v,) for v in self.tables.get(fqtn, [])]})()


def _library(base, cql):
    import base64
    return {"resourceType": "Library", "url": f"{base}/Library/Visits", "version": "1.0.0", "name": "Visits",
            "content": [{"contentType": "text/cql", "data": base64.b64encode(cql.encode()).decode()}]}


def test_the_deploy_checks_as_the_import_does_then_writes_the_knowledge_tables(monkeypatch):
    """DEPLOY-DEFAULT-ENVIRONMENT, CONTENT-IMPORT-MEASURES — what the import's check accepts lands in knowledge; a
    value set already in the table counts."""
    from shipped_sources import knowledge_tables

    base = "http://example.org/fhir"
    cql = (f"library Visits version '1.0.0'\nvalueset \"Office Visit\": '{base}/ValueSet/office-visit'\n"
           "define \"X\": true\n")
    seen = {}
    monkeypatch.setattr(knowledge_tables, "load", lambda spark, result, **kw: seen.setdefault("load", (result, kw))
                        and {"library": len(result.libraries)})
    monkeypatch.setattr(knowledge_tables, "ensure_valueset_table", lambda spark, **kw: None)
    spark = _CatalogSpark({"`main`.`knowledge`.`valueset`": [f"{base}/ValueSet/office-visit"]})
    rows = knowledge_tables.load_knowledge(spark, [_library(base, cql)], catalog="main", source="examples")
    result, kw = seen["load"]
    assert rows == {"library": 1} and kw["authority_schema"] == "knowledge" and kw["catalog"] == "main"
    assert {r.source_package for r in result.libraries} == {"examples"}


def test_the_deploy_refuses_content_the_import_refuses(monkeypatch):
    from cql_databricks.data_bindings.measure_import import ImportContentError
    from shipped_sources import knowledge_tables

    monkeypatch.setattr(knowledge_tables, "load", lambda *a, **kw: pytest.fail("nothing may be written"))
    cql = "library Visits version '1.0.0'\nvalueset \"Office Visit\": 'http://example.org/vs/x'\n"
    with pytest.raises(ImportContentError, match="no codes"):
        knowledge_tables.load_knowledge(_CatalogSpark(), [_library("http://example.org/fhir", cql)], catalog="main",
                                        source="examples")
