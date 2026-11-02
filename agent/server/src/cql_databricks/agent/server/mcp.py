"""SVC-MCP-TOOLS — the MCP surface that publishes the tools (`agent/README.md`).

**The tools live in the service (``cql_databricks.agent.service``: ``data.py``, ``definitions.py``,
``saved.py`` and ``experiments.py``); this file only publishes them.** That split is the requirement, not
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

**Collaborators are injected** (`create_mcp`): caller-owned SQL, complete-selection readers, the research
collaborators and contributed tools. No latest-output discovery or change-log lookup. Every tool runs in one
environment, which the wiring picks (`AGENT-DEFAULT-ENVIRONMENT`); no tool takes one as an argument.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP

from cql_databricks.agent import skills
from cql_databricks.agent.service import data, definitions, experiments, saved
from cql_databricks.agent.service.model import NodeAddress
from cql_databricks.result.selection_reading import SelectedOutput

from .instructions import standing_instructions
from .registry import ToolRegistry

log = logging.getLogger("cql_databricks.agent.server.mcp")

#: RUN-CLIENT-RESULT-SETS — resolve a target from an explicit complete-selection locator.
ReferenceLookup = Callable[[dict, str], SelectedOutput]


@dataclass(frozen=True)
class Research:
    """The collaborators of the research tools, each resolved per call as the caller (`SEC-OBO`).

    AGENT-DEFINITION-DISCOVERY, AGENT-VALUE-SET-LOOKUP, AGENT-SAVED-RESULTS, AGENT-INLINE-EXPERIMENTS."""

    corpus: definitions.CorpusSource
    code_lookup: definitions.CodeLookup
    translate: experiments.Translator
    inline: experiments.InlineRunner
    scoring_for: experiments.ScoringLookup
    saved: Callable[[], saved.SavedResultsEnv]
    #: RUN-ADDRESS-LOCATION — the caller's file source for folder locations.
    files: Callable[[], Any] = lambda: None


def create_mcp(*, warehouse: data.Warehouse,
               reference_for: ReferenceLookup,
               computed_for: data.ComputedLookup,
               research: Research,
               contributed: ToolRegistry | None = None) -> FastMCP:
    """The CQL on Databricks MCP server: the data tools over saved results, the research tools (definitions,
    saved results, inline experiments) and the skill pair. None of them saves anything (`SVC-MCP-TOOLS`).

    The docstring of each tool below is the ONLY documentation an external agent gets — MCP hands
    the model the name, the signature and this text. So each says what the tool answers and, where
    it matters, what it deliberately does not: an agent that does not know `get_output` returns a
    handle rather than rows will ask for a million rows and be told a number instead.

    **``instructions`` is the other half of that** (`SVC-MCP-SKILLS`). A client receives it at
    ``initialize``, before it reads any tool, and it carries what no single tool's docstring owns —
    whose number the caller is holding, that an output is a place rather than an event, that this
    surface saves nothing. The Designer's own drawer uses the SAME string as its system prompt, which is
    what keeps one measure from having two accounts of how to be reasoned about.

    **``contributed`` are tools another feature registers** (`AGENT-TOOL-REGISTRY`). The designer's
    ``get_specification`` arrives this way: the Specification is the designer's, and the agent may not
    import it (`DEC-AGENT-DESIGNER-CYCLE`). They are installed FIRST, so ``get_specification`` — the
    tool the instructions say to start with — still leads the list. The sentence that says to start
    with it is the contributor's too (``ToolRegistry.instructions``): the agent's own App serves no
    ``get_specification``, so its instructions must not name one (`APP-PER-FEATURE`).
    """
    mcp = FastMCP("cql-databricks", instructions=standing_instructions(
        contributed.instructions if contributed else ""))
    for fn in contributed or ():
        mcp.tool(fn)

    @mcp.tool
    def list_tables(result_set: dict) -> list[str]:
        """Compiler-issued output names in one complete result selection. Pass a resultSet locator
        returned by Designer or a publishing Job: destination, session_id and selection_id.
        Names are exact and library-qualified. This performs no computation or latest-result discovery."""
        return data.list_tables(computed_for, result_set)

    def output_address(reference):
        # CLIENT-ENVIRONMENT: the environment is the calculation request's, not the result's identity.
        origin = reference.origin
        request = reference.results.request or {}
        if "environment" not in request:
            raise ValueError("the result selection does not name its environment; reopen it from its "
                             "calculation request")
        return NodeAddress(origin["library"], origin["version"], origin["define"], request["environment"])

    @mcp.tool
    def get_output(result_set: dict, define: str) -> dict[str, Any]:
        """One selected output as a HANDLE, bounded preview and recorded physical schema. It
        does not return the rows of the entire result. Pass an exact name from list_tables and the same
        complete resultSet locator. Missing, corrupt or superseded selections fail. Reads only;
        it never computes. Use the pinned handle in query_data for your own derivations."""
        reference = reference_for(result_set, define)
        return data.get_output(output_address(reference), reference, warehouse=warehouse).to_dict()

    @mcp.tool
    def query_data(sql: str) -> list[list[Any]]:
        """Run ONE read against the lakehouse — over a handle from `get_output`, or over the data's own
        views.

        This is how you actually answer a question: counts, breakdowns, demographic cuts,
        comparisons across two handles. Reads only — a single statement beginning with SELECT or
        WITH. Results are capped, so aggregate rather than asking for everything.

        **What you get back is YOUR derivation, not the measure's result.** If it disagrees with a
        figure from `get_output`, both may be correct; say which is which. Never present a number
        you computed here as the measure's own.
        """
        return data.query_data(sql, warehouse=warehouse)

    @mcp.tool
    def get_freshness(result_set: dict, define: str) -> dict[str, Any]:
        """Successful-completion evidence, not a verdict about source freshness. Pass the same
        complete resultSet locator and exact define name used by get_output. computedAt is the saved
        successful computation time, including empty results. The cql-completion-v1 response states
        sourceFreshness=not_tracked; no source changes or table modification timestamps are consulted."""
        reference = reference_for(result_set, define)
        return data.get_freshness(output_address(reference), reference).to_dict()

    # -- Definitions and value sets (AGENT-DEFINITION-DISCOVERY, AGENT-VALUE-SET-LOOKUP) --------------

    @mcp.tool
    def list_definitions() -> list[dict[str, Any]]:
        """Every definition (CQL library) in the environment's knowledge: library, version, kind (measure or
        library), title and description. Measures come first.

        Match the user's words to a title or description yourself, by meaning, then ASK the user which
        one to use, or whether to write a new definition. Never choose one silently."""
        return definitions.list_definitions(corpus=research.corpus)

    @mcp.tool
    def get_definition(library: str, version: str, location: str | None = None) -> dict[str, Any] | None:
        """One definition's CQL exactly as written, with its title, description and, for a measure, its
        declared scoring. Null when no such library is there.

        Explain a criterion from this CQL and quote it, so the user can check your reading. This CQL is
        also the baseline_cql for an experiment. location is where the library is read: store:<catalog>.<schema> or folder:<path>; omit it for the environment's knowledge."""
        return definitions.get_definition(library, version, corpus=research.corpus, location=location,
                                          files=research.files() if location else None)

    @mcp.tool
    def list_value_sets() -> list[dict[str, Any]]:
        """Every value set the environment's definitions declare: its URL, the names authors declared it
        under, and the libraries (name|version) that use it. Match a clinical concept to the names
        yourself, by meaning. Value sets carry no other title."""
        return definitions.list_value_sets(corpus=research.corpus)

    @mcp.tool
    def value_sets_with_code(code: str, system: str | None = None) -> list[dict[str, Any]]:
        """Every value set in the terminology that contains a code, optionally only in one code system
        URL. Each lists the matching system and code, its declared names, and the libraries using it."""
        return definitions.value_sets_with_code(code, system, lookup=research.code_lookup,
                                                corpus=research.corpus)

    # -- Saved results (AGENT-SAVED-RESULTS) ------------------------------------------------------

    @mcp.tool
    def find_results(library: str, version: str,
                     period_start: str | None = None, period_end: str | None = None,
                     location: str | None = None) -> dict[str, Any]:
        """Whether you already saved a calculation of exactly this definition and measurement period, over
        the data you are working in: among your draft results, then your published ones. NEVER computes.

        When found, it says when it was computed (computedAt) and returns a resultSet locator. Tell the
        user when it was computed and ask whether to reuse it. A miss is found=false with a reason.
        The period defaults to the current calendar year; dates are YYYY-MM-DD. location is where the library is read: store:<catalog>.<schema> or folder:<path>; omit it for the environment's knowledge."""
        return saved.find_results(library, version, (period_start, period_end), env=research.saved(),
                                  location=location)

    @mcp.tool
    def get_population_summary(result_set: dict, scoring: str | None = None) -> dict[str, Any]:
        """A saved calculation's populations and score, the figures the Designer shows. Pass the
        resultSet from find_results.

        Each population is counted as written: its own define's subjects. The score (proportion rate,
        ratio or cohort count) is counted inside the population cascade, and carries its own numerator
        and base; the two need not add up. scoring overrides the measure's declared scoring.
        provenance=engine: this is the measure's computed result."""
        return saved.get_population_summary(result_set, env=research.saved(), scoring=scoring)

    # -- Experiments: inline, saved nowhere (AGENT-INLINE-EXPERIMENTS, AGENT-CQL-REVIEW) ------------

    @mcp.tool
    def check_cql(cql: str, baseline_cql: str | None = None, location: str | None = None) -> dict[str, Any]:
        """Translate a whole CQL library, with its includes from the environment's knowledge, and report every
        diagnostic. Runs nothing. With baseline_cql, also returns a unified diff to show the user
        BEFORE you run the CQL. With a location, includes resolve there first: store:<catalog>.<schema> or
        folder:<path>."""
        return experiments.check_cql(cql, baseline_cql, translate=research.translate, location=location)

    @mcp.tool
    def run_cql(cql: str, period_start: str | None = None, period_end: str | None = None,
                scoring: str | None = None, defines: list[str] | None = None,
                baseline_cql: str | None = None, location: str | None = None) -> dict[str, Any]:
        """Run a whole CQL library inline over the data you are working in, and save nothing.

        Returns each define's subjects counted as written (the populations by default, or the defines
        you name), the score when a scoring is declared or passed, the exact CQL that ran, and its diff
        against baseline_cql when given. provenance=experiment: never present this as the measure's
        official result. Show the user the CQL, or offer to, before running CQL you wrote. With a location,
        includes resolve there first."""
        return experiments.run_cql(cql, (period_start, period_end), scoring=scoring, defines=defines,
                                   inline=research.inline, scoring_for=research.scoring_for,
                                   baseline_cql=baseline_cql, location=location)

    @mcp.tool
    def compare_cql(baseline_cql: str, cql: str, period_start: str | None = None,
                    period_end: str | None = None, scoring: str | None = None,
                    defines: list[str] | None = None, location: str | None = None) -> dict[str, Any]:
        """Run the original and the edited library inline, both from the data as it is now, and compare
        them. Saves nothing.

        Returns both summaries (counts, score, CQL), the diff, and per define the count before and
        after with how many subjects entered and left. Every difference comes from the edit alone.
        provenance=experiment. Review the edit with the user before calling this. With a location, includes
        resolve there first."""
        return experiments.compare_cql(baseline_cql, cql, (period_start, period_end), scoring=scoring,
                                       defines=defines, inline=research.inline, scoring_for=research.scoring_for,
                                       location=location)

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
