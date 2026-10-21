"""DEPLOY-APP-GRANTS / SVC-PACKAGING — the deployed App's requirements.txt must cover pyproject.toml.

`pyproject.toml` and `deploy/requirements.txt` are TWO declarations of ONE dependency set: the first is
what local dev installs, the second is what the Databricks App container installs. Nothing makes them
agree, and drift between them is invisible locally (a developer's venv already has the package) and fatal
in the deployed App. It has happened twice — `psycopg_pool` (`DEPLOY-APP-GRANTS`, a startup crash) and
`cql_databricks` (`SVC-PACKAGING`, a crash on the first measure run from the deployed Designer). This
test is the third one's cost.

It compares NAMES only, not version specifiers: the deploy file legitimately pins tighter floors than
pyproject (`databricks-sdk>=0.122`) and names one dependency as a UC-volume wheel path rather than a PyPI
requirement, and neither of those is drift.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

def _repo_root() -> Path:
    """Anchored on a marker, not a parent count. These paths moved twice in the restructure."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "settings.gradle").is_file():
            return candidate
    raise RuntimeError("repository root not found")


# This guard deliberately spans several directories, and that is the point of it: the dependency
# DECLARATIONS live with the distributions each App runs, and the deployed install list lives with the
# App that ships it. They are statements of one dependency set, in different layers, and drift between them
# is a ModuleNotFoundError in the deployed App that local dev never sees. One App per feature since
# APP-PER-FEATURE, so one requirements file per App, each checked against what that App runs.
_ROOT = _repo_root()
_DESIGNER_ROOT = _ROOT / "designer" / "server"
_COMMON = _ROOT / "common" / "pyproject.toml"

#: App -> (its requirements.txt, the pyprojects of the distributions it runs).
APPS = {
    "designer": (_DESIGNER_ROOT / "deploy" / "requirements.txt",
                 (_DESIGNER_ROOT / "pyproject.toml", _COMMON)),
    "agent": (_ROOT / "agent" / "deploy" / "requirements.txt", (_ROOT / "agent" / "pyproject.toml", _COMMON)),
}

# Base dependencies an App deliberately does NOT pip install. `local_env.load_local_env()` imports
# dotenv only when a local `.env` exists (SVC-LOCAL-CONFIG-BUNDLE); a deployed App has no checkout and gets
# its env injected by the platform, so the import is never reached there.
_NOT_DEPLOYED = {"python-dotenv",
                 # COMMON-LIBRARY. stage-app.sh copies its package into each App as SOURCE, so pip
                 # never installs it.
                 "cql-databricks-common",
                 # The agent, staged as source: its wheel declares cql-databricks-common, which pip in an
                 # App cannot resolve (not on PyPI).
                 "cql-databricks-agent"}


def _normalize(name: str) -> str:
    """PEP 503 name normalization — `cql_databricks` and `cql_databricks` are the same project."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject_base_dependencies(pyprojects) -> set[str]:
    specs = [spec for pyproject in pyprojects
             for spec in tomllib.loads(pyproject.read_text())["project"]["dependencies"]]
    names = set()
    for spec in specs:
        # Strip extras and any version specifier: `uvicorn[standard]>=0.30` -> `uvicorn`.
        names.add(_normalize(re.split(r"[\[<>=!~;]", spec, maxsplit=1)[0].strip()))
    return names


def _requirements_names(requirements: Path) -> set[str]:
    names = set()
    for raw in requirements.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(".whl"):
            # A wheel path (SVC-PACKAGING): the project name is the filename's first `-`-separated field.
            names.add(_normalize(Path(line).name.split("-")[0]))
            continue
        names.add(_normalize(re.split(r"[\[<>=!~;]", line, maxsplit=1)[0].strip()))
    return names


@pytest.mark.parametrize("app", sorted(APPS))
def test_every_base_dependency_is_deployed(app):
    requirements, pyprojects = APPS[app]
    missing = _pyproject_base_dependencies(pyprojects) - _requirements_names(requirements) - {
        _normalize(n) for n in _NOT_DEPLOYED}
    assert not missing, (
        f"{requirements} is missing {sorted(missing)}, declared as base dependencies of what the {app} "
        "App runs. The App container installs ONLY requirements.txt, so this is a ModuleNotFoundError "
        "in the deployed App that local dev cannot see.")


@pytest.mark.parametrize("app", ["designer", "agent"])
def test_the_apps_that_serve_the_agents_tools_stage_it_as_source_not_its_wheel(app):
    """The agent wheel's `cql-databricks-common` dependency is not on PyPI, so pip in the App fails on it."""
    assert "cql-databricks-agent" not in _requirements_names(APPS[app][0])
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    assert f'stage "$out_{app}"' in stage and '"$agent_src"' in stage.split(f'stage "$out_{app}"', 1)[1].split("\n", 1)[0]


