"""SVC-MCP-TOOLS — the agent's App (`APP-PER-FEATURE`, `agent/README.md`).

The agent-facing App: the agent's tools and **nothing else**. No SPA, no `/api`, no compiler proxy —
an external Genie Agent needs the tools, and every other route this process could serve is surface
it does not need and should not expose.

**Deployed as `mcp-cql-databricks`**, because Databricks requires an MCP server's App name to begin with
``mcp-`` for AI Playground to recognize it. Only the deployed `name:` in `databricks.yml` carries the
platform's prefix (`ACC-BRAND-NAMING`).

**It serves the agent's own tools only.** The Designer's ``get_specification`` is served where the
Designer is, in its assistant drawer; the agent may not import the Designer (`DEC-AGENT-DESIGNER-CYCLE`),
so this App does not have it, and its standing instructions do not name it.

Run: ``python -m cql_databricks.agent.server.app`` (the App's `app.yaml` command).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from cql_databricks_common import config
from cql_databricks_common.warehouse import close_sessions
from cql_databricks_common.workspace import install_forwarded_identity

from .wiring import assistant_server, mcp_http_app

log = logging.getLogger("cql_databricks.agent.server.app")


def create_mcp_app() -> FastAPI:
    """The ASGI app the `mcp-cql-databricks` deployment serves.

    RUN-CLIENT-RESULT-SETS — reads explicit complete selections using the caller's SQL grants.
    """
    # SVC-MCP-TRANSPORT — the MCP app owns the literal path `/mcp`, and the parent mounts it at the
    # ROOT. Mounting it AT `/mcp` instead would serve `/mcp/`, leaving the bare `/mcp` — the URL an
    # agent is actually configured with — as a 307 to it. A redirect is not free here: a client that
    # does not follow one on POST fails outright, and a browser treats a redirected CORS preflight
    # as a hard error. This App has no SPA catch-all to work around, so it simply serves the path.
    http = mcp_http_app(assistant_server(), path="/mcp")
    # The lifespan is the wiring — see `mcp_http_app`. Without it every tool call fails at runtime
    # while the server looks healthy. RUN-CLIENT-SESSION-REUSE — it also ends the held warehouse sessions.
    @asynccontextmanager
    async def lifespan(app):
        try:
            async with http.lifespan(app):
                yield
        finally:
            close_sessions()

    app = FastAPI(title="CQL on Databricks MCP", version="0.1.0", lifespan=lifespan)
    # SVC-MCP-TRANSPORT — CORS, because the clients that REGISTER this server do it from a browser.
    # Without it the preflight is answered 405 with no `Access-Control-Allow-Origin` and the add
    # fails before a single JSON-RPC call is made. `*` is the right scope: the origins are Databricks
    # workspace and agent-console hosts we do not enumerate, and this surface carries no cookie or
    # ambient credential for an origin to abuse — every call is authorized by the forwarded user
    # token below, which a cross-origin page cannot mint.
    # No `expose_headers` for `mcp-session-id`: this transport is stateless and mints none, and
    # exposing a header we never send would imply a session a client could try to hold.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    # SEC-OBO — the tools read result tables through the SQL warehouse, so they act as the SIGNED-IN
    # user exactly as the product App's routes do. An agent calling this server is a person behind
    # a model, and computing their measure with this App's own service principal would give every
    # caller the same authority regardless of what Unity Catalog grants them.
    install_forwarded_identity(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        """What the platform polls. Deliberately NOT a tool call — a health check that exercised the
        warehouse would report a cold warehouse as a dead app."""
        return {"status": "ok", "surface": "mcp"}

    # LAST, and that is load-bearing: a mount at `/` matches every path, and Starlette takes the
    # first route that matches. Registered before `/health`, it would answer the platform's health
    # poll with the MCP transport's 404.
    app.mount("/", http)

    log.info("SVC-MCP-TOOLS: MCP server ready at /mcp")
    return app


def main() -> None:
    import uvicorn
    uvicorn.run(create_mcp_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
