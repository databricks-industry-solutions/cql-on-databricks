"""DEPLOY-AGENT-REGISTRATION — put the agent App under Unity Gateway, per user, as the deployer.

Genie One calls only MCP servers connected through Unity Gateway; a Databricks App it is not connected to is
invisible to it. Run by ``scripts/deploy/deploy.sh`` after the Apps are started. Idempotent: a redeploy that
finds everything in place changes nothing, touches no credential and makes no account call, so only a deploy
that creates or repoints the connection needs an account admin.

1. A Unity Catalog HTTP connection to the App's ``/mcp``, marked ``is_mcp_connection``, signing each user in
   through an OAuth client, so their own identity reaches the agent App and its tools run as them (`SEC-OBO`).
   The workspace's OIDC server offers no dynamic client registration, so the client is an account OAuth app
   integration, ``<connection>_u2m``. The deploy creates it through the account API with the account profile
   ``agent_account_profile`` names, so that deployer must be an account admin. Without the profile, a connection
   that must be created or repointed is left alone and the deploy says Genie One is not connected: connecting
   Genie One is opt-in. A shared service-principal credential (M2M) is never used: every Genie One user would act
   with one principal's grants.
2. An MCP Service securable over that connection, in ``agent_skills_schema``, beside the skills. The users group
   gets ``EXECUTE`` on it, never ``USE CONNECTION``, which would let a caller reach the server around Unity
   Gateway's tool selection and audit.

The client's secret is returned only when the client is created, and goes straight into the connection: it is
never printed, stored or asked for. A connection that must be created or repointed therefore gets a new client,
and each user signs in again.

Ported from the sibling cdc-demo repository's ``scripts/register_mcp.py``. Unlike it, this fails the deploy on
any error, takes its group from ``databricks.local.yml`` and has no M2M mode.

    DATABRICKS_CONFIG_PROFILE=<profile> python scripts/deploy/register_agent_mcp.py --app mcp-cql-databricks \\
        --schema <catalog.schema> --users-group <group> --account-profile <account profile>
"""
from __future__ import annotations

import argparse
import sys

#: UC uses this redirect for an HTTP connection's OAuth sign-in. The OAuth client must list it exactly.
REDIRECT_PATH = "/login/oauth/http.html"

#: A per-user connection refreshes without sending the user back to a browser, so it needs offline_access.
SCOPES = ("all-apis", "offline_access")

#: The MCP Service's id in the schema.
SERVICE_ID = "cql_databricks"



class RegistrationError(RuntimeError):
    """A registration step that cannot complete. Fails the deploy, naming what to do."""


#: ``ensure_connection``'s answer when the connection must change and no account profile was given.
NEEDS_ACCOUNT = "needs an account admin"


def connection_name(workspace_id: int | str) -> str:
    """A connection is a metastore securable, and a metastore spans workspaces: the id keeps one per workspace."""
    return f"mcp_cql_databricks_{workspace_id}"


def connection_options(host: str, app_url: str, client_id: str) -> dict[str, str]:
    """The connection's options, without its secret. ``mcp_resource`` binds the token to this server (RFC 8707)."""
    app_url = app_url.rstrip("/")
    return {"host": app_url, "port": "443", "base_path": "/mcp", "is_mcp_connection": "true",
            "oauth_scope": " ".join(SCOPES), "client_id": client_id,
            "token_endpoint": f"{host}/oidc/v1/token", "authorization_endpoint": f"{host}/oidc/v1/authorize",
            "mcp_resource": f"{app_url}/mcp"}


def client_name(connection: str) -> str:
    """The account OAuth client's name: one per connection, so one per workspace."""
    return f"{connection}_u2m"


def find_client(account, name: str):
    """The account's OAuth client named ``name``, or None. Refused unless the deployer is an account admin."""
    from databricks.sdk.errors import PermissionDenied

    try:
        found = [c for c in account.custom_app_integration.list() if c.name == name]
    except PermissionDenied as error:
        raise RegistrationError(
            f"the account API refused to list OAuth clients ({error}). Creating the agent's OAuth client needs an "
            "account admin: an account admin runs this deploy, with agent_account_profile naming their account "
            "profile (docs/deploy.md, The agent in Genie One).") from error
    if len(found) > 1:
        raise RegistrationError(f"{len(found)} account OAuth clients are named {name}; delete all but one")
    return found[0] if found else None


def new_client(account, name: str, redirect: str, existing):
    """A new OAuth client, replacing ``existing``: a client's secret is returned only when it is created."""
    if existing is not None:
        account.custom_app_integration.delete(existing.integration_id)
    created = account.custom_app_integration.create(name=name, confidential=True, redirect_urls=[redirect],
                                                    scopes=list(SCOPES))
    print(f"--> {'replaced' if existing is not None else 'created'} account OAuth client {name}")
    return created


def keep_client(account, client, redirect: str) -> None:
    """Put the redirect and scopes back on a kept client when someone changed them. Its client ID stays."""
    if redirect in (client.redirect_urls or []) and set(client.scopes or []) == set(SCOPES):
        return
    account.custom_app_integration.update(client.integration_id, redirect_urls=[redirect], scopes=list(SCOPES))
    print(f"--> restored the redirect and scopes of account OAuth client {client.name}")


