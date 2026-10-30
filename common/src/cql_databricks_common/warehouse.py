"""RUN-CLIENT-SESSION-REUSE — the App's SQL warehouse sessions, held per identity for the life of the process.

An App holds a warehouse session the way a notebook holds its Spark session: one per signed-in user (and one
for the App's service principal) per warehouse, opened on its first statement and kept until the process
shuts down (:func:`close_sessions`, from the App's lifespan). So the runtime functions' sandbox starts once per
user, not once per run, measure or request. A lost session is a cache miss, never a wrong answer: a restart or
another replica pays the start once more.

SEC-OBO — a user's session is keyed by the email the Apps platform forwards and authenticates with that user's
newest forwarded token: every request swaps its own client in before it runs a statement. One user's session
is never handed to another.
"""
from __future__ import annotations

import threading

from cql_databricks.data_bindings import WarehouseSession, warehouse_session

from .workspace import acting_as_service_principal, forwarded_identity, service_principal_client, user_client

#: The key of this process's own identity: the service principal deployed, the developer locally.
_APP = ""


class _Current:
    """The newest ``WorkspaceClient`` for one identity; a held session reads its config on every request."""

    def __init__(self, client) -> None:
        self.client = client

    @property
    def config(self):
        return self.client.config


_held: dict[tuple[str, str], tuple[_Current, WarehouseSession]] = {}
_lock = threading.Lock()


def user_session(warehouse_id: str) -> WarehouseSession:
    """RUN-CLIENT-SESSION-REUSE, SEC-OBO — the signed-in user's held session on ``warehouse_id``.

    Call it on the request's thread: it reads the request's forwarded identity. Locally, the one user is the
    developer's own profile (`SEC-LOCAL-IDENTITY`), so it is the App's session.
    """
    client = user_client()
    if not acting_as_service_principal():
        return _session(_APP, warehouse_id, client)
    identity = forwarded_identity()
    email = identity[1] if identity else ""
    if not email:
        raise RuntimeError("SEC-OBO: no forwarded email on this request, so its warehouse session has no owner")
    return _session(email, warehouse_id, client)


def service_principal_session(warehouse_id: str) -> WarehouseSession:
    """RUN-CLIENT-SESSION-REUSE — the App's own held session on ``warehouse_id`` (the run log, the corpus)."""
    return _session(_APP, warehouse_id, service_principal_client())


def close_sessions() -> None:
    """End every held session. The App's lifespan calls it at shutdown."""
    with _lock:
        sessions = [session for _, session in _held.values()]
        _held.clear()
    for session in sessions:
        session.close()


def _session(identity: str, warehouse_id: str, client) -> WarehouseSession:
    key = (identity, warehouse_id)
    with _lock:
        held = _held.get(key)
        if held is None:
            current = _Current(client)
            held = _held[key] = (current, warehouse_session(current, warehouse_id))
        else:
            held[0].client = client
        return held[1]
