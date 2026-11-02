"""DEPLOY-AGENT-SKILLS — publish every skill, from one definition, to Unity Catalog and the workspace skills folder.

Run by ``scripts/deploy/publish-skills.sh``, which the deploy and the bundle script ``publish_skills`` call.
Idempotent: a skill whose published text is unchanged is left alone in both places.

**One definition, two places.** Each skill in :data:`PUBLISHED_SKILLS` is its ``SKILL.md`` in ``agent/skills/``
(``name`` and ``description`` frontmatter over the body), and that one file is written, as it is, to:

- **Unity Catalog**, as a UC skill in ``agent_skills_schema``, readable by the agent's users group. Genie One loads
  skills from here, once an admin has turned on "Unity Gateway skills in Genie".
- **The workspace skills folder**, ``/Workspace/.assistant/skills/<name>/SKILL.md``, readable by the App users
  group. Genie Code loads workspace skills from here; it does not read UC skills. Only a workspace admin can create
  the folder, and a new one is readable by admins alone, so this creates it and grants the read. A deployer who is
  not a workspace admin gets ``/Shared/CqlDatabricks/skills`` instead, with a loud warning that Genie Code won't
  load skills from there.

A Databricks Asset Bundle has no resource type for either place, so this runs as a bundle script, like the
example notebooks.

Every skill is read through the agent's skills package (`SVC-MCP-SKILLS`), the same file the agent serves through
``get_skill``, so the two cannot drift. A skill holds no deployment value, because the agent serves the same file
verbatim (`CONTENT-IMPORT-SKILL`). `clinical-explain` is not published: it is the
Designer's narration contract.

    DATABRICKS_CONFIG_PROFILE=<profile> python scripts/deploy/publish_skills.py --schema <catalog.schema> \\
        --users-group <account group> --workspace-users-group <group>
"""
from __future__ import annotations

import argparse
import io
import re
import sys

#: Every published skill, by its folder name in ``agent/skills/``.
PUBLISHED_SKILLS = ("population-question", "experiment", "explain-cql", "cql-authoring", "import-quality-measures")

WORKSPACE_SKILLS = "/Workspace/.assistant/skills"
SHARED_SKILLS = "/Shared/CqlDatabricks/skills"


class PublishError(RuntimeError):
    """A configuration that cannot be published. Fails the deploy, naming what to set."""


def skill_id(name: str) -> str:
    return name.replace("-", "_")


def skill_document(name: str) -> str:
    """The one ``SKILL.md`` both places get: the file the agent serves, as it is."""
    from cql_databricks.agent import skills

    return skills.get_skill(name)


# ── Unity Catalog ────────────────────────────────────────────────────────────

def ensure_schema(workspace, schema: str) -> None:
    from databricks.sdk.errors import NotFound

    catalog, name = schema.split(".", 1)
    try:
        workspace.schemas.get(schema)
    except NotFound:
        workspace.schemas.create(name=name, catalog_name=catalog,
                                 comment="DEPLOY-AGENT-SKILLS: the CQL on Databricks skills.")
        print(f"--> created schema {schema}")


def grant(workspace, securable_type: str, full_name: str, group: str, privileges: list[str]) -> None:
    from databricks.sdk.service.catalog import PermissionsChange, Privilege

    workspace.grants.update(securable_type=securable_type, full_name=full_name,
                            changes=[PermissionsChange(principal=group, add=[Privilege(p) for p in privileges])])


