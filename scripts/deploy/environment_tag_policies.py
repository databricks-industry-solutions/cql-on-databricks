"""DEPLOY-ENVIRONMENT-TAG-POLICIES — make the environment tags governed, so only the project's admins can tag a catalog.

`scripts/deploy/environment-tag-policies.sh` runs this before the deploy tags any catalog (``deploy.sh`` step [7.9],
or ``databricks bundle run environment_tag_policies``). For each of the three environment tags
(`CLIENT-ENVIRONMENT`) it makes the tag policy that governs the key, with its allowed values, and gives the deployer
and each of ``admins`` the assigner role on it. Without the assigner role, owning a catalog is not enough to tag it an
environment or the default.

Idempotent: a policy is created once and its values set when they differ; roles are added to a policy's rule set,
keeping every other rule. Any failure exits non-zero, which fails the deploy. Creating a policy needs the create
privilege on governed tags (workspace admins have it unless an account admin turned it off).
"""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from cql_databricks.compiler.environment import DEFAULT_TAG, NAME_TAG, SUPPORTED_LAYOUTS, VERSION_TAG

#: DEPLOY-ENVIRONMENT-TAG-POLICIES — each governed key and its allowed values; none allows any value.
POLICIES: dict[str, tuple[str, ...]] = {
    NAME_TAG: (),
    VERSION_TAG: tuple(SUPPORTED_LAYOUTS),
    DEFAULT_TAG: ("true",),
}

DESCRIPTIONS = {
    NAME_TAG: "CQL on Databricks: the catalog is the environment of this name (CLIENT-ENVIRONMENT).",
    VERSION_TAG: "CQL on Databricks: the environment's layout version (CLIENT-ENVIRONMENT).",
    DEFAULT_TAG: "CQL on Databricks: the catalog is the default environment (CLIENT-ENVIRONMENT).",
}

#: The account role that may assign a governed tag.
ASSIGNER = "roles/tagPolicy.assigner"


def principal(name: str) -> str:
    """An admin as the account access-control names it: an email is a user, an application ID a service principal,
    anything else a group."""
    name = name.strip()
    if not name:
        raise ValueError("an admin is a user's email, a service principal's application ID or a group name")
    if "@" in name:
        return f"users/{name}"
    parts = name.split("-")
    if [len(p) for p in parts] == [8, 4, 4, 4, 12]:
        return f"servicePrincipals/{name}"
    return f"groups/{name}"


def ensure_policy(workspace, key: str, values: Sequence[str]) -> str:
    """The policy governing ``key`` with exactly ``values``, created or updated. Returns its id."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.tags import TagPolicy, Value

    wanted = TagPolicy(tag_key=key, description=DESCRIPTIONS[key], values=[Value(name=v) for v in values])
    try:
        held = workspace.tag_policies.get_tag_policy(key)
    except NotFound:
        created = workspace.tag_policies.create_tag_policy(wanted)
        print(f"--> created tag policy {key}", file=sys.stderr)
        return created.id
    if sorted(v.name for v in held.values or []) != sorted(values):
        workspace.tag_policies.update_tag_policy(key, wanted, update_mask="values")
        print(f"--> set the allowed values of tag policy {key}: {', '.join(values) or 'any'}", file=sys.stderr)
    return held.id


def grant_assigners(workspace, account: str, policy_id: str, principals: Sequence[str]) -> list[str]:
    """Add ``principals`` to the policy's assigner role, keeping every other rule. Returns those added."""
    from databricks.sdk.service.iam import GrantRule, RuleSetUpdateRequest

    name = f"accounts/{account}/tagPolicies/{policy_id}/ruleSets/default"
    proxy = workspace.account_access_control_proxy
    current = proxy.get_rule_set(name=name, etag="")
    rules = list(current.grant_rules or [])
    held = next((r for r in rules if r.role == ASSIGNER), None)
    added = [p for p in principals if held is None or p not in (held.principals or [])]
    if not added:
        return []
    if held is None:
        rules.append(GrantRule(role=ASSIGNER, principals=list(added)))
    else:
        held.principals = [*(held.principals or []), *added]
    proxy.update_rule_set(name=name, rule_set=RuleSetUpdateRequest(name=name, etag=current.etag, grant_rules=rules))
    return added


def provision(workspace, admins: Sequence[str]) -> None:
    """DEPLOY-ENVIRONMENT-TAG-POLICIES — every policy, and the assigner role for the deployer and ``admins``."""
    account = workspace.config.account_id
    if not account:
        raise ValueError("the workspace's account ID is not known; the SDK reads it from the host")
    deployer = workspace.current_user.me().user_name
    principals = list(dict.fromkeys([f"users/{deployer}", *(principal(a) for a in admins)]))
    for key, values in POLICIES.items():
        added = grant_assigners(workspace, account, ensure_policy(workspace, key, values), principals)
        if added:
            print(f"--> {', '.join(added)} may assign {key}", file=sys.stderr)


def admins_of(variables: dict) -> list[str]:
    """The ``admins`` bundle variable: a list of users, service principals or groups; empty means the deployer."""
    value = (variables.get("admins") or {}).get("value") or []
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise SystemExit(f"!!! admins must be a list of names in databricks.local.yml, not {value!r}")
    return value


def main(argv: list[str]) -> None:
    """Read the resolved bundle variables (``bundle validate -o json``) and provision."""
    from databricks.sdk import WorkspaceClient

    config, profile = argv
    admins = admins_of(json.loads(config)["variables"])
    try:
        provision(WorkspaceClient(profile=profile), admins)
    except Exception as error:
        raise SystemExit(f"!!! the environment tag policies failed: {error}\n!!! Creating a tag policy needs the "
                         f"create privilege on governed tags, and granting its roles needs its manager role "
                         f"(DEPLOY-ENVIRONMENT-TAG-POLICIES).") from error
    print(f"==> DEPLOY-ENVIRONMENT-TAG-POLICIES: {', '.join(POLICIES)} governed; assigners: the deployer"
          f"{', ' + ', '.join(admins) if admins else ''}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
