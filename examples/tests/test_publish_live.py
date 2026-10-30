"""Guarded LIVE test of BIND-PUBLISH: publish a data source from two synthetic tables, then read it back.

On serverless through Databricks Connect, as the caller. It creates a scratch schema in the deployment's
catalog, writes a synthetic Patient and Condition table into it, publishes that schema as a data source with
``publish_data_source``, checks the tag and calls each ``retrieve_<Type>``, then drops the schema. The
model's schema comes from the deployed compiler App (`CLIENT-RESOURCE-SCHEMA`).

SKIPS unless the live prerequisites are present, so it is safe in an otherwise offline run. ``scripts/test.sh
integration`` sets them all, and fails the run if this skips:
  - env CQL_DATABRICKS_LIVE=1 and DATABRICKS_SERVERLESS=1, with Databricks auth (DATABRICKS_CONFIG_PROFILE);
  - env CQL_DATABRICKS_ENDPOINT and CQL_DATABRICKS_TOKEN: the compiler App's URL and a bearer token;
  - env CQL_DATABRICKS_PUBLISH_CATALOG: a catalog where the caller may CREATE SCHEMA, CREATE FUNCTION and
    APPLY TAG (the bundle's ``uc_catalog``).
"""
import os
import uuid

import pytest

ENDPOINT = os.environ.get("CQL_DATABRICKS_ENDPOINT")
TOKEN = os.environ.get("CQL_DATABRICKS_TOKEN")
CATALOG = os.environ.get("CQL_DATABRICKS_PUBLISH_CATALOG")

pytestmark = pytest.mark.skipif(
    os.environ.get("CQL_DATABRICKS_LIVE", "").strip().lower() not in {"1", "true", "yes", "on"}
    or not (ENDPOINT and TOKEN and CATALOG),
    reason="set CQL_DATABRICKS_LIVE=1, CQL_DATABRICKS_ENDPOINT, CQL_DATABRICKS_TOKEN and "
           "CQL_DATABRICKS_PUBLISH_CATALOG (scripts/test.sh integration sets them) to run the live publish")


@pytest.fixture(scope="module")
def spark():
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.serverless(True).getOrCreate()


@pytest.fixture(scope="module")
def scratch(spark):
    """A fresh schema holding two synthetic tables, dropped with everything in it afterwards."""
    schema = f"cqlpublish_{uuid.uuid4().hex[:10]}"
    target = f"`{CATALOG}`.`{schema}`"
    spark.sql(f"CREATE SCHEMA {target} COMMENT 'BIND-PUBLISH live test scratch; safe to drop'")
    try:
        spark.sql(f"CREATE TABLE {target}.`patient` AS "
                  f"SELECT * FROM VALUES ('pub-p1', '1980-04-01', 'female'), ('pub-p2', '1975-09-12', 'male') "
                  f"AS t(id, birthDate, gender)")
        spark.sql(f"CREATE TABLE {target}.`condition` AS "
                  f"SELECT 'pub-c1' AS id, named_struct('reference', 'Patient/pub-p1') AS subject, "
                  f"'2026-02-01' AS onsetDateTime")
        yield CATALOG, schema
    finally:
        spark.sql(f"DROP SCHEMA IF EXISTS {target} CASCADE")


def test_a_published_source_is_tagged_and_each_function_returns_its_table(spark, scratch):
    from cql_databricks.compiler import CqlClient, publish_data_source
    from cql_databricks.compiler.reshape import ERROR

    catalog, schema = scratch
    client = CqlClient.builder().endpoint(ENDPOINT).auth_bearer(TOKEN).build()
    name = schema  # unique per run, so no other data source in scope shares it
    plan = publish_data_source(
        spark, f"{catalog}.{schema}", name=name, description="BIND-PUBLISH live test", client=client,
        fhir={"Patient": f"{catalog}.{schema}.patient", "Condition": f"{catalog}.{schema}.condition"})
    assert [i for i in plan.issues if i.severity == ERROR] == []

    layout = plan.run()
    assert sorted(layout.bindings) == ["Condition", "Patient"]

    tags = spark.sql(
        "SELECT tag_value FROM system.information_schema.schema_tags "
        "WHERE catalog_name = :catalog AND schema_name = :schema AND tag_name = 'cql_databricks_data_source'",
        args={"catalog": catalog, "schema": schema}).collect()
    assert [r["tag_value"] for r in tags] == [name]

    patients = spark.sql(f"SELECT `__subject_id`, `__resource`.`gender` AS gender, "
                         f"`__resource`.`__resource_type` AS t FROM `{catalog}`.`{schema}`.`retrieve_Patient`()"
                         ).collect()
    assert sorted((r["__subject_id"], r["gender"], r["t"]) for r in patients) == [
        ("pub-p1", "female", "Patient"), ("pub-p2", "male", "Patient")]
    conditions = spark.sql(f"SELECT `__subject_id`, `__resource`.`id` AS id "
                           f"FROM `{catalog}`.`{schema}`.`retrieve_Condition`()").collect()
    assert [(r["__subject_id"], r["id"]) for r in conditions] == [("pub-p1", "pub-c1")]
