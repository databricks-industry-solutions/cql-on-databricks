"""SVC-MCP-TOOLS — the MCP transport publishes the five tools and nothing else.

What these pin is the CONTRACT AN EXTERNAL AGENT SEES, which no other test covers: the tool names it
can call, the fact that every one of them takes its scope as explicit arguments
(`APP-ASSISTANT-CONTEXT` — a tool reading ambient state would work in CQL on Databricks and break for a Genie
Agent), and that each carries the description MCP hands the model as its only documentation.

A tool that loses its docstring still passes every test in `test_assistant_tools.py` and is useless
to the client the surface exists for, which is why that is asserted here rather than assumed.
"""
from __future__ import annotations

import asyncio

import pytest
# Imported at MODULE level, not inside the helpers: this file is `from __future__ import
# annotations`, so FastAPI resolves a handler's annotations against module globals. A `Request`
# imported inside a function is invisible there, and FastAPI silently reclassifies the parameter as
# a QUERY argument — the handler then 422s with `missing query parameter: request`, which looks
# like a routing bug and is not one. (`app.py` imports these at module level already.)
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.testclient import TestClient

from types import SimpleNamespace

from cql_databricks.agent import skills
from cql_databricks.agent.registry import ToolRegistry
from cql_databricks.agent.server import create_mcp
from cql_databricks.agent.wiring import mcp_http_app

#: The five that answer questions ABOUT a measure. This set is the one that must not grow
#: (`SVC-MCP-TOOLS`): `get_output` is the primitive precisely so that a new question shape is the
#: agent reasoning over data rather than a sixth entry here.
DATA_TOOLS = {"get_specification", "list_data_sources", "list_tables", "get_output",
              "query_data", "get_freshness"}

#: The skill pair (`SVC-MCP-SKILLS`). Counted separately because they are a different KIND of thing —
#: they answer nothing about a measure, they publish the contracts the answer is written under — and
#: collapsing the two sets would let a question-shaped tool arrive under cover of the larger total.
SKILL_TOOLS = {"list_skills", "get_skill"}

EXPECTED_TOOLS = DATA_TOOLS | SKILL_TOOLS


#: A registered Data Source, as `list_data_sources` reads one. A `SimpleNamespace` rather than the real
#: dataclass because the tool touches four attributes and the seam exists precisely so a test need not
#: build a registry (`docs/coding-standards.md`, the storage-seam pattern).
SOURCE = SimpleNamespace(id="sample-patients", name="Sample Patients",
                         description="Fabricated patient data for development",
                         change_data_log="cat.sch.change_log")


def _contributed() -> ToolRegistry:
    """A stand-in for the designer's contribution (AGENT-TOOL-REGISTRY): the agent publishes whatever is
    registered, and `get_specification` is the tool the designer registers in the real Apps."""
    registry = ToolRegistry()

    @registry.tool
    def get_specification(library: str, version: str) -> dict | None:
        """What a measure DECLARES: its defines and populations. Start here; every other tool takes a
        define name that this one lists."""
        return {"library": library}

    return registry


@pytest.fixture
def server():
    return create_mcp(
        warehouse=lambda sql: None,
        reference_for=lambda address: None, computed_for=lambda *scope: None,
        data_sources=lambda: [SOURCE],
        contributed=_contributed(),
    )


class TestContributedTools:
    """AGENT-TOOL-REGISTRY — tools another feature registers, published like the agent's own."""

    def test_without_a_contribution_the_specification_tool_is_absent(self):
        bare = create_mcp(warehouse=lambda sql: None,
                          reference_for=lambda *args: None, computed_for=lambda *scope: [],
                          data_sources=lambda: [])
        assert "get_specification" not in _tools(bare)

    def test_a_contributed_tool_leads_the_list(self, server):
        assert next(iter(_tools(server))) == "get_specification"

    def test_a_name_registered_twice_is_refused(self):
        registry = ToolRegistry()

        @registry.tool
        def twin() -> None:
            """One."""

        with pytest.raises(ValueError, match="already registered"):
            def twin() -> None:  # noqa: F811 — the duplicate is the point
                """Two."""
            registry.tool(twin)


def _tools(server) -> dict:
    """``{name: Tool}`` as the client sees them — name, description and JSON-Schema parameters."""
    return {t.name: t for t in asyncio.run(server.list_tools())}


