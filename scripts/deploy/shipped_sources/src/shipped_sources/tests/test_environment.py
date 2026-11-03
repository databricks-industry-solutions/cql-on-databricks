"""DEPLOY-DEFAULT-ENVIRONMENT, DATA-SYNTH-LARGE, DATA-ENVIRONMENT-LAYOUT, RUN-CLIENT-DRAFT-RESULTS — filling the default
environment, whose schemas the bundle makes, and building one outside the bundle. No Spark: the statements are
recorded, and the one environment query is answered from synthetic catalog tags."""
from __future__ import annotations

import pytest

from shipped_sources import environment

TAGS = [{"catalog": "other_cat", "tag": "cql_databricks_environment", "value": "other", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment_version", "value": "2", "comment": None}]


def _build(tags, *, default=True, maintenance="maint-sp"):
    statements, order = [], []

    def run(sql, params=None):
        assert "catalog_tags" in sql
        return tags

    def load():
        order.append(len(statements))
        return {"Patient": 3}

    counts = environment.build(statements.append, run, catalog="env_cat", name="example", description="It's synthetic",
                               default=default, load=load, users="app users", maintenance=maintenance,
                               content_readers=["designer-sp"])
    return statements, order[0], counts


def test_schemas_come_first_then_the_content_then_views_tags_and_grants():
    statements, loaded_at, counts = _build(TAGS)
    assert counts == {"Patient": 3}
    schemas = [s for s in statements if s.startswith("CREATE SCHEMA IF NOT EXISTS")]
    assert [s.split("`")[3] for s in schemas] == ["data", "data_store", "knowledge", "knowledge_store", "results",
                                                  "draft_results"]
    assert loaded_at == len(schemas)
    views = statements[loaded_at:loaded_at + 3]
    assert views == [
        "CREATE OR REPLACE VIEW `env_cat`.`knowledge`.`valueset` AS "
        "SELECT valueset_id, system, code, version FROM `env_cat`.`knowledge_store`.`valueset`",
        "CREATE OR REPLACE VIEW `env_cat`.`knowledge`.`library` AS SELECT * FROM `env_cat`.`knowledge_store`.`library`",
        "CREATE OR REPLACE VIEW `env_cat`.`knowledge`.`measure` AS SELECT * FROM `env_cat`.`knowledge_store`.`measure`"]
    assert statements[loaded_at + 3] == (
        "ALTER CATALOG `env_cat` SET TAGS ('cql_databricks_environment' = 'example', "
        "'cql_databricks_environment_version' = '2', 'cql_databricks_environment_default' = 'true')")
    assert statements[loaded_at + 4] == "COMMENT ON CATALOG `env_cat` IS 'It\\'s synthetic'"


def test_the_catalog_is_the_default_only_if_no_other_visible_catalog_is():
    tagged_default = TAGS + [{"catalog": "other_cat", "tag": "cql_databricks_environment_default", "value": "true",
                              "comment": None}]
    statements, _, _ = _build(tagged_default)
    [tags] = [s for s in statements if s.startswith("ALTER CATALOG")]
    assert "cql_databricks_environment_default" not in tags
    statements, _, _ = _build(tagged_default, default=False)
    assert all("cql_databricks_environment_default" not in s for s in statements)


def test_users_read_published_schemas_and_create_only_their_own_draft_tables():
    statements, _, _ = _build(TAGS)
    users = [s for s in statements if s.endswith("TO `app users`")]
    assert users == ["GRANT USE CATALOG ON CATALOG `env_cat` TO `app users`",
                     "GRANT USE SCHEMA, SELECT ON SCHEMA `env_cat`.`data` TO `app users`",
                     "GRANT USE SCHEMA, SELECT ON SCHEMA `env_cat`.`knowledge` TO `app users`",
                     "GRANT USE SCHEMA, SELECT ON SCHEMA `env_cat`.`results` TO `app users`",
                     "GRANT USE SCHEMA, CREATE TABLE ON SCHEMA `env_cat`.`draft_results` TO `app users`"]
    assert not any("_store" in s or "SELECT ON SCHEMA `env_cat`.`draft_results`" in s or "MODIFY" in s
                   for s in users)


def test_maintenance_manages_draft_results_and_the_designer_reads_only_content():
    statements, _, _ = _build(TAGS)
    assert "GRANT USE SCHEMA, MANAGE ON SCHEMA `env_cat`.`draft_results` TO `maint-sp`" in statements
    designer = [s for s in statements if s.endswith("TO `designer-sp`")]
    assert designer == ["GRANT USE CATALOG ON CATALOG `env_cat` TO `designer-sp`",
                        "GRANT USE SCHEMA, SELECT ON SCHEMA `env_cat`.`knowledge` TO `designer-sp`"]
    statements, _, _ = _build(TAGS, maintenance=None)
    assert not any("MANAGE" in s for s in statements)


def test_filling_makes_no_schema_and_no_grant():
    """DEPLOY-DEFAULT-ENVIRONMENT: the default environment's schemas and schema grants are the bundle's."""
    statements = []
    counts = environment.fill(statements.append, lambda *a: TAGS, catalog="env_cat", name="example",
                              description="d", default=True, load=lambda: {"Patient": 3})
    assert counts == {"Patient": 3}
    assert statements[:3] == environment.content_view_statements("env_cat")
    assert [s.split(" ", 2)[:2] for s in statements[3:]] == [["ALTER", "CATALOG"], ["COMMENT", "ON"]]


def test_the_grants_a_bundle_cannot_make():
    """USE CATALOG on a catalog the bundle does not hold, and the maintenance principal's MANAGE."""
    assert environment.catalog_grant("env_cat", "app users") == "GRANT USE CATALOG ON CATALOG `env_cat` TO `app users`"
    assert environment.maintenance_grant("env_cat", "maint-sp") == (
        "GRANT USE SCHEMA, MANAGE ON SCHEMA `env_cat`.`draft_results` TO `maint-sp`")


def test_a_failing_statement_fails_the_build():
    def sql(statement):
        if "draft_results" in statement:
            raise RuntimeError("PERMISSION_DENIED")

    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        environment.build(sql, lambda *a: [], catalog="c", name="n", description="d", default=True,
                          load=lambda: {}, users="u")


def test_each_type_has_a_published_view_over_its_store_table():
    """BIND-PUBLISHED-VIEW, BIND-CONTRACT, DATA-SUBJECT-KEY: the store holds the contract, so the view selects it."""
    assert environment.data_view("c", "MedicationRequest") == (
        "CREATE OR REPLACE VIEW `c`.`data`.`medicationrequest` AS "
        "SELECT `patient_id`, `resource` FROM `c`.`data_store`.`medicationrequest`")