def test_volume_wheel_placeholder_is_substituted_at_stage_time():
    """SVC-PACKAGING — the committed file carries the placeholder; stage-app.sh bakes the real path.

    A literal `/Volumes/...` here would hard-code one workspace's catalog into source. A placeholder that
    nothing substitutes would fail the App's pip install — hence the staging guard this asserts exists.
    """
    for requirements, _ in APPS.values():
        _assert_placeholder_relative(requirements.read_text())
    stage = (_repo_root() / "scripts" / "deploy" / "stage-app.sh").read_text()
    for token in ("CQL_DATABRICKS_ARTIFACT_VOLUME", "CQL_SPARK_CLIENT_VERSION"):
        assert token in stage, f"scripts/deploy/stage-app.sh must substitute __{token}__ into the staged requirements.txt"
    assert "requirements.txt" in stage


def _assert_placeholder_relative(text: str) -> None:
    assert ("__CQL_DATABRICKS_ARTIFACT_VOLUME__/wheels/"
            "cql_databricks-__CQL_SPARK_CLIENT_VERSION__-py3-none-any.whl") in text, (
        "the cql-databricks wheel line must stay placeholder-relative in source, in BOTH the volume "
        "path and the version (SVC-PACKAGING, SVC-WHEEL-VERSION)")


def test_no_wheel_version_is_hard_coded_in_this_repo():
    """SVC-WHEEL-VERSION — the wheel filenames' versions live in the pyprojects that build them.

    A literal `cql_databricks-0.0.0` / `cql_databricks-0.3.0` anywhere here is a second declaration
    of a number another package owns; that drift is what failed the 2026-08-09 deploy at the guard in
    `deploy-runtime-artifacts.sh` step [d]. Sources read the version or glob for it.
    """
    pattern = re.compile(r"(cql_databricks|cql_databricks)-\d+\.\d+\.\d+")
    offenders = []
    for path in (_repo_root() / "scripts").rglob("*.sh"):
        offenders += [f"{path.name}: {m.group(0)}" for m in pattern.finditer(path.read_text())]
    for path in (p for root in (_ROOT / "common" / "src", _DESIGNER_ROOT / "src") for p in root.rglob("*.py")):
        if path.name == "test_deploy_requirements.py":
            continue
        offenders += [f"{path.name}: {m.group(0)}" for m in pattern.finditer(path.read_text())]
    for requirements, _ in APPS.values():
        offenders += [f"{requirements}: {m.group(0)}" for m in pattern.finditer(requirements.read_text())]
    assert not offenders, (
        f"version-pinned wheel filenames found: {offenders} — read the version from the package's "
        "pyproject.toml or resolve the wheel by glob (SVC-WHEEL-VERSION)")


