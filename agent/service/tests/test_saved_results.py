"""AGENT-SAVED-RESULTS — find_results looks in the caller's private schema, then the result scopes, and
never computes. The client's preparation and resolution are stubbed; they have their own tests."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cql_databricks.agent.service import saved
from cql_databricks.result import ResultsUnavailable

PREPARED = SimpleNamespace(library="Diabetes", version="1", targets=("Diabetes.Numerator",),
                           request={"period": ["2026-01-01", "2026-12-31"]})


def env(scopes=("shared.published", "shared.other")):
    return saved.SavedResultsEnv(run=object(), content=object(), compiler=object(), private_catalog="results",
                                 runtime_catalog="runtime", runtime_schema="runtime", result_scopes=scopes,
                                 scoring_for=lambda *a: "proportion")


def outcome(schema, when):
    return SimpleNamespace(result=SimpleNamespace(artifact=SimpleNamespace(table=(*schema.split("."), "t")),
                                                  completed_at=when))


@pytest.fixture
def client(monkeypatch):
    calls = {}
    monkeypatch.setattr(saved, "prepare_calculation", lambda **kw: PREPARED)
    monkeypatch.setattr(saved, "selection_address", lambda results: {"selection_id": "s1"})

    def resolve(prepared, *targets, **kw):
        calls.update(kw, targets=targets)
        if calls.get("miss"):
            raise ResultsUnavailable("no completed result for Diabetes.Numerator")
        return SimpleNamespace(outcomes=(outcome("results.me", datetime(2026, 10, 1, tzinfo=timezone.utc)),
                                         outcome("shared.published", datetime(2026, 10, 12, tzinfo=timezone.utc))))

    monkeypatch.setattr(saved, "resolve_results", resolve)
    return calls


def test_the_private_schema_is_read_first_then_the_clients_own_result_scopes(client, monkeypatch):
    monkeypatch.setattr(saved, "find_private_destination", lambda catalog, run: "PRIVATE")
    found = saved.find_results("Diabetes", "1", "population", (None, None), env=env())
    assert client["destination"] == "PRIVATE" and client["purpose"] == "private"
    # The client reads the configured scopes itself; an explicit empty list would be refused there.
    assert "catalog_scopes" not in client
    assert found["found"] and found["computedAt"] == "2026-10-12T00:00:00+00:00"
    assert found["schemas"] == ["results.me", "shared.published"]
    assert found["resultSet"] == {"selection_id": "s1"}


def test_with_no_private_schema_the_first_scope_is_read_as_published(client, monkeypatch):
    monkeypatch.setattr(saved, "find_private_destination", lambda catalog, run: None)
    saved.find_results("Diabetes", "1", "population", (None, None), env=env())
    assert client["destination"] == ("shared", "published") and client["purpose"] == "published"
    assert "catalog_scopes" not in client


def test_a_miss_is_an_answer_not_a_computation(client, monkeypatch):
    monkeypatch.setattr(saved, "find_private_destination", lambda catalog, run: "PRIVATE")
    client["miss"] = True
    found = saved.find_results("Diabetes", "1", "population", (None, None), env=env())
    assert found == {"found": False, "reason": "no completed result for Diabetes.Numerator"}


def test_nowhere_to_look_says_so(client, monkeypatch):
    monkeypatch.setattr(saved, "find_private_destination", lambda catalog, run: None)
    found = saved.find_results("Diabetes", "1", "population", (None, None), env=env(scopes=()))
    assert found["found"] is False and "no private results schema" in found["reason"]
