"""SEC-OBO — the BFF acts as the signed-in user for every data-touching call.

The seam is `workspace.user_client()`, fed by a request-scoped forwarded token. What these tests
pin is the POLICY around it, because every failure mode here is silent: an App that quietly does a
user's work with its own service principal looks exactly like one that works.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks_common import workspace
from cql_databricks_common.workspace import (
    FORWARDED_EMAIL_HEADER, FORWARDED_TOKEN_HEADER, bind_forwarded_identity, forwarded_identity,
    user_client,
)


def _process_identity(auth_type: str):
    """Patch what THIS PROCESS authenticates as — the one thing `user_client` branches on."""
    return patch.object(
        workspace, "service_principal_client",
        MagicMock(return_value=SimpleNamespace(
            config=SimpleNamespace(auth_type=auth_type, host="https://ws.example.com"))))


@pytest.fixture(autouse=True)
def _clean_context():
    bind_forwarded_identity(None, None)
    yield
    bind_forwarded_identity(None, None)


def test_deployed_without_a_forwarded_token_raises_rather_than_using_the_app_identity():
    """The failure this refuses to have.

    Continuing as the service principal would hand every user the App's authority, silently, and
    exactly where it matters most — the calls that read the customer's data. `SEC-OBO` says the
    accelerator adds NO ambient authority, so the absence of a user is an error, not a default.
    """
    with _process_identity("oauth-m2m"), pytest.raises(RuntimeError, match="SEC-OBO"):
        user_client()


def test_deployed_with_a_forwarded_token_acts_as_that_user():
    with _process_identity("oauth-m2m"):
        bind_forwarded_identity("tok-abc", "ada@example.org")
        with patch("databricks.sdk.WorkspaceClient") as ctor:
            user_client()

    assert ctor.call_args.kwargs["token"] == "tok-abc"
    assert ctor.call_args.kwargs["host"] == "https://ws.example.com"


def test_locally_the_shared_client_already_is_the_signed_in_user():
    """SEC-LOCAL-IDENTITY — there is exactly one identity, and it is a person's.

    Not a fallback: it is the other target. A developer's `databricks auth login` profile resolves
    to `databricks-cli`, no token is forwarded because nothing forwards one, and building a second
    client would only re-mint the token the shared one already holds (`SEC-WS-CLIENT-SHARED`).
    """
    with _process_identity("databricks-cli") as sp:
        assert user_client() is sp.return_value


def test_a_forwarded_token_never_leaks_into_the_shared_client():
    """The user client is built per request and never cached, because the token belongs to one user
    and expires on its own schedule."""
    with _process_identity("oauth-m2m"):
        bind_forwarded_identity("tok-abc", "ada@example.org")
        with patch("databricks.sdk.WorkspaceClient") as ctor:
            user_client()
            user_client()

    assert ctor.call_count == 2, "a per-request client was cached across requests"


# --- the middleware that binds it ---------------------------------------------------------------


def _app() -> FastAPI:
    """The binding middleware alone, over a route that reports what it saw."""
    app = FastAPI()

    @app.middleware("http")
    async def _bind(request, call_next):
        bind_forwarded_identity(request.headers.get(FORWARDED_TOKEN_HEADER),
                                request.headers.get(FORWARDED_EMAIL_HEADER))
        return await call_next(request)

    @app.get("/sync")
    def sync_route() -> dict:
        return {"identity": forwarded_identity()}

    return app


def test_a_sync_route_sees_the_bound_identity():
    """Starlette runs a plain `def` endpoint in a threadpool that COPIES the request context.

    If it did not, the seam would have to be a parameter on every signature between the route and
    the SDK call — which is the design this middleware exists to avoid.
    """
    client = TestClient(_app())
    body = client.get("/sync", headers={FORWARDED_TOKEN_HEADER: "tok-abc",
                                        FORWARDED_EMAIL_HEADER: "ada@example.org"}).json()
    assert body["identity"] == ["tok-abc", "ada@example.org"]


def test_a_request_without_the_headers_binds_nothing():
    """Nothing forwarded means no user — which `user_client` turns into an error when deployed,
    and into the local dev identity otherwise. The middleware itself decides neither."""
    assert TestClient(_app()).get("/sync").json()["identity"] is None


def test_one_requests_identity_does_not_survive_into_the_next():
    client = TestClient(_app())
    client.get("/sync", headers={FORWARDED_TOKEN_HEADER: "tok-ada"})
    assert TestClient(_app()).get("/sync").json()["identity"] is None
