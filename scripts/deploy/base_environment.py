"""DEPLOY-BASE-ENVIRONMENT — publish the workspace base environment that carries the client wheel.

`scripts/deploy/base-environment.sh` runs this after the wheels are in the runtime Volume (``deploy.sh`` step [4.5], or
``databricks bundle run base_environment``). It writes the environment's YAML into the Volume, naming the one client
wheel this deploy built, then creates the base environment ``CQL on Databricks`` over it, or refreshes it. Databricks
builds a base environment once and caches it, and each content change gives the wheel a new version
(`SVC-WHEEL-VERSION`), so every deploy refreshes it. A serverless notebook or job that selects it has the client with
no install cell.

With ``default_base_environment`` true it becomes the workspace default for new serverless notebooks. That default is
workspace-wide, for every user, so it is the implementer's choice: false leaves the default as it is and never unsets
it.

Idempotent. Any failure exits non-zero, which fails the deploy: a missing wheel, a refused call, or an environment
Databricks could not build.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import timedelta

#: The base environment's id, the last part of its resource name.
ENVIRONMENT_ID = "cql-on-databricks"
NAME = f"workspace-base-environments/{ENVIRONMENT_ID}"
DISPLAY_NAME = "CQL on Databricks"

#: The workspace's one default-environment record.
DEFAULT = "default-workspace-base-environment"

#: The serverless environment version, the one the example notebooks run on (`SVC-EXAMPLE-NOTEBOOKS`).
ENVIRONMENT_VERSION = "4"

#: How long a build of the environment may take before the deploy fails.
BUILD_TIMEOUT = timedelta(minutes=30)


def wheel_path(volume: str, version: str) -> str:
    """The client wheel the deploy uploaded, by its exact version (`SVC-WHEEL-VERSION`)."""
    return f"{volume}/wheels/cql_databricks-{version}-py3-none-any.whl"


#: The environment's YAML, at the root of the runtime Volume beside ``wheels/``.
SPEC_FILE = "cql-databricks-env.yaml"


def spec_path(volume: str) -> str:
    """Where the environment's YAML is written in the runtime Volume."""
    return f"{volume}/{SPEC_FILE}"


def spec_yaml(wheel: str) -> str:
    """The environment's YAML: the environment version and the client wheel. The wheel's own dependencies come from
    the package index."""
    return f'environment_version: "{ENVIRONMENT_VERSION}"\ndependencies:\n  - {wheel}\n'


def ensure_environment(workspace, filepath: str):
    """The base environment over ``filepath``: created, re-pointed, or refreshed so Databricks rebuilds it from the
    file as it is now. Waits for the build and returns the environment."""
    from databricks.sdk.common.lro import LroOptions
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.environments import BaseEnvironmentType, WorkspaceBaseEnvironment

    wanted = WorkspaceBaseEnvironment(display_name=DISPLAY_NAME, filepath=filepath,
                                      base_environment_type=BaseEnvironmentType.CPU)
    api = workspace.environments
    try:
        held = api.get_workspace_base_environment(NAME)
    except NotFound:
        operation, action = api.create_workspace_base_environment(
            wanted, workspace_base_environment_id=ENVIRONMENT_ID), "created"
    else:
        if held.filepath != filepath:
            operation, action = api.update_workspace_base_environment(NAME, wanted), "re-pointed"
        else:
            operation, action = api.refresh_workspace_base_environment(NAME), "refreshed"
    print(f"--> {action} base environment {DISPLAY_NAME} ({NAME}); waiting for its build", file=sys.stderr)
    return operation.wait(LroOptions(timeout=BUILD_TIMEOUT))


def make_default(workspace) -> None:
    """Make this base environment the workspace default for new serverless notebooks (CPU)."""
    from databricks.sdk.common.types.fieldmask import FieldMask
    from databricks.sdk.service.environments import DefaultWorkspaceBaseEnvironment

    api = workspace.environments
    current = api.get_default_workspace_base_environment(DEFAULT).cpu_workspace_base_environment
    if current == NAME:
        print(f"--> {DISPLAY_NAME} is already the workspace default", file=sys.stderr)
        return
    api.update_default_workspace_base_environment(
        DEFAULT, DefaultWorkspaceBaseEnvironment(name=DEFAULT, cpu_workspace_base_environment=NAME),
        FieldMask(["cpu_workspace_base_environment"]))
    print(f"--> {DISPLAY_NAME} is now the workspace default for new serverless notebooks"
          f"{f', in place of {current}' if current else ''}", file=sys.stderr)


def publish(workspace, *, volume: str, version: str, default: bool) -> str:
    """DEPLOY-BASE-ENVIRONMENT — write the YAML naming the client wheel, build the base environment over it, and make
    it the workspace default when ``default``. Returns the environment's resource name."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.environments import WorkspaceBaseEnvironmentCacheStatus

    wheel = wheel_path(volume, version)
    try:
        workspace.files.get_metadata(wheel)
    except NotFound as error:
        raise ValueError(f"{wheel} is not in the runtime Volume; the deploy uploads it before this step "
                         f"(scripts/deploy/deploy-runtime-artifacts.sh)") from error
    filepath = spec_path(volume)
    workspace.files.upload(filepath, io.BytesIO(spec_yaml(wheel).encode("utf-8")), overwrite=True)
    built = ensure_environment(workspace, filepath)
    if built.status != WorkspaceBaseEnvironmentCacheStatus.CREATED:
        status = built.status.value if built.status else "no status"
        raise RuntimeError(f"Databricks did not build {DISPLAY_NAME}: {status}"
                           f"{f' ({built.message})' if built.message else ''}")
    if default:
        make_default(workspace)
    return NAME


def flag(variables: dict, name: str) -> bool:
    """A boolean bundle variable, false when unset. Anything but true or false fails, naming it."""
    value = str((variables.get(name) or {}).get("value", False)).lower()
    if value not in ("true", "false"):
        raise SystemExit(f"!!! {name} must be true or false in databricks.local.yml, not {value}")
    return value == "true"


def volume_of(variables: dict) -> str:
    """The runtime Volume, from the variables that name it (`SVC-CATALOG-LAYOUT`)."""
    parts = [(variables.get(k) or {}).get("value") or "" for k in ("uc_catalog", "uc_schema", "runtime_volume")]
    if not all(parts):
        raise SystemExit("!!! uc_catalog, uc_schema and runtime_volume must all be set to name the runtime Volume")
    return "/Volumes/" + "/".join(parts)


def main(argv: list[str]) -> None:
    """Read the resolved bundle variables (``bundle validate -o json``) and publish, for the client wheel version
    given."""
    from databricks.sdk import WorkspaceClient

    config, profile, version = argv
    if not version:
        raise SystemExit("!!! no client wheel version (SVC-WHEEL-VERSION)")
    variables = json.loads(config).get("variables") or {}
    volume, default = volume_of(variables), flag(variables, "default_base_environment")
    try:
        name = publish(WorkspaceClient(profile=profile), volume=volume, version=version, default=default)
    except Exception as error:
        raise SystemExit(f"!!! the base environment failed: {error}\n!!! Base environments are managed in the "
                         f"workspace's admin settings, so this step may need a workspace admin "
                         f"(DEPLOY-BASE-ENVIRONMENT).") from error
    where = ("the workspace default for new serverless notebooks" if default else
             "not the workspace default (default_base_environment is false); select it in a notebook's Environment "
             "pane, under Base environment")
    print(f"==> DEPLOY-BASE-ENVIRONMENT: {DISPLAY_NAME} ({name}) carries the client {version}; {where}",
          file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
