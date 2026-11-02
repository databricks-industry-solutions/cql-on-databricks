"""SVC-MCP-TRANSPORT, AGENT-TOOL-REGISTRY — the standalone MCP App, against the real `create_mcp_app`.

The standalone App is the agent's own since APP-PER-FEATURE: it serves the agent's tools and not the
Designer's `get_specification`.
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

#: What the standalone App publishes: the agent's data tools, its research tools and the skill pair
#: (APP-PER-FEATURE, SVC-MCP-TOOLS amended 2026-10-29).
EXPECTED_TOOLS = {"list_tables", "get_output", "query_data",
                  "get_freshness", "list_skills", "get_skill",
                  "list_definitions", "get_definition", "list_value_sets", "value_sets_with_code",
                  "find_results", "get_population_summary", "check_cql", "run_cql", "compare_cql"}


class TestStandaloneTransport:
    """SVC-MCP-TRANSPORT — the three things that decide whether a client can REGISTER this server.

    **All three shipped broken, and none of them failed a test or a deploy.** `mcp-cql-databricks` came up
    healthy, answered `initialize` with 200 and appeared in the agent console's list; adding it
    errored with nothing more specific than a refusal. The reason it stayed invisible is that every
    other test here drives the server OBJECT, and all three faults live in the transport around it.

    Run against the REAL `create_mcp_app` rather than a reconstruction of it, unlike `TestMounted`
    above: an assembly copied into a test pins the copy, and the copy is not what deploys. It needs no
    Lakebase (`SPLIT-LAKEBASE`); the data-source registry reads lazily, and neither `initialize` nor
    `tools/list` touches it.
    """

    @pytest.fixture
    def app(self, monkeypatch):
        # SPLIT-LAKEBASE — no Lakebase configured: the MCP App starts without one.
        monkeypatch.delenv("CQL_LAKEBASE_DSN", raising=False)
        monkeypatch.delenv("CQL_LAKEBASE_ENDPOINT", raising=False)
        from cql_databricks.agent.server.app import create_mcp_app
        return create_mcp_app()

    def _rpc(self, client, method: str, id_: int):
        return client.post("/mcp", follow_redirects=False,
                           headers={"Accept": "application/json, text/event-stream",
                                    "Origin": "https://acme.cloud.databricks.com"},
                           json={"jsonrpc": "2.0", "id": id_, "method": method, "params": {}
                                 if method != "initialize" else
                                 {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}}})

    def test_a_client_that_never_echoes_a_session_id_can_still_list_the_tools(self, app):
        """The registration probe: `initialize`, then `tools/list`, carrying nothing between them.

        Stateful is FastMCP's default and it fails EXACTLY here — `initialize` returns 200 with an
        `mcp-session-id`, and the next request without that header is answered 400 "Missing session
        ID". A handshake that succeeds and a follow-up that does not is why the failure reads as
        "cannot be added" rather than as a transport error.
        """
        with TestClient(app) as client:
            first = self._rpc(client, "initialize", 1)
            assert first.status_code == 200
            assert first.headers.get("mcp-session-id") is None, (
                "the server minted a session, so it is stateful: every client that does not echo "
                "the id back is one handshake away from a 400 it cannot act on")

            listed = self._rpc(client, "tools/list", 2)
            assert listed.status_code == 200, listed.text
            for tool in EXPECTED_TOOLS:
                assert tool in listed.text

    def test_the_url_an_agent_is_configured_with_is_not_a_redirect(self, app):
        """`https://<app>/mcp` must BE the endpoint, not a 307 to `/mcp/`.

        Mounting the MCP app at `/mcp` (rather than mounting it at the root and letting it own the
        path) leaves the bare URL as a redirect. A client that does not follow one on POST fails
        outright, and a browser treats a redirected CORS preflight as a hard error — so the URL a
        person copies out of `databricks apps get` is the one that does not work.
        """
        with TestClient(app) as client:
            assert self._rpc(client, "initialize", 1).status_code == 200

    def test_a_browser_may_preflight_the_endpoint(self, app):
        """The clients that register this server do it from a browser, so CORS is not optional.

        Without the middleware the preflight is answered **405 with no
        `Access-Control-Allow-Origin`**, and the add fails before any JSON-RPC is sent.
        """
        with TestClient(app) as client:
            pre = client.options("/mcp", headers={
                "Origin": "https://acme.cloud.databricks.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type"})
            assert pre.status_code == 200
            assert pre.headers.get("access-control-allow-origin") == "*"
            assert self._rpc(client, "initialize", 1).headers.get(
                "access-control-allow-origin") == "*"

    def test_the_health_poll_is_not_swallowed_by_the_root_mount(self, app):
        """A mount at `/` matches every path, and Starlette takes the first route that matches.

        Registered before `/health`, the mount answers the platform's poll with the MCP transport's
        404 — and an App that fails its health check never reaches the list this whole fix is about.
        """
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "surface": "mcp"}


def test_the_agent_app_serves_no_specification_and_does_not_name_one(monkeypatch):
    """APP-PER-FEATURE — `get_specification` is the Designer's; the agent's App neither serves nor names it."""
    import anyio

    monkeypatch.delenv("CQL_LAKEBASE_DSN", raising=False)
    from cql_databricks.agent.server.app import create_mcp_app
    from cql_databricks.agent.server.wiring import assistant_server

    create_mcp_app()  # builds without the Designer installed in its import graph
    server = assistant_server()
    names = {t.name for t in anyio.run(server.list_tools)}
    assert names == EXPECTED_TOOLS
    assert "get_specification" not in (server.instructions or "")
