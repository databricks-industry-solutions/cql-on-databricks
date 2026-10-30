"""RUN-CLIENT-MIGRATION, SEC-OBO — agent reads share the durable client and caller SQL credentials.

RUN-CLIENT-SESSION-REUSE — every tool reads on the signed-in user's held warehouse session
(``cql_databricks_common.warehouse``), the same one the Designer's runs use in its process.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastmcp import FastMCP

from cql_databricks.compiler.compile_shim import CqlDatabricksCompileProvider
from cql_databricks.compiler.config import resolve_catalog_scopes
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.workspace_files import WorkspaceLibraryFiles
from cql_databricks.compiler.resolve import resolve
from cql_databricks.data_bindings import ContentResolver
from cql_databricks.result import ResultsUnavailable, run_inline
from cql_databricks.result.insights import parse_scoring
from cql_databricks.result.relation import quote
from cql_databricks.result.selection_reading import SelectedOutput, open_selection
from cql_databricks_common import config
from cql_databricks_common.compiler import compile_provider
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import user_client

from cql_databricks.agent.service.definitions import folder_scoring
from cql_databricks.agent.service.saved import SavedResultsEnv

from .mcp import Research, create_mcp
from .registry import ToolRegistry

#: The most value-set hits ``value_sets_with_code`` reads; a code in more is a code to narrow by system.
CODE_HIT_LIMIT = 500


def user_files() -> WorkspaceLibraryFiles:
    """RUN-ADDRESS-LOCATION — folder locations, read through the Workspace API as the signed-in user (`SEC-OBO`)."""
    return WorkspaceLibraryFiles(user_client)


def assistant_server(*, contributed: ToolRegistry | None = None) -> FastMCP:
    """RUN-CLIENT-RESULT-SETS — exact complete selections from Designer or publishing Jobs, read only."""
    def runner():
        warehouse = config.warehouse_id()
        if not warehouse:
            raise RuntimeError("WAREHOUSE_ID is required for agent result reads")
        return user_session(warehouse)

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
                              compiler=compile_provider(config.compiler_endpoint(), config.compiler_token()),
                              files=user_files())

    def warehouse(sql):
        return runner().rows(sql)

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
                      data_sources=sources, research=research(runner), contributed=contributed)


def research(runner) -> Research:
    """AGENT-DEFINITION-DISCOVERY, AGENT-SAVED-RESULTS, AGENT-INLINE-EXPERIMENTS — the research tools'
    collaborators, each a read or an inline run on the signed-in user's held session (`SEC-OBO`)."""
    def content(run) -> ContentResolver:
        return ContentResolver(lambda: run, resolve_catalog_scopes())

    def compiler():
        return compile_provider(config.compiler_endpoint(), config.compiler_token())

    def runtime() -> tuple[str, str]:
        catalog = config.runtime_catalog()
        if not catalog:
            raise RuntimeError("CQL_RUNTIME_CATALOG is not configured: inline runs and value-set lookups read "
                               "its runtime schema")
        return catalog, config.runtime_schema()

    def corpus():
        return content(runner()).corpus()

    def code_lookup(code: str, system: str | None):
        catalog, schema = runtime()
        params = {"code": code, **({"system": system} if system else {})}
        rows = runner()(f"SELECT DISTINCT valueset_id, system, code FROM {quote(catalog)}.{quote(schema)}.valuesets "
                        f"WHERE code = :code{' AND system = :system' if system else ''} "
                        f"ORDER BY valueset_id LIMIT {CODE_HIT_LIMIT}", params)
        return [(r["valueset_id"], r["system"], r["code"]) for r in rows]

    def translate(cql: str, location: str | None = None) -> dict:
        resolution = resolve(content(runner()), cql=cql, location=location, files=user_files() if location else None)
        result = compiler().translate_result(resolution.source, resolution.include_sources)
        return {"library": resolution.name, "version": resolution.version,
                "ok": result["ok"] and not resolution.unresolved,
                "diagnostics": [*resolution.unresolved_messages(), *result["diagnostics"]]}

    def inline(cql: str, data_source: str, period, location: str | None = None):
        run = runner()
        catalog, schema = runtime()
        return run_inline(content=content(run), run=run, compiler=CqlDatabricksCompileProvider(compiler()),
                          data_source=data_source, cql=cql, runtime_catalog=catalog, runtime_schema=schema,
                          period=period, location=location, files=user_files() if location else None)

    def scoring_for(library: str, version: str, location: str | None = None) -> str | None:
        at = Location.parse(location) if location else None
        if at is not None and at.kind == "folder":
            return folder_scoring(user_files(), at.path, library, version)
        return parse_scoring(corpus().measure_resource(library, version, store=at.catalog_schema if at else None))

    def saved() -> SavedResultsEnv:
        run = runner()
        catalog, schema = runtime()
        scopes = tuple(s for s in (resolve_catalog_scopes() or []) if s.count(".") == 1)
        return SavedResultsEnv(run=run, content=content(run), compiler=compiler(),
                               private_catalog=config.designer_results_catalog(), runtime_catalog=catalog,
                               runtime_schema=schema, result_scopes=scopes, scoring_for=scoring_for,
                               files=user_files())

    return Research(corpus=corpus, code_lookup=code_lookup, translate=translate, inline=inline,
                    scoring_for=scoring_for, saved=saved, files=user_files)


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