def publish_uc_skill(workspace, schema: str, name: str, document: str) -> str:
    """Create or update one UC skill. Returns ``created``, ``updated`` or ``kept``; an unchanged one is kept."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.catalog import Skill

    catalog, schema_name = schema.split(".", 1)
    path = f"/Skills/{catalog}/{schema_name}/{skill_id(name)}/SKILL.md"
    try:
        workspace.ai_gateway.get_skill(f"skills/{schema}.{skill_id(name)}")
        action = "updated"
        try:
            if workspace.files.download(path).contents.read().decode() == document:
                return "kept"
        except NotFound:
            pass
    except NotFound:
        description = re.search(r'^description: "?(.*?)"?$', document, re.M).group(1).replace('\\"', '"')
        workspace.ai_gateway.create_skill(Skill(comment=description), parent=f"schemas/{schema}",
                                          skill_id=skill_id(name))
        action = "created"
    workspace.files.upload(path, io.BytesIO(document.encode()), overwrite=True)
    workspace.ai_gateway.finalize_skill(f"skills/{schema}.{skill_id(name)}")
    return action


# ── The workspace skills folder ──────────────────────────────────────────────

def workspace_root(workspace, group: str) -> str:
    """The workspace skills folder, created and readable by ``group``; ``/Shared`` when the deployer can't create it."""
    from databricks.sdk.errors import PermissionDenied
    from databricks.sdk.service.workspace import WorkspaceObjectAccessControlRequest, WorkspaceObjectPermissionLevel

    try:
        workspace.workspace.mkdirs(WORKSPACE_SKILLS)
    except PermissionDenied as e:
        print(f"!!! could not create {WORKSPACE_SKILLS}: {e}\n"
              f"!!! Only a workspace admin can. Publishing to {SHARED_SKILLS} instead, where Genie Code won't load\n"
              f"!!! the skills: an admin runs the deploy, or a user copies a skill folder into "
              f"/Users/<them>/.assistant/skills/.")
        workspace.workspace.mkdirs(SHARED_SKILLS)
        return SHARED_SKILLS
    folder = workspace.workspace.get_status(WORKSPACE_SKILLS)
    workspace.workspace.update_permissions("directories", str(folder.object_id), access_control_list=[
        WorkspaceObjectAccessControlRequest(group_name=group, permission_level=WorkspaceObjectPermissionLevel.CAN_READ)])
    return WORKSPACE_SKILLS


def publish_workspace_skill(workspace, root: str, name: str, document: str) -> str:
    """Write one ``SKILL.md`` under ``root``. Returns ``created``, ``updated`` or ``kept``."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.workspace import ImportFormat

    path = f"{root}/{name}/SKILL.md"
    try:
        if workspace.workspace.download(path).read().decode() == document:
            return "kept"
        action = "updated"
    except NotFound:
        action = "created"
    workspace.workspace.mkdirs(f"{root}/{name}")
    workspace.workspace.upload(path, document.encode(), format=ImportFormat.RAW, overwrite=True)
    return action


def publish(workspace, *, schema: str, users_group: str, workspace_users_group: str) -> None:
    """Every skill, to both places, with its grants. Raises on any failure, which fails the deploy."""
    if schema.count(".") != 1 or not all(schema.split(".")):
        raise PublishError(f"agent_skills_schema must be catalog.schema; got {schema!r}")
    if not users_group:
        raise PublishError("no users group: set agent_users_group in databricks.local.yml")
    if not workspace_users_group:
        raise PublishError("no workspace users group: set workspace_users_group in databricks.local.yml")
    documents = {name: skill_document(name) for name in PUBLISHED_SKILLS}
    ensure_schema(workspace, schema)
    grant(workspace, "catalog", schema.split(".", 1)[0], users_group, ["USE_CATALOG"])
    grant(workspace, "schema", schema, users_group, ["USE_SCHEMA"])
    root = workspace_root(workspace, workspace_users_group)
    for name, document in documents.items():
        uc = publish_uc_skill(workspace, schema, name, document)
        grant(workspace, "skill", f"{schema}.{skill_id(name)}", users_group, ["READ_VOLUME"])
        folder = publish_workspace_skill(workspace, root, name, document)
        print(f"--> skill {name}: UC {schema}.{skill_id(name)} {uc}; {root}/{name} {folder}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schema", required=True, help="catalog.schema the UC skills are published in")
    parser.add_argument("--users-group", required=True, help="the account group that reads the UC skills")
    parser.add_argument("--workspace-users-group", required=True,
                        help="the group that reads the workspace skills folder")
    args = parser.parse_args()

    from databricks.sdk import WorkspaceClient

    try:
        publish(WorkspaceClient(), schema=args.schema, users_group=args.users_group,
                workspace_users_group=args.workspace_users_group)
    except PublishError as error:
        print(f"!!! {error}", file=sys.stderr)
        return 1
    print("==> published. Genie loads UC skills only where an admin has turned on 'Unity Gateway skills in Genie'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
