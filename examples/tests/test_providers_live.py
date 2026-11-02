"""Guarded LIVE tests of the resolvers (CLIENT-ENVIRONMENT-RESOLVER, CLIENT-DATA-RESOLVER, CLIENT-TERMINOLOGY-RESOLVER,
BIND-CALL-SITE, BIND-PUBLISHED-VIEW, BIND-RELATION), as the caller.

On a SQL warehouse, the paths the Designer and the agent's MCP tools run in an environment (`CLIENT-ENVIRONMENT`): an
inline run through ``run_inline`` (the agent's ``run_cql``), a code lookup through the environment's terminology (its
``value_sets_with_code``), and a durable preparation and materialization into the environment's ``draft_results`` (the
Designer's run), then the same run reused. On serverless Spark, one run on each shipped data resolver: the
environment's views, tables, a volume folder and DataFrames, and value sets from a volume folder.

Every run is in the environment ``CQL_DATABRICKS_ENVIRONMENT`` names, else the default one; scratch copies of its
Patient and Condition views live in a schema of ``CQL_DATABRICKS_PUBLISH_CATALOG`` created and dropped here. The
value set is the example measures' synthetic diabetes set.

SKIPS unless the live prerequisites are present. ``scripts/test.sh integration`` sets them all, and fails the run if
this skips: CQL_DATABRICKS_LIVE=1, DATABRICKS_SERVERLESS=1 with Databricks auth (DATABRICKS_CONFIG_PROFILE);
CQL_DATABRICKS_ENDPOINT and CQL_DATABRICKS_TOKEN (the compiler); CQL_DATABRICKS_PUBLISH_CATALOG (the bundle's
``uc_catalog``, where the runtime functions are in ``runtime``); CQL_DATABRICKS_WAREHOUSE_ID.
"""
import io
import json
import os
import uuid

import pytest

ENDPOINT = os.environ.get("CQL_DATABRICKS_ENDPOINT")
TOKEN = os.environ.get("CQL_DATABRICKS_TOKEN")
CATALOG = os.environ.get("CQL_DATABRICKS_PUBLISH_CATALOG")
WAREHOUSE = os.environ.get("CQL_DATABRICKS_WAREHOUSE_ID")
ENVIRONMENT = os.environ.get("CQL_DATABRICKS_ENVIRONMENT") or None
VALUE_SET = "http://example.org/fhir/ValueSet/diabetes"
CODE = ("http://hl7.org/fhir/sid/icd-10-cm", "E10.9")
TYPES = ("Patient", "Condition")

CQL = f"""library ResolversLive version '0.0.1'
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers
valueset "Diabetes": '{VALUE_SET}'
context Patient
define "Initial Population": exists [Patient]
define "Numerator": exists [Condition: "Diabetes"]
"""

pytestmark = pytest.mark.skipif(
    os.environ.get("CQL_DATABRICKS_LIVE", "").strip().lower() not in {"1", "true", "yes", "on"}
    or not (ENDPOINT and TOKEN and CATALOG and WAREHOUSE),
    reason="set CQL_DATABRICKS_LIVE=1, CQL_DATABRICKS_ENDPOINT, CQL_DATABRICKS_TOKEN, CQL_DATABRICKS_PUBLISH_CATALOG and "
           "CQL_DATABRICKS_WAREHOUSE_ID (scripts/test.sh integration sets them) to run the live resolver tests")


def _compiler():
    from cql_databricks.compiler import RemoteProvider, StaticBearer
    from cql_databricks.compiler.compile_shim import CqlDatabricksCompileProvider

    return CqlDatabricksCompileProvider(RemoteProvider(ENDPOINT, StaticBearer(TOKEN), timeout=300.0))


@pytest.fixture(scope="module")
def warehouse():
    from cql_databricks_common.warehouse import user_session

    return user_session(WAREHOUSE)


@pytest.fixture(scope="module")
def environment(warehouse):
    """CLIENT-ENVIRONMENT-RESOLVER: the environment the run is in, its resolver and its content, found as the caller."""
    from cql_databricks.compiler import EnvironmentResolver

    environments = EnvironmentResolver(warehouse)
    env = environments.resolve(ENVIRONMENT)
    return environments, env, environments.content(env)


@pytest.fixture(scope="module")
def spark():
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.serverless(True).getOrCreate()


@pytest.fixture(scope="module")
def scratch(warehouse, environment):
    """A schema holding copies of the environment's Patient and Condition views, and a volume, dropped after."""
    from cql_databricks.compiler.environment import DATA

    _, env, _ = environment
    schema = f"cqlresolvers_{uuid.uuid4().hex[:10]}"
    target = f"`{CATALOG}`.`{schema}`"
    warehouse(f"CREATE SCHEMA {target} COMMENT 'resolver live test scratch; safe to drop'")
    try:
        for fhir_type in TYPES:
            warehouse(f"CREATE TABLE {target}.`{fhir_type.lower()}` AS SELECT __subject_id, __resource "
                      f"FROM {env.schema(DATA)}.{fhir_type.lower()}")
        warehouse(f"CREATE VOLUME {target}.`files`")
        yield {"schema": f"{CATALOG}.{schema}", "volume": f"/Volumes/{CATALOG}/{schema}/files"}
    finally:
        warehouse(f"DROP SCHEMA IF EXISTS {target} CASCADE")


# ------------------------------------------------------------------ warehouse: the Designer and the agent


