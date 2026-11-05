"""AGENT-SAVED-RESULTS, AGENT-DEFAULT-ENVIRONMENT — find_results looks among the caller's own results in the
environment, then the official ones, and never computes. The client's preparation and resolution are stubbed; they
have their own tests (`RUN-CLIENT-RESULT-OWNERSHIP`)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cql_databricks.agent.service import saved
from cql_databricks.resolvers.environment import Environment
from cql_databricks.resolvers.interfaces import Resolvers
from cql_databricks.result import ResultsUnavailable

ENVIRONMENT = Environment(name="synthetic-population", catalog="synthetic", version="3")
RESULTS = object()
RESOLVERS = Resolvers(data=None, terminology=None, results=RESULTS, environment=ENVIRONMENT.name)

PREPARED = SimpleNamespace(library="Diabetes", version="1", targets=("Diabetes.Numerator",),
                           request={"period": ["2026-01-01", "2026-12-31"]})


def env():
    return saved.SavedResultsEnv(run=object(), content=object(), compiler=object(), resolvers=RESOLVERS,
                                 readers=lambda address: RESOLVERS, runtime_catalog="runtime", runtime_schema="runtime",
                                 scoring_for=lambda *a: "proportion")


def outcome(schema, when):
    return SimpleNamespace(result=SimpleNamespace(artifact=SimpleNamespace(table=(*schema.split("."), "t")),
                                                  completed_at=when))


@pytest.fixture
def client(monkeypatch):
    calls = {}

    def prepare(**kw):
        calls["prepared"] = kw
        return PREPARED

    def resolve(prepared, *targets, **kw):
        calls.update(kw, targets=targets)
        if calls.get("miss"):
            raise ResultsUnavailable("no completed result for Diabetes.Numerator")
        return SimpleNamespace(outcomes=(
            outcome("synthetic.draft_results", datetime(2026, 10, 1, tzinfo=timezone.utc)),
            outcome("synthetic.results", datetime(2026, 10, 12, tzinfo=timezone.utc))))

    monkeypatch.setattr(saved, "prepare_calculation", prepare)
    monkeypatch.setattr(saved, "selection_address", lambda results: {"selection_id": "s1"})
    monkeypatch.setattr(saved, "resolve_results", resolve)
    return calls


def test_a_draft_lookup_reads_the_callers_own_results_in_the_environment(client):
    """RUN-CLIENT-RESULT-OWNERSHIP — one read-only lookup through the environment's result resolver: the caller's
    own in draft_results, then the official ones in results."""
    found = saved.find_results("Diabetes", "1", (None, None), env=env())
    assert client["prepared"]["resolvers"] is RESOLVERS
    assert client["results"] is RESULTS
    assert found["found"] and found["environment"] == "synthetic-population"
    assert found["computedAt"] == "2026-10-12T00:00:00+00:00"
    assert found["schemas"] == ["synthetic.draft_results", "synthetic.results"]
    assert found["resultSet"] == {"selection_id": "s1"}


def test_a_miss_is_an_answer_not_a_computation(client):
    client["miss"] = True
    found = saved.find_results("Diabetes", "1", (None, None), env=env())
    assert found == {"found": False, "reason": "no completed result for Diabetes.Numerator"}
