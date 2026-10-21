"""RUN-CLIENT-MIGRATION, SEC-OBO — agent reads share the durable client and caller SQL credentials."""
from __future__ import annotations

from types import SimpleNamespace

from fastmcp import FastMCP

from cql_databricks.compiler.config import resolve_catalog_scopes
from cql_databricks.data_bindings import ContentResolver, warehouse_runner
from cql_databricks.result import ResultsUnavailable
from cql_databricks.result.results import warehouse_execute
from cql_databricks.result.selection_reading import SelectedOutput, open_selection
from cql_databricks_common import config
from cql_databricks_common.compiler import compile_provider
from cql_databricks_common.workspace import user_client

from .registry import ToolRegistry
from .server import create_mcp


def assistant_server(*, contributed: ToolRegistry | None = None) -> FastMCP:
    """RUN-CLIENT-RESULT-SETS — exact complete selections from Designer or publishing Jobs, read only."""
    def runner():
        warehouse = config.warehouse_id()
        if not warehouse:
            raise RuntimeError("WAREHOUSE_ID is required for agent result reads")
        return warehouse_runner(user_client(), warehouse)

    def reference(address: dict, define: str) -> SelectedOutput:
        results = selected(address)
        if define not in results.results:
            raise ResultsUnavailable(f"{define!r} is not an output in the complete selection")
        return SelectedOutput(results, define)

    def computed(address: dict) -> list[str]:
        return list(selected(address).results)

    def selected(address: dict):
        run = runner()
        return open_selection(address, run=run, content=ContentResolver(lambda: run, resolve_catalog_scopes()),
                              compiler=compile_provider(config.compiler_endpoint(), config.compiler_token()))

    def warehouse(sql):
        warehouse_id = config.warehouse_id()
        if not warehouse_id:
            raise RuntimeError("WAREHOUSE_ID is required for agent result reads")
        return warehouse_execute(user_client(), warehouse_id)(sql)

    def sources():
        run = runner()
        content = ContentResolver(lambda: run, resolve_catalog_scopes())
        result = []
        for schema in content.data_source_schemas():
            rows = run("SELECT comment FROM system.information_schema.schemata "
                       "WHERE catalog_name = :catalog AND schema_name = :schema",
                       {"catalog": schema.catalog, "schema": schema.schema})
            if len(rows) != 1:
                raise PermissionError(f"cannot read source schema metadata for {schema.name!r}")
            result.append(SimpleNamespace(id=schema.name, name=schema.name, description=rows[0]["comment"]))
        return result

    return create_mcp(warehouse=warehouse, reference_for=reference, computed_for=computed,
                      data_sources=sources, contributed=contributed)


def mcp_http_app(server: FastMCP, *, path: str = "/"):
    """The MCP ASGI app for a wired server.

    **The caller MUST pass the returned app's ``lifespan`` to its parent** (
    ``FastAPI(lifespan=mcp_app.lifespan)``): FastMCP's streamable-HTTP session manager is started
    there, and a mount whose lifespan never ran answers every call with "task group was not
    initialized" — a server that lists its tools and can serve none of them, with nothing failing at
    startup to say so.

    **``stateless_http=True`` is the transport contract, not a tuning knob** (`SVC-MCP-TRANSPORT`).
    Stateful is FastMCP's default: ``initialize`` mints an `mcp-session-id` that every later request
    must echo, and a client that does not — which is what a registration probe doing
    ``initialize`` then ``tools/list`` is — gets **400 "Missing session ID"** immediately after a
    handshake that returned 200. That is the shape of "the server is listed but cannot be added".
    It is also wrong for Apps regardless of client: the session map is process memory, so a replica
    change or a restart invalidates a session no caller was told had ended.

    ``path`` is the caller's, because the two deployments cannot both be right. The standalone App
    owns its whole URL space and serves the literal ``/mcp`` an agent is configured with; the
    product App has an SPA catch-all underneath and mounts this at ``/mcp`` instead.
    """
    return server.http_app(path=path, stateless_http=True)
