"""CONTENT-RESOLVER — the resolver an App process holds, reading the tagged schemas as the App's service principal.

One per process and shared by every request: the corpus is loaded once and held in memory
(`KNOWLEDGE-LAKE-DIRECT`), so it is read with the App's own identity rather than any one user's. The
SQL warehouse is the App's configured warehouse; a read with none configured fails loud, naming it.
CLIENT-DISCOVERY-SCOPE — every resolver the Designer builds discovers only in the deployment's catalog scopes,
the client's own ``CQL_DATABRICKS_CATALOG_SCOPES`` setting, baked into the App by the deploy.
"""
from __future__ import annotations

from cql_databricks.compiler.config import resolve_catalog_scopes
from cql_databricks.data_bindings import ContentResolver, SqlRunner, warehouse_runner

from cql_databricks_common import config


def app_sql_runner() -> SqlRunner:
    """A runner over the App's warehouse as its service principal. Built on first use, not at startup."""
    from cql_databricks_common.workspace import service_principal_client
    return warehouse_runner(service_principal_client(), config.warehouse_id() or "")


def app_content_resolver() -> ContentResolver:
    return ContentResolver(app_sql_runner, resolve_catalog_scopes())


def user_content_resolver(run: SqlRunner) -> ContentResolver:
    """A resolver over ``run``, the signed-in user's runner, in the deployment's catalog scopes."""
    return ContentResolver(lambda: run, resolve_catalog_scopes())
