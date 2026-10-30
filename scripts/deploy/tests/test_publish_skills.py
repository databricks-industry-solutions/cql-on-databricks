"""DEPLOY-AGENT-SKILLS — every skill, from one definition, published as a UC skill and to the workspace skills
folder, readable by the users groups."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("publish_skills", _ROOT / "scripts/deploy/publish_skills.py")
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)

SCHEMA = "synthetic.agent"
ARGS = dict(schema=SCHEMA, users_group="clinicians", workspace_users_group="users")


class Workspace:
    """The workspace APIs publishing calls, recording every write."""

    def __init__(self, schema_exists=True, workspace_admin=True):
        self.calls: list[tuple] = []
        self.workspace_admin = workspace_admin
        self.folder: dict[str, bytes] = {}
        self.workspace = SimpleNamespace(mkdirs=self._mkdirs, get_status=lambda p: SimpleNamespace(object_id=7),
                                         update_permissions=self._permissions, download=self._ws_download,
                                         upload=self._ws_upload)
        self.skills: set[str] = set()
        self.store: dict[str, bytes] = {}
        self.schema_exists = schema_exists
        self.schemas = SimpleNamespace(get=self._get_schema, create=lambda **kw: self.calls.append(("schema", kw)))
        self.ai_gateway = SimpleNamespace(get_skill=self._get_skill, create_skill=self._create_skill,
                                          finalize_skill=lambda name: self.calls.append(("finalize", name)))
        self.grants = SimpleNamespace(update=lambda **kw: self.calls.append(("grant", kw)))
        self.files = SimpleNamespace(upload=self._upload, download=self._download)

    def _get_schema(self, name):
        if not self.schema_exists:
            raise NotFound("no schema")

    def _get_skill(self, name):
        if name not in self.skills:
            raise NotFound("no skill")

    def _create_skill(self, skill, *, parent, skill_id):
        self.calls.append(("create", parent, skill_id))
        self.skills.add(f"skills/{SCHEMA}.{skill_id}")

    def _upload(self, path, contents, overwrite):
        self.calls.append(("upload", path))
        self.store[path] = contents.read()

    def _download(self, path):
        if path not in self.store:
            raise NotFound("no file")
        return SimpleNamespace(contents=io.BytesIO(self.store[path]))

    def _mkdirs(self, path):
        if path.startswith("/Workspace/.assistant") and not self.workspace_admin:
            from databricks.sdk.errors import PermissionDenied
            raise PermissionDenied("not an admin")
        self.calls.append(("mkdirs", path))

    def _permissions(self, object_type, object_id, *, access_control_list):
        acl = access_control_list[0]
        self.calls.append(("acl", object_type, object_id, acl.group_name, acl.permission_level.value))

    def _ws_download(self, path):
        if path not in self.folder:
            raise NotFound("no file")
        return io.BytesIO(self.folder[path])

    def _ws_upload(self, path, content, *, format, overwrite):
        self.calls.append(("ws-upload", path))
        self.folder[path] = content

    def writes(self):
        return [c for c in self.calls if c[0] not in ("grant", "mkdirs", "acl")]


def test_each_skill_is_created_uploaded_and_finalized():
    w = Workspace()
    pub.publish(w, **ARGS)
    assert [c[2] for c in w.calls if c[0] == "create"] == [
        "population_question", "experiment", "explain_cql", "cql_authoring", "import_quality_measures"]
    assert ("finalize", f"skills/{SCHEMA}.experiment") in w.calls
    document = w.store["/Skills/synthetic/agent/experiment/SKILL.md"].decode()
    assert document.startswith("---\nname: experiment\ndescription: ")
    assert "Review the CQL with the user" in document
    assert not any("clinical" in c[2] for c in w.calls if c[0] == "create")


def test_the_published_body_is_the_served_skill_verbatim():
    from cql_databricks.agent import skills

    w = Workspace()
    pub.publish(w, **ARGS)
    document = w.store["/Skills/synthetic/agent/explain_cql/SKILL.md"].decode()
    assert document == skills.get_skill("explain-cql")


def test_the_users_group_can_read_every_skill():
    w = Workspace()
    pub.publish(w, **ARGS)
    grants = [(c[1]["securable_type"], c[1]["full_name"], [p.value for p in c[1]["changes"][0].add])
              for c in w.calls if c[0] == "grant"]
    assert ("catalog", "synthetic", ["USE_CATALOG"]) in grants
    assert ("schema", SCHEMA, ["USE_SCHEMA"]) in grants
    assert ("skill", f"{SCHEMA}.cql_authoring", ["READ_VOLUME"]) in grants
    assert all(c[1]["changes"][0].principal == "clinicians" for c in w.calls if c[0] == "grant")


def test_a_redeploy_with_unchanged_skills_writes_nothing():
    w = Workspace()
    pub.publish(w, **ARGS)
    before = len(w.writes())
    pub.publish(w, **ARGS)
    assert w.writes()[before:] == []


def test_a_changed_skill_is_uploaded_and_finalized_again(monkeypatch):
    w = Workspace()
    pub.publish(w, **ARGS)
    w.store["/Skills/synthetic/agent/experiment/SKILL.md"] = b"an older text"
    before = len(w.writes())
    pub.publish(w, **ARGS)
    assert w.writes()[before:] == [("upload", "/Skills/synthetic/agent/experiment/SKILL.md"),
                                   ("finalize", f"skills/{SCHEMA}.experiment")]


def test_a_missing_schema_is_created():
    w = Workspace(schema_exists=False)
    pub.publish(w, **ARGS)
    assert ("schema", {"name": "agent", "catalog_name": "synthetic",
                       "comment": "DEPLOY-AGENT-SKILLS: the CQL on Databricks skills."}) in w.calls


@pytest.mark.parametrize("change", [{"schema": "noschema"}, {"users_group": ""}, {"workspace_users_group": ""}])
def test_bad_configuration_is_refused_before_any_write(change):
    w = Workspace()
    with pytest.raises(pub.PublishError):
        pub.publish(w, **{**ARGS, **change})
    assert w.calls == []


def test_one_document_goes_to_both_places():
    w = Workspace()
    pub.publish(w, **ARGS)
    for name in pub.PUBLISHED_SKILLS:
        uc = w.store[f"/Skills/synthetic/agent/{pub.skill_id(name)}/SKILL.md"]
        assert w.folder[f"/Workspace/.assistant/skills/{name}/SKILL.md"] == uc
        assert uc.decode().startswith(f"---\nname: {name}\n")


def test_the_import_skill_is_the_served_skill_with_no_deployment_values():
    """CONTENT-IMPORT-SKILL — the import skill is the served file like the others, and holds no placeholder."""
    from cql_databricks.agent import skills

    w = Workspace()
    pub.publish(w, **ARGS)
    document = w.folder["/Workspace/.assistant/skills/import-quality-measures/SKILL.md"].decode()
    assert document == skills.get_skill("import-quality-measures")
    assert "__CQL_DATABRICKS_" not in document


def test_the_workspace_folder_is_created_and_readable_by_the_app_users():
    w = Workspace()
    pub.publish(w, **ARGS)
    assert ("mkdirs", "/Workspace/.assistant/skills") in w.calls
    assert ("acl", "directories", "7", "users", "CAN_READ") in w.calls


def test_a_deployer_who_is_not_an_admin_publishes_to_shared_and_says_so(capsys):
    w = Workspace(workspace_admin=False)
    pub.publish(w, **ARGS)
    assert "/Shared/CqlDatabricks/skills/experiment/SKILL.md" in w.folder
    assert not any(c[0] == "acl" for c in w.calls)
    assert "Genie Code won't load" in capsys.readouterr().out
