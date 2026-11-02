"""AGENT-TOOL-REGISTRY, SPEC-MEASURE-METADATA — the Designer's contribution to the agent's tool surface,
and the one Specification composition it shares with the route."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cql_databricks.agent.server.mcp import Research, create_mcp
from cql_databricks_designer import assistant_tools


class _Corpus:
    def __init__(self, error: Exception | None = None, source: str | None = None):
        self.error = error
        self.source = source

    def measure_resource(self, library, version):
        if self.error:
            raise self.error
        return None

    def library(self, library, version):
        return SimpleNamespace(source=self.source) if self.source is not None else None


class _Content:
    def __init__(self, corpus):
        self._corpus = corpus

    def corpus(self):
        return self._corpus


class _Contents:
    """DOC-ENVIRONMENT-PICKER — one environment's content, whichever environment is asked for."""

    def __init__(self, content):
        self.content, self.asked = content, []

    def get(self, name):
        self.asked.append(name)
        return self.content


def test_the_designer_contributes_exactly_get_specification():
    tools = assistant_tools.designer_tools(contents=_Contents(_Content(_Corpus())))
    assert tools.names() == ["get_specification"]


def _unused(*args, **kwargs):
    raise AssertionError("this test does not exercise the research tools")


def test_the_tool_serves_the_same_composition_as_the_route(monkeypatch):
    """One composition, two clients: the tool calls `specification_document`, which the route calls too."""
    calls = []
    monkeypatch.setattr(assistant_tools, "specification_document",
                        lambda library, version, *, content, location=None: calls.append((library, version)) or {"ok": 1})
    server = create_mcp(warehouse=lambda sql: None,
                        reference_for=lambda address, define: None, computed_for=lambda address: [],
                        research=Research(**{name: _unused for name in Research.__dataclass_fields__}),
                        contributed=assistant_tools.designer_tools(contents=_Contents(_Content(_Corpus()))))
    asyncio.run(server.call_tool("get_specification", {"library": "ExampleMeasure", "version": "0.0.001"}))
    assert calls == [("ExampleMeasure", "0.0.001")]


def test_a_failed_measure_read_is_logged_not_raised():
    """The Measure resource is an enrichment; a corpus failure must not take the document down."""
    content = _Content(_Corpus(error=RuntimeError("warehouse down")))
    assert assistant_tools.specification_document("ExampleMeasure", "0.0.001", content=content) is None


def test_no_resource_is_no_specification():
    assert assistant_tools.specification_document("ExampleMeasure", "0.0.001",
                                                  content=_Content(_Corpus())) is None
