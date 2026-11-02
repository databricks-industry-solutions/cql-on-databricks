"""DEPLOY-PROJECT-ADMINS — every deploy makes each of the project's admins a workspace admin. A synthetic SCIM boundary:
no live identities."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("project_admins", _ROOT / "scripts/deploy/project_admins.py")
admins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(admins)


class Workspace:
    def __init__(self, users=(), principals=(), groups=None, members=()):
        self.users_held = {u: f"u-{u}" for u in users}
        self.principals_held = {p: f"sp-{p}" for p in principals}
        self.groups_held = {"admins": "g-admins", **(groups or {})}
        self.members = [SimpleNamespace(value=m) for m in members]
        self.patches = []

        def find(held, key):
            def list_(filter, attributes):
                name = filter.split('"')[1]
                return [SimpleNamespace(id=held[name], members=self.members)] if name in held else []
            return list_

        def create_user(user_name):
            self.users_held[user_name] = f"u-{user_name}"
            return SimpleNamespace(id=f"u-{user_name}")

        def create_principal(application_id):
            self.principals_held[application_id] = f"sp-{application_id}"
            return SimpleNamespace(id=f"sp-{application_id}")

        def patch(id, operations, schemas):
            self.patches.append((id, [v["value"] for op in operations for v in op.value]))

        self.users = SimpleNamespace(list=find(self.users_held, "userName"), create=create_user)
        self.service_principals = SimpleNamespace(list=find(self.principals_held, "applicationId"),
                                                  create=create_principal)
        self.groups = SimpleNamespace(list=find(self.groups_held, "displayName"), patch=patch)


SP = "01234567-89ab-cdef-0123-456789abcdef"


def test_a_user_not_in_the_workspace_is_added_then_made_an_admin():
    workspace = Workspace()
    assert admins.provision(workspace, ["admin@synthetic.example"]) == ["admin@synthetic.example"]
    assert "admin@synthetic.example" in workspace.users_held
    assert workspace.patches == [("g-admins", ["u-admin@synthetic.example"])]


def test_a_service_principal_and_a_group_are_admins_too():
    workspace = Workspace(groups={"synthetic-admins": "g-synthetic"})
    admins.provision(workspace, [SP, "synthetic-admins"])
    assert workspace.patches == [("g-admins", [f"sp-{SP}"]), ("g-admins", ["g-synthetic"])]


def test_a_second_deploy_changes_nothing():
    workspace = Workspace(users=["admin@synthetic.example"], members=["u-admin@synthetic.example"])
    assert admins.provision(workspace, ["admin@synthetic.example"]) == []
    assert workspace.patches == []


def test_a_group_not_in_the_workspace_fails_naming_it():
    with pytest.raises(ValueError, match="'synthetic-admins' in admins is not in this workspace"):
        admins.provision(Workspace(), ["synthetic-admins"])


def test_no_admins_adds_no_one():
    workspace = Workspace()
    assert admins.provision(workspace, []) == []


def test_the_deploy_adds_them_before_it_builds_anything():
    deploy = (_ROOT / "scripts/deploy/deploy.sh").read_text()
    assert deploy.index('"$here/scripts/deploy/project_admins.py"') < deploy.index('echo "==> [2/7]')
    assert "workspace_users_group is empty" in deploy
    assert '--workspace-users-group "$(var workspace_users_group)"' in (
        _ROOT / "scripts/deploy/publish-skills.sh").read_text()
