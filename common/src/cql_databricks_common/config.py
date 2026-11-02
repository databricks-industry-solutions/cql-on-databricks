"""Config resolution for the CQL on Databricks backend (SVC-COMPILE-ENDPOINT, SVC-DEPLOY-TARGETS).

Env-based, mirroring the retired Node paths.ts so local↔Databricks is a host:port change (same HTTP
contract). Every knob has a sensible local default; a deployed App overrides via the environment.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit


def compiler_endpoint() -> str:
    """The CQL Compiler Service base URL (`SVC-COMPILE-ENDPOINT`): see `_feature_app_endpoint`. Local dev
    defaults to the JVM host on 127.0.0.1:8080."""
    return _feature_app_endpoint("CQL_COMPILER", "http://127.0.0.1:8080")


def _feature_app_endpoint(prefix: str, local_default: str) -> str:
    """Another feature's base URL, from ``<prefix>_APP`` deployed or ``<prefix>_ENDPOINT`` locally.

    **Deployed, the App knows the other App's NAME, not its URL.** The bundle declares the other App as
    an `app` resource and `app.yaml` binds ``<prefix>_APP`` to it with `valueFrom`, which the platform
    resolves to the App's name; the URL is then read from the Apps API (`w.apps.get(name).url`), as the
    platform documents.

    Locally there is no App: ``<prefix>_ENDPOINT`` or ``local_default``. Setting both is a configuration
    error, not a preference.
    """
    app = os.environ.get(f"{prefix}_APP")
    url = os.environ.get(f"{prefix}_ENDPOINT")
    if app and url:
        raise RuntimeError(f"{prefix}_APP ({app}) and {prefix}_ENDPOINT ({url}) are both set; set one: "
                           f"{prefix}_APP in a deployed App, {prefix}_ENDPOINT for a local run.")
    if app:
        return _app_url(app)
    return url or local_default


_APP_URLS: dict[str, str] = {}


def _app_url(name: str) -> str:
    """The URL of the Databricks App ``name``, looked up once per process with this App's own identity."""
    if name not in _APP_URLS:
        from .workspace import service_principal_client

        url = service_principal_client().apps.get(name=name).url
        if not url:
            raise RuntimeError(f"the Databricks App {name!r} has no URL yet; is it deployed?")
        _APP_URLS[name] = url.rstrip("/")
    return _APP_URLS[name]


def compiler_token() -> str | None:
    """Optional RFC 6750 bearer token for the Compiler Service (set on Databricks; unset locally)."""
    tok = os.environ.get("CQL_COMPILER_TOKEN")
    return tok if tok else None


def spark_remote() -> str:
    """The Spark Connect endpoint in-process runs execute against (SVC-RUN-EVAL / SVC-BINDING-RUNTIME).
    Local dev points at the standalone OSS Connect server; CQL_DATABRICKS_CONNECT overrides (same env the
    Compiler Service uses, for one source of truth)."""
    return os.environ.get("CQL_DATABRICKS_CONNECT") or "sc://localhost:15002"


def databricks_cluster_id() -> str | None:
    """The Databricks all-purpose cluster the in-process run targets via databricks-connect
    (`SVC-RUN-DBCONNECT`). When set, `run()` builds a `DatabricksSession` — which AUTO-STARTS a terminated
    cluster — instead of the local `sc://` Spark Connect session of `spark_remote()`. The accelerator
    supplies the Databricks-specific session; the `cql()` client only receives it (`APP-DECOUPLE`). Auth
    resolves from the ambient Databricks config chain (`DATABRICKS_CONFIG_PROFILE` locally, the App
    identity on Databricks). Unset -> the local `spark_remote()`."""
    cid = os.environ.get("DATABRICKS_CLUSTER_ID")
    return cid or None


def databricks_serverless() -> bool:
    """Route the in-process run at Databricks SERVERLESS compute via databricks-connect
    (`SVC-RUN-DBCONNECT`) instead of a specific all-purpose cluster. Truthy `DATABRICKS_SERVERLESS` wins
    over `databricks_cluster_id()`."""
    return os.environ.get("DATABRICKS_SERVERLESS", "").strip().lower() in {"1", "true", "yes", "on"}


def spa_dist_dir() -> Path:
    """The built React SPA (Vite `dist/`) the backend serves as static files. Absent in a fresh checkout
    until `npm run ui:build`; the app tolerates that (API still serves) and logs a hint."""
    return Path(__file__).resolve().parent.parent / "dist"


