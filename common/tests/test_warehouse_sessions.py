"""RUN-CLIENT-SESSION-REUSE, SEC-OBO — the App holds one warehouse session per identity per warehouse."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cql_databricks_common import warehouse


@pytest.fixture
def held(monkeypatch):
    """Synthetic identities; sessions are recorded, never connected."""
    built = []

    def session(client, warehouse_id):
        made = SimpleNamespace(client=client, warehouse=warehouse_id, closed=False)
        made.close = lambda: setattr(made, "closed", True)
        built.append(made)
        return made

    monkeypatch.setattr(warehouse, "warehouse_session", session)
    monkeypatch.setattr(warehouse, "_held", {})
    state = SimpleNamespace(built=built, deployed=True, identity=("token-a", "a@example.com"), client=None)
    monkeypatch.setattr(warehouse, "acting_as_service_principal", lambda: state.deployed)
    monkeypatch.setattr(warehouse, "forwarded_identity", lambda: state.identity)
    monkeypatch.setattr(warehouse, "user_client", lambda: state.client)
    monkeypatch.setattr(warehouse, "service_principal_client", lambda: "app-client")
    return state


def test_a_user_keeps_one_session_per_warehouse_across_requests(held):
    """RUN-CLIENT-SESSION-REUSE — the next request, run or measure reuses the user's warm session."""
    held.client = SimpleNamespace(config="request-1")
    first = warehouse.user_session("wh")
    held.client = SimpleNamespace(config="request-2")
    assert warehouse.user_session("wh") is first
    assert warehouse.user_session("other") is not first
    assert len(held.built) == 2


def test_a_held_session_authenticates_with_the_users_newest_client(held):
    """SEC-OBO — each request swaps its own client in, so an expired forwarded token is never used."""
    held.client = SimpleNamespace(config="request-1")
    session = warehouse.user_session("wh")
    held.client = SimpleNamespace(config="request-2")
    warehouse.user_session("wh")
    assert session.client.config == "request-2"


def test_one_users_session_is_never_handed_to_another(held):
    """SEC-OBO — sessions are keyed by the forwarded user, so two users never share one."""
    held.client = SimpleNamespace(config="a")
    mine = warehouse.user_session("wh")
    held.identity, held.client = ("token-b", "b@example.com"), SimpleNamespace(config="b")
    assert warehouse.user_session("wh") is not mine


def test_a_request_with_no_forwarded_user_gets_no_session(held):
    """SEC-OBO — deployed, a session needs an owner; it never falls back to the App's."""
    held.identity = ("token", "")
    with pytest.raises(RuntimeError, match="SEC-OBO"):
        warehouse.user_session("wh")


def test_locally_the_user_is_the_app(held):
    """SEC-LOCAL-IDENTITY — locally the one user is the developer's profile, so it is the App's session."""
    held.deployed, held.identity, held.client = False, None, SimpleNamespace(config="profile")
    assert warehouse.user_session("wh") is warehouse.service_principal_session("wh")


def test_close_sessions_ends_every_held_session(held):
    """RUN-CLIENT-SESSION-REUSE — the App's lifespan ends them at shutdown; the next use opens a new one."""
    held.client = SimpleNamespace(config="a")
    first = warehouse.user_session("wh")
    warehouse.service_principal_session("wh")
    warehouse.close_sessions()
    assert all(s.closed for s in held.built)
    assert warehouse.user_session("wh") is not first
