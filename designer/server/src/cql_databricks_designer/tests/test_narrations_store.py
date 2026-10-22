"""APP-NARRATIVE-OVERLAY, APP-AI-CACHE — the narration cache: its table, its key, its reads and writes.

A fake SQL runner records statements; nothing reaches a warehouse.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cql_databricks_designer.explain import narrations
from cql_databricks_designer.explain.narrations import NarrationKey, NarrationStore

KEY = NarrationKey(user="reader@example.org", library="SampleBP", version="1.0.0",
                   source_sha256=narrations.source_digest("library SampleBP"),
                   contract_sha256=narrations.contract_digest())


class _Runner:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, statement, params=None):
        self.calls.append((statement, dict(params or {})))
        return self.rows if statement.startswith("SELECT") else []


def test_the_ddl_names_every_column_and_requires_the_key():
    ddl = narrations.create_statement("synthetic_dev")
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS `synthetic_dev`.`designer`.`narrations`")
    for name, kind in narrations.COLUMNS:
        assert f"`{name}` {kind}" in ddl
    assert "`user` STRING NOT NULL" in ddl and "`instance` STRING NOT NULL" in ddl
    assert "`endpoint` STRING NOT NULL" not in ddl


def test_a_lookup_selects_the_newest_row_for_the_whole_key():
    runner = _Runner([{"instance": "<Specification/>"}])
    store = NarrationStore(lambda: runner, lambda: "synthetic_dev")
    assert store.latest(KEY) == "<Specification/>"
    statement, params = runner.calls[0]
    assert "ORDER BY `generated_at` DESC LIMIT 1" in statement
    assert params == {"user": KEY.user, "library": "SampleBP", "version": "1.0.0",
                      "source": KEY.source_sha256, "contract": KEY.contract_sha256}


def test_a_miss_is_remembered_and_a_save_replaces_it():
    """One read per key per process; a save is what the next lookup returns, with no second read."""
    runner = _Runner([])
    store = NarrationStore(lambda: runner, lambda: "synthetic_dev")
    assert store.latest(KEY) is None and store.latest(KEY) is None
    assert len(runner.calls) == 1
    store.save(KEY, instance="<Specification/>", endpoint="chat-endpoint", attempts=2, app_version=None)
    assert store.latest(KEY) == "<Specification/>"
    assert [c[0].split()[0] for c in runner.calls] == ["SELECT", "INSERT"]


def test_a_save_inserts_one_row_and_moves_the_generation():
    """The generation is the cache stamp's term (PERF-CACHE-REVISION): cached Specifications go stale."""
    runner = _Runner()
    store = NarrationStore(lambda: runner, lambda: "synthetic_dev")
    store.save(KEY, instance="<Specification/>", endpoint="chat-endpoint", attempts=1, app_version="build-1")
    statement, params = runner.calls[0]
    assert statement.startswith("INSERT INTO `synthetic_dev`.`designer`.`narrations`")
    assert params["attempts"] == "1" and params["endpoint"] == "chat-endpoint"
    assert params["app_version"] == "build-1" and params["instance"] == "<Specification/>"
    assert store.generation == 1


def test_a_failed_lookup_raises_and_is_not_remembered():
    calls = []

    def failing(statement, params=None):
        calls.append(statement)
        raise RuntimeError("table missing")
    store = NarrationStore(lambda: failing, lambda: "synthetic_dev")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            store.latest(KEY)
    assert len(calls) == 2


def test_the_key_moves_with_the_cql_source():
    assert narrations.source_digest("library A") != narrations.source_digest("library A ")


def test_the_deployed_user_is_the_forwarded_email(monkeypatch):
    from cql_databricks_common import workspace
    monkeypatch.setattr(workspace, "acting_as_service_principal", lambda: True)
    workspace.bind_forwarded_identity("token", "reader@example.org")
    assert narrations.signed_in_user() == "reader@example.org"
    workspace.bind_forwarded_identity(None, None)
    with pytest.raises(RuntimeError, match="no x-forwarded-email"):
        narrations.signed_in_user()


def _provision_module():
    for parent in Path(__file__).resolve().parents:
        script = parent / "scripts" / "deploy" / "provision-narrations.py"
        if script.is_file():
            spec = importlib.util.spec_from_file_location("provision_narrations", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("scripts/deploy/provision-narrations.py not found")


def test_the_deploy_creates_the_table_and_grants_only_the_app():
    """No group grant: nothing outside the App reads a narration."""
    calls: list[str] = []

    def run(statement, params=None):
        calls.append(statement)
        if "information_schema.columns" in statement:
            return [{"column_name": n, "full_data_type": k.lower()} for n, k in narrations.COLUMNS]
        return []
    _provision_module().provision(run, "synthetic_dev", "app-sp")
    grants = [c for c in calls if c.startswith("GRANT")]
    assert calls[0] == narrations.create_statement("synthetic_dev")
    assert grants[-1] == "GRANT SELECT, MODIFY ON TABLE `synthetic_dev`.`designer`.`narrations` TO `app-sp`"
    assert all("`app-sp`" in g for g in grants)


def test_the_deploy_fails_on_a_table_that_differs_from_its_definition():
    def run(statement, params=None):
        if "information_schema.columns" in statement:
            return [{"column_name": "user", "full_data_type": "string"}]
        return []
    with pytest.raises(RuntimeError, match="ALTER TABLE"):
        _provision_module().provision(run, "synthetic_dev", "app-sp")
