"""Guarded LIVE test of BIND-PUBLISH: plan an environment's data views over two synthetic tables, and read them.

On serverless through Databricks Connect, as the caller. It creates a scratch schema in the deployment's
catalog, writes a synthetic Patient and Condition table into it, plans the environment's data views over them with
``publish_data_source``, queries each view's body without creating it, then drops the schema. The
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


def test_a_publish_plan_reshapes_each_table_to_the_contract(spark, scratch):
    """BIND-PUBLISH, BIND-RESHAPE: the plan for an environment's data views over two tables. It is reviewed, not
    run: running it replaces the environment's own ``data`` views, so each view's body is queried instead."""
    from cql_databricks.compiler import CqlClient, publish_data_source
    from cql_databricks.compiler.reshape import ERROR

    catalog, schema = scratch
    client = CqlClient.builder().endpoint(ENDPOINT).auth_bearer(TOKEN).build()
    fhir = {"Patient": f"{catalog}.{schema}.patient", "Condition": f"{catalog}.{schema}.condition"}
    plan = publish_data_source(spark, catalog, client=client, fhir=fhir)
    assert [i for i in plan.issues if i.severity == ERROR] == []
    assert plan.views == {"Condition": f"{catalog}.data.condition", "Patient": f"{catalog}.data.patient"}
    bodies = {}
    for statement in plan.statements:
        head, body = statement.split(" AS ", 1)
        assert head.startswith("CREATE OR REPLACE VIEW ")
        bodies[head.rsplit(".", 1)[1].strip("`")] = body

    patients = spark.sql(f"SELECT `__subject_id`, `__resource`.`gender` AS gender "
                         f"FROM ({bodies['patient']})").collect()
    assert sorted((r["__subject_id"], r["gender"]) for r in patients) == [("pub-p1", "female"), ("pub-p2", "male")]
    conditions = spark.sql(f"SELECT `__subject_id`, `__resource`.`id` AS id FROM ({bodies['condition']})").collect()
    assert [(r["__subject_id"], r["id"]) for r in conditions] == [("pub-p1", "pub-c1")]