def test_an_inline_run_reads_the_environments_views(warehouse, environment):
    """CLIENT-INLINE-WAREHOUSE, BIND-PUBLISHED-VIEW: the agent's run_cql, as the caller."""
    from cql_databricks.result import run_inline

    _, env, content = environment
    with run_inline(content=content, run=warehouse, compiler=_compiler(), environment=env, cql=CQL,
                    runtime_catalog=CATALOG, period=("2026-01-01", "2026-12-31")) as run:
        counts = run.counts()
    assert counts["Initial Population"] > 0
    assert 0 < counts["Numerator"] < counts["Initial Population"]


def test_a_code_is_found_in_the_environments_terminology(environment):
    """AGENT-VALUE-SET-LOOKUP: the agent's value_sets_with_code."""
    environments, env, _ = environment
    hits = environments.terminology(env).find_code(CODE[1], CODE[0])
    assert VALUE_SET in {h.id for h in hits}
    assert all(h.system == CODE[0] for h in hits)


def test_a_draft_run_is_kept_then_reused_by_its_owner(warehouse, environment):
    """RUN-CLIENT-DRAFT-RESULTS, RUN-CLIENT-RESULT-OWNERSHIP: the Designer's run, then the same run reused."""
    from cql_databricks.result import materialize, prepare_calculation

    _, env, content = environment

    def run():
        prepared = prepare_calculation(content=content, run=warehouse, compiler=_compiler(), environment=env,
                                       cql=CQL, runtime_catalog=CATALOG, period=("2026-01-01", "2026-12-31"))
        return materialize(prepared, *prepared.targets, run=warehouse, environment=env, purpose="private")

    run()
    again = run()
    assert {o.decision for o in again.outcomes} == {"reused"}


# ------------------------------------------------------------------ Spark: each shipped data resolver


def _exec(spark, environment, data, terminology):
    """CLIENT-RESOLVERS: ``CQL`` run with ``data`` and ``terminology``, its libraries from the environment's knowledge."""
    from cql_databricks.compiler import CqlClient
    from cql_databricks.compiler.knowledge_resolvers import library_provider
    from cql_databricks.compiler.resolver_binder import ResolverBinder
    from cql_databricks.data_bindings import spark_runner

    environments, env, content = environment
    compiler = _compiler()
    binder = ResolverBinder(data=data, terminology=terminology, run=lambda: spark_runner(spark), spark=spark,
                            schemas=compiler.resource_schemas)
    client = (CqlClient.builder().provider(compiler).spark(spark).udf_schema(f"{CATALOG}.runtime")
              .library_provider(library_provider(environments.knowledge(env, content))).data_binder(binder).build())
    frames = client.cql(CQL).exec("Initial Population", "Numerator")
    return {name: frame.filter("__val").count() for name, frame in frames.items()}


@pytest.fixture(scope="module")
def expected(spark, environment):
    """The counts over the environment's own views, which every other resolver must reproduce."""
    environments, env, _ = environment
    counts = _exec(spark, environment, environments.data(env), environments.terminology(env))
    assert counts["Initial Population"] > 0 and counts["Numerator"] > 0
    return counts


def test_tables_answer_as_the_environment(spark, environment, scratch, expected):
    from cql_databricks.compiler import TableDataResolver

    environments, env, _ = environment
    tables = TableDataResolver({t: f"{scratch['schema']}.{t.lower()}" for t in TYPES})
    assert _exec(spark, environment, tables, environments.terminology(env)) == expected


def test_dataframes_answer_as_the_environment_and_are_never_kept(spark, environment, scratch, expected):
    from cql_databricks.compiler import DataFrameDataResolver

    environments, env, _ = environment
    frames = DataFrameDataResolver({t: spark.table(f"{scratch['schema']}.{t.lower()}") for t in TYPES})
    assert not frames.reusable
    assert _exec(spark, environment, frames, environments.terminology(env)) == expected


def test_a_volume_folder_and_value_set_json_answer_as_the_environment(spark, environment, scratch, expected):
    """BIND-RELATION, CLIENT-TERMINOLOGY-RESOLVER: FHIR NDJSON and a ValueSet in a volume, read with read_files."""
    from databricks.sdk import WorkspaceClient

    from cql_databricks.compiler import FolderTerminologyResolver, VolumeDataResolver
    from cql_databricks.compiler.environment import TERMINOLOGY

    _, env, _ = environment
    files = WorkspaceClient().files
    for fhir_type in TYPES:
        rows = spark.sql(f"SELECT to_json(__resource) AS j FROM {scratch['schema']}.{fhir_type.lower()}").collect()
        body = "\n".join(json.dumps(_fhir(json.loads(r["j"]), fhir_type)) for r in rows)
        files.upload(f"{scratch['volume']}/fhir/{fhir_type}.ndjson", io.BytesIO(body.encode()), overwrite=True)
    codes = spark.sql(f"SELECT system, code FROM {env.schema(TERMINOLOGY)}.valueset WHERE valueset_id = '{VALUE_SET}'"
                      ).collect()
    value_set = {"resourceType": "ValueSet", "url": VALUE_SET,
                 "expansion": {"contains": [{"system": r["system"], "code": r["code"]} for r in codes]}}
    files.upload(f"{scratch['volume']}/vs/diabetes.json", io.BytesIO(json.dumps(value_set).encode()), overwrite=True)
    assert _exec(spark, environment, VolumeDataResolver(f"{scratch['volume']}/fhir", types=list(TYPES)),
                 FolderTerminologyResolver(f"{scratch['volume']}/vs")) == expected


def _fhir(resource, fhir_type):
    """A contract struct back to FHIR JSON: the lineage fields dropped, the type restored."""
    out = {k: v for k, v in resource.items() if not k.startswith("__")}
    out["resourceType"] = fhir_type
    return out
