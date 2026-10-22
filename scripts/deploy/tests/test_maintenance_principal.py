"""RUN-CLIENT-MAINTENANCE — the deploy-owned maintenance principal, the bundle's Job and the Job's entry point."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cql_databricks.result.maintenance import MaintenanceReport

_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = _load("maintenance_principal", _ROOT / "scripts/deploy/maintenance_principal.py")
job_script = _load("maintenance_job_script", _ROOT / "scripts/jobs/maintenance.py")
provision_maintenance_principal = deploy.provision_maintenance_principal


class Workspace:
    """Synthetic SDK boundary for the maintenance principal; no live identities or grants."""

    def __init__(self):
        self.config = SimpleNamespace(host="https://synthetic.example/", account_id="synthetic-account")
        self.principals, self.created, self.grant_calls, self.permission_calls = [], [], [], []
        self.rule_sets, self.rule_set_updates = {}, []
        self.service_principals = SimpleNamespace(list=self._list, create=self._create)
        self.grants = SimpleNamespace(update=lambda *a, **kw: self.grant_calls.append((a, kw)))
        self.permissions = SimpleNamespace(update=lambda *a, **kw: self.permission_calls.append((a, kw)))
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name="deployer@synthetic.example"))
        self.account_access_control_proxy = SimpleNamespace(get_rule_set=self._get_rule_set,
                                                            update_rule_set=self._update_rule_set)

    def _get_rule_set(self, *, name, etag):
        return SimpleNamespace(name=name, etag=f"etag-{len(self.rule_set_updates)}",
                               grant_rules=list(self.rule_sets.get(name, [])))

    def _update_rule_set(self, *, name, rule_set):
        assert rule_set.name == name and rule_set.etag == f"etag-{len(self.rule_set_updates)}"
        self.rule_set_updates.append(name)
        self.rule_sets[name] = rule_set.grant_rules

    def _list(self, *, filter):
        return [p for p in self.principals if filter == f'displayName eq "{p.display_name}"']

    def _create(self, **kwargs):
        assert "entitlements" not in kwargs and "external_id" not in kwargs
        principal = SimpleNamespace(application_id=f"synthetic-app-{len(self.created)}", **kwargs)
        self.created.append(principal)
        self.principals.append(principal)
        return principal


def test_principal_is_created_once_without_secret_and_granted_on_the_catalog():
    """RUN-CLIENT-MAINTENANCE — deployment owns a dedicated identity; retries reuse it."""
    workspace = Workspace()
    first = provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    second = provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    assert first == second == "synthetic-app-0" and len(workspace.created) == 1
    assert workspace.created[0].display_name.startswith("cql-databricks-designer-maintenance-")
    (args, kwargs), _ = workspace.grant_calls
    assert args == ("catalog", "synthetic_shared")
    change, = kwargs["changes"]
    assert change.principal == first and [p.value for p in change.add] == ["USE_CATALOG", "USE_SCHEMA", "MANAGE"]
    (args, kwargs), _ = workspace.permission_calls
    assert args == ("warehouses", "synthetic-wh")
    acl, = kwargs["access_control_list"]
    assert acl.service_principal_name == first and acl.permission_level.value == "CAN_USE"


def test_deployer_gets_the_run_as_role_once_and_other_rules_are_kept():
    """RUN-CLIENT-MAINTENANCE — `bundle deploy` can bind the principal as the Job's run-as only with this role."""
    workspace = Workspace()
    name = "accounts/synthetic-account/servicePrincipals/synthetic-app-0/ruleSets/default"
    manager = SimpleNamespace(role="roles/servicePrincipal.manager", principals=["users/deployer@synthetic.example"])
    workspace.rule_sets[name] = [manager]
    provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    assert workspace.rule_set_updates == [name]
    kept, added = workspace.rule_sets[name]
    assert kept is manager
    assert added.role == "roles/servicePrincipal.user" and added.principals == ["users/deployer@synthetic.example"]


