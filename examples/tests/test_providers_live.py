"""Guarded LIVE tests of the providers (CLIENT-DATA-PROVIDER, CLIENT-DEFAULT-RESOLVER, CLIENT-TERMINOLOGY-PROVIDER,
BIND-PROVIDER-REQUEST, BIND-RELATION), as the caller.

On a SQL warehouse, the paths the Designer and the agent's MCP tools run (`CLIENT-DEFAULT-RESOLVER`): an inline run
through ``run_inline`` (the agent's ``run_cql``), a code lookup through the tagged terminology (its
``value_sets_with_code``), and a durable preparation and materialization (the Designer's run) into a scratch schema.
On serverless Spark, one run on each shipped data provider: the tagged data source, tables, functions, a volume
folder and DataFrames, and value sets from a volume folder.

Every run reads the tagged data source named by ``CQL_DATABRICKS_LIVE_DATA_SOURCE`` (default
``synthetic-population-small``) and its scratch copies; scratch objects live in a schema of
``CQL_DATABRICKS_PUBLISH_CATALOG`` created and dropped here. The value set is the example measures' synthetic
diabetes set.

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
SOURCE = os.environ.get("CQL_DATABRICKS_LIVE_DATA_SOURCE", "synthetic-population-small")
VALUE_SET = "http://example.org/fhir/ValueSet/diabetes"
CODE = ("http://hl7.org/fhir/sid/icd-10-cm", "E10.9")

CQL = f"""library ProvidersLive version '0.0.1'
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
           "CQL_DATABRICKS_WAREHOUSE_ID (scripts/test.sh integration sets them) to run the live provider tests")


def _compiler():
    from cql_databricks.compiler import RemoteProvider, StaticBearer
    from cql_databricks.compiler.compile_shim import CqlDatabricksCompileProvider

    return CqlDatabricksCompileProvider(RemoteProvider(ENDPOINT, StaticBearer(TOKEN), timeout=300.0))


@pytest.fixture(scope="module")
def warehouse():
    from cql_databricks_common.warehouse import user_session

    return user_session(WAREHOUSE)


@pytest.fixture(scope="module")
def content(warehouse):
    from cql_databricks.data_bindings import ContentResolver

    return ContentResolver(lambda: warehouse, [CATALOG])


@pytest.fixture(scope="module")
def spark():
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.serverless(True).getOrCreate()


@pytest.fixture(scope="module")
def scratch(warehouse, content):
    """A schema holding copies of the source's Patient and Condition on the contract, and a volume, dropped after."""
    from cql_databricks.compiler.tagged import TaggedResolver

    source = TaggedResolver(content, warehouse).data(SOURCE)
    schema = f"cqlproviders_{uuid.uuid4().hex[:10]}"
    target = f"`{CATALOG}`.`{schema}`"
    warehouse(f"CREATE SCHEMA {target} COMMENT 'provider live test scratch; safe to drop'")
    try:
        for fhir_type in ("Patient", "Condition"):
            relation = source.retrieve(_request(fhir_type)).name
            call = relation + "()" if type(source.retrieve(_request(fhir_type))).__name__ == "FunctionRelation" else relation
            warehouse(f"CREATE TABLE {target}.`{fhir_type.lower()}` AS SELECT __subject_id, __resource FROM {call}")
        warehouse(f"CREATE VOLUME {target}.`files`")
        yield {"schema": f"{CATALOG}.{schema}", "volume": f"/Volumes/{CATALOG}/{schema}/files", "source": source}
    finally:
        warehouse(f"DROP SCHEMA IF EXISTS {target} CASCADE")


def _request(fhir_type):
    from cql_databricks.compiler.roles import RetrieveRequest

    return RetrieveRequest(fhir_type=fhir_type)


def _counts(rows):
    return {k: int(v) for k, v in rows.items()}


# ------------------------------------------------------------------ warehouse: the Designer and the agent


def test_an_inline_run_reads_the_tagged_source_and_terminology(warehouse, content):
    """CLIENT-INLINE-WAREHOUSE, CLIENT-DEFAULT-RESOLVER: the agent's run_cql, as the caller."""
    from cql_databricks.result import run_inline

    with run_inline(content=content, run=warehouse, compiler=_compiler(), data_source=SOURCE, cql=CQL,
                    runtime_catalog=CATALOG, period=("2026-01-01", "2026-12-31")) as run:
        counts = run.counts()
    assert counts["Initial Population"] > 0
    assert 0 < counts["Numerator"] < counts["Initial Population"]


def test_a_code_is_found_in_the_tagged_terminology(warehouse, content):
    """AGENT-VALUE-SET-LOOKUP: the agent's value_sets_with_code."""
    from cql_databricks.compiler.tagged import TaggedResolver

    hits = TaggedResolver(content, warehouse).terminology().find_code(CODE[1], CODE[0])
    assert VALUE_SET in {h.id for h in hits}
    assert all(h.system == CODE[0] for h in hits)