def target() -> str:
    """The deploy target label surfaced on /api/health (SVC-DEPLOY-TARGETS). Local unless overridden."""
    return os.environ.get("CQL_APP_TARGET") or "local"


def warehouse_id() -> str | None:
    """The serverless SQL warehouse id for UDTF auto-provisioning (SDP-SPACE, DATA-VIEW-SCAFFOLD).
    When set, Data Source deploy auto-creates missing retrieve UDTFs on it (RUN-CLIENT-WAREHOUSE-CONNECTOR)."""
    return os.environ.get("WAREHOUSE_ID") or os.environ.get("CQL_WAREHOUSE_ID") or None


def assistant_endpoint() -> str:
    """APP-ASSISTANT — the Model Serving chat endpoint the assistant drawer's tool loop calls.

    Reached as the signed-in user (`SEC-OBO`), so there is no key to store (`agent/README.md`). Set by
    the bundle variable `assistant_endpoint`, which deploys and local runs both read. There is no
    default: the workspace chooses the model, and an unset name fails here rather than calling a
    model nobody picked. Any chat endpoint on Model Serving works.

    It is a NAME, not a discovery call — resolving the "best available" model at startup would mean the
    answer a clinician got changed when the workspace's endpoint list did, with nothing recording
    which model produced it.
    """
    endpoint = os.environ.get("CQL_ASSISTANT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("CQL_ASSISTANT_ENDPOINT is not set. Set the bundle variable assistant_endpoint "
                           "in databricks.local.yml to a chat endpoint in your workspace (APP-ASSISTANT).")
    return endpoint


def canonical_base() -> str:
    """DOC-OPEN-SAVE — the base of the canonical URL a library or measure created in the Designer gets:
    ``<base>/Library/<Name>`` and ``<base>/Measure/<Name>``.

    One base for the whole deployment, set by the bundle variable `canonical_base`, whose default is the
    base the example measures use. Unset here means the bundle was not read, which fails rather than
    inventing an identity a library keeps for good.
    """
    base = os.environ.get("CQL_CANONICAL_BASE", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("CQL_CANONICAL_BASE is not set. Set the bundle variable canonical_base "
                           "in databricks.local.yml (DOC-OPEN-SAVE).")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        raise RuntimeError(f"CQL_CANONICAL_BASE must be an absolute http(s) URL, got {base!r} (DOC-OPEN-SAVE).")
    return base


def designer_catalog() -> str:
    """APP-RUN-LOG, DEC-RUN-LOG-LOCATION — the catalog holding the shared ``designer`` schema (the run log).

    Falls back to ``UC_CATALOG``, as the
    bundle variable defaults to ``uc_catalog``. Neither set is an error: there is no default catalog name.
    """
    catalog = (os.environ.get("CQL_DESIGNER_CATALOG") or os.environ.get("UC_CATALOG") or "").strip()
    if not catalog:
        raise RuntimeError("CQL_DESIGNER_CATALOG and UC_CATALOG are not configured: set uc_catalog "
                           "in databricks.local.yml")
    return catalog


def app_version() -> str | None:
    """APP-RUN-LOG — the deployed build (the source revision ``stage-app.sh`` bakes in), so a run log row
    can be tied to the code that ran it. None locally."""
    return os.environ.get("CQL_APP_VERSION") or None


def runtime_catalog() -> str | None:
    """SVC-RUNTIME-UDF-UC — the UC catalog holding the `cql_*` runtime UDFs the emitted SQL calls (BARE:
    `cql_age_at(...)`, `cql_to_interval(...)`, …). The engine emits these unqualified and there is no
    per-request UDF-catalog override (the four `CompileRequest.overrides` keys don't include one), so on a
    Databricks (serverless/cluster) run the session must resolve them by search path.
    None locally (the OSS session registers the UDFs itself)."""
    return os.environ.get("CQL_RUNTIME_CATALOG") or None


def runtime_schema() -> str:
    """The schema (under `runtime_catalog()`) holding the `cql_*` UDFs; default 'runtime' (SVC-RUNTIME-UDF-UC)."""
    return os.environ.get("CQL_RUNTIME_SCHEMA") or "runtime"


#: The `materialize.retrieves` values the engine accepts (`pipeline-runtime.md`).
MATERIALIZE_RETRIEVES = ("none", "costly", "all")


