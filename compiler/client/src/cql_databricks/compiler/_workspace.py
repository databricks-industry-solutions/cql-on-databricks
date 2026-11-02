"""Lazy Databricks SDK access, isolated behind small seams (`SVC-DISCOVERY-PLUGIN`).

Every function imports the Databricks SDK **lazily** and does exactly one workspace lookup, so the rest of
the plugin is pure orchestration that unit-tests can drive by monkeypatching these seams — no SDK and no
workspace needed for the tests. The concrete SDK surfaces here (the Apps API shape, the ambient-token
source) are **validated at deployment on a real workspace**. An App lookup that fails raises
:class:`AppLookupError` naming the App and the cause. The workspace host and the ambient token return ``None``
when unresolved, and the call site raises one actionable error (`SVC-ZERO-CONFIG-NOTEBOOK`).
"""
from __future__ import annotations

from typing import Optional


class AppLookupError(RuntimeError):
    """The workspace App registry gave no App: it has no App by that name, or this identity may not read it."""


def _client():
    """A `WorkspaceClient` from the ambient environment (notebook/job auth). Lazy import so the module —
    and the unit tests — load without the Databricks SDK present."""
    from databricks.sdk import WorkspaceClient  # lazy
    return WorkspaceClient()


def workspace_host() -> Optional[str]:
    """The workspace URL (basis for the OIDC token endpoint). ``None`` if not resolvable."""
    try:
        return _client().config.host
    except Exception:
        return None


def _get_app(app_name: str) -> dict:
    """The App-registry record for ``app_name``, from ``GET /api/2.0/apps/{name}``.

    One REST call, on the stable path the SERVERLESS runtime's SDK also reaches. Its typed ``apps`` wrapper
    calls a dead ``/preview/apps/instances/...`` path there, so it is not used. Raises :class:`AppLookupError`
    naming the App when it is not found or this identity may not read it."""
    from databricks.sdk.errors import NotFound, PermissionDenied  # lazy

    try:
        client = _client()
    except Exception as e:
        raise AppLookupError(f"App '{app_name}' cannot be looked up: no Databricks client could be made ({e}). "
                             f"Set its endpoint explicitly to bypass discovery.") from e
    try:
        return client.api_client.do("GET", f"/api/2.0/apps/{app_name}")
    except NotFound as e:
        raise AppLookupError(f"App '{app_name}' was not found in the workspace App registry: {e}") from e
    except PermissionDenied as e:
        raise AppLookupError(f"App '{app_name}' cannot be read by this identity (permission denied): {e}") from e


def app_url(app_name: str) -> Optional[str]:
    """The deployed Compiler Service App's serving URL from the workspace App registry. ``None`` when the App
    exists but has no URL yet."""
    return _get_app(app_name).get("url")


def app_oauth_client_id(app_name: str) -> Optional[str]:
    """The App's OAuth resource-principal client id — the token-exchange ``audience``. ``None`` if absent."""
    app = _get_app(app_name)
    return app.get("oauth2_app_client_id") or app.get("service_principal_client_id")


def ambient_subject_token() -> Optional[str]:
    """The ambient notebook/job identity token to exchange (RFC 8693 ``subject_token``), or ``None``.

    VALIDATE ON WORKSPACE: the ambient-token source (SDK auth headers / notebook context) is
    deployment-specific."""
    try:
        headers = _client().config.authenticate() or {}
        auth = headers.get("Authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else None
    except Exception:
        return None