def test_a_durable_run_keeps_results_under_the_providers_identity(warehouse, content, scratch):
    """CLIENT-PROVIDER-IDENTITY, CLIENT-RESULT-STORE: the Designer's run, then the same run reused."""
    from cql_databricks.result import materialize, prepare_calculation

    def run():
        prepared = prepare_calculation(content=content, run=warehouse, compiler=_compiler(), data_source=SOURCE,
                                       cql=CQL, runtime_catalog=CATALOG, period=("2026-01-01", "2026-12-31"))
        return materialize(prepared, *prepared.targets, run=warehouse,
                           destination=tuple(scratch["schema"].split(".")), catalog_scopes=[scratch["schema"]])

    first = run()
    assert {o.decision for o in first.outcomes} == {"computed"}
    again = run()
    assert {o.decision for o in again.outcomes} == {"reused"}


# ------------------------------------------------------------------ Spark: each shipped provider


def _client(spark, content, data, terminology=None):
    from cql_databricks.compiler import CqlClient
    from cql_databricks.compiler.knowledge_providers import DeltaKnowledge, library_provider
    from cql_databricks.compiler.provider_binder import ProviderBinder
    from cql_databricks.data_bindings import spark_runner

    compiler = _compiler()
    binder = ProviderBinder(data=data, terminology=terminology, run=lambda: spark_runner(spark), spark=spark,
                            schemas=compiler.resource_schemas)
    return (CqlClient.builder().provider(compiler).spark(spark).udf_schema(f"{CATALOG}.runtime")
            .library_provider(library_provider(DeltaKnowledge(lambda: content))).data_binder(binder).build())


def _exec(spark, content, data, terminology=None):
    query = _client(spark, content, data, terminology).cql(CQL)
    frames = query.exec("Initial Population", "Numerator")
    return {name: frame.filter("__val").count() for name, frame in frames.items()}


@pytest.fixture(scope="module")
def expected(spark, content, warehouse):
    from cql_databricks.compiler.tagged import TaggedResolver

    resolver = TaggedResolver(content, warehouse)
    counts = _exec(spark, content, resolver.data(SOURCE), resolver.terminology())
    assert counts["Initial Population"] > 0 and counts["Numerator"] > 0
    return counts, resolver.terminology(), content


def test_tables_answer_as_the_tagged_source(spark, scratch, expected):
    from cql_databricks.compiler.data_providers import Tables

    counts, terminology, content = expected
    tables = Tables({t: f"{scratch['schema']}.{t.lower()}" for t in ("Patient", "Condition")})
    assert _exec(spark, content, tables, terminology) == counts


def test_functions_answer_as_the_tagged_source(spark, scratch, expected):
    from cql_databricks.compiler.data_providers import Functions

    counts, terminology, content = expected
    source = scratch["source"]
    functions = Functions({t: f"{source.catalog}.{source.schema}.retrieve_{t.lower()}" for t in ("Patient", "Condition")})
    assert _exec(spark, content, functions, terminology) == counts


def test_dataframes_answer_as_the_tagged_source_and_are_never_kept(spark, scratch, expected):
    from cql_databricks.compiler.data_providers import Frames

    counts, terminology, content = expected
    frames = Frames({t: spark.table(f"{scratch['schema']}.{t.lower()}") for t in ("Patient", "Condition")})
    assert _exec(spark, content, frames, terminology) == counts


def test_a_volume_folder_and_value_set_json_answer_as_the_tagged_source(spark, scratch, expected):
    """BIND-RELATION, CLIENT-TERMINOLOGY-PROVIDER: FHIR JSON and a ValueSet in a volume, read with read_files."""
    from databricks.sdk import WorkspaceClient

    from cql_databricks.compiler.data_providers import VolumeFolder
    from cql_databricks.compiler.terminology_providers import ValueSetFolder

    counts, _, content = expected
    files = WorkspaceClient().files
    for fhir_type in ("Patient", "Condition"):
        rows = spark.sql(f"SELECT to_json(__resource) AS j FROM {scratch['schema']}.{fhir_type.lower()}").collect()
        body = "\n".join(json.dumps(_fhir(json.loads(r["j"]), fhir_type)) for r in rows)
        files.upload(f"{scratch['volume']}/fhir/{fhir_type}.ndjson", io.BytesIO(body.encode()), overwrite=True)
    codes = spark.sql(f"SELECT system, code FROM {CATALOG}.example_measures.valueset WHERE valueset_id = '{VALUE_SET}'"
                      ).collect()
    value_set = {"resourceType": "ValueSet", "url": VALUE_SET,
                 "expansion": {"contains": [{"system": r["system"], "code": r["code"]} for r in codes]}}
    files.upload(f"{scratch['volume']}/vs/diabetes.json", io.BytesIO(json.dumps(value_set).encode()), overwrite=True)
    assert _exec(spark, content, VolumeFolder(f"{scratch['volume']}/fhir", types=["Patient", "Condition"]),
                 ValueSetFolder(f"{scratch['volume']}/vs")) == counts


def _fhir(resource, fhir_type):
    """A contract struct back to FHIR JSON: the lineage fields dropped, the type restored."""
    out = {k: v for k, v in resource.items() if not k.startswith("__")}
    out["resourceType"] = fhir_type
    return out
