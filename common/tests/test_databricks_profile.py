"""SVC-DEPLOY-ENV-LOCAL, SVC-LOCAL-CONFIG-BUNDLE — the Databricks profile comes from config, never source.

Local tools take the profile from config, never from a name baked into the code, so retargeting a
workspace is a config edit. The resolver reads it from the gitignored config instead; these lock in
the order (explicit, databricks.local.yml, environment), that `.env` is never a source, and that
nothing is invented when no config names one.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from cql_databricks_common import databricks_profile as dp

_LOCAL_YML = """\
# a comment: profile: not-this-one
targets:
  default:
    workspace:
      host: https://example.cloud.databricks.com
      profile: from-bundle   # trailing comment
    variables:
      uc_catalog: "acme"
  staging:
    workspace:
      profile: "from-staging"
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty bundle root as the working directory, with no profile in the environment."""
    (tmp_path / "databricks.yml").write_text("bundle:\n  name: t\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    monkeypatch.delenv("DATABRICKS_BUNDLE_TARGET", raising=False)
    return tmp_path


def test_nothing_configured_is_none(repo):
    assert dp.databricks_profile() is None


def test_explicit_wins(repo):
    (repo / "databricks.local.yml").write_text(_LOCAL_YML)
    assert dp.databricks_profile("from-flag") == "from-flag"


def test_bundle_profile_per_target(repo):
    (repo / "databricks.local.yml").write_text(_LOCAL_YML)
    assert dp.databricks_profile() == "from-bundle"
    assert dp.databricks_profile(target="staging") == "from-staging"
    assert dp.databricks_profile(target="absent") is None


def test_bundle_target_env(repo, monkeypatch):
    (repo / "databricks.local.yml").write_text(_LOCAL_YML)
    monkeypatch.setenv("DATABRICKS_BUNDLE_TARGET", "staging")
    assert dp.databricks_profile() == "from-staging"


def test_bundle_beats_env(repo, monkeypatch):
    (repo / "databricks.local.yml").write_text(_LOCAL_YML)
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "from-env")
    assert dp.databricks_profile() == "from-bundle"


def test_env_when_no_bundle(repo, monkeypatch):
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "from-env")
    assert dp.databricks_profile() == "from-env"


def test_dotenv_is_never_a_source(repo):
    """A workspace setting has one home; local_env refuses a .env that sets one."""
    (repo / ".env").write_text("DATABRICKS_CONFIG_PROFILE=from-dotenv\n")
    assert dp.databricks_profile() is None


def test_found_from_a_subdirectory(repo):
    (repo / "databricks.local.yml").write_text(_LOCAL_YML)
    sub = repo / "synth" / "src"
    sub.mkdir(parents=True)
    os.chdir(sub)
    assert dp.databricks_profile() == "from-bundle"


def test_script_entry_point_is_stdlib_only(repo):
    """Shell tooling runs the file with a bare interpreter — no package import, no venv."""
    (repo / "databricks.local.yml").write_text(_LOCAL_YML)
    out = subprocess.run([sys.executable, "-I", dp.__file__, "-t", "staging"],
                         capture_output=True, text=True, check=True, cwd=repo)
    assert out.stdout.strip() == "from-staging"
