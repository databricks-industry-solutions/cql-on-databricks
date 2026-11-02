"""DEPLOY-PROJECT-ADMINS — make each of the project's admins an admin of the workspace, on every deploy.

``deploy.sh`` runs this early (step [1.6]), as the deployer, who must be a workspace admin. Each principal in the
``admins`` bundle variable is added to the workspace if it is not in it, then to the workspace's ``admins`` group: a
user by email, a service principal by application ID. A group in ``admins`` must already be in the workspace; one
that is not fails, naming it. The same principals assign the environment tags (`DEPLOY-ENVIRONMENT-TAG-POLICIES`).

Idempotent: a principal already in the group is left alone, and nothing is ever removed. Any failure exits non-zero,
which fails the deploy.

    python scripts/deploy/project_admins.py --admins '["admin@example.com"]'
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

#: The workspace's built-in admin group.
ADMINS_GROUP = "admins"


def _first(found) -> object | None:
    return next(iter(found), None)


def member_id(workspace, name: str) -> str:
    """The workspace's id for ``name``, adding a user or service principal to the workspace when it is not in it."""
    name = name.strip()
    if "@" in name:
        user = _first(workspace.users.list(filter=f'userName eq "{name}"', attributes="id,userName"))
        if user is None:
            user = workspace.users.create(user_name=name)
            print(f"--> added {name} to the workspace", file=sys.stderr)
        return user.id
    if [len(p) for p in name.split("-")] == [8, 4, 4, 4, 12]:
        principal = _first(workspace.service_principals.list(filter=f'applicationId eq "{name}"',
                                                             attributes="id,applicationId"))
        if principal is None:
            principal = workspace.service_principals.create(application_id=name)
            print(f"--> added service principal {name} to the workspace", file=sys.stderr)
        return principal.id
    group = _first(workspace.groups.list(filter=f'displayName eq "{name}"', attributes="id,displayName"))
    if group is None:
        raise ValueError(f"the group {name!r} in admins is not in this workspace; assign it to the workspace first")
    return group.id


def provision(workspace, admins: Sequence[str]) -> list[str]:
    """DEPLOY-PROJECT-ADMINS — every admin in the workspace's ``admins`` group. Returns those added to it."""
    from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema

    group = _first(workspace.groups.list(filter=f'displayName eq "{ADMINS_GROUP}"', attributes="id,members"))
    if group is None:
        raise ValueError(f"this workspace has no {ADMINS_GROUP!r} group")
    members = {m.value for m in group.members or []}
    added = []
    for name in admins:
        mid = member_id(workspace, name)
        if mid in members:
            continue
        workspace.groups.patch(group.id, operations=[Patch(op=PatchOp.ADD, path="members", value=[{"value": mid}])],
                               schemas=[PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP])
        members.add(mid)
        added.append(name)
        print(f"--> {name} is a workspace admin", file=sys.stderr)
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--admins", required=True, help="the admins bundle variable, as JSON")
    args = parser.parse_args(argv)
    admins = json.loads(args.admins)
    if not isinstance(admins, list) or not all(isinstance(a, str) and a.strip() for a in admins):
        raise SystemExit(f"!!! admins must be a list of names in databricks.local.yml, not {admins!r}")

    from cql_databricks_common.workspace import service_principal_client

    try:
        # Locally the shared client IS the deployer (`SEC-LOCAL-IDENTITY`): the profile's OAuth.
        provision(service_principal_client(), admins)
    except Exception as error:
        raise SystemExit(f"!!! adding the project's admins failed: {error}\n!!! Adding a principal to the workspace "
                         f"and to its admins group needs a workspace admin (DEPLOY-PROJECT-ADMINS).") from error
    print(f"==> DEPLOY-PROJECT-ADMINS: workspace admins include {', '.join(admins) or 'no one besides the deployer'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
