"""Unit tests for load (CONTENT-FHIR-VARIANT) — no Spark, no network."""
from __future__ import annotations

import pytest

from ..load import (
    _KNOWLEDGE_SOURCE_SQL,
    _KNOWLEDGE_TABLE_DDL,
    StaleKnowledgeTableError,
    _assert_variant_schema,
)


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
    _assert_variant_schema(spark, "acme.ecqi_measures.measure")  # does not raise


def test_pre_variant_table_fails_loudly():
    """CORE-NO-SILENT-MISLOWER — CREATE TABLE IF NOT EXISTS is a no-op against an existing
    table, so without this guard the MERGE fails later with DELTA_MERGE_UNRESOLVED_EXPRESSION,
    which names a missing column but not the cause."""
    spark = _FakeSpark({"url": "StringType()", "version": "StringType()",
                        "name": "StringType()", "title": "StringType()",
                        "resource_json": "StringType()"})
    with pytest.raises(StaleKnowledgeTableError) as excinfo:
        _assert_variant_schema(spark, "acme.ecqi_measures.measure")
    message = str(excinfo.value)
    assert "DROP TABLE acme.ecqi_measures.measure" in message   # actionable
    assert "resource_json" in message                   # shows what it found
    assert "re-ingest" in message


def test_struct_resource_column_is_not_accepted():
    """A STRUCT `resource` is the never-shipped design; only VARIANT is valid."""
    spark = _FakeSpark({"url": "StringType()", "version": "StringType()",
                        "resource": "StructType([StructField('url', StringType())])"})
    with pytest.raises(StaleKnowledgeTableError):
        _assert_variant_schema(spark, "acme.ecqi_measures.library")


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
    sql = _KNOWLEDGE_SOURCE_SQL.format(view="stg")
    assert sql.count("parse_json(") == 1
    assert "variant_get(parsed, '$.url', 'string')" in sql
    assert "variant_get(parsed, '$.version', 'string')" in sql


def test_variant_navigation_is_portable():
    """`resource:path` is Databricks-only and fails to parse on OSS Spark."""
    sql = _KNOWLEDGE_SOURCE_SQL.format(view="stg")
    assert "variant_get(" in sql
    assert "parsed:" not in sql


# --- CONTENT-IMPORT-TARGET-TAG ---------------------------------------------------------------------

class _RecordingSpark:
    def __init__(self):
        self.statements = []

    def sql(self, statement):
        self.statements.append(statement)


def test_tag_loaded_declares_both_schemas_for_the_resolver():
    from content_ingest.load import tag_loaded
    spark = _RecordingSpark()
    tag_loaded(spark, catalog="main", knowledge=("ecqi_measures", "my-measures"), terminology=("vsac", "vsac"))
    assert spark.statements == [
        "ALTER SCHEMA `main`.`ecqi_measures` SET TAGS ('cql_databricks_knowledge' = 'my-measures')",
        "ALTER SCHEMA `main`.`vsac` SET TAGS ('cql_databricks_terminology' = 'vsac')",
    ]


def test_tag_loaded_skips_a_target_given_as_none():
    from content_ingest.load import tag_loaded
    spark = _RecordingSpark()
    tag_loaded(spark, catalog="main", knowledge=None, terminology=("vsac", "vsac"))
    assert len(spark.statements) == 1 and "terminology" in spark.statements[0]


def test_tagging_without_a_catalog_is_an_error_not_a_silent_skip():
    import pytest
    from content_ingest.load import tag_loaded
    with pytest.raises(ValueError, match="Unity Catalog"):
        tag_loaded(_RecordingSpark(), catalog=None, knowledge=("ecqi_measures", "my-measures"), terminology=None)
