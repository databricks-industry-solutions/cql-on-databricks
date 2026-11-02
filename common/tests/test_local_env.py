"""SVC-LOCAL-CONFIG-BUNDLE — workspace settings come only from the bundle; `.env` cannot repeat them.

The bundle is read through `databricks bundle validate -o json`; these tests hand the loader a fake
runner returning that JSON, so no CLI or workspace is involved.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cql_databricks_common import local_env

_RESOLVED = {
    "workspace": {"host": "https://example.cloud.databricks.com", "profile": "p1"},
    "variables": {
        "uc_catalog": {"value": "cat"},
        "designer_catalog": {"value": "cat_shared"},
        "warehouse_id": {"value": "wh1"},
    },
}


def _runner(stdout: str, stderr: str = "", rc: int = 0):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)
    run.calls = calls
    return run


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A bundle root with a databricks.local.yml, as the working directory, and a clean env."""
    (tmp_path / "databricks.yml").write_text("bundle:\n  name: t\n")
    (tmp_path / "databricks.local.yml").write_text("targets: {}\n")
    monkeypatch.chdir(tmp_path)
    for name in (*local_env.BUNDLE_OWNED, local_env.LOADED_MARKER, "DATABRICKS_BUNDLE_TARGET"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_maps_bundle_to_env(repo):
    run = _runner(json.dumps(_RESOLVED))
    assert local_env.bundle_values(run=run) == {
        "DATABRICKS_HOST": "https://example.cloud.databricks.com",
        "DATABRICKS_CONFIG_PROFILE": "p1",
        "UC_CATALOG": "cat", "CONTENT_CATALOG": "cat", "CQL_RUNTIME_CATALOG": "cat",
        "CQL_DESIGNER_CATALOG": "cat_shared",
        "WAREHOUSE_ID": "wh1",
    }
    cmd, kw = run.calls[0]
    assert cmd == ["databricks", "bundle", "validate", "-t", "default", "-o", "json"]
    assert kw["cwd"] == repo


def test_an_empty_bundle_value_is_omitted_so_the_consumers_default_applies(repo):
    resolved = json.loads(json.dumps(_RESOLVED))
    resolved["variables"]["warehouse_id"] = {"value": ""}
    assert "WAREHOUSE_ID" not in local_env.bundle_values(run=_runner(json.dumps(resolved)))


def test_target_flows_to_validate(repo):
    run = _runner(json.dumps(_RESOLVED))
    local_env.bundle_values("staging", run=run)
    assert run.calls[0][0][4] == "staging"


def test_validate_error_with_config_is_tolerated(repo):
    """validate prints the resolved config even when it reports e.g. an unstaged payload dir."""
    run = _runner(json.dumps(_RESOLVED), stderr="Error: stat evaluator/server/build/mcp", rc=1)
    assert local_env.bundle_values(run=run)["UC_CATALOG"] == "cat"


def test_validate_without_config_fails_loud(repo):
    with pytest.raises(local_env.LocalEnvError, match="not logged in"):
        local_env.bundle_values(run=_runner("", stderr="not logged in", rc=1))


def test_no_local_bundle_is_offline(repo):
    (repo / "databricks.local.yml").unlink()
    run = _runner("unused")
    assert local_env.bundle_values(run=run) == {}
    assert run.calls == []


def test_dotenv_repeating_a_workspace_setting_is_refused(repo):
    (repo / ".env").write_text("DATABRICKS_SERVERLESS=1\nexport UC_CATALOG=x\n# WAREHOUSE_ID=commented\n")
    with pytest.raises(local_env.LocalEnvError, match="UC_CATALOG") as e:
        local_env.check_dotenv()
    assert "WAREHOUSE_ID" not in str(e.value)


def test_dotenv_with_local_settings_only_passes(repo):
    (repo / ".env").write_text("DATABRICKS_SERVERLESS=1\nCQL_MATERIALIZE_RETRIEVES=all\n")
    local_env.check_dotenv()


def test_load_sets_env_once_and_bundle_wins(repo, monkeypatch, capsys):
    monkeypatch.setenv("UC_CATALOG", "from-shell")
    monkeypatch.setattr(local_env, "bundle_values",
                        lambda target=None, *, root=None: {"UC_CATALOG": "cat"})
    (repo / ".env").write_text("CQL_MATERIALIZE_RETRIEVES=all\n")
    monkeypatch.delenv("CQL_MATERIALIZE_RETRIEVES", raising=False)
    local_env.load_local_env()
    assert os.environ["UC_CATALOG"] == "cat"
    assert os.environ["CQL_MATERIALIZE_RETRIEVES"] == "all"
    assert os.environ[local_env.LOADED_MARKER] == "default"
    assert "from-shell" in capsys.readouterr().err  # replaced loudly, not silently
    # A second call (a child of run-app.sh) does nothing.
    monkeypatch.setattr(local_env, "bundle_values", lambda *a, **k: pytest.fail("validated twice"))
    local_env.load_local_env()


def test_shell_entry_point(repo, tmp_path):
    """Shell tooling runs the file with a bare interpreter; a fake `databricks` stands in for the CLI."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "databricks"
    fake.write_text(f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(_RESOLVED)}\nJSON\n")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in local_env.BUNDLE_OWNED}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    out = subprocess.run([sys.executable, local_env.__file__, "--shell"], capture_output=True,
                         text=True, check=True, cwd=repo, env=env)
    assert "export UC_CATALOG=cat\n" in out.stdout
    assert f"export {local_env.LOADED_MARKER}=default" in out.stdout
