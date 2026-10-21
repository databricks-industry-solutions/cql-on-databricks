"""DATA-SOURCE-UC-TAG — data sources read from their tags: the model, the read-only lookup, and the
declaration a loader runs.

The lookup is driven by a real ``ContentResolver`` over a fake SQL runner, which answers the tag
search (data-bindings' half) and the layout reads (the compiler's half). Caching is the caller's (the
Designer caches per signed-in user, `routes/data_sources.py`).
"""
from __future__ import annotations

import pytest

from cql_databricks.compiler.data_source import DataBinding, DataSource, DataSourceLayoutError, list_data_sources
from cql_databricks.data_bindings import ContentResolver, declaration_statements

MODELED = ["Encounter", "Patient"]


class FakeWorkspace:
    """Schemas by ``(catalog, schema)``: their tag value, comment, routines and change-log columns."""

    def __init__(self, schemas: dict[tuple[str, str], dict]) -> None:
        self.schemas = schemas
        self.calls: list[str] = []

    def __call__(self, statement, params=None):
        self.calls.append(statement)
        if "schema_tags" in statement:
            return [{"catalog_name": c, "schema_name": s, "tag_name": "cql_databricks_data_source",
                     "tag_value": v["tag"]} for (c, s), v in sorted(self.schemas.items())]
        found = self.schemas[(params["catalog"], params["schema"])]
        if "information_schema.routines" in statement:
            return [{"routine_name": r} for r in found.get("routines", [])]
        if "information_schema.columns" in statement:
            # ``change_log`` here is the per-type ``change_data_log``'s columns; no schema has the period log.
            if params["table"] != "change_data_log":
                return []
            return [{"column_name": c} for c in found.get("change_log", [])]
        if "information_schema.schemata" in statement:
            return [{"comment": found.get("comment")}]
        raise AssertionError(statement)


def _list(workspace: FakeWorkspace) -> list[DataSource]:
    return list_data_sources(ContentResolver(lambda: workspace, ["main"]), workspace, MODELED)


FHIR_PROD = {("main", "fhir"): {"tag": "fhir_prod", "comment": "Our patients",
                                "routines": ["retrieve_patient", "retrieve_encounter"],
                                "change_log": ["data_type", "updated_at"]}}


# --- model -------------------------------------------------------------------

def test_the_id_is_the_tag_value_and_the_wire_omits_what_is_absent():
    source = DataSource(name="fhir_prod", catalog="main", schema="fhir",
                        data_bindings={"Patient": DataBinding("main.fhir.retrieve_Patient")})
    assert source.id == "fhir_prod" and source.target == "main.fhir"
    assert source.to_dict() == {
        "id": "fhir_prod", "name": "fhir_prod", "catalog": "main", "schema": "fhir",
        "dataBindings": {"Patient": {"function": "main.fhir.retrieve_Patient"}}}


# --- lookup ------------------------------------------------------------------

def test_a_source_is_its_tag_its_comment_and_what_its_schema_holds():
    [source] = _list(FakeWorkspace(FHIR_PROD))
    assert source.to_dict() == {
        "id": "fhir_prod", "name": "fhir_prod", "catalog": "main", "schema": "fhir",
        "description": "Our patients",
        "dataBindings": {"Encounter": {"function": "main.fhir.retrieve_Encounter"},
                         "Patient": {"function": "main.fhir.retrieve_Patient"}},
        "changeDataLog": "main.fhir.change_data_log"}


def test_a_tagged_schema_that_breaks_the_layout_fails_the_whole_list():
    broken = {**FHIR_PROD, ("main", "junk"): {"tag": "junk", "routines": []}}
    with pytest.raises(DataSourceLayoutError, match="main.junk"):
        _list(FakeWorkspace(broken))


# --- declaration -------------------------------------------------------------

def test_declaring_a_source_is_its_tag_then_its_comment():
    assert declaration_statements(name="fhir_prod", catalog="main", schema="fhir",
                                  description="Bob's patients") == [
        "ALTER SCHEMA `main`.`fhir` SET TAGS ('cql_databricks_data_source' = 'fhir_prod')",
        "COMMENT ON SCHEMA `main`.`fhir` IS 'Bob\\'s patients'"]