def test_principal_lookup_refuses_ambiguous_or_mismatched_identities():
    """RUN-CLIENT-MAINTENANCE — never choose between principals or adopt a changed one."""
    workspace = Workspace()
    provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    workspace.principals[0].active = False
    with pytest.raises(ValueError, match="does not match"):
        provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    workspace.principals.append(workspace.principals[0])
    with pytest.raises(ValueError, match="multiple"):
        provision_maintenance_principal(workspace, catalog="synthetic_shared", warehouse="synthetic-wh")
    assert len(workspace.grant_calls) == 1


def _job() -> dict:
    return yaml.safe_load((_ROOT / "databricks.yml").read_text())["resources"]["jobs"]["cql_databricks_maintenance"]


def test_the_bundle_deploys_one_daily_job_as_the_maintenance_principal():
    """RUN-CLIENT-MAINTENANCE — STANDARD daily maintenance, not tied to any interactive session or compiler call."""
    job = _job()
    assert job["name"] == "cql-databricks-designer-maintenance"
    assert job["run_as"] == {"service_principal_name": "${var.maintenance_principal}"}
    assert job["schedule"]["quartz_cron_expression"] == "0 0 3 * * ?" and job["schedule"]["timezone_id"] == "UTC"
    assert job["max_concurrent_runs"] == 1 and not job["queue"]["enabled"]
    assert job["performance_target"] == "STANDARD"
    task, = job["tasks"]
    assert task["spark_python_task"]["python_file"] == "./scripts/jobs/maintenance.py"
    assert (_ROOT / "scripts/jobs/maintenance.py").is_file()
    parameters = task["spark_python_task"]["parameters"]
    parsed = job_script.parser().parse_args(parameters)
    assert parsed.retention_days == 7 and parsed.apply and parsed.job_run_as
    assert parsed.private_catalog == "${var.designer_results_catalog}"
    assert not any("secret" in part or "compiler" in part for part in parameters)
    wheel, = job["environments"][0]["spec"]["dependencies"]
    assert wheel.endswith("/wheels/cql_databricks-${var.client_wheel_version}-py3-none-any.whl")


def test_deploy_creates_the_principal_before_the_bundle_deploys_the_job():
    """RUN-CLIENT-MAINTENANCE — the Job's run-as must exist before `bundle deploy` creates the Job."""
    deploy_sh = (_ROOT / "scripts/deploy/deploy.sh").read_text()
    grant = deploy_sh.index("grant-maintenance-access.sh")
    assert grant < deploy_sh.index("&& databricks bundle deploy")
    assert "export BUNDLE_VAR_maintenance_principal=" in deploy_sh
    assert "export BUNDLE_VAR_client_wheel_version=" in deploy_sh


def test_job_entry_point_uses_job_identity_and_reports_exact_deletions(monkeypatch, capsys):
    """RUN-CLIENT-MAINTENANCE — no profile, borrowed credentials or compiler call inside a Job."""
    import databricks.sdk
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    client, runner = object(), object()
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: client)
    monkeypatch.setattr(job_script, "warehouse_runner", lambda w, warehouse: runner if w is client else None)
    captured = {}
    def maintain(**kwargs):
        captured.update(kwargs)
        return MaintenanceReport("synthetic_private", datetime(2026, 1, 1, tzinfo=timezone.utc), (),
                                 deleted=(("synthetic_private", "synthetic_schema", "synthetic_table"),),
                                 dry_run=False)
    monkeypatch.setattr(job_script, "maintain_private_results", maintain)
    command = ["--private-catalog", "synthetic_private", "--warehouse", "synthetic-warehouse", "--apply",
               "--job-run-as"]
    assert job_script.main(command) == 0
    assert captured["run"] is runner and captured["dry_run"] is False
    output = json.loads(capsys.readouterr().out)
    assert len(output["deleted"]) == 1 and output["cutoff"].endswith("+00:00")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "synthetic-local")
    with pytest.raises(ValueError, match="Job run-as"):
        job_script.main(command)
