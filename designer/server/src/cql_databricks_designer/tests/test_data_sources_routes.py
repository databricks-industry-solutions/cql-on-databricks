"""DATA-SOURCE-UC-TAG, DATA-BINDINGS, SEC-OBO — the Designer serves the data sources and the Bind action itself.

Since EVALUATOR-REMOVE (`DEC-EVALUATOR-APP-REHOME`) there is no evaluator App to forward to. The list is read as
the signed-in user and cached per user; a reload drops only the caller's entry. Bind runs each step with the
user's own client and reports each step's outcome in the stream.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks.data_bindings.bindings import BindStep
from cql_databricks.compiler.data_source import DataBinding, DataSource
from cql_databricks_designer.routes import data_sources as routes


def _source(name: str) -> DataSource:
    return DataSource(name=name, catalog="main", schema=name,
                      data_bindings={"Patient": DataBinding(f"main.{name}.retrieve_Patient")})


class _Users:
    """Who is signed in, and how many lookups each user's reads cost."""

    def __init__(self) -> None:
        self.current = "a@example.com"
        self.loads: list[str] = []

    def load(self) -> list[DataSource]:
        self.loads.append(self.current)
        return [_source(f"src_{self.current.split('@')[0]}")]


@pytest.fixture
def users():
    return _Users()


def _client(cache: routes.DataSourceCache) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router(data_sources=cache, content=object()))  # type: ignore[arg-type]
    return TestClient(app)


def test_each_user_sees_their_own_list_cached_until_their_reload(users):
    cache = routes.DataSourceCache(users.load, lambda: users.current)
    client = _client(cache)
    assert [s["id"] for s in client.get("/api/data-sources").json()] == ["src_a"]
    assert client.get("/api/data-sources/src_a").json()["dataBindings"] == {
        "Patient": {"function": "main.src_a.retrieve_Patient"}}
    users.current = "b@example.com"
    assert [s["id"] for s in client.get("/api/data-sources").json()] == ["src_b"]
    assert users.loads == ["a@example.com", "b@example.com"]
    generation = cache.generation
    assert client.post("/api/data-sources/reload").json() == {"ok": True}
    assert cache.generation > generation
    client.get("/api/data-sources")
    users.current = "a@example.com"
    client.get("/api/data-sources")
    assert users.loads == ["a@example.com", "b@example.com", "b@example.com"]


def test_a_source_the_caller_cannot_see_is_a_404_that_says_so(users):
    res = _client(routes.DataSourceCache(users.load, lambda: users.current)).get("/api/data-sources/nope")
    assert res.status_code == 404
    assert "cql_databricks_data_source = 'nope'" in res.json()["error"]


def test_a_failed_lookup_is_an_error_not_an_empty_list():
    def broken() -> list[DataSource]:
        raise RuntimeError("main.junk does not have the data-source layout")
    client = TestClient(_client(routes.DataSourceCache(broken, lambda: "")).app, raise_server_exceptions=False)
    assert client.get("/api/data-sources").status_code == 500


def test_bind_runs_each_step_as_the_signed_in_user_and_reports_each_outcome(monkeypatch, users):
    monkeypatch.setenv("WAREHOUSE_ID", "wh")
    monkeypatch.setenv("CQL_RUNTIME_CATALOG", "cat")
    me = SimpleNamespace(name="the user's held session")
    monkeypatch.setattr(routes, "user_session", lambda warehouse: (seen.update(warehouse=warehouse), me)[1])
    seen = {}

    def plan(run, catalog, schema, content):
        """RUN-CLIENT-SESSION-REUSE — the steps run on the signed-in user's held session."""
        seen.update(run=run, at=(catalog, schema))
        def refused() -> str:
            raise PermissionError("PERMISSION_DENIED: not the owner of cat.runtime.valuesets")
        return [BindStep("measures view", lambda: "cat.runtime.measures"), BindStep("valuesets view", refused)]

    monkeypatch.setattr(routes, "plan_bind_steps", plan)
    res = _client(routes.DataSourceCache(users.load, lambda: users.current)).post("/api/data-bindings/bind")
    assert res.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[len("data: "):]) for line in res.text.split("\n\n") if line]
    assert seen == {"warehouse": "wh", "run": me, "at": ("cat", "runtime")}
    assert [e["stage"] for e in events] == ["start", "step", "step", "done"]
    assert events[2]["status"] == "error" and "not the owner" in events[2]["error"]
    assert events[-1]["created"] == ["cat.runtime.measures"]


def test_bind_without_a_warehouse_is_refused_before_any_stream(monkeypatch, users):
    monkeypatch.delenv("WAREHOUSE_ID", raising=False)
    monkeypatch.delenv("CQL_WAREHOUSE_ID", raising=False)
    res = _client(routes.DataSourceCache(users.load, lambda: users.current)).post("/api/data-bindings/bind")
    assert res.status_code == 400 and "WAREHOUSE_ID" in res.json()["error"]
