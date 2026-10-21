"""The ``databricks-app`` provider-kind entry point — App-registry endpoint discovery + OAuth
token-exchange (`SVC-DISCOVERY-PLUGIN`; the endpoint + auth halves of `BUILD-RESOLVE`).

``cql-databricks``'s ``resolve_provider(conf)`` reads ``spark.cqldatabricks.provider.kind``; when it is
``databricks-app`` it loads this factory (``make_provider(conf) -> CompileProvider``) instead of the OSS
``remote`` path — so the App URL is discovered from the workspace App registry (OSS has no app registry)
and requests are authed by serverless→App OAuth token-exchange, all without the OSS core importing the
Databricks SDK. Set ``provider.kind = databricks-app`` explicitly or via the env-attached wrapper that
sets the ``spark.cqldatabricks.*`` block; then ``cql("…")`` runs zero-config (`SVC-ZERO-CONFIG-NOTEBOOK`).
"""
from __future__ import annotations

from typing import Mapping

from . import _workspace  # SDK-free at import (its Databricks imports are lazy, inside functions)

# Default App name (the `tag → default app name → fail` convention floor); override per deployment.
DEFAULT_APP_NAME = "cql-databricks-compiler"
CONF_APP_NAME = "spark.cqldatabricks.databricks.appName"
# SVC-COMPILER-M2M (SUPERSEDED) — the settings of the retired M2M mode. Setting any of them is an error.
RETIRED_M2M_CONFS = tuple(f"spark.cqldatabricks.databricks.{name}" for name in (
    "compilerAuth", "compilerClientId", "compilerSecretScope", "compilerSecretKey"))

# RFC 8693 subject-token type for the exchange. The ambient notebook/job identity token
# (`_workspace.ambient_subject_token`, `auth_type: runtime`) is an OPAQUE Databricks token, NOT a JWT —
# Databricks' OIDC endpoint classifies it under this Databricks-specific type URI. Using the generic
# `...:token-type:jwt` here makes `/oidc/v1/token` reject it with `400 invalid_request` (validated on a
# development workspace 2026-07). This is a platform-specific INPUT the plugin supplies; the exchange logic
# itself stays in the OSS client (`APP-DECOUPLE`).
DATABRICKS_PAT_TOKEN_TYPE = "urn:databricks:params:oauth:token-type:personal-access-token"

# SVC-EXCHANGE-RETRY — a Job's exchange sometimes fails with `invalid_grant` for a few minutes, then
# succeeds: on the night of 2026-10-10, every first attempt of two examples-Job runs failed and every retry a
# minute or two later got past it. The cause is unknown and could not be reproduced on demand, so the exchange
# retries that one error for up to this long before it fails. Remove this when the platform stops returning it.
EXCHANGE_RETRY_ERRORS = ("invalid_grant",)
EXCHANGE_RETRY_SECONDS = 180.0


def app_credentials(app_name: str, *, conf_hint: str):
    """``(url, OAuth2TokenExchange)`` for one deployed App, by name (`SVC-DISCOVERY-PLUGIN`).

    Resolves the App URL from the workspace App registry, then builds the RFC 8693 exchange from the
    workspace OIDC endpoint, the App's OAuth client id (audience) and the ambient identity token. Shared by
    the Compiler Service provider and the evaluator's engine (EVALUATOR-CLIENT-EVALUATE), so a notebook
    reaches every App the same way. ``conf_hint`` names the setting that overrides ``app_name``, for the
    error."""
    from cql_databricks.compiler import OAuth2TokenExchange

    url = _workspace.app_url(app_name)
    if not url:
        raise ValueError(
            f"cannot resolve App '{app_name}' in the workspace App registry — set '{conf_hint}' to the "
            f"deployed app name, or set its endpoint explicitly to bypass discovery.")

    host = _workspace.workspace_host()
    audience = _workspace.app_oauth_client_id(app_name)
    subject = _workspace.ambient_subject_token()
    if not (host and audience and subject):
        missing = [n for n, v in (("workspace host", host), ("app OAuth client id", audience),
                                  ("ambient subject token", subject)) if not v]
        raise ValueError(
            f"resolved App URL '{url}' but cannot assemble OAuth token-exchange (missing: "
            f"{', '.join(missing)}). Configure auth explicitly (spark.cqldatabricks.auth.*) or run in a "
            f"context with an ambient Databricks identity.")

    return url, OAuth2TokenExchange(
        token_url=f"{host.rstrip('/')}/oidc/v1/token",
        subject_token=subject,
        audience=audience,
        subject_token_type=DATABRICKS_PAT_TOKEN_TYPE,
        retry_errors=EXCHANGE_RETRY_ERRORS,
        retry_seconds=EXCHANGE_RETRY_SECONDS)


def make_provider(conf: Mapping[str, str]):
    """Build a ``RemoteProvider`` targeting the Compiler Service App, authed by OAuth token-exchange.

    Resolves the App URL (``spark.cqldatabricks.databricks.appName`` → default name → FAIL) from the workspace
    App registry, then constructs the engine's generic ``OAuth2TokenExchange`` (RFC 8693) from the
    workspace OIDC endpoint + the App's OAuth client id (audience) + the ambient identity token. The
    exchange itself is standard OIDC and lives in the OSS client; only these Databricks-specific INPUTS
    are supplied here (`compiler-service.md` section 6.5)."""
    # Imported from the OSS client (a runtime dependency); the exchange logic is NOT reimplemented here.
    from cql_databricks.compiler import RemoteProvider

    # SVC-COMPILER-M2M is SUPERSEDED by SVC-EXCHANGE-RETRY: the token exchange is the only path. A Job that still
    # sets the retired M2M settings fails here rather than quietly running on the exchange instead.
    retired = [key for key in RETIRED_M2M_CONFS if conf.get(key)]
    if retired:
        raise ValueError(f"{', '.join(retired)} retired with SVC-COMPILER-M2M: remove them; the compiler App "
                         f"is reached with the caller's own identity (SVC-EXCHANGE-RETRY)")
    url, creds = app_credentials(conf.get(CONF_APP_NAME) or DEFAULT_APP_NAME, conf_hint=CONF_APP_NAME)
    return RemoteProvider(url, creds)