def test_private_catalog_configuration_and_maintenance_grant_ship_together():
    """RUN-CLIENT-PRIVATE-PROVISIONING, RUN-CLIENT-MAINTENANCE — the catalog is an administrator
    prerequisite: no creation, no user grants, no bundle-owned private data resource."""
    bundle = (_ROOT / "databricks.yml").read_text()
    deploy = (_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    descriptor = (_DESIGNER_ROOT / "deploy" / "app.yaml").read_text()
    assert 'designer_results_catalog:' in bundle and "results_catalog:" not in bundle.replace("designer_results_catalog:", "")
    assert "content: bash ./scripts/deploy/grant-maintenance-access.sh" in bundle
    assert 'bash "$here/scripts/deploy/grant-maintenance-access.sh"' in deploy
    assert not (_ROOT / "scripts" / "provision-private-catalog.sh").exists()
    grants = (_ROOT / "scripts" / "deploy" / "grant-maintenance-access.sh").read_text()
    assert 'scripts/deploy/maintenance_principal.py" "$cfg" "$profile"' in grants
    principal = (_ROOT / "scripts" / "deploy" / "maintenance_principal.py").read_text()
    assert "provision_maintenance_principal(WorkspaceClient(profile=profile)" in principal
    for text in (grants, principal):
        assert "CREATE CATALOG" not in text and "app_users_group" not in text
    # The Job's run-as is set by the deploy, never by hand: nothing in the local config names it.
    assert 'export BUNDLE_VAR_maintenance_principal="$_maintenance_principal"' in deploy
    assert "maintenance_principal" not in (_ROOT / "databricks.local.yml.example").read_text()
    assert 'export BUNDLE_VAR_designer_results_catalog="${_designer_results_catalog}"' in deploy
    assert '"__CQL_DESIGNER_RESULTS_CATALOG__": os.environ.get("BUNDLE_VAR_designer_results_catalog", "")' in stage
    assert 'name: "CQL_DESIGNER_RESULTS_CATALOG"\n    value: "__CQL_DESIGNER_RESULTS_CATALOG__"' in descriptor
    # RUN-CLIENT-PRIVATE-PROVISIONING — App identities never hold inherited data privileges on the catalog
    # that also holds private schemas: catalog-level USE_CATALOG/CREATE_SCHEMA only, the rest per schema.
    app_grants = (_ROOT / "scripts" / "deploy" / "grant-app-access.sh").read_text()
    assert 'if [ "$cat" = "$private_catalog" ]; then' in app_grants
    assert "name.startswith('cql_private_')" in app_grants and "name == 'information_schema'" in app_grants
    assert 'private_schema_format="cql-private-schema-v1"' in app_grants
    assert '\\"remove\\":$schema_privs' in app_grants
    from cql_databricks.result.private import SCHEMA_FORMAT
    assert SCHEMA_FORMAT == "cql-private-schema-v1"
    # RUN-CLIENT-MIGRATION — Designer runs refuse to start without the runtime catalog (calculation_env.py).
    assert 'name: "CQL_RUNTIME_CATALOG"\n    value: "__CQL_RUNTIME_CATALOG__"' in descriptor
    assert "\n  catalogs:" not in bundle.split("\nresources:", 1)[1]


def _provision_run_log():
    import importlib.util
    spec = importlib.util.spec_from_file_location("provision_run_log", _ROOT / "scripts" / "deploy" / "provision-run-log.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_log_ddl_and_grants_ship_together():
    """APP-RUN-LOG, DEC-RUN-LOG-LOCATION — one deploy step creates the tables and grants them, as the deployer."""
    from cql_databricks_designer import run_log
    calls = []

    def run(sql, params=None):
        calls.append(sql)
        if "information_schema.columns" in sql:
            return [{"table_name": table, "column_name": name, "full_data_type": kind.lower()}
                    for table, columns in ((run_log.RUNS, run_log.RUN_COLUMNS), (run_log.STEPS, run_log.STEP_COLUMNS))
                    for name, kind in columns]
        return []
    _provision_run_log().provision(run, "synthetic_cat", "synthetic-sp", "synthetic-readers")
    assert calls[:2] == list(run_log.create_statements("synthetic_cat"))
    for table in ("runs", "run_steps"):
        assert f"GRANT MODIFY ON TABLE `synthetic_cat`.`designer`.`{table}` TO `synthetic-sp`" in calls
        assert f"GRANT SELECT ON TABLE `synthetic_cat`.`designer`.`{table}` TO `synthetic-readers`" in calls
    assert "GRANT USE SCHEMA ON SCHEMA `synthetic_cat`.`designer` TO `synthetic-readers`" in calls
    assert not any("ON SCHEMA" in sql and ("SELECT" in sql or "MODIFY" in sql) for sql in calls)

    bundle = (_ROOT / "databricks.yml").read_text()
    deploy = (_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    assert "catalog_name: ${var.designer_catalog}\n      name: designer" in bundle
    # Bundle keys are unique across resource types; `cql_databricks_designer` is the App (found by validate).
    assert "    cql_databricks_designer_shared:\n      catalog_name: ${var.designer_catalog}" in bundle
    assert "run_log_readers_group:" in bundle and 'default: "${var.uc_catalog}"' in bundle
    assert "provision-run-log.py" in deploy and "run_log_readers_group is empty" in deploy
    assert deploy.index("bundle deploy -t") < deploy.index('"$here/scripts/deploy/provision-run-log.py"')
    descriptor = (_DESIGNER_ROOT / "deploy" / "app.yaml").read_text()
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    for placeholder in ("__CQL_DESIGNER_CATALOG__", "__CQL_APP_VERSION__"):
        assert placeholder in descriptor and placeholder in stage
    # The App never creates its own tables (DEC-RUN-LOG-LOCATION): no DDL runs outside the deploy step.
    assert "create_statements" not in (_DESIGNER_ROOT / "src" / "cql_databricks_designer" / "routes" / "runs.py").read_text()
    assert "create_statements" not in (_DESIGNER_ROOT / "src" / "cql_databricks_designer" / "calculation_env.py").read_text()


def test_a_run_log_table_that_drifted_from_its_definition_fails_the_deploy():
    """APP-RUN-LOG — IF NOT EXISTS leaves an old table alone, so a mismatch must stop the deploy, not pass."""
    def run(sql, params=None):
        if "information_schema.columns" in sql:
            return [{"table_name": "runs", "column_name": "run_id", "full_data_type": "string"}]
        return []
    with pytest.raises(RuntimeError, match="do not match their definition"):
        _provision_run_log().provision(run, "synthetic_cat", "synthetic-sp", "synthetic-readers")


def test_every_app_we_deploy_discovers_only_in_the_deployments_catalog_scopes(catalog_scopes):
    """CLIENT-DISCOVERY-SCOPE — the Apps whose deployment we own take their scopes from databricks.local.yml.

    One required bundle variable, refused empty by the deploy, staged into both Apps as the client's own session
    setting, read for local runs from the same file, and exported to the deploy's conformance bind.
    """
    from cql_databricks_designer.data_bindings import app_content_resolver, user_content_resolver
    expected = ["synthetic_dev.cql", "synthetic_shared"]
    assert [str(s) for s in app_content_resolver().scopes] == expected
    assert [str(s) for s in user_content_resolver(lambda *a: []).scopes] == expected

    bundle = (_ROOT / "databricks.yml").read_text()
    deploy = (_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    assert "  catalog_scopes:\n" in bundle
    assert "catalog_scopes is empty" in deploy and 'export BUNDLE_VAR_catalog_scopes="${_catalog_scopes}"' in deploy
    assert 'export CQL_DATABRICKS_CATALOG_SCOPES="$_catalog_scopes"' in deploy
    assert deploy.index("catalog_scopes is empty") < deploy.index('"$here/scripts/deploy/stage-app.sh"')
    assert '"__CQL_DATABRICKS_CATALOG_SCOPES__": os.environ.get("BUNDLE_VAR_catalog_scopes", "")' in stage
    for descriptor in (_DESIGNER_ROOT / "deploy" / "app.yaml", _ROOT / "agent" / "deploy" / "app.yaml"):
        assert ('- name: "CQL_DATABRICKS_CATALOG_SCOPES"\n    value: "__CQL_DATABRICKS_CATALOG_SCOPES__"'
                in descriptor.read_text()), descriptor
    from cql_databricks_common.local_env import BUNDLE_OWNED
    assert BUNDLE_OWNED["CQL_DATABRICKS_CATALOG_SCOPES"] == ("variables", "catalog_scopes", "value")
    assert "resolve_catalog_scopes()" in (_ROOT / "scripts" / "deploy" / "provision-samples.py").read_text()
