"""RUN-CLIENT-MAINTENANCE — the deployment-owned identity the daily `cql-databricks-designer-maintenance` Job runs as.

`scripts/deploy/grant-maintenance-access.sh` runs this before `bundle deploy`, and `deploy.sh` passes the printed
application ID to the bundle as `maintenance_principal`, the Job's run-as (`databricks.yml`). Never an App or user
identity.
"""
from __future__ import annotations

import json
import sys
from hashlib import sha256

#: Granted on the private catalog. MANAGE is inherited by every schema and table and deletes objects
#: the principal does not own; the USE privileges reach every user's schema.
MAINTENANCE_PRIVILEGES = ("USE_CATALOG", "USE_SCHEMA", "MANAGE")

#: The role a Job's run-as needs on its service principal. Creating a principal grants its creator only
#: `roles/servicePrincipal.manager`, and `bundle deploy` then fails with 403 PERMISSION_DENIED.
RUN_AS_ROLE = "roles/servicePrincipal.user"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _grant_deployer_run_as(workspace, application_id: str) -> None:
    """RUN-CLIENT-MAINTENANCE — let the deploying user set the principal as the Job's run-as.

    Adds the deployer to `RUN_AS_ROLE` in the principal's rule set and keeps every other rule. The
    account ID comes from the SDK config, which resolves it from the host's `/.well-known/databricks-config`.
    """
    from databricks.sdk.service.iam import GrantRule, RuleSetUpdateRequest

    account = _text(workspace.config.account_id or "", "workspace account ID")
    deployer = "users/" + _text(workspace.current_user.me().user_name or "", "deploying user")
    name = f"accounts/{account}/servicePrincipals/{application_id}/ruleSets/default"
    proxy = workspace.account_access_control_proxy
    current = proxy.get_rule_set(name=name, etag="")
    rules = list(current.grant_rules or [])
    if any(rule.role == RUN_AS_ROLE and deployer in (rule.principals or []) for rule in rules):
        return
    rules.append(GrantRule(role=RUN_AS_ROLE, principals=[deployer]))
    proxy.update_rule_set(name=name, rule_set=RuleSetUpdateRequest(name=name, etag=current.etag, grant_rules=rules))


def provision_maintenance_principal(workspace, *, catalog: str, warehouse: str) -> str:
    """RUN-CLIENT-MAINTENANCE — create or reuse the Job's dedicated identity, then grant it.

    The display name carries a hash of this workspace and private catalog, so retries reuse it.
    Workspace SCIM does not keep externalId (observed 2026-10-10: it came back empty and a retry
    created a duplicate), so the name is the lookup key.
    It has no secret: a Job run-as needs none, so no credential can leak. The deploying user gets
    `RUN_AS_ROLE` on it, so `bundle deploy` can set it as the Job's run-as. Grants are additive and
    never revoke anything. Returns the application ID to use as the Job's run-as.
    """
    from databricks.sdk.service.catalog import PermissionsChange, Privilege
    from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

    _text(catalog, "private catalog")
    _text(warehouse, "warehouse")
    installation = sha256((workspace.config.host.rstrip("/") + "\n" + catalog.casefold()).encode()).hexdigest()
    name = "cql-databricks-designer-maintenance-" + installation[:12]
    matching = list(workspace.service_principals.list(filter=f'displayName eq "{name}"'))
    if len(matching) > 1:
        raise ValueError("multiple maintenance principals claim this catalog; refusing to choose")
    if matching:
        principal = matching[0]
        if principal.display_name != name or not principal.active:
            raise ValueError("existing maintenance principal does not match this catalog")
    else:
        principal = workspace.service_principals.create(display_name=name, active=True)
    if not principal.application_id:
        raise ValueError("maintenance principal creation returned no application ID")
    _grant_deployer_run_as(workspace, principal.application_id)
    workspace.grants.update("catalog", catalog, changes=[PermissionsChange(
        principal=principal.application_id, add=[Privilege[privilege] for privilege in MAINTENANCE_PRIVILEGES])])
    workspace.permissions.update("warehouses", warehouse, access_control_list=[AccessControlRequest(
        service_principal_name=principal.application_id, permission_level=PermissionLevel.CAN_USE)])
    return principal.application_id


def main(argv: list[str]) -> None:
    """Read the resolved bundle variables (``bundle validate -o json``), provision, print the application ID last."""
    from databricks.sdk import WorkspaceClient

    config, profile = argv
    variables = json.loads(config)["variables"]

    def required(name: str) -> str:
        """Deployment values never fall back to another workspace."""
        value = variables[name]["value"]
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"!!! {name} must be set in databricks.local.yml")
        return value

    catalog = required("designer_results_catalog")
    warehouse = required("warehouse_id")
    # A single-catalog deployment is allowed: per-user schemas, not the catalog, separate private results.
    if catalog.casefold() == required("uc_catalog").casefold():
        print(f"==> NOTE: Designer results share catalog '{catalog}' with the product's data; MANAGE covers all of it.",
              file=sys.stderr)
        # DEPLOY-APP-GRANTS — in a shared catalog the Apps are granted per schema (grant-app-access.sh).
        print("==> NOTE: the Apps see only the schemas that exist at deploy time. After loading a new schema, redeploy"
              " or run scripts/deploy/grant-app-access.sh.", file=sys.stderr)
    try:
        principal = provision_maintenance_principal(WorkspaceClient(profile=profile), catalog=catalog,
                                                    warehouse=warehouse)
    except Exception as error:
        privileges = ", ".join(name.replace("_", " ") for name in MAINTENANCE_PRIVILEGES)
        raise SystemExit(f"!!! maintenance principal setup failed: {error}\n"
                         f"!!! The catalog must exist (created by an administrator); creating the principal needs"
                         f" workspace admin, giving yourself the Service Principal: User role on it needs its"
                         f" Manager role, and granting needs catalog OWNERSHIP or MANAGE:\n"
                         f"!!!   GRANT {privileges} ON CATALOG `{catalog}` TO `<principal>`;") from error
    print(f"==> maintenance principal {principal}: {', '.join(MAINTENANCE_PRIVILEGES)} on catalog {catalog}; "
          f"CAN_USE on warehouse {warehouse}; you hold {RUN_AS_ROLE} on it. No Designer user grants were changed.",
          file=sys.stderr)
    print(principal)


if __name__ == "__main__":
    main(sys.argv[1:])