class TestPublishedSurface:
    def test_publishes_exactly_the_five_data_tools_and_the_skill_pair(self, server):
        # A tool appearing here without `agent/README.md` and a requirement moving first is the
        # drift this asserts against.
        assert set(_tools(server)) == EXPECTED_TOOLS

    def test_the_question_answering_surface_is_still_five(self, server):
        # The load-bearing half. `SVC-MCP-SKILLS` added two tools that answer nothing about a
        # measure; if that made it easier for a SIXTH question shape to arrive, the skill pair cost
        # more than it bought.
        assert set(_tools(server)) - SKILL_TOOLS == DATA_TOOLS

    def test_every_tool_documents_itself_for_the_model(self, server):
        # MCP hands the model the name, the signature and the description — that is the entire
        # interface an agent that has never seen CQL on Databricks gets.
        for name, tool in _tools(server).items():
            assert tool.description, f"{name} has no description for the client to read"
            assert len(tool.description) > 80, f"{name}'s description is too thin to act on"

    def test_the_data_tools_take_their_scope_explicitly(self, server):
        # APP-ASSISTANT-CONTEXT: the UI's selection PREFILLS these; nothing is read from ambient
        # state. If a required argument ever disappears from one of these signatures, an external
        # caller has lost the ability to say what it means.
        tools = _tools(server)
        for name in ("get_output", "get_freshness"):
            required = set(tools[name].parameters.get("required", []))
            assert {"result_set", "define"} <= required, name
        assert {"result_set"} <= set(
            tools["list_tables"].parameters.get("required", []))

    def test_the_period_is_optional_everywhere_it_appears(self, server):
        # A measure with no measurement period is addressable (`_naming.period_year` omits the
        # segment rather than guessing today's year), so demanding one would make a legitimate
        # address unreachable.
        tools = _tools(server)
        for name in ("list_tables", "get_output", "get_freshness"):
            required = set(tools[name].parameters.get("required", []))
            assert "period_start" not in required and "period_end" not in required, name

    def test_get_output_tells_the_model_it_returns_a_handle_not_rows(self, server):
        # The one misunderstanding that makes this surface useless: an agent that thinks it gets rows
        # asks for a million and is handed a row count instead.
        description = _tools(server)["get_output"].description.lower()
        assert "handle" in description
        assert "does not return the rows" in description

    def test_query_data_tells_the_model_whose_number_it_produced(self, server):
        # SVC-ANSWER-PROVENANCE — the distinction has to reach the model, not just the payload.
        description = _tools(server)["query_data"].description.lower()
        assert "your derivation" in description
        assert "reads only" in description

    def test_get_freshness_refuses_to_hand_over_a_verdict(self, server):
        description = _tools(server)["get_freshness"].description.lower()
        assert "evidence, not a verdict" in description


