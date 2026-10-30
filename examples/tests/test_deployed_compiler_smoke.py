"""Guarded LIVE integration test — Path A of the notebook cql() smoke (SVC-RUN-EVAL over EXEC-SQL-BACKEND).

Hits the DEPLOYED CQL on Databricks Compiler Service App via the OSS client's RemoteProvider + a StaticBearer token,
compiles `define "One": 1 + 1`, runs the emitted SQL in a LOCAL SparkSession, and asserts One == 2. This
is the automated fixture for the vetted end-to-end capability (the notebook path is validated by hand /
on serverless; this covers the client->deployed-compiler->SQL->Spark wire that a local task CAN drive).

SKIPS automatically unless the live prerequisites are present, so it is safe to include in an otherwise
offline `Test: All`:
  - env CQL_DATABRICKS_TOKEN  — a bearer token: `databricks auth token -p <profile>` (parse .access_token)
  - pyspark importable + a JDK (JAVA_HOME) for the local Spark run
  - the App must be RUNNING — a stopped App redirects, surfaced here as a clear FAIL (not a skip)
  - env CQL_DATABRICKS_ENDPOINT — the deployed compiler App's URL (there is no default; see below)
"""
import os

import pytest

# There is deliberately NO default host. The previous literal was a stale hostname from an earlier App
# name, and the monorepo rename then corrupted it into `https://server/compiler-...` — a slash in
# the authority, so not a parseable URL at all. A wrong-but-plausible default is worse than none here:
# it turns "you did not configure an endpoint" into a connection error against someone else's workspace.
# Every App gets a fresh databricksapps.com hostname on deploy, so the value cannot live in source.
#   databricks apps get cql-databricks-compiler -p <profile> -o json | jq -r .url
ENDPOINT = os.environ.get("CQL_DATABRICKS_ENDPOINT")

# BOTH are required, and the endpoint is now part of the guard rather than a defaulted literal — so an
# unconfigured run SKIPS with an actionable reason instead of dialling a stale host.
pytestmark = pytest.mark.skipif(
    not (os.environ.get("CQL_DATABRICKS_TOKEN") and ENDPOINT),
    reason="set CQL_DATABRICKS_ENDPOINT (the deployed compiler App's URL) and CQL_DATABRICKS_TOKEN "
           "(`databricks auth token -p <profile>`), and start the App, to run the live smoke")


@pytest.fixture(scope="module")
def spark():
    # SVC-RUN-DBCONNECT: when a Databricks cluster/serverless is configured, run on that compute via
    # databricks-connect (the cluster auto-starts) instead of a local SparkSession — mirroring how the
    # Designer BFF runs (cql_databricks_designer/run.py). Auth resolves from the ambient Databricks config
    # (DATABRICKS_CONFIG_PROFILE). Otherwise fall back to local Spark so the smoke still runs offline.
    cluster = os.environ.get("DATABRICKS_CLUSTER_ID")
    serverless = os.environ.get("DATABRICKS_SERVERLESS", "").strip().lower() in {"1", "true", "yes", "on"}
    if cluster or serverless:
        from databricks.connect import DatabricksSession
        b = DatabricksSession.builder
        s = (b.serverless(True) if serverless else b.clusterId(cluster)).getOrCreate()
        yield s
        return  # a shared cluster session — don't stop it
    pytest.importorskip("pyspark")  # skip cleanly if pyspark isn't installed
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[1]").appName("cql-databricks-live-smoke")
         .config("spark.ui.enabled", "false").getOrCreate())
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


def test_define_one_equals_two(spark):
    from cql_databricks.compiler import RemoteProvider, StaticBearer, load_library, cql
    from cql_databricks.compiler.providers import VALUE_COLUMN

    provider = RemoteProvider(ENDPOINT, StaticBearer(os.environ["CQL_DATABRICKS_TOKEN"]))
    # No context arg: a context-free define defaults to the CQL-standard Unfiltered (LANG-CONTEXT-DEFAULT),
    # so `1 + 1` compiles data-free — the forced-Patient default that once needed a workaround is gone.
    lib = load_library(cql='define "One": 1 + 1', provider=provider)

    q = cql(lib, spark=spark)                        # lazy CqlQuery handle (BUILD-CLIENT-QUERY)
    assert q.describe().defines == ["One"]           # compile reached the deployed App
    df = q.exec("One")                               # exec() terminal: one target -> DataFrame
    # EMIT-SQL-VALUE-COLUMN: a define's result is (__subject_id, __val), not a column named for the define.
    assert df.collect()[0][VALUE_COLUMN] == 2        # end-to-end: notebook->compiler->SQL->Spark
