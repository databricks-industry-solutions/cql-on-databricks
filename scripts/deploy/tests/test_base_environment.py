"""DEPLOY-BASE-ENVIRONMENT — the deploy publishes a base environment naming the client wheel it built, rebuilds it each
deploy, and makes it the workspace default only when asked. A synthetic SDK boundary: no live workspace."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("base_environment", _ROOT / "scripts/deploy/base_environment.py")
base_environment = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base_environment)

VOLUME = "/Volumes/synthetic_catalog/runtime/artifacts"
VERSION = "0.1.1+src0123456789ab"
WHEEL = f"{VOLUME}/wheels/cql_databricks-{VERSION}-py3-none-any.whl"
SPEC = f"{VOLUME}/cql-databricks-env.yaml"


class Operation:
    def __init__(self, built):
        self.built = built

    def wait(self, opts=None):
        return self.built


class Workspace:
    """Files in the Volume, and the environments API, held in memory."""

    def __init__(self, *, files=(WHEEL,), held=None, status="CREATED", default=None):
        from databricks.sdk.errors import NotFound
        from databricks.sdk.service.environments import WorkspaceBaseEnvironmentCacheStatus

        self.files_held = set(files)
        self.uploads: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.held = held
        self.default = default
        built = SimpleNamespace(status=WorkspaceBaseEnvironmentCacheStatus(status), message="synthetic message")

        def get_metadata(path):
            if path not in self.files_held:
                raise NotFound(path)
            return SimpleNamespace()

        def upload(path, contents, overwrite=None):
            self.uploads[path] = contents.read().decode("utf-8")
            self.calls.append(("upload", path, overwrite))

        def get(name):
            if self.held is None:
                raise NotFound(name)
            return self.held

        def create(env, workspace_base_environment_id=None):
            self.calls.append(("create", env.display_name, env.filepath, env.base_environment_type.value,
                               workspace_base_environment_id))
            return Operation(built)

        def update(name, env):
            self.calls.append(("update", name, env.filepath))
            return Operation(built)

        def refresh(name):
            self.calls.append(("refresh", name))
            return Operation(built)

        def get_default(name):
            return SimpleNamespace(cpu_workspace_base_environment=self.default)

        def update_default(name, value, mask):
            self.calls.append(("default", name, value.cpu_workspace_base_environment, mask.ToJsonString()))

        self.files = SimpleNamespace(get_metadata=get_metadata, upload=upload)
        self.environments = SimpleNamespace(
            get_workspace_base_environment=get, create_workspace_base_environment=create,
            update_workspace_base_environment=update, refresh_workspace_base_environment=refresh,
            get_default_workspace_base_environment=get_default,
            update_default_workspace_base_environment=update_default)


def test_a_first_deploy_creates_it_over_a_yaml_naming_the_exact_wheel():
    workspace = Workspace()
    name = base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=False)
    assert name == "workspace-base-environments/cql-on-databricks"
    assert workspace.uploads[SPEC] == f'environment_version: "4"\ndependencies:\n  - {WHEEL}\n'
    assert ("create", "CQL on Databricks", SPEC, "CPU", "cql-on-databricks") in workspace.calls


def test_a_redeploy_refreshes_it_so_the_new_wheel_is_built_in():
    workspace = Workspace(held=SimpleNamespace(filepath=SPEC))
    base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=False)
    assert ("refresh", "workspace-base-environments/cql-on-databricks") in workspace.calls
    assert not [c for c in workspace.calls if c[0] in ("create", "update")]


def test_an_environment_over_another_file_is_re_pointed():
    workspace = Workspace(held=SimpleNamespace(filepath="/Volumes/other/runtime/artifacts/old.yaml"))
    base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=False)
    assert ("update", "workspace-base-environments/cql-on-databricks", SPEC) in workspace.calls


def test_a_missing_wheel_fails_before_anything_is_written():
    workspace = Workspace(files=())
    with pytest.raises(ValueError, match="is not in the runtime Volume"):
        base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=False)
    assert workspace.calls == []


def test_an_environment_databricks_could_not_build_fails_the_deploy():
    workspace = Workspace(status="FAILED")
    with pytest.raises(RuntimeError, match="did not build CQL on Databricks: FAILED .synthetic message."):
        base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=True)
    assert not [c for c in workspace.calls if c[0] == "default"]


def test_the_workspace_default_is_left_alone_unless_asked():
    workspace = Workspace(default="workspace-base-environments/someone-elses")
    base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=False)
    assert not [c for c in workspace.calls if c[0] == "default"]


def test_asked_it_becomes_the_cpu_default():
    workspace = Workspace(default="workspace-base-environments/someone-elses")
    base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=True)
    assert ("default", "default-workspace-base-environment", "workspace-base-environments/cql-on-databricks",
            "cpu_workspace_base_environment") in workspace.calls


def test_already_the_default_it_is_not_set_again():
    workspace = Workspace(default="workspace-base-environments/cql-on-databricks")
    base_environment.publish(workspace, volume=VOLUME, version=VERSION, default=True)
    assert not [c for c in workspace.calls if c[0] == "default"]


@pytest.mark.parametrize("value, expected", [(None, False), (False, False), (True, True), ("true", True),
                                             ("false", False)])
def test_default_base_environment_is_false_unless_set_true(value, expected):
    variables = {} if value is None else {"default_base_environment": {"value": value}}
    assert base_environment.flag(variables, "default_base_environment") is expected


def test_any_other_value_of_the_flag_fails_naming_it():
    with pytest.raises(SystemExit, match="default_base_environment must be true or false"):
        base_environment.flag({"default_base_environment": {"value": "yes"}}, "default_base_environment")


def test_the_volume_comes_from_the_variables_that_name_it():
    variables = json.loads(json.dumps({"uc_catalog": {"value": "synthetic_catalog"}, "uc_schema": {"value": "runtime"},
                                       "runtime_volume": {"value": "artifacts"}}))
    assert base_environment.volume_of(variables) == VOLUME
    with pytest.raises(SystemExit, match="runtime Volume"):
        base_environment.volume_of({"uc_catalog": {"value": "synthetic_catalog"}})