class TestSkillSurface:
    """SVC-MCP-SKILLS — the guidance reaches the client that has never seen CQL on Databricks.

    The drawer can read `skills.py` off disk, so none of this is about it. What is asserted here is
    the half that only MCP can carry: an external Genie Agent gets the same standing instructions and
    can reach the same contracts, because otherwise "one implementation, two clients" holds for the
    tools and quietly fails for everything governing how they are used.
    """

    def test_the_server_hands_the_client_its_standing_instructions(self, server):
        # Delivered at `initialize`, before any tool is read. Identity, not similarity: the drawer's
        # system prompt is this same object, and a copy is what would drift.
        assert server.instructions == skills.ANSWERING_INSTRUCTIONS

    def test_the_instructions_carry_what_no_single_tool_docstring_owns(self, server):
        # Each of these spans the surface, so none of them has a tool to live on. They are the four
        # things a caller gets wrong reading the five tools one at a time.
        instructions = server.instructions.lower()
        assert "your derivation" in instructions          # SVC-ANSWER-PROVENANCE
        assert "one complete selection" in instructions  # RUN-CLIENT-RESULT-SETS
        assert "evidence, not a verdict" in instructions  # SVC-TOOL-FRESHNESS
        assert "read-only" in instructions                # KNOWLEDGE-FAIL-LOUD

    def test_the_readonly_rule_bounds_the_STORE_and_not_the_subject(self, server):
        """SPEC-EXPLAIN-AGAIN — re-explaining is generation, and generation is what this is for.

        The read-only rule bounds the STORE, not the SUBJECT — a distinction that matters because
        the Designer's Explain control sends re-explanation requests. The contract is asserted
        both ways because dropping either one is a real failure with a plausible-looking diff:
        without the permission the feature is dead, and without the boundary a conversation appears
        to have rewritten a reviewed clinical document.
        """
        instructions = server.instructions.lower()
        assert "re-explaining is the one exception" in instructions
        # Matched on the two halves rather than the whole clause: the sentence wraps, and a line break
        # inside an assertion is a test that fails the next time someone reflows a paragraph.
        assert "persisting a narration" in instructions
        assert "is designer's own act" in instructions

    def test_the_instructions_stay_short_enough_to_always_send(self, server):
        # The tier split is the design: this is paid for on EVERY conversation, and a skill body is
        # paid for only when the task needs one. An instructions block that grew to a skill's length
        # would have quietly merged the two tiers back together.
        assert len(server.instructions) < 4000, (
            "ANSWERING_INSTRUCTIONS is always-on context — anything this long belongs in a skill, "
            "fetched on demand by `get_skill`")

    def test_the_catalog_is_reachable_and_names_the_published_skills(self, server):
        catalog = asyncio.run(server.call_tool("list_skills")).structured_content["result"]
        assert {entry["name"] for entry in catalog} == {"clinical-explain"}
        for entry in catalog:
            assert entry["description"], f"{entry['name']} gives an agent nothing to choose on"

    def test_a_skill_body_arrives_verbatim(self, server):
        # SPEC-EXPLAIN-VOICE — the same bytes the EXPLAIN generator is handed and the same file a
        # person reads. Asserted through the TOOL, so this covers the
        # transport as well as the loader: a body mangled on its way out is the failure that would
        # leave the agent and the person following two different contracts.
        result = asyncio.run(server.call_tool("get_skill", {"name": "clinical-explain"}))
        assert result.content[0].text == skills.get_skill("clinical-explain")


class TestMounted:
    """The two ways the mount silently stops serving, both found by wiring it and trying it.

    Neither is visible in `create_app`'s code, and neither raises at startup — the server comes up,
    lists five tools, and fails only when an agent calls one. `create_app` itself needs a live
    Lakebase, so these reconstruct its two structural decisions against the real MCP app.
    """

    def _app(self, server):
        # Through the SHARED wiring, not `server.http_app` directly: `create_app` builds its mount
        # that way, and a reconstruction that bypasses it would keep passing after the real one
        # changed (`SVC-MCP-TRANSPORT` moved statelessness in there).
        http = mcp_http_app(server)
        # THE LIFESPAN IS THE WIRING. Without `lifespan=http.lifespan` the streamable-HTTP session
        # manager is never started and every call answers "task group was not initialized".
        app = FastAPI(lifespan=http.lifespan)
        app.mount("/mcp", http)

        @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
        def _bare(request: Request) -> RedirectResponse:
            return RedirectResponse(url="/mcp/", status_code=307)

        # ...and the SPA fallback goes on AFTER, as it does in `create_app`.
        @app.get("/{path:path}")
        def _spa(path: str) -> PlainTextResponse:
            return PlainTextResponse("<!doctype html> the SPA")

        return app

    def _initialize(self, client, url: str):
        return client.post(url, headers={"Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "1"}}})

    def test_an_agent_reaches_the_tools_and_not_the_SPA(self, server):
        with TestClient(self._app(server)) as client:
            response = self._initialize(client, "/mcp/")
            assert response.status_code == 200
            assert "the SPA" not in response.text
            assert "cql-databricks" in response.text        # the server identifying itself

    def test_the_url_a_human_would_write_also_works(self, server):
        # `https://<app>/mcp` is what goes in an agent's config. Without the alias it falls to the
        # GET-only SPA catch-all and answers 405 — an error that says nothing about the cause.
        with TestClient(self._app(server)) as client:
            response = self._initialize(client, "/mcp")
            assert response.status_code == 200
            assert "cql-databricks" in response.text

    def test_the_catch_all_still_serves_everything_else(self, server):
        with TestClient(self._app(server)) as client:
            assert "the SPA" in client.get("/measures").text
