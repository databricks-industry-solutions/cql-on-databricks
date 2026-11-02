"""Lazy Databricks SDK access, isolated behind small seams (`SVC-DISCOVERY-PLUGIN`).

Every function imports the Databricks SDK **lazily** and does exactly one workspace lookup, so the rest of
the plugin is pure orchestration that unit-tests can drive by monkeypatching these seams — no SDK and no
workspace needed for the tests. The concrete SDK surfaces here (the Apps API shape, the ambient-token
source) are **validated at deployment on a real workspace**; each seam fails soft (returns ``None``) so the
call site raises one actionable error rather than leaking a stack trace (`SVC-ZERO-CONFIG-NOTEBOOK`).
"""
from __future__ import annotations

from typing import Optional


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


def _get_app(app_name: str):
    """The App-registry record for ``app_name`` (a dict or a typed SDK object), or ``None``.

    Resilient to Databricks SDK version drift across runtimes: the typed ``apps`` wrapper differs by SDK
    version, and the SERVERLESS runtime ships a stale SDK whose Apps wrapper calls a DEAD
    ``/preview/apps/instances/...`` path (validated on the reference workspace: ``NotFound``). So we
    hit the STABLE current REST path (``GET /api/2.0/apps/{name}``) directly via the low-level
    ``api_client`` first — that works on both classic and serverless — and only fall back to the typed
    ``apps.get`` wrapper if ``api_client.do`` is unavailable. Fails soft so an unresolved lookup falls
    through to a loud, actionable error at the call site (`make_provider`)."""
    try:
        c = _client()
    except Exception:
        return None
    try:
        r = c.api_client.do("GET", f"/api/2.0/apps/{app_name}")
        if r:  # a dict on success
            return r
    except (AttributeError, TypeError):
        pass  # no api_client.do on this SDK — try the typed wrapper
    except Exception:
        return None  # a real lookup failure (not-found / permission) — fail soft
    try:
        return c.apps.get(name=app_name)
    except Exception:
        return None


def _app_field(app, name: str) -> Optional[str]:
    """Read ``name`` from an App record that may be a dict (REST) or a typed SDK object (wrapper)."""
    if app is None:
        return None
    return app.get(name) if isinstance(app, dict) else getattr(app, name, None)


def app_url(app_name: str) -> Optional[str]:
    """The deployed Compiler Service App's serving URL from the workspace App registry, or ``None``."""
    return _app_field(_get_app(app_name), "url")


def app_oauth_client_id(app_name: str) -> Optional[str]:
    """The App's OAuth resource-principal client id — the token-exchange ``audience``. ``None`` if absent."""
    app = _get_app(app_name)
    return _app_field(app, "oauth2_app_client_id") or _app_field(app, "service_principal_client_id")


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
