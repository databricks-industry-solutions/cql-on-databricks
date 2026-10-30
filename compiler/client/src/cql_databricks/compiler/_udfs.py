"""Automatic registration of the opaque-UDF catalog on the runtime cluster (`SVC-RUNTIME-UDF`).

The engine's emitted SQL calls opaque Scala UDFs (`cql_temporal_*`, `cql_choice_nav_*`, ~25-30 ops,
`EXEC-UDF-LIBRARY`) BY NAME. The classes reach the driver JVM classpath as a cluster-scoped library
(`scripts/deploy-udf.sh` installs the `cql-databricks-runtime` JAR from a UC Volume); this module does the OTHER
half — REGISTERING the names on the session — automatically, so a notebook never has to.

This is the **single-user-classic** path of `SVC-RUNTIME-UDF`. On a shared (USER_ISOLATION) cluster it is a
NO-OP: the session is a **Spark Connect** session with **no `spark._jvm` at all** (probed: `hasattr(spark,
"_jvm")` is False), and `spark.sql.extensions` was disproven there too. The PRIMARY shared-cluster path is
instead a one-line `%scala` cell the author runs — `com.databricks.cql.platform.CqlDatabricks.init(spark)` (the
`platform:uc-runtime` entry point wrapping the engine catalog) — which registers from the driver JVM into the
shared notebook session. See `SVC-RUNTIME-UDF` / `docs/deploy.md`

It still helps on a **JVM-backed** single-user classic session, where `spark._jvm` is present: it calls
`CqlRuntimeUdfs.registerAll(spark)` directly so the catalog is available even if the cluster conf was not
set. Everywhere the gateway is ABSENT (shared / serverless / databricks-connect Spark Connect,
`SVC-RUN-DBCONNECT`) it is a best-effort NO-OP — never breaking the zero-config path. `registerAll` is
idempotent, so calling it once per `connect()` is safe.
"""
from __future__ import annotations

import os

# Fully-qualified name of the engine's catalog object (in the installed `cql-databricks-runtime` JAR).
_CATALOG_CLASS = "com.databricks.cql.runtime.udf.CqlRuntimeUdfs"

# Escape hatch: set to a falsey-ish value to skip auto-registration entirely (e.g. when the catalog is
# registered some other way, or to isolate a problem). Any value other than these disables it.
_ENV_DISABLE = "CQL_DATABRICKS_DISABLE_UDF_AUTOREGISTER"
_TRUTHY = {"1", "true", "yes", "on"}


def _autoregister_enabled() -> bool:
    return (os.environ.get(_ENV_DISABLE, "") or "").strip().lower() not in _TRUTHY


def register_runtime_udfs(spark) -> bool:
    """Best-effort: register the opaque-UDF catalog on ``spark`` via its py4j JVM gateway. Returns ``True``
    iff registration actually ran (a JVM-backed session with the catalog class on the classpath), else
    ``False`` — never raises. See the module docstring for why this only fires on a JVM-backed session."""
    if spark is None or not _autoregister_enabled():
        return False
    # `_jvm` / `_jsparkSession` exist only on a classic JVM-backed PySpark session. A Spark Connect session
    # (serverless / databricks-connect) has neither; getattr guards keep this a clean no-op there.
    jvm = getattr(spark, "_jvm", None)
    jspark = getattr(spark, "_jsparkSession", None)
    if jvm is None or jspark is None:
        return False
    try:
        # Walk the py4j gateway one package segment at a time, DERIVED from `_CATALOG_CLASS` rather than
        # spelled out again. Two spellings of one class name is how this broke during the monorepo rename:
        # the dotted constant was updated and a hand-written `getattr` chain beside it was not, leaving the
        # walk pointed at a package that no longer exists — and because every failure here is swallowed by
        # design, it would have degraded silently to "UDFs never auto-register" rather than raising.
        catalog = jvm
        for segment in _CATALOG_CLASS.split("."):
            catalog = getattr(catalog, segment)
        catalog.registerAll(jspark)
        return True
    except Exception:
        # ClassNotFound (JAR not installed / cluster not restarted), a py4j access restriction (UC shared
        # mode), or any JVM error — the pre-catalog status quo, exactly like OSS `_ship_udf_catalog`.
        return False
