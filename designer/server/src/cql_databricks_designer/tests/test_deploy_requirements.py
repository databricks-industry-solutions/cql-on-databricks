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
#: BUILD-TAXONOMY — the agent's components; stage-app.sh installs all three into each App that serves the agent.
_AGENT = ("service", "server", "skills")

#: App -> (its requirements.txt, the pyprojects of the distributions it runs).
APPS = {
    "designer": (_DESIGNER_ROOT / "deploy" / "requirements.txt",
                 (_DESIGNER_ROOT / "pyproject.toml", _COMMON)),
    "agent": (_ROOT / "agent" / "server" / "deploy" / "requirements.txt",
              tuple(_ROOT / "agent" / c / "pyproject.toml" for c in _AGENT) + (_COMMON,)),
}

# Base dependencies an App deliberately does NOT pip install. `local_env.load_local_env()` imports
# dotenv only when a local `.env` exists (SVC-LOCAL-CONFIG-BUNDLE); a deployed App has no checkout and gets
# its env injected by the platform, so the import is never reached there.
_NOT_DEPLOYED = {"python-dotenv",
                 # COMMON-LIBRARY. stage-app.sh copies its package into each App as SOURCE, so pip
                 # never installs it.
                 "cql-databricks-common",
                 # The agent, staged into the payload: its server declares cql-databricks-common, which pip
                 # in an App cannot resolve (not on PyPI).
                 "cql-databricks-agent-service", "cql-databricks-agent-server", "cql-databricks-agent-skills"}


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
def test_the_apps_that_serve_the_agents_tools_stage_it_not_its_wheel(app):
    """The agent server's `cql-databricks-common` dependency is not on PyPI, so pip in the App fails on it."""
    names = _requirements_names(APPS[app][0])
    assert not {n for n in names if n.startswith("cql-databricks-agent")}, names
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    assert f'stage_agent "$out_{app}"' in stage
    for component in _AGENT:
        assert f'"$here/agent/{component}"' in stage, component


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


def test_the_bundle_makes_the_default_environments_schemas_and_their_grants():
    """DEPLOY-DEFAULT-ENVIRONMENT, DEPLOY-APP-GRANTS, DATA-ENVIRONMENT-LAYOUT, RUN-CLIENT-DRAFT-RESULTS — every schema of
    the layout is a bundle resource in uc_catalog with its comment; the users read the published schemas and create
    tables in draft_results; the Designer's principal reads knowledge and terminology; no store is granted."""
    import yaml
    from shipped_sources.environment import SCHEMAS

    schemas = yaml.safe_load((_ROOT / "databricks.yml").read_text())["resources"]["schemas"]
    env = {r["name"]: r for key, r in schemas.items() if key.startswith("environment_")}
    assert {n: (r["catalog_name"], r["comment"]) for n, r in env.items()} == {
        n: ("${var.uc_catalog}", c) for n, c in SCHEMAS.items()}
    users, designer = "${var.app_users_group}", "${resources.apps.cql_databricks_designer.service_principal_client_id}"
    grants = {n: {g["principal"]: g["privileges"] for g in r.get("grants", [])} for n, r in env.items()}
    assert grants == {
        "data": {users: ["USE_SCHEMA", "SELECT"]},
        "terminology": {users: ["USE_SCHEMA", "SELECT"], designer: ["USE_SCHEMA", "SELECT"]},
        "knowledge": {users: ["USE_SCHEMA", "SELECT"], designer: ["USE_SCHEMA", "SELECT"]},
        "results": {users: ["USE_SCHEMA", "SELECT"]},
        "draft_results": {users: ["USE_SCHEMA", "CREATE_TABLE"]},
        "data_store": {}, "terminology_store": {}, "knowledge_store": {}}


