"""`SEC-APP-TO-APP` — how the BFF authenticates to the Compiler Service App.

A deployed Databricks App is fronted by the platform's own OAuth gate, and the gate is in front of
EVERY path (there is no unauthenticated `/health`). A request that carries no acceptable token is
answered with `302 -> /login`, and a client that follows redirects gets `200 text/html` holding the
Databricks sign-in page — which is what a JSON client reports as a "non-JSON response" and what the
CQL on Databricks SPA showed on load as `translate failed: validation service error (502)`. The compiler App's
OWN bearer check (`CQL_COMPILER_TOKEN`, `CompilerServiceApp.HostConfig`) is never reached, and a
static bearer could not pass the gate anyway — the gate wants a workspace OAuth token.

**The token is this process's own Databricks identity.** Deployed that is the CQL on Databricks App's service
principal; locally it is the developer's OAuth (`SEC-LOCAL-IDENTITY`). It is read off the shared
`WorkspaceClient` so the SDK owns caching and the hourly refresh (`SEC-WS-CLIENT-SHARED`) — no
token is minted per call site, and there is no separate credential to rotate. The gate then
authorizes that principal against the target App's permissions, which is why the bundle declares
the compiler App as a `CAN_USE` **app resource** of the CQL on Databricks App (`DEPLOY-APP-GRANTS`,
`databricks.yml`): the CQL on Databricks service principal is created BY the deploy, so nothing that ran
earlier could have granted it anything.

**No RFC 8693 exchange here, unlike the notebook client.** `SVC-DISCOVERY-PLUGIN`
(`cql-databricks/provider.py`) exchanges the ambient notebook token for an audience-scoped one
because that ambient token is an opaque Databricks token the gate will not take. An App's service
principal already holds a real workspace OAuth access token, and the gate accepts it directly.
Adding an exchange would be a second way to spell the same thing.

**One path per target, chosen by the endpoint.** An endpoint on `*.databricksapps.com` is
gate-fronted and uses the identity token; anything else is the local JVM host, which uses the
static `CQL_COMPILER_TOKEN` (`config.compiler_token()`) or no auth at all. That is target
detection, not a fallback chain — neither target ever tries the other's credential, and setting
both is a configuration error that raises rather than silently preferring one.
"""
from __future__ import annotations

from urllib.parse import urlparse

#: Databricks Apps are served from this domain, and everything on it sits behind the platform OAuth
#: gate. The endpoint alone therefore says which front door a request has to get through.
APPS_DOMAIN = ".databricksapps.com"


def is_app_endpoint(endpoint: str) -> bool:
    """True when ``endpoint`` is a deployed Databricks App (and so behind the platform OAuth gate)."""
    host = urlparse(endpoint).hostname or ""
    return host.endswith(APPS_DOMAIN)


def identity_bearer() -> str:
    """This process's Databricks OAuth access token (`SEC-APP-TO-APP`, `SEC-WS-CLIENT-SHARED`).

    The App's service principal deployed, the developer's own OAuth locally. Taken from the shared
    client's credential strategy, which caches and refreshes it.
    """
    from cql_databricks_common.workspace import service_principal_client

    headers = service_principal_client().config.authenticate() or {}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise RuntimeError(
            "SEC-APP-TO-APP: the Databricks credential strategy produced no Bearer token "
            f"(got headers {sorted(headers)}), so the Compiler Service App's OAuth gate cannot be "
            "passed. Deployed, this means the App has no service principal credentials; locally, "
            "run `databricks auth login` (or scripts/auth-preflight.sh).")
    return auth[7:].strip()


def bearer(endpoint: str, static_token: str | None = None) -> str | None:
    """The `Authorization: Bearer` value for a request to ``endpoint``, or ``None`` for no auth.

    ``static_token`` is the local JVM host's `CQL_COMPILER_TOKEN`. It is meaningless against a
    gate-fronted App, so supplying both is rejected rather than quietly ignored.
    """
    if not is_app_endpoint(endpoint):
        return static_token or None
    if static_token:
        raise RuntimeError(
            f"SEC-APP-TO-APP: CQL_COMPILER_TOKEN is set while the Compiler Service endpoint is a "
            f"Databricks App ({endpoint}). A static bearer cannot pass the platform OAuth gate — the "
            "App is reached with this process's own Databricks identity. Unset CQL_COMPILER_TOKEN.")
    return identity_bearer()
