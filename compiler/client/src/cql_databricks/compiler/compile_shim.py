"""Notebook-side compile adaptation + the `cql_*` catalog discovery that feeds it
(`SVC-NOTEBOOK-RUNTIME-BIND`).

ONE shim remains, entirely client-side, without touching the compiler area (`APP-DECOUPLE`):

* **SVC-RUN-SERVERLESS-BARRIER** — rewrite the engine's ``CACHE TABLE <n> AS <select>`` materialization
  barriers to ``CREATE OR REPLACE TEMP VIEW``. Databricks serverless rejects ``CACHE TABLE AS SELECT``
  (``NOT_SUPPORTED_WITH_SERVERLESS``) and a measure population is ALWAYS materialized, so every measure
  would fail. The rewrite is applied UNCONDITIONALLY rather than behind a "am I on serverless" branch: a
  barrier only cuts lineage / caches, so the swap changes latency, never the computed rows
  (`DATA-MATERIALIZE-POLICY`) — one path, no compute-shaped behavior fork.

**UDF schema qualification is now ordinary configuration**, set in :func:`~cql_databricks.compiler.zeroconfig.connect`
as a first-class field rather than mutating the request body. The client's ``.udf_schema(...)`` method and
``_sources_json`` field emit ``sources.udfs.schema`` without the body mutation that used to be needed here.
This reduces the places where the accelerator and the engine can disagree about the same wire.

The UDF schema is still DISCOVERED, not configured: UC itself records where the catalog was registered, so
:func:`discover_udf_schema` asks ``system.information_schema.routines``. That keeps
`SVC-ZERO-CONFIG-NOTEBOOK` intact — a notebook names no catalog, no schema, and no endpoint.
"""
from __future__ import annotations

from cql_databricks.compiler.providers import CompileRequest, RemoteProvider

# The engine renders a materialization barrier as `CACHE TABLE <ident> AS\n<select>` (engine `SqlRender`).
# The view form has an identical body, so the barrier is a pure keyword-prefix swap.
_CACHE_PREFIX = "CACHE TABLE "
_VIEW_PREFIX = "CREATE OR REPLACE TEMP VIEW "

# One `cql_*` function locates the whole catalog: the deploy registers all nine into a single schema
# (`scripts/deploy/register-uc-udfs.sh`, SVC-RUNTIME-UDF-UC), so whichever schema holds this one holds them all.
_PROBE_UDF = "cql_age_at"


def discover_udf_schema(spark) -> str:
    """The ``catalog.schema`` holding the registered ``cql_*`` UC function catalog (`SVC-RUNTIME-UDF-UC`).

    Asks Unity Catalog where the catalog actually landed rather than taking it as configuration — UC's own
    ``system.information_schema.routines`` is the record of the deploy, so a notebook needs no catalog
    name (`SVC-ZERO-CONFIG-NOTEBOOK`). Raises when the catalog is absent: an unregistered ``cql_*`` catalog
    means every temporal / quantity / age operator fails later with an opaque ``UNRESOLVED_ROUTINE``, so it
    is reported here with the fix (`CORE-NO-SILENT-MISLOWER`)."""
    if spark is None:
        raise RuntimeError(
            "cannot discover the cql_* UDF catalog without a Spark session — call connect(spark) from a "
            "Databricks notebook or job, or pass udf_schema='<catalog>.<schema>' explicitly.")
    rows = spark.sql(
        "SELECT routine_catalog, routine_schema FROM system.information_schema.routines "
        f"WHERE routine_name = '{_PROBE_UDF}'").collect()
    if not rows:
        raise RuntimeError(
            f"the cql_* UDF catalog is not registered in Unity Catalog (no '{_PROBE_UDF}' in "
            "system.information_schema.routines). Deploy it with scripts/deploy/register-uc-udfs.sh "
            "(SVC-RUNTIME-UDF-UC), or pass udf_schema='<catalog>.<schema>' explicitly.")
    return f"{rows[0]['routine_catalog']}.{rows[0]['routine_schema']}"


class CqlDatabricksCompileProvider(RemoteProvider):
    """A ``RemoteProvider`` that rewrites ``CACHE TABLE`` barriers to temp views
    (`SVC-RUN-SERVERLESS-BARRIER`). The request itself is the OSS provider's — this only post-processes the
    compiled script, so every other wire concern stays in one place."""

    def __init__(self, base: RemoteProvider) -> None:
        super().__init__(base._base, base._credentials, base._timeout)

    def _post(self, path: str, request: CompileRequest) -> dict:
        body = super()._post(path, request)
        return self._rewrite_barriers(body) if path == "/compile-sql" else body

    @staticmethod
    def _rewrite_barriers(body: dict) -> dict:
        """Rewrite every `cache_table` statement to a temp `view` so the script runs on serverless."""
        for stmt in body.get("statements", []):
            if stmt.get("kind") == "cache_table":
                sql = stmt.get("sql", "")
                if sql.startswith(_CACHE_PREFIX):
                    stmt["sql"] = _VIEW_PREFIX + sql[len(_CACHE_PREFIX):]
                    stmt["kind"] = "view"
        return body
