"""Unit tests for load (CONTENT-FHIR-VARIANT) — no Spark, no network."""
from __future__ import annotations

import pytest

from cql_databricks.data_bindings.measure_import.knowledge_merge import SINGLE_ROW, knowledge_merge_sql
from cql_databricks.data_bindings.measure_import.load import (
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


def test_one_merge_serves_a_staged_view_and_a_single_parameterized_row():
    """DOC-OPEN-SAVE — the import and the Designer's Save write a knowledge row with the same statement."""
    staged = knowledge_merge_sql("k.m.library", "stg")
    row = knowledge_merge_sql("k.m.library", SINGLE_ROW)
    assert staged.replace("FROM stg", "") == row.replace(f"FROM {SINGLE_ROW}", "")
    assert ":resource_json" in row


def test_a_version_already_there_is_replaced():
    """DEC-PUBLISH-IMMUTABLE — the import and publishing both replace a published (url, version)."""
    row = knowledge_merge_sql("k.m.library", SINGLE_ROW)
    assert "WHEN MATCHED THEN UPDATE" in row and "WHEN NOT MATCHED THEN INSERT" in row
