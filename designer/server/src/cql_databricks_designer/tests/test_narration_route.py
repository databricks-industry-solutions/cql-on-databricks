"""APP-NARRATIVE-OVERLAY — ``POST /api/libraries/{name}/{version}/narration``: stages, then one terminal event.

The compile, the model and the cache are fakes; the stream, the checks and the composition are real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks_designer.routes import narration as route

from .test_explain_accounting import REFERENCE_SET, VALID

SOURCE = "library SampleBP version '1.0.0'"


class _Corpus:
    def library(self, name, version):
        return SimpleNamespace(source=SOURCE) if (name, version) == ("SampleBP", "1.0.0") else None

    def measure_resource(self, name, version):
        return None


class _Content:
    def corpus(self):
        return _Corpus()


class _Store:
    def __init__(self, fail: bool = False):
        self.saved: dict = {}
        self.fail = fail

    def latest(self, key):
        return self.saved.get(key)

    def save(self, key, *, instance, endpoint, attempts, app_version):
        if self.fail:
            raise RuntimeError("MODIFY denied")
        self.saved[key] = instance


def _client(monkeypatch, *replies, compiled: bool = True, store: _Store | None = None):
    replies = list(replies)
    model = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=replies.pop(0)), finish_reason="stop")]))))
    monkeypatch.setattr(route, "signed_in_user", lambda: "reader@example.org")
    monkeypatch.setattr(route, "serving_client", lambda: model)
    monkeypatch.setattr(route.config, "assistant_endpoint", lambda: "chat-endpoint")
    monkeypatch.setattr(route.config, "app_version", lambda: "build-1")
    monkeypatch.setattr("cql_databricks_designer.assistant_tools.signed_in_user", lambda: "reader@example.org")
    monkeypatch.setattr(route, "resolve", lambda content, cql: cql)
    monkeypatch.setattr(route, "translate_root", lambda translate, resolution: SimpleNamespace(
        ok=compiled, elm="{}" if compiled else None, diagnostics=() if compiled else ("syntax error at 3:1",)))
    monkeypatch.setattr(route, "build_reference_set", lambda elm: SimpleNamespace(to_dict=lambda: REFERENCE_SET))
    store = store or _Store()
    app = FastAPI()
    app.include_router(route.router(compiler=SimpleNamespace(translate=None), content=_Content(), narrations=store))
    return TestClient(app), store


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]


def test_a_passing_narration_is_saved_and_served_composed(monkeypatch):
    client, store = _client(monkeypatch, VALID)
    events = _events(client.post("/api/libraries/SampleBP/1.0.0/narration"))
    assert [e.get("stage") for e in events if e["type"] == "stage"] == ["compiling", "writing", "checking", "saving"]
    final = events[-1]
    assert final["type"] == "narration" and final["attempts"] == 1
    assert "SampleBP::Hypertension Diagnoses" in final["specification"]["elements"]
    (key,) = store.saved
    assert (key.user, key.library, key.version) == ("reader@example.org", "SampleBP", "1.0.0")


def test_a_rejected_narration_reports_its_problems_and_caches_nothing(monkeypatch):
    client, store = _client(monkeypatch, "not xml", "still not xml")
    events = _events(client.post("/api/libraries/SampleBP/1.0.0/narration"))
    assert [e["type"] for e in events].count("problems") == 1
    final = events[-1]
    assert final["type"] == "rejected" and final["attempts"] == 2
    assert final["problems"][0]["kind"] == "invalid"
    assert store.saved == {}


def test_a_library_that_does_not_compile_is_an_error(monkeypatch):
    client, _ = _client(monkeypatch, compiled=False)
    final = _events(client.post("/api/libraries/SampleBP/1.0.0/narration"))[-1]
    assert final == {"type": "error", "error": "SampleBP@1.0.0 did not compile: syntax error at 3:1"}


def test_a_failed_cache_write_is_an_error_not_a_narration(monkeypatch):
    """An uncached narration would vanish on reload, so it is reported rather than shown."""
    client, _ = _client(monkeypatch, VALID, store=_Store(fail=True))
    final = _events(client.post("/api/libraries/SampleBP/1.0.0/narration"))[-1]
    assert final == {"type": "error", "error": "MODIFY denied"}


def test_an_unknown_library_is_a_404(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.post("/api/libraries/Nope/1.0.0/narration").status_code == 404


def test_a_missing_endpoint_is_refused_before_the_stream_opens(monkeypatch):
    client, _ = _client(monkeypatch)

    def unset():
        raise RuntimeError("CQL_ASSISTANT_ENDPOINT is not set")
    monkeypatch.setattr(route.config, "assistant_endpoint", unset)
    response = client.post("/api/libraries/SampleBP/1.0.0/narration")
    assert response.status_code == 400 and "CQL_ASSISTANT_ENDPOINT" in response.json()["error"]