def test_the_default_environment_and_its_maintenance_grant_ship_together():
    """DEPLOY-DEFAULT-ENVIRONMENT, RUN-CLIENT-MAINTENANCE, RUN-CLIENT-DRAFT-RESULTS — the deploy builds the default
    environment in uc_catalog and grants it; there is no results catalog and no per-user schema."""
    bundle = (_ROOT / "databricks.yml").read_text()
    deploy = (_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    descriptor = (_DESIGNER_ROOT / "deploy" / "app.yaml").read_text()
    for text in (bundle, deploy, stage, descriptor, (_ROOT / "databricks.local.yml.example").read_text()):
        assert "designer_results_catalog" not in text and "DESIGNER_RESULTS_CATALOG" not in text
    assert "content: bash ./scripts/deploy/grant-maintenance-access.sh" in bundle
    assert 'bash "$here/scripts/deploy/grant-maintenance-access.sh"' in deploy
    grants = (_ROOT / "scripts" / "deploy" / "grant-maintenance-access.sh").read_text()
    assert 'scripts/deploy/maintenance_principal.py" "$cfg" "$profile"' in grants
    principal = (_ROOT / "scripts" / "deploy" / "maintenance_principal.py").read_text()
    assert "provision_maintenance_principal(WorkspaceClient(profile=profile)" in principal
    assert 'catalog = required("uc_catalog")' in principal
    for text in (grants, principal):
        assert "CREATE CATALOG" not in text and "app_users_group" not in text
    # The Job's run-as is set by the deploy, never by hand: nothing in the local config names it.
    assert 'export BUNDLE_VAR_maintenance_principal="$_maintenance_principal"' in deploy
    assert "maintenance_principal" not in (_ROOT / "databricks.local.yml.example").read_text()
    # DEPLOY-DEFAULT-ENVIRONMENT — step [8] fills the environment the bundle made, and makes the two grants a bundle
    # cannot: the users' USE CATALOG and the maintenance principal's MANAGE on draft_results.
    assert ('"$here/scripts/deploy/provision-examples.py" --catalog "$_uc_catalog" --users-group "$users_group"'
            in deploy)
    assert '--maintenance-principal "$_maintenance_principal"\n)' in deploy
    provision = (_ROOT / "scripts" / "deploy" / "provision-examples.py").read_text()
    assert "import catalog_grant, fill, maintenance_grant" in provision and "default=True" in provision
    assert "build(" not in provision
    # RUN-CLIENT-DRAFT-RESULTS — App identities never hold a catalog-wide data privilege, which draft_results would
    # inherit: catalog-level USE_CATALOG only, and an earlier deploy's catalog-wide grants revoked.
    app_grants = (_ROOT / "scripts" / "deploy" / "grant-app-access.sh").read_text()
    assert "catalog_privs='[\"USE_CATALOG\"]'" in app_grants
    assert "revoked_privs='[\"USE_SCHEMA\",\"SELECT\"]'" in app_grants and '\\"remove\\":$revoked_privs' in app_grants
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
    """APP-RUN-LOG, DEC-RUN-LOG-LOCATION — one deploy step creates the tables, as the deployer; the bundle grants
    their schema, and the step grants the catalog the bundle does not hold."""
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
    grants = [sql for sql in calls if sql.startswith("GRANT")]
    assert grants == ["GRANT USE CATALOG ON CATALOG `synthetic_cat` TO `synthetic-sp`",
                      "GRANT USE CATALOG ON CATALOG `synthetic_cat` TO `synthetic-readers`"]

    bundle = (_ROOT / "databricks.yml").read_text()
    deploy = (_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    assert "catalog_name: ${var.designer_catalog}\n      name: designer" in bundle
    # Bundle keys are unique across resource types; `cql_databricks_designer` is the App (found by validate).
    assert "    cql_databricks_designer_shared:\n      catalog_name: ${var.designer_catalog}" in bundle
    shared = bundle.split("    cql_databricks_designer_shared:", 1)[1].split("\n\n", 1)[0]
    assert ("- principal: ${resources.apps.cql_databricks_designer.service_principal_client_id}\n"
            "          privileges: [USE_SCHEMA, MODIFY]") in shared
    assert "- principal: ${var.run_log_readers_group}\n          privileges: [USE_SCHEMA, SELECT]" in shared
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


def _run_log_tables(runs_columns):
    """A warehouse whose ``runs`` table has ``runs_columns``, recording every statement."""
    from cql_databricks_designer import run_log
    calls = []

    def run(sql, params=None):
        calls.append(sql)
        if "information_schema.columns" in sql:
            steps = [(run_log.STEPS, name, kind) for name, kind in run_log.STEP_COLUMNS]
            runs = [(run_log.RUNS, name, kind) for name, kind in runs_columns]
            return [{"table_name": t, "column_name": n, "full_data_type": k.lower()} for t, n, k in runs + steps]
        return []
    return run, calls


def test_an_earlier_run_log_is_migrated_once():
    """APP-RUN-LOG — a runs table with the old data_source column gets column mapping and the rename, once."""
    from cql_databricks_designer import run_log
    old = [("data_source", kind) if name == "environment" else (name, kind) for name, kind in run_log.RUN_COLUMNS]
    run, calls = _run_log_tables(old)
    assert _provision_run_log().migrate(run, "synthetic_cat") == [
        "ALTER TABLE `synthetic_cat`.`designer`.`runs` SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name', "
        "'delta.minReaderVersion' = '2', 'delta.minWriterVersion' = '5')",
        "ALTER TABLE `synthetic_cat`.`designer`.`runs` RENAME COLUMN `data_source` TO `environment`"]
    run, calls = _run_log_tables(run_log.RUN_COLUMNS)
    assert _provision_run_log().migrate(run, "synthetic_cat") == []
    run, calls = _run_log_tables([*run_log.RUN_COLUMNS, ("data_source", "STRING")])
    assert _provision_run_log().migrate(run, "synthetic_cat") == []
    assert not any(sql.startswith("ALTER") for sql in calls)


def test_no_app_we_deploy_discovers_by_catalog_scope():
    """CLIENT-ENVIRONMENT — nothing is discovered in catalog scopes: the Apps find environments by catalog tag, so no
    deploy step, App descriptor or local setting carries catalog_scopes."""
    from cql_databricks_common.local_env import BUNDLE_OWNED
    texts = [(_ROOT / "databricks.yml").read_text(), (_ROOT / "scripts" / "deploy" / "deploy.sh").read_text(),
             (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text(),
             (_ROOT / "scripts" / "deploy" / "provision-examples.py").read_text(),
             (_DESIGNER_ROOT / "deploy" / "app.yaml").read_text(),
             (_ROOT / "agent" / "server" / "deploy" / "app.yaml").read_text(),
             (_ROOT / "databricks.local.yml.example").read_text()]
    for text in texts:
        assert "catalog_scopes" not in text and "CATALOG_SCOPES" not in text
    assert "CQL_DATABRICKS_CATALOG_SCOPES" not in BUNDLE_OWNED


def test_the_client_wheel_is_checked_under_python_311():
    """SVC-PY311-SYNTAX — the Apps import the client wheel at startup, so staging parses its source under 3.11 too."""
    stage = (_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    assert 'PYTHONPYCACHEPREFIX="$(mktemp -d)" python3.11 -m compileall -q "$here/compiler/client/src/cql_databricks"' in stage
    assert stage.index("compiler/client/src/cql_databricks") < stage.index("staged OK")


def test_the_apps_that_use_the_compiler_are_created_one_after_the_other():
    """DEPLOY-APP-GRANTS — each App's compiler resource writes the compiler App's permissions; two at once conflict, so
    the Designer references the agent App, the bundle's only ordering edge between resources."""
    import yaml
    apps = yaml.safe_load((_ROOT / "databricks.yml").read_text())["resources"]["apps"]
    users = [k for k, a in apps.items() if any("app" in r for r in a.get("resources") or [])]
    assert sorted(users) == ["cql_databricks_agent", "cql_databricks_designer"]
    assert "${resources.apps.cql_databricks_agent.name}" in apps["cql_databricks_designer"]["description"]
