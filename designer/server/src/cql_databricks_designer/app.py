"""The Designer's App (`SVC-APP-TOPOLOGY`, `APP-PER-FEATURE`) — the SPA's backend (BFF).

Serves the built React SPA (dist/) and the Designer's `/api` surface. It proxies authoring compiles to the
CQL Compiler Service over HTTP (translate), and reads the picked environment's knowledge for library discovery and
include resolution (KNOWLEDGE-LAKE-DIRECT, `DOC-ENVIRONMENT-PICKER`).

It runs calculations through the client as the signed-in user (`RUN-CLIENT-MIGRATION`) in the environment the user
picks, and lists the environments the user can see (`routes/environments.py`). Browser -> Designer -> Compiler
Service / SQL warehouse.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cql_databricks.agent.server.wiring import assistant_server
from cql_databricks_common import config
from cql_databricks_designer.cache_stamp import CacheStamp, install as install_cache_revision
from cql_databricks_common.compiler import CompilerClient, compile_provider
from cql_databricks_designer.data_bindings import app_contents
from cql_databricks_common.warehouse import close_sessions
from cql_databricks_common.workspace import install_forwarded_identity

from .calculation_env import calculation_env

from .assistant_tools import designer_tools
from .routes import (
    knowledge as knowledge_routes,
    assistant as assistant_routes,
    compile as compile_routes,
    designer as designer_routes,
    deployment as deployment_routes,
    environments as environments_routes,
    runs as runs_routes,
    save as save_routes,
)

log = logging.getLogger("cql_databricks_designer")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """RUN-CLIENT-SESSION-REUSE — the held warehouse sessions end with the process."""
    try:
        yield
    finally:
        close_sessions()


def create_app() -> FastAPI:
    app = FastAPI(title="CQL-on-Databricks Designer", version="0.1.0", lifespan=_lifespan)

    # PERF-RESPONSE-GZIP — /api/translate with include_elm returns ~3 MB of ELM JSON, which was
    # going over the wire uncompressed even though the browser asks for gzip. ELM is extremely
    # repetitive: measured on a nine-library closure, level 6 takes it to 250 KB (8.2%) for 18 ms of
    # CPU, where level 9 buys 0.8% more for 3.4x the cost. Starlette excludes text/event-stream by
    # default (DEFAULT_EXCLUDED_CONTENT_TYPES), so the SSE progress streams are untouched — their
    # chunks would otherwise sit in zlib's buffer, since the responder never flushes per chunk.
    #
    # THAT EXCLUSION IS A VERSION FLOOR, NOT A PROPERTY OF THE MIDDLEWARE (starlette>=0.46, pinned in
    # pyproject.toml AND deploy/requirements.txt). It arrived in 0.46 and the deployed App ran below it
    # for a while, gzipping every SSE stream: the compute gauge sat on the client's own "connecting…"
    # for the whole of a Job that was running normally, then took the entire run's events at once when
    # the stream closed. Nothing here can detect that — the middleware reports no encoding decision — so
    # the floor is the only thing holding it, and lowering it re-opens the bug in the deployed App only.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    compiler = CompilerClient(config.compiler_endpoint(), config.compiler_token())
    # DOC-ENVIRONMENT-PICKER — the environments, read as each signed-in user and cached per user.
    environments = environments_routes.user_environments()
    # KNOWLEDGE-LAKE-DIRECT — each environment's content, held per process; nothing is read until first asked.
    contents = app_contents()

    # APP-ASSISTANT — the drawer's `/api/assistant/ask` loop dispatches through the agent's server
    # object, in process: the tool definitions it sends the model are generated from
    # `mcp_server.list_tools()`, so there is no second set of schemas to keep in step. The Designer adds
    # its own `get_specification` (AGENT-TOOL-REGISTRY). The agent's MCP surface is the agent's App;
    # this one mounts no `/mcp` (`APP-PER-FEATURE`).
    mcp_server = assistant_server(contributed=designer_tools(contents=contents))

    # PERF-CACHE-REVISION — browser caching for the whole reading surface, with ONE thing that
    # busts it. The stamp is a digest of the corpus generation and the data-source cache's generation plus
    # this process's build, so a knowledge or data-source reload invalidates every cached body here;
    # `cache_stamp.py` holds the whole policy and states what the digest cannot see.
    install_cache_revision(app, CacheStamp(version=app.version, readers={
        # `generation` moves on every load and reload, and reading it never forces a load.
        "knowledge": lambda: [contents.generation],
        "environments": lambda: [environments.generation],
    }))

    # SEC-OBO — bind the signed-in user's forwarded credentials to the request context, so that
    # `workspace.user_client()` can act as them from anywhere in the call graph.
    #
    # INSTALLED LAST, SO IT RUNS FIRST: Starlette runs the last-registered middleware outermost. The
    # cache stamp above is computed in its own middleware, per user; registered before it,
    # the binding ran inside the stamp and the stamp saw no user at all.
    install_forwarded_identity(app)

    # ---------------------------------------------------------------- the HTTP surface
    #
    # One router per concern (`routes/`), each built from an already-assembled collaborator set.
    #
    # ORDER IS LOAD-BEARING, and it is stated here rather than inside any router. Everything below is
    # registered BEFORE the SPA catch-all, because that catch-all matches `/{path:path}` and swallows
    # whatever follows it. `tests/test_app_routes.py` pins the order for exactly that reason.
    app.include_router(deployment_routes.router(compiler=compiler))
    app.include_router(compile_routes.router(compiler=compiler, contents=contents))
    app.include_router(knowledge_routes.router(contents=contents))
    app.include_router(designer_routes.router(contents=contents))
    # DOC-OPEN-SAVE — save a library where it was opened, as the signed-in user.
    app.include_router(save_routes.router(compiler=compiler, contents=contents, warehouse=environments_routes.warehouse))
    app.include_router(environments_routes.router(environments=environments))
    # RUN-CLIENT-MIGRATION — caller-owned durable calculations and complete-selection reads.
    app.include_router(runs_routes.router(
        compiler=compile_provider(config.compiler_endpoint(), config.compiler_token()), env=calculation_env()))
    app.include_router(assistant_routes.router(mcp_server=mcp_server))

    # Serve the built SPA (Vite dist/) at the root, AFTER the /api routes. Absent until `npm run
    # ui:build`; tolerate that so the API is still usable in a fresh checkout.
    # APP-URL-ROUTING — the SPA uses BrowserRouter (history-based client-side routing). Non-API paths
    # that don't match a static asset must fall back to index.html so the React router can handle them
    # (e.g. /settings, /measures, /classic/home opened in a new tab or refreshed).
    dist = config.spa_dist_dir()
    if dist.is_dir():
        index_html = dist / "index.html"
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

        @app.get("/{path:path}")
        async def _spa_fallback(request: Request, path: str) -> FileResponse:
            # Serve the actual file if it exists in dist/ (favicon, manifest, etc.), else index.html.
            candidate = dist / path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(index_html))
    else:
        log.warning("SPA dist not found at %s — run `npm run ui:build`; API-only until then.", dist)

    return app
