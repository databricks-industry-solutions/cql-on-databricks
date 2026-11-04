"""DATA-ENVIRONMENT-LAYOUT, DATA-CONTRACT — the shared population lander's pure parts. No Spark."""
from __future__ import annotations

import pytest

from shipped_sources.population import RESOURCE_TYPES, land, resource_ddl, store_table


def test_every_bound_type_has_a_struct_in_the_model():
    for fhir_type in RESOURCE_TYPES:
        assert resource_ddl(fhir_type).upper().startswith("STRUCT<")


def test_an_unmodeled_type_fails():
    with pytest.raises(ValueError, match="not a modeled FHIR type"):
        resource_ddl("NotAType")


def test_store_tables_live_in_the_data_store_named_for_the_type():
    assert store_table("c", "MedicationRequest") == "`c`.`data_store`.`medicationrequest`"


def test_a_population_with_an_unbound_type_fails():
    with pytest.raises(ValueError, match="no store table for FHIR types"):
        land(object(), catalog="c", raw_by_type={"Patient": None, "Spaceship": None},
             valid_from="2026-01-01", valid_to="2026-12-31")


def test_a_population_without_patients_fails():
    with pytest.raises(ValueError, match="needs Patient rows"):
        land(object(), catalog="c", raw_by_type={"Condition": None},
             valid_from="2026-01-01", valid_to="2026-12-31")


class _Writer:
    def __init__(self, writes):
        self.writes, self.options = writes, {}

    def mode(self, mode):
        self.options["mode"] = mode
        return self

    def option(self, key, value):
        self.options[key] = value
        return self

    def saveAsTable(self, table):
        self.writes.append((table, self.options))


class _Spark:
    """Records each store-table write and answers every count with 0."""

    def __init__(self):
        self.writes, self.statements = [], []

    def createDataFrame(self, rows, schema):
        return None

    def sql(self, statement):
        self.statements.append(statement)

    def table(self, name):
        return type("T", (), {"count": lambda self: 0})()


def test_a_load_replaces_each_store_table_schema_and_all(monkeypatch):
    """DEPLOY-DEFAULT-ENVIRONMENT (idempotent) — a redeploy whose resource model grew lands over the tables an earlier
    deploy wrote, so every write overwrites the table's schema with its data."""
    from cql_databricks.compiler import reshape

    spark = _Spark()
    monkeypatch.setattr(reshape, "schema_from_ddl", lambda fhir_type, ddl, patient_context: ddl)
    monkeypatch.setattr(reshape, "json_frame",
                        lambda spark_, raw, schema, column: type("F", (), {"write": _Writer(spark.writes)})())
    counts = land(spark, catalog="c", raw_by_type={"Patient": None}, valid_from="2026-01-01", valid_to="2026-12-31")
    assert [table for table, _ in spark.writes] == [store_table("c", t) for t in RESOURCE_TYPES]
    assert all(options == {"mode": "overwrite", "overwriteSchema": "true"} for _, options in spark.writes)
    # DEPLOY-DEFAULT-ENVIRONMENT: each table is dropped before it is written, so no property naming an old column
    # survives the new schema.
    assert [s for s in spark.statements if s.startswith("DROP TABLE")] == [
        f"DROP TABLE IF EXISTS {store_table('c', t)}" for t in RESOURCE_TYPES]
    assert counts == {t: 0 for t in RESOURCE_TYPES}
