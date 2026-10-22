"""AGENT-TOOL-REGISTRY, SPEC-MEASURE-METADATA, APP-NARRATIVE-OVERLAY — the Designer's contribution to the
agent's tool surface, and the one Specification composition it shares with the route."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cql_databricks.agent.server import create_mcp
from cql_databricks_designer import assistant_tools
from cql_databricks_designer.explain import narrations as narration_module

NS = "https://cql-databricks.databricks.com/spec/1.0"
NARRATION = f"""<Specification xmlns="{NS}" schemaVersion="1.0">
  <Identity library="ExampleMeasure" version="0.0.001" context="patients"/>
  <Provenance/>
  <PopulationCriteria>
    <Population kind="initial-population" define="Initial Population" library="ExampleMeasure"
                stableId="s1" id="pop-IP">
      <Narrative provenance="narrated" method="ai-drafted" sourceTag="description" sourceSpan="1">Adults
        with a qualifying visit.</Narrative>
    </Population>
  </PopulationCriteria>
</Specification>"""


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


class _Narrations:
    """A store holding at most one instance, and the keys it was asked for."""

    def __init__(self, instance: str | None = None):
        self.instance = instance
        self.asked: list = []

    def latest(self, key):
        self.asked.append(key)
        return self.instance


def test_the_designer_contributes_exactly_get_specification():
    tools = assistant_tools.designer_tools(content=_Content(_Corpus()), narrations=_Narrations())
    assert tools.names() == ["get_specification"]


def test_the_tool_serves_the_same_composition_as_the_route(monkeypatch):
    """One composition, two clients: the tool calls `specification_document`, which the route calls too."""
    calls = []
    monkeypatch.setattr(assistant_tools, "specification_document",
                        lambda library, version, *, content, narrations: calls.append((library, version)) or {"ok": 1})
    server = create_mcp(warehouse=lambda sql: None,
                        reference_for=lambda address, define: None, computed_for=lambda address: [],
                        data_sources=lambda: [],
                        contributed=assistant_tools.designer_tools(content=_Content(_Corpus()),
                                                                   narrations=_Narrations()))
    asyncio.run(server.call_tool("get_specification", {"library": "ExampleMeasure", "version": "0.0.001"}))
    assert calls == [("ExampleMeasure", "0.0.001")]


def test_a_failed_measure_read_is_logged_not_raised():
    """The Measure resource is an enrichment; a corpus failure must not take the document down."""
    content = _Content(_Corpus(error=RuntimeError("warehouse down")))
    assert assistant_tools.specification_document("ExampleMeasure", "0.0.001", content=content,
                                                  narrations=_Narrations()) is None


def test_the_users_cached_narration_is_composed_in(monkeypatch):
    """APP-NARRATIVE-OVERLAY — the cached narration of this exact CQL, for the signed-in user, is served."""
    monkeypatch.setattr(assistant_tools, "signed_in_user", lambda: "reader@example.org")
    store = _Narrations(NARRATION)
    document = assistant_tools.specification_document(
        "ExampleMeasure", "0.0.001", content=_Content(_Corpus(source="library ExampleMeasure")), narrations=store)
    prose = document["elements"]["ExampleMeasure::Initial Population"]["description"]
    assert prose["provenance"] == "narrated" and prose["method"] == "ai-drafted"
    (key,) = store.asked
    assert key.user == "reader@example.org"
    assert key.source_sha256 == narration_module.source_digest("library ExampleMeasure")
    assert key.contract_sha256 == narration_module.contract_digest()


def test_no_cached_narration_and_no_resource_is_no_specification(monkeypatch):
    monkeypatch.setattr(assistant_tools, "signed_in_user", lambda: "reader@example.org")
    content = _Content(_Corpus(source="library ExampleMeasure"))
    assert assistant_tools.specification_document("ExampleMeasure", "0.0.001", content=content,
                                                  narrations=_Narrations()) is None


def test_a_failed_narration_read_is_logged_not_raised(monkeypatch):
    """The narration is an enrichment too: an unreadable cache serves the document without it."""
    def unreadable(key):
        raise RuntimeError("table missing")
    monkeypatch.setattr(assistant_tools, "signed_in_user", lambda: "reader@example.org")
    store = SimpleNamespace(latest=unreadable)
    content = _Content(_Corpus(source="library ExampleMeasure"))
    assert assistant_tools.specification_document("ExampleMeasure", "0.0.001", content=content,
                                                  narrations=store) is None
