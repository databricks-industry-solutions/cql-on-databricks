"""SVC-MCP-TOOLS — the MCP transport that publishes the tools (`agent/README.md`).

**The tools live in ``tools.py``; this file only publishes them.** That split is the requirement, not
tidiness: the Designer's own assistant and an external Genie Agent are two CLIENTS of one surface, so the
tools cannot acquire a shape that depends on either. Everything here is registration and argument
plumbing — if logic starts appearing in this module, the surface has begun to be shaped by its
transport.

**Aligned with the Databricks AI dev kit** (`databricks-solutions/ai-dev-kit`),
which is the current Databricks standard practice for this: a FastMCP server mounted at ``/mcp`` on
the same Databricks App that serves the product, authenticated by the App's own Databricks identity
rather than by an MCP-level scheme of our own. The kit lists both ``mcp`` and ``fastmcp``; we depend
on ``fastmcp`` alone, which brings the official ``mcp`` SDK with it — two pins that must stay
mutually compatible is a version puzzle with no upside.

**Collaborators are injected** (`create_mcp`): caller-owned SQL, complete-selection readers,
source discovery and contributed tools. No latest-output discovery or change-log lookup.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from . import skills, tools
from cql_databricks.result.selection_reading import SelectedOutput

from .model import NodeAddress
from .registry import ToolRegistry

log = logging.getLogger("cql_databricks.agent.server")

#: RUN-CLIENT-RESULT-SETS — resolve a target from an explicit complete-selection locator.
ReferenceLookup = Callable[[dict, str], SelectedOutput]


def create_mcp(*, warehouse: tools.Warehouse,
               reference_for: ReferenceLookup,
               computed_for: tools.ComputedLookup,
               data_sources: tools.DataSourceLister,
               contributed: ToolRegistry | None = None) -> FastMCP:
    """The CQL on Databricks MCP server: five read-only tools over the results we generate, plus the skill pair.

    The docstring of each tool below is the ONLY documentation an external agent gets — MCP hands
    the model the name, the signature and this text. So each says what the tool answers and, where
    it matters, what it deliberately does not: an agent that does not know `get_output` returns a
    handle rather than rows will ask for a million rows and be told a number instead.

    **``instructions`` is the other half of that** (`SVC-MCP-SKILLS`). A client receives it at
    ``initialize``, before it reads any tool, and it carries what no single tool's docstring owns —
    whose number the caller is holding, that an output is a place rather than an event, that this
    surface is read-only. The Designer's own drawer uses the SAME string as its system prompt, which is
    what keeps one measure from having two accounts of how to be reasoned about.

    **``contributed`` are tools another feature registers** (`AGENT-TOOL-REGISTRY`). The designer's
    ``get_specification`` arrives this way: the Specification is the designer's, and the agent may not
    import it (`DEC-AGENT-DESIGNER-CYCLE`). They are installed FIRST, so ``get_specification`` — the
    tool the instructions say to start with — still leads the list. The sentence that says to start
    with it is the contributor's too (``ToolRegistry.instructions``): the agent's own App serves no
    ``get_specification``, so its instructions must not name one (`APP-PER-FEATURE`).
    """
    mcp = FastMCP("cql-databricks", instructions=skills.standing_instructions(
        contributed.instructions if contributed else ""))
    for fn in contributed or ():
        mcp.tool(fn)

    @mcp.tool
    def list_data_sources() -> list[dict[str, Any]]:
        """The data this measure can be run against — and where every `data_source_id` comes from.

        Result tools require an explicit resultSet locator from Designer or a publishing Job.
        This source list does not discover completed calculations or grant access to them.

        Each entry carries what the registration recorded about the DATA — whose records, what period,
        whether it is real or fabricated — so it is also how you tell a synthetic development source
        from a live one before quoting a figure at somebody. Source changes are not tracked; use an
        explicit recompute when source data or terminology contents change.
        """
        return tools.list_data_sources(lister=data_sources)

    @mcp.tool
    def list_tables(result_set: dict) -> list[str]:
        """Compiler-issued output names in one complete result selection. Pass a resultSet locator
        returned by Designer or a publishing Job: destination, session_id and selection_id.
        Names are exact and library-qualified. This performs no computation or latest-result discovery."""
        return tools.list_tables(computed_for, result_set)

    def output_address(reference):
        # RUN-CLIENT-RESULT-IDENTITY: the data source is the calculation request's, not the result's identity.
        origin = reference.origin
        request = reference.results.request or {}
        if "data_source" not in request:
            raise ValueError("the result selection does not name its data source; reopen it from its "
                             "calculation request")
        return NodeAddress(origin["library"], origin["version"], origin["define"], request["data_source"])

    @mcp.tool
    def get_output(result_set: dict, define: str) -> dict[str, Any]:
        """One selected output as a HANDLE, bounded preview and recorded physical schema. It
        does not return the rows of the entire result. Pass an exact name from list_tables and the same
        complete resultSet locator. Missing, corrupt or superseded selections fail. Reads only;
        it never computes. Use the pinned handle in query_data for your own derivations."""
        reference = reference_for(result_set, define)
        return tools.get_output(output_address(reference), reference, warehouse=warehouse).to_dict()

    @mcp.tool
    def query_data(sql: str) -> list[list[Any]]:
        """Run ONE read against the lakehouse — over a handle from `get_output`, or over a source's
        own tables.

        This is how you actually answer a question: counts, breakdowns, demographic cuts,
        comparisons across two handles. Reads only — a single statement beginning with SELECT or
        WITH. Results are capped, so aggregate rather than asking for everything.

        **What you get back is YOUR derivation, not the measure's result.** If it disagrees with a
        figure from `get_output`, both may be correct; say which is which. Never present a number
        you computed here as the measure's own.
        """
        return tools.query_data(sql, warehouse=warehouse)

    @mcp.tool
    def get_freshness(result_set: dict, define: str) -> dict[str, Any]:
        """Successful-completion evidence, not a verdict about source freshness. Pass the same
        complete resultSet locator and exact define name used by get_output. computedAt is the saved
        successful computation time, including empty results. The cql-completion-v1 response states
        sourceFreshness=not_tracked; no source changes or table modification timestamps are consulted."""
        reference = reference_for(result_set, define)
        return tools.get_freshness(output_address(reference), reference).to_dict()

    # -- The skill pair (`SVC-MCP-SKILLS`) ---------------------------------------------------
    # NOT a sixth and seventh question shape — the thing `SVC-MCP-TOOLS` refuses. These answer
    # nothing about a measure; they publish the CONTRACTS the product writes under, so an external
    # agent and the Designer's own drawer explain a criterion in one voice. They are tools rather than MCP
    # resources or prompts because a tool is the one primitive every client honours, and the client
    # this surface exists for is the one that has never seen our UI.

    @mcp.tool
    def list_skills() -> list[dict[str, str]]:
        """The contracts CQL on Databricks publishes for writing about a measure, and when each applies.

        Cheap — names and one-line descriptions only. Read this first and fetch a body with
        `get_skill` when the task matches one; the bodies are long, and loading one you do not need
        costs you the context you were going to answer from.
        """
        return [skill.to_dict() for skill in skills.list_skills()]

    @mcp.tool
    def get_skill(name: str) -> str:
        """One contract's full text, to follow while you write.

        Fetch it BEFORE writing prose that explains measure logic — a define's description, a
        population's rationale, an answer describing what a criterion does. It is the same contract
        the product's own generated explanations are written under, so following it is what makes
        your answer read as part of the document rather than as a second opinion beside it.
        """
        return skills.get_skill(name)

    return mcp
