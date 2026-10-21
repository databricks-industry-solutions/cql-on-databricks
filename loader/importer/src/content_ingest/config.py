"""Environment-based configuration for content-ingest (CONTENT-INGEST, SVC-LOCAL-CONFIG-BUNDLE).

All settings come from environment variables. In a checkout they are loaded by
``scripts/with-local-env.sh`` — workspace settings from ``databricks.local.yml``, local-run settings
and secrets from the optional root ``.env`` — never read from a file here.

Auth resolves from the ambient Databricks CLI profile (``databricks auth login``)
or ``DATABRICKS_TOKEN``.
"""
from __future__ import annotations

import os

def _repo_root() -> str | None:
    """The checkout root: the nearest directory above this file holding databricks.yml."""
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isfile(os.path.join(d, "databricks.yml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def require_local_env() -> None:
    """Refuse to run from a checkout whose local config was not loaded (SVC-LOCAL-CONFIG-BUNDLE).

    Without it every setting falls to its default — local Spark, no catalog — and the run fails or
    succeeds against the wrong place. The marker is set by scripts/with-local-env.sh.
    """
    if os.environ.get("CQL_LOCAL_ENV_TARGET"):
        return
    root = _repo_root()
    present = [f for f in ("databricks.local.yml", ".env")
               if root and os.path.isfile(os.path.join(root, f))]
    if present:
        raise SystemExit(f"content-ingest: {' and '.join(present)} found but not loaded. "
                         "Run: scripts/with-local-env.sh python -m content_ingest ...")


def catalog() -> str | None:
    """The Unity Catalog catalog to write into (``CONTENT_CATALOG``), or None when unset.
    There is no default name: a catalog is workspace config (SVC-LOCAL-CONFIG-BUNDLE)."""
    return os.environ.get("CONTENT_CATALOG", "").strip() or None


def umls_api_key() -> str | None:
    """UMLS API key for VSAC ValueSet expansion (``UMLS_API_KEY``)."""
    return os.environ.get("UMLS_API_KEY")


def cache_dir() -> str:
    """Local directory for downloaded package tarballs (``CONTENT_CACHE_DIR``)."""
    default = os.path.join(os.path.expanduser("~"), ".content-ingest", "cache")
    return os.environ.get("CONTENT_CACHE_DIR", default)


def databricks_host() -> str | None:
    """Databricks workspace URL (``DATABRICKS_HOST``).  When set, content-ingest uses
    ``databricks-connect`` (DatabricksSession) instead of a local SparkSession."""
    return os.environ.get("DATABRICKS_HOST")


def databricks_cluster_id() -> str | None:
    """Classic cluster id for databricks-connect (``DATABRICKS_CLUSTER_ID``)."""
    return os.environ.get("DATABRICKS_CLUSTER_ID")


def databricks_serverless() -> bool:
    """Use serverless compute (``DATABRICKS_SERVERLESS=true``)."""
    return os.environ.get("DATABRICKS_SERVERLESS", "").lower() in ("true", "1", "yes")


def spark_remote() -> str | None:
    """Spark Connect remote URL (``SPARK_REMOTE``).  Overrides databricks-connect when set."""
    return os.environ.get("SPARK_REMOTE")