def ensure_connection(workspace, account, name: str, host: str, app_url: str) -> str:
    """Create the connection, or repoint it when its options differ. Returns ``created``, ``updated``, ``kept`` or
    ``NEEDS_ACCOUNT``.

    A connection that already points at this App through its client is kept without an account call. With an
    account, its client is checked too, and the redirect and scopes put back. A connection that must be created
    or repointed needs a secret, and only a new client has one, so it needs ``account``."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.catalog import ConnectionType

    try:
        existing = workspace.connections.get(name)
    except NotFound:
        existing = None
    current = (existing.options or {}).get("client_id") if existing is not None else None
    in_place = bool(current) and all((existing.options or {}).get(k) == v
                                     for k, v in connection_options(host, app_url, current).items())
    if account is None:
        return "kept" if in_place else NEEDS_ACCOUNT
    redirect = f"{host}{REDIRECT_PATH}"
    client = find_client(account, client_name(name))
    if in_place and client is not None and client.client_id == current:
        keep_client(account, client, redirect)
        return "kept"
    created = new_client(account, client_name(name), redirect, client)
    full = {**connection_options(host, app_url, created.client_id), "client_secret": created.client_secret}
    if existing is None:
        workspace.connections.create(name=name, connection_type=ConnectionType.HTTP, options=full,
                                     comment="DEPLOY-AGENT-REGISTRATION: per-user MCP connection to the CQL on "
                                             "Databricks agent")
        return "created"
    workspace.connections.update(name=name, options=full)
    return "updated"


def ensure_service(workspace, schema: str, connection: str) -> None:
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.catalog import McpService, McpServiceConfig, McpServiceConfigSourceConnection

    try:
        workspace.ai_gateway.get_mcp_service(f"mcp-services/{schema}.{SERVICE_ID}")
        return
    except NotFound:
        pass
    workspace.ai_gateway.create_mcp_service(
        McpService(comment="CQL on Databricks: definitions, saved results and inline experiments "
                           "(SVC-MCP-TOOLS).",
                   config=McpServiceConfig(source_connection=McpServiceConfigSourceConnection(
                       name=f"connections/{connection}"))),
        parent=f"schemas/{schema}", mcp_service_id=SERVICE_ID)
    print(f"--> created MCP Service {schema}.{SERVICE_ID}")


def grant(workspace, securable_type: str, full_name: str, group: str, privileges: list[str]) -> None:
    from databricks.sdk.service.catalog import PermissionsChange, Privilege

    workspace.grants.update(securable_type=securable_type, full_name=full_name,
                            changes=[PermissionsChange(principal=group, add=[Privilege(p) for p in privileges])])


def register(workspace, account, *, app_name: str, schema: str, users_group: str) -> str | None:
    """Every step, in order. Returns the connection name each user signs in to, or None when the connection must
    change and ``account`` is None. The schema exists already: the skills step creates it (`DEPLOY-AGENT-SKILLS`)."""
    if schema.count(".") != 1 or not all(schema.split(".")):
        raise RegistrationError(f"agent_skills_schema must be catalog.schema; got {schema!r}")
    if not users_group:
        raise RegistrationError("no users group: set agent_users_group in databricks.local.yml")
    host = workspace.config.host.rstrip("/")
    connection = connection_name(workspace.get_workspace_id())
    app = workspace.apps.get(app_name)
    if not app.url:
        raise RegistrationError(f"App {app_name} has no URL yet; it must be deployed first")
    action = ensure_connection(workspace, account, connection, host, app.url.rstrip("/"))
    if action == NEEDS_ACCOUNT:
        return None
    print(f"--> connection {connection}: {action}")
    if action == "updated":
        print("--> the connection has a new OAuth client: every user signs in to it again")
    ensure_service(workspace, schema, connection)
    grant(workspace, "catalog", schema.split(".", 1)[0], users_group, ["USE_CATALOG"])
    grant(workspace, "schema", schema, users_group, ["USE_SCHEMA"])
    grant(workspace, "mcp_service", f"{schema}.{SERVICE_ID}", users_group, ["EXECUTE"])
    print(f"--> granted EXECUTE on {schema}.{SERVICE_ID} to {users_group}")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True)
    parser.add_argument("--schema", required=True, help="catalog.schema for the MCP Service")
    parser.add_argument("--users-group", required=True)
    parser.add_argument("--account-profile", default="",
                        help="an account admin's account-level CLI profile, from agent_account_profile. Empty: "
                             "a connection that must be created or repointed is left alone")
    args = parser.parse_args()

    from databricks.sdk import AccountClient, WorkspaceClient

    try:
        account = AccountClient(profile=args.account_profile) if args.account_profile else None
        connection = register(WorkspaceClient(), account, app_name=args.app, schema=args.schema,
                              users_group=args.users_group)
    except RegistrationError as error:
        print(f"!!! {error}", file=sys.stderr)
        return 1
    if connection is None:
        print("!!! GENIE ONE IS NOT CONNECTED to the agent: its connection is missing or points elsewhere, and only "
              "an account admin can create its OAuth client. Set agent_account_profile in databricks.local.yml to "
              "an account admin's account-level profile and deploy again (docs/deploy.md, The agent in Genie One).")
        return 0
    print("==> registered. Each user signs in to the connection once, as themselves:")
    print(f"    Catalog > External data > Connections > {connection} > Sign in")
    print("    Then, in Genie One, add the MCP Service under its tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
