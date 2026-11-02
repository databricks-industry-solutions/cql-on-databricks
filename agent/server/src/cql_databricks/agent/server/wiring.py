"""RUN-CLIENT-MIGRATION, SEC-OBO — agent reads share the durable client and caller SQL credentials.

RUN-CLIENT-SESSION-REUSE — every tool reads on the signed-in user's held warehouse session
(``cql_databricks_common.warehouse``), the same one the Designer's runs use in its process.
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from fastmcp import FastMCP

from cql_databricks.compiler.compile_shim import CqlDatabricksCompileProvider
from cql_databricks.compiler.environment import Environment, EnvironmentResolver
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.workspace_files import WorkspaceLibraryFiles
from cql_databricks.compiler.resolve import resolve
from cql_databricks.result import ResultsUnavailable, run_inline
from cql_databricks.result.insights import parse_scoring
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

#: AGENT-DEFAULT-ENVIRONMENT — the environment the Designer's drawer passes for one request; unset everywhere else,
#: so Genie and MCP clients run in the default. A ``ContextVar`` for the reason ``workspace._forwarded`` is one: the
#: drawer dispatches tools in process, and no tool takes the environment as an argument.
_environment: contextvars.ContextVar[str | None] = contextvars.ContextVar("cql_databricks_agent_environment",
                                                                         default=None)


@contextmanager
def in_environment(name: str | None) -> Iterator[None]:
    """AGENT-DEFAULT-ENVIRONMENT, DOC-ENVIRONMENT-PICKER — run the tools called inside in the environment ``name``,
    the one the Designer has picked; None is the default."""
    token = _environment.set(name)
    try:
        yield
    finally:
        _environment.reset(token)


def current_environment() -> str | None:
    """AGENT-DEFAULT-ENVIRONMENT — the environment the drawer bound for this tool call, or None for the default. For a
    contributed tool (`AGENT-TOOL-REGISTRY`) that reads the environment's content its own way."""
    return _environment.get()


def tool_environment(run) -> tuple[EnvironmentResolver, Environment]:
    """AGENT-DEFAULT-ENVIRONMENT — the environment the tools run in, the drawer's, else the default, found as the
    caller through ``run`` in one query (`CLIENT-ENVIRONMENT-RESOLVER`), with the resolver that found it."""
    environments = EnvironmentResolver(run)
    return environments, environments.resolve(_environment.get())


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
        """RUN-CLIENT-REQUEST-REOPEN — a selection reopens in the environment its calculation ran in."""
        run = runner()
        named = (address.get("calculation") or {}).get("environment")
        if not named:
            raise ValueError("the result-set address names no environment; reopen it from its calculation request")
        environments = EnvironmentResolver(run)
        return open_selection(address, run=run, content=environments.content(environments.get(named)),
                              compiler=compile_provider(config.compiler_endpoint(), config.compiler_token()),
                              files=user_files())

    def warehouse(sql):
        return runner().rows(sql)

    return create_mcp(warehouse=warehouse, reference_for=reference, computed_for=computed,
                      research=research(runner), contributed=contributed)


def research(runner) -> Research:
    """AGENT-DEFINITION-DISCOVERY, AGENT-SAVED-RESULTS, AGENT-INLINE-EXPERIMENTS — the research tools'
    collaborators, each a read or an inline run on the signed-in user's held session (`SEC-OBO`), in the
    environment the tools run in (`AGENT-DEFAULT-ENVIRONMENT`)."""
    def content(run):
        environments, environment = tool_environment(run)
        return environments.content(environment)

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
        """AGENT-VALUE-SET-LOOKUP, BIND-PUBLISHED-VIEW: the environment's terminology view, as the caller."""
        run = runner()
        environments, environment = tool_environment(run)
        terminology = environments.terminology(environment)
        hits = terminology.find_code(code, system)[:CODE_HIT_LIMIT]
        return [(hit.id, getattr(hit, "system", None) or system, code) for hit in hits]

    def translate(cql: str, location: str | None = None) -> dict:
        resolution = resolve(content(runner()), cql=cql, location=location, files=user_files() if location else None)
        result = compiler().translate_result(resolution.source, resolution.include_sources)
        return {"library": resolution.name, "version": resolution.version,
                "ok": result["ok"] and not resolution.unresolved,
                "diagnostics": [*resolution.unresolved_messages(), *result["diagnostics"]]}

    def inline(cql: str, period, location: str | None = None):
        run = runner()
        catalog, schema = runtime()
        environments, environment = tool_environment(run)
        return run_inline(content=environments.content(environment), run=run,
                          compiler=CqlDatabricksCompileProvider(compiler()), environment=environment, cql=cql,
                          runtime_catalog=catalog, runtime_schema=schema, period=period, location=location,
                          files=user_files() if location else None)

    def scoring_for(library: str, version: str, location: str | None = None) -> str | None:
        at = Location.parse(location) if location else None
        if at is not None and at.kind == "folder":
            return folder_scoring(user_files(), at.path, library, version)
        return parse_scoring(corpus().measure_resource(library, version, store=at.catalog_schema if at else None))

    def saved() -> SavedResultsEnv:
        run = runner()
        catalog, schema = runtime()
        environments, environment = tool_environment(run)
        return SavedResultsEnv(run=run, content=environments.content(environment), compiler=compiler(),
                               environment=environment, runtime_catalog=catalog, runtime_schema=schema,
                               scoring_for=scoring_for, files=user_files())

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
