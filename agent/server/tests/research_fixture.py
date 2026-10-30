"""AGENT-DEFINITION-DISCOVERY, AGENT-SAVED-RESULTS, AGENT-INLINE-EXPERIMENTS — a Research stand-in for
tests that build the MCP server but do not exercise the research tools. Each collaborator fails loud if
called, so a test that reaches one by accident says so."""
from __future__ import annotations

from cql_databricks.agent.server.mcp import Research

#: The tools the research collaborators serve (`SVC-MCP-TOOLS`, amended 2026-10-29).
RESEARCH_TOOLS = {"list_definitions", "get_definition", "list_value_sets", "value_sets_with_code",
                  "find_results", "get_population_summary", "check_cql", "run_cql", "compare_cql"}


def _unused(name):
    def call(*args, **kwargs):
        raise AssertionError(f"this test does not exercise {name}")
    return call


def fake_research(**changes) -> Research:
    fields = {name: _unused(name) for name in
              ("corpus", "code_lookup", "translate", "inline", "scoring_for", "saved")}
    return Research(**(fields | changes))
