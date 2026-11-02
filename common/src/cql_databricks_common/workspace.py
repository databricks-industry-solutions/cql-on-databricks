"""The BFF's Databricks SDK client, shared process-wide (`SEC-WS-CLIENT-SHARED`).

Every SDK call the backend makes goes through `service_principal_client()`. Constructing a
`WorkspaceClient` per call site — the pattern this module replaces — gives each one its own
`Config`, and the SDK caches an access token **per Config instance**. That is wasteful deployed
(a token round-trip per construction) and broken locally: the `databricks-cli` credential
strategy forks `databricks auth token`, which re-mints the token and rewrites the OS keyring on
*every* invocation. Two of those overlapping is a lost race — on macOS the loser's keychain
write fails with `exit status 45` ("item already exists"), surfacing as
`cannot get access token: forced token refresh: cache update`, and the rotated refresh token can
be dropped entirely, which is what forces an unexpected browser login.

One client per process makes that refresh single and serialized: the SDK's token source caches
in memory behind its own lock (`databricks.sdk.oauth.Refreshable`).

Identity: this client carries the **app's** identity — the service principal deployed, the
developer's own OAuth locally (`SEC-LOCAL-IDENTITY`). It is for infrastructure work: the app's own
plumbing — the app-to-app call to the Compiler Service, and the whole
ORCHESTRATION plane: every evaluation Job (scheduled AND interactive) and every Lakeflow pipeline
deploy, plus the workspace files those pipelines read back.

`user_client()` is the other half (`SEC-OBO`). Unity Catalog, the SQL warehouse and the assistant's
Foundation Model call act as the **signed-in user**, built per request from that user's forwarded
token and never cached here.

The boundary between app and user identity follows ORCHESTRATION versus DATA (`SEC-OBO-SCOPE-GAP`).
Apps user authorization has no `jobs` and no `pipelines` scope — there is no user token that can run
a Job or deploy a pipeline, so those calls have nowhere to stand but the app's own identity. The cost
is real and recorded on `SEC-OBO`: a pipeline run by the service principal reads the customer's
tables as the service principal, so UC row filters and column masks apply to it rather than to the reader.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

log = logging.getLogger(__name__)

_client: WorkspaceClient | None = None
_lock = threading.Lock()

#: `SEC-OBO` — the Databricks App platform forwards the signed-in user's OAuth access token on every
#: request under this header, once the App declares `user_api_scopes` and a workspace admin has
#: enabled user authorization. Absent header, absent user identity: there is nothing to fall back to.
FORWARDED_TOKEN_HEADER = "x-forwarded-access-token"

#: The signed-in user's email, forwarded on every request whether or not user authorization is on.
#: Used for logging and for the `cql_databricks_user` Job tag; the AUTHORITATIVE identity for naming and
#: `run_as` comes from `current_user.me()` on the token's own client, not from a header.
FORWARDED_EMAIL_HEADER = "x-forwarded-email"

#: Bound per request by `cql_databricks_designer.app`'s middleware. A `ContextVar` rather than a parameter
#: because the seam it feeds is `user_client()`, which is reached from ~30 call sites across the
#: orchestrator, the executor and the readers — threading a client through all of them would put the
#: identity decision in every signature between the route and the call. Starlette runs sync endpoints
#: in a threadpool that COPIES the context, so a plain `def` route sees it; a thread the BFF spawns
#: itself does NOT — `/api/run-stream` therefore captures `forwarded_identity()` on the request and
#: re-binds it inside its worker.
_forwarded: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "cql_databricks_forwarded_identity", default=None)


def service_principal_client() -> WorkspaceClient:
    """The process-wide `WorkspaceClient` for the app's own identity (`SEC-WS-CLIENT-SHARED`).

    Built on first use and reused thereafter. Do not use for user-scoped work (`SEC-OBO`).
    """
    global _client
    with _lock:
        if _client is None:
            from databricks.sdk import WorkspaceClient

            _client = WorkspaceClient()
            log.info("Databricks workspace client created (host=%s, auth=%s)",
                     _client.config.host, _client.config.auth_type)
        return _client


def acting_as_service_principal() -> bool:
    """Whether THIS PROCESS authenticates as a service principal rather than a person.

    That is the whole of the deployed/local distinction, and it is read off the resolved SDK config
    rather than from an env var or a target label so it cannot disagree with the credential actually
    in use. Deployed, the App injects `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` and the
    strategy resolves to `oauth-m2m`; locally the developer's `databricks auth login` profile
    resolves to `databricks-cli` or `oauth-u2m` (`SEC-LOCAL-IDENTITY`).
    """
    return (service_principal_client().config.auth_type or "") == "oauth-m2m"


def bind_forwarded_identity(token: str | None, email: str | None) -> None:
    """Bind the signed-in user's forwarded credentials to this request's context (`SEC-OBO`)."""
    _forwarded.set((token or "", email or "") if (token or email) else None)


def forwarded_identity() -> tuple[str, str] | None:
    """The bound `(token, email)`, or ``None`` when nothing was forwarded."""
    return _forwarded.get()


def install_forwarded_identity(app) -> None:
    """Bind the signed-in user's forwarded credentials on every request to ``app`` (`SEC-OBO`).

    A MIDDLEWARE rather than a FastAPI dependency because it must cover EVERY route, including the
    SPA fallback and the mounted MCP transport: a route added without a thought about identity must
    inherit the user's, and the failure mode of forgetting a dependency is the App's own service
    principal silently doing the user's work. It binds unconditionally and decides nothing;
    `user_client()` owns the policy, in one place.

    **Called by BOTH deployments** (`create_app` and `create_mcp_app`). `mcp-cql-databricks` is a Databricks
    App like any other and its tools read the same result tables — a second copy of this would be a
    second answer to who a request is from.
    """
    @app.middleware("http")
    async def _bind_signed_in_user(request, call_next):
        bind_forwarded_identity(request.headers.get(FORWARDED_TOKEN_HEADER),
                                request.headers.get(FORWARDED_EMAIL_HEADER))
        return await call_next(request)


def user_client() -> WorkspaceClient:
    """A `WorkspaceClient` acting as the **signed-in user** (`SEC-OBO`).

    Unity Catalog, the SQL warehouse and the assistant's model call go through this. The
    orchestration plane does NOT: Jobs, pipelines and the workspace files those pipelines read are
    the app's own, because no user token can carry them (`SEC-OBO-SCOPE-GAP`). The
    no-ambient-authority claim holds of what a user READS through CQL on Databricks, not of what
    CQL on Databricks RUNS on their behalf — that boundary is recorded on `SEC-OBO`.

    **Built per request and never cached**, unlike `service_principal_client()`. The forwarded token
    belongs to one user and expires on its own schedule; a process-wide cache of it would serve one
    user's identity to the next request. That costs a `Config` construction per call, which is the
    price of the seam — the SDK mints no token here, it is handed one.

    **One path per target, chosen by THIS PROCESS's own identity** — the same shape as
    `app_auth.bearer()`, and for the same reason:

    - Deployed (a service principal): the forwarded token is REQUIRED. Its absence means user
      authorization is not enabled on the App, and continuing as the service principal would hand
      every user the App's authority — silently, and exactly where it matters most
      (`CORE-NO-SILENT-MISLOWER`). So it raises.
    - Locally (a person's OAuth): the shared client already IS the signed-in user, there being
      exactly one (`SEC-LOCAL-IDENTITY`). It is returned unchanged.

    Neither target ever tries the other's credential. This is target detection, not a fallback chain.
    """
    identity = _forwarded.get()
    token = identity[0] if identity else ""
    if not acting_as_service_principal():
        return service_principal_client()
    if not token:
        raise RuntimeError(
            f"SEC-OBO: no {FORWARDED_TOKEN_HEADER} on this request, so there is no signed-in user "
            "to act as. CQL on Databricks is running as a service principal and will not use its own identity "
            "for a user's data. Enable user authorization on the App and declare its "
            "`user_api_scopes` (databricks.yml).")

    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=service_principal_client().config.host, token=token,
                           auth_type="pat")


def workspace_url() -> str:
    """The browser URL of the Databricks workspace this deployment belongs to (`APP-TOUR`).

    A Databricks App is served from `*.databricksapps.com`, a different origin from the workspace it
    was deployed into, so the SPA cannot derive this and it cannot be a relative link. It is read from
    the client's own resolved config — the App identity's host deployed, the developer's profile
    locally — rather than from a deploy knob, so there is nothing to keep in step.

    Raises when no host resolves, which is the only honest answer: a link built on a guess would land
    a reader on someone else's workspace.
    """
    host = (service_principal_client().config.host or "").rstrip("/")
    if not host:
        raise RuntimeError("no Databricks workspace host resolved from the SDK config chain")
    return host


def reset_client() -> None:
    """Drop the cached client so the next call rebuilds it. Tests only."""
    global _client
    with _lock:
        _client = None
