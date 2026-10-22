"""RUN-CLIENT-REQUEST-REOPEN — real durable runtime, synthetic compiler and warehouse boundaries."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from threading import Lock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks.compiler.providers import DatasetRecipe, DurableIdentity, PipelinePlan
from cql_databricks.data_bindings import ExecutedStatement
from cql_databricks.result import durable, PrivateDestination
from cql_databricks.result.calculation import PreparedCalculation
from cql_databricks.result import selection_reading
from cql_databricks_designer.routes import runs
from cql_databricks_designer.run_log import RunContext

BODY = {"cql": "library Synthetic version '1'\ndefine Output: 1", "trigger": "automatic",
        "data_source_id": "synthetic", "session_id": "tab",
        "overrides": {"periodStart": "2026-01-01", "periodEnd": "2026-12-31"}}
SCOPE = {"dataSourceId": "synthetic", "library": "Synthetic", "version": "1",
         "periodStart": "2026-01-01", "periodEnd": "2026-12-31"}


@pytest.fixture
def setup(monkeypatch):
    """RUN-CLIENT-CATALOG-CHECKPOINTS — only compiler and warehouse I/O are synthesized."""
    state = SimpleNamespace(tables={}, writes=[], sql=[], failed=False, denied=False, results=None,
                            provisioned=False, warehouses=[], unusable=set(), logged=[], log_fails=False)
    private = PrivateDestination("synthetic_private", "synthetic-user")
    lock = Lock()

    def prepare(**kwargs):
        state.preparation = kwargs
        compiled = PipelinePlan([DatasetRecipe(
            name="output", target="Output", library="Synthetic", library_version="1", define="Output",
            query="SELECT 'synthetic' AS __subject_id, true AS __val", reads=[], statements=[],
            # A different CQL text compiles to a different fingerprint, as with the real compiler.
            durable=DurableIdentity(format="cql-result-identity-v1",
                                    fingerprint=hashlib.sha256(kwargs.get("cql", "").encode()).hexdigest(),
                                    reusable=True, parameters={}))], "synthetic-compiler")
        request = {key: kwargs[key] for key in ("cql", "library", "version", "runtime_catalog",
                   "runtime_schema", "data_source", "period") if key in kwargs}
        request["period"] = list(request["period"])
        request["targets"] = ["Synthetic.Output"]
        return PreparedCalculation("Synthetic", "1", "synthetic", ("Synthetic.Output",), compiled, request)

    def run(sql, params=None):
        with lock:
            state.sql.append(sql)
            parts = re.findall(r"`([^`]+)`", sql)
            if sql == "SELECT current_user() AS principal":
                return [{"principal": private.owner}]
            if sql.startswith("SELECT schema_owner"):
                return [{"schema_owner": private.owner, "comment": "cql-private-schema-v1"}]
            if sql.startswith("SELECT schema_name"):
                return [{"schema_name": params["schema"]}] if state.provisioned else []
            if sql.startswith("SHOW TABLES"):
                return [dict(tableName=t[2], isTemporary=False) for t in state.tables if t[:2] == tuple(parts)]
            if sql.startswith("SELECT t.table_catalog"):
                return [dict(table_catalog=t[0], table_schema=t[1], table_name=t[2], tag_name=k, tag_value=v)
                        for t, tags in state.tables.items() if t[0] == parts[0] and t[1] == params["schema"]
                        and t[2] in json.loads(params["tables"]) for k, v in (tags.items() or [(None, None)])]
            if sql.startswith("SELECT table_name, column_name"):
                return [dict(table_name=t[2], column_name=k, full_data_type=v)
                        for t in state.tables if t[0] == parts[0] and t[1] == params["schema"]
                        and t[2] in json.loads(params["tables"])
                        for k, v in [("__subject_id", "string"), ("__val", "boolean")]]
            if sql.startswith("CREATE OR REPLACE"):
                state.writes.append(sql)
                if state.failed:
                    raise RuntimeError("synthetic output failed")
                state.tables.setdefault(tuple(parts[:3]), {})
                return []
            if sql.startswith("ALTER TABLE"):
                state.tables[tuple(parts[:3])].update(dict(re.findall(r"'([^']+)' = '([^']+)'", sql)))
                return []
            pytest.fail(sql)

    def reader(sql):
        state.sql.append(sql)
        assert "information_schema" not in sql and "__eval_id" not in sql and "VERSION AS OF" not in sql
        return [(label, 0, None, 0) for label in re.findall(r"'([^']+)' AS d", sql)]

    def compute(*args, **kwargs):
        state.compute_options = kwargs
        state.results = durable.materialize(*args, **kwargs)
        return state.results

    def user_run(warehouse=None, observer=None):
        if state.denied:
            raise PermissionError("SEC-OBO: signed-in caller required")
        state.warehouses.append(warehouse)
        if warehouse in state.unusable:
            def refuse(sql, params=None):
                raise RuntimeError(f"PERMISSION_DENIED: no CAN_USE on warehouse {warehouse}")
            return refuse
        if observer is None:
            return run

        def observed(sql, params=None):
            # APP-RUN-LOG — as warehouse_runner does: every statement is observed, failed ones included.
            started = datetime.now(timezone.utc)
            try:
                return run(sql, params)
            finally:
                observer(ExecutedStatement(statement=sql, statement_id=f"stmt-{len(state.sql)}", state="SUCCEEDED",
                                           started_at=started, ended_at=datetime.now(timezone.utc)))
        return observed

    def run_log(entry):
        if state.log_fails:
            raise RuntimeError("synthetic run log failure")
        state.logged.append(entry)

    def provision(*args, **kwargs):
        state.provisioned = True
        return private

    monkeypatch.setattr(runs, "prepare_calculation", prepare)
    monkeypatch.setattr(selection_reading, "prepare_calculation", prepare)
    monkeypatch.setattr(runs, "materialize", compute)
    monkeypatch.setattr(runs, "ensure_private_destination", provision)
    state.listing = {"default": "wh-default", "warehouses": [{"id": "wh-default", "name": "Shared", "state": "RUNNING"}]}
    env = runs.CalculationEnv(user_execute=lambda warehouse=None: reader, user_run=user_run,
        settings=lambda: runs.CalculationSettings(private.catalog, "synthetic_runtime"),
        warehouses=lambda: state.listing, content=lambda _: object(),
        run_context=lambda warehouse: RunContext(user="synthetic-user", warehouse_id=warehouse or "wh-default",
                                                 app_version="synthetic-build"),
        run_log=run_log)
    app = FastAPI()
    app.include_router(runs.router(compiler=SimpleNamespace(resource_types=lambda: ["Patient"]), env=env))
    state.run, state.prepare = run, prepare
    return TestClient(app), state


def result_request(result):
    return {**SCOPE, "draft": result["draft"], "resultSet": result["resultSet"]}


def test_stream_executes_as_caller_and_reports_completed_empty_result(setup):
    """RUN-CLIENT-CATALOG-CHECKPOINTS — checkpoint tags, not row counts, certify completed empty output."""
    client, state = setup
    response = client.post("/api/run-stream", json=BODY)
    events = [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]
    assert [event["stage"] for event in events] == ["compiling", "executing", "executing", "executing", "done"]
    done = events[-1]
    assert done["identity"]["draft"].startswith("durable.")
    assert done["resultSet"]["calculation"]["cql"] == BODY["cql"]
    assert "outcomes" not in done["resultSet"]
    assert done["results"]["defines"][0]["subjects"] == 0
    assert done["outcomes"][0]["decision"] == "computed"
    assert len(state.writes) == 1
    assert not any("DESCRIBE" in sql or "VERSION AS OF" in sql for sql in state.sql)


def test_stream_reports_each_definition_as_it_finishes(setup):
    """RUN-PROGRESS-TASKS — the plan, then one frame per step, each carrying every definition's state."""
    client, _ = setup
    response = client.post("/api/run-stream", json=BODY)
    events = [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]
    progress = [event["tasks"] for event in events if "tasks" in event]
    assert progress == [[{"taskKey": "output", "status": "running"}],
                        [{"taskKey": "output", "status": "completed"}]]


def test_a_failed_step_is_streamed_as_failed_before_the_error(setup):
    """RUN-PROGRESS-TASKS — a failed definition is terminal, so the gauge can reach the end of a stopped run."""
    client, state = setup
    state.failed = True
    response = client.post("/api/run-stream", json={**BODY, "recompute": True})
    events = [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]
    assert events[-2]["tasks"] == [{"taskKey": "output", "status": "failed"}]
    assert events[-1]["stage"] == "error" and "synthetic output failed" in events[-1]["error"]
    assert len(state.logged) == 1


def test_recompute_and_cutoff_reach_runtime_and_same_retry_reuses(setup):
    """RUN-CLIENT-REFRESH-RESUME — identical explicit cutoff does not rewrite completed work."""
    client, state = setup
    request = {**BODY, "recompute": True, "computed_since": "2026-01-01T00:00:00Z"}
    first = client.post("/api/run", json=request)
    second = client.post("/api/run", json=request)
    assert first.status_code == second.status_code == 200
    assert second.json()["outcomes"][0]["decision"] == "reused"
    assert len(state.writes) == 1


def test_failed_refresh_makes_current_selection_unavailable(setup):
    """RUN-CLIENT-CATALOG-CHECKPOINTS — no historical-version promise during a partial refresh."""
    client, state = setup
    first = client.post("/api/run", json=BODY).json()
    state.failed = True
    failure = client.post("/api/run", json={**BODY, "recompute": True})
    assert failure.status_code == 400 and "synthetic output failed" in failure.json()["error"]
    assert client.post("/api/results", json=result_request(first)).status_code == 409


def test_refresh_refuses_the_old_selection_and_reopens_the_new_one(setup):
    """RUN-CLIENT-REQUEST-REOPEN — old selection digest cannot relabel a newer result."""
    client, _ = setup
    first = client.post("/api/run", json=BODY).json()
    second = client.post("/api/run", json={**BODY, "recompute": True}).json()
    assert client.post("/api/results", json=result_request(first)).status_code == 409
    assert client.post("/api/results", json=result_request(second)).status_code == 200


@pytest.mark.parametrize("scope", [{"library": "Other"}, {"version": "2"},
                                  {"dataSourceId": "other"}, {"periodStart": "2025-01-01"}])
def test_request_cannot_relabel_a_different_scope(setup, scope):
    """RUN-CLIENT-RESULT-SETS — displayed period, data source and library must match the selected request."""
    client, _ = setup
    first = client.post("/api/run", json=BODY).json()
    assert client.post("/api/results", json=result_request(first) | scope).status_code == 409


@pytest.mark.parametrize("address", [{}, {"session_id": "old"}, {"destination": ["x", "y"]}])
def test_legacy_addresses_fail_without_storage_reads(setup, address):
    """RUN-CLIENT-MIGRATION — no SDP/session-index fallback."""
    client, state = setup
    assert client.post("/api/results", json={**SCOPE, "resultSet": address}).status_code == 409
    assert not state.sql


def test_no_caller_is_refused_before_streaming(setup):
    """SEC-OBO — missing user identity never selects service credentials."""
    client, state = setup
    state.denied = True
    assert client.post("/api/run-stream", json=BODY).status_code == 503
    assert not state.writes


@pytest.mark.parametrize("unsupported", [{"mode": "changed"}, {"limit": 5},
                                         {"data_as_of": "2026-01-01"}, {"binding_revision": "old"}])
def test_unsupported_contracts_fail_without_writes(setup, unsupported):
    """RUN-CLIENT-MIGRATION — one explicit durable execution contract."""
    client, state = setup
    assert client.post("/api/run", json={**BODY, **unsupported}).status_code == 422
    assert not state.writes


def test_designer_instance_header_supplies_the_logical_session(setup):
    """ACC-BRAND-NAMING, RUN-CLIENT-RESULT-SETS — browser and server agree on the renamed header."""
    client, state = setup
    request = {key: value for key, value in BODY.items() if key != "session_id"}
    response = client.post("/api/run", json=request, headers={"x-designer-instance": "synthetic-tab"})
    assert response.status_code == 200
    assert state.results.selection.session_id == runs._session(
        "synthetic-tab", "Synthetic", "1", "synthetic", ("2026-01-01", "2026-12-31"))


def test_all_derivation_routes_receive_one_current_closure(setup, monkeypatch):
    """PIPE-RESULT-DERIVATIONS — profile, metrics, reads and flow share the same request."""
    client, state = setup
    result = client.post("/api/run", json=BODY).json()
    request = result_request(result)
    monkeypatch.setattr(runs, "resolve_data_source", lambda *a, **kw: SimpleNamespace(bindings={}))
    captured = []
    def derive(**kwargs):
        captured.append(kwargs)
        assert kwargs["closure"].results.selection.selection_id == result["resultSet"]["selection_id"]
        return SimpleNamespace(to_dict=lambda: {"ok": True})
    monkeypatch.setattr(runs, "build_measure_metrics", derive)
    monkeypatch.setattr(runs, "build_read_metrics", lambda **kw: derive(**kw).to_dict())
    monkeypatch.setattr(runs, "build_cohort_flow", derive)
    monkeypatch.setattr(runs, "build_define_profile", derive)
    for suffix in ("reads", "metrics", "flow", "defines/Output/profile"):
        assert client.post(f"/api/libraries/Synthetic/1/{suffix}", json=request).status_code == 200
    assert len(captured) == 4 and len(state.writes) == 1


REOPEN = {"cql": BODY["cql"], "dataSourceId": "synthetic", "periodStart": "2026-01-01", "periodEnd": "2026-12-31"}
TAB = {"x-designer-instance": "tab"}


def test_reopen_shows_own_results_without_computing(setup, monkeypatch):
    """RUN-CLIENT-REQUEST-REOPEN — opening a measure reads the caller's exact results; it never computes."""
    client, state = setup
    first = client.post("/api/run", json=BODY, headers=TAB).json()
    monkeypatch.setattr(runs, "ensure_private_destination", lambda *a, **kw: pytest.fail("provisioned on read"))
    monkeypatch.setattr(runs, "materialize", lambda *a, **kw: pytest.fail("computed on read"))
    state.writes.clear()
    response = client.post("/api/reopen", json=REOPEN, headers=TAB)
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["identity"]["draft"] == first["draft"]
    assert body["outcomes"][0]["decision"] == "reused"
    assert body["results"]["versionKind"] == "private"
    assert client.post("/api/results", json=result_request({"draft": body["identity"]["draft"],
                                                             "resultSet": body["resultSet"]})).status_code == 200
    assert not state.writes


def test_reopen_miss_is_an_answer_and_creates_nothing(setup):
    """RUN-CLIENT-REQUEST-REOPEN — nothing computed, or edited CQL, is `found: false`, never a run."""
    client, state = setup
    never = client.post("/api/reopen", json=REOPEN, headers=TAB)
    assert never.status_code == 200 and never.json() == {"found": False, "reason": "nothing computed yet"}
    assert not any(sql.startswith("CREATE") for sql in state.sql)
    client.post("/api/run", json=BODY, headers=TAB)
    state.writes.clear()
    edited = client.post("/api/reopen", json={**REOPEN, "cql": BODY["cql"] + "\ndefine Other: 2"}, headers=TAB)
    assert edited.status_code == 200 and edited.json()["found"] is False
    assert not state.writes


def test_designer_has_no_published_results_route(setup):
    """RUN-CLIENT-PUBLISHED (amended 2026-10-10) — the Designer never reads Job-published results."""
    client, _ = setup
    assert client.post("/api/published-selection", json={}).status_code == 404


def test_chosen_warehouse_reaches_every_transport(setup):
    """APP-COMPUTE-TARGET — runs, the reopen lookup and every result read use the named warehouse."""
    client, state = setup
    result = client.post("/api/run", json={**BODY, "warehouse_id": "wh-chosen"}, headers=TAB).json()
    client.post("/api/reopen", json={**REOPEN, "warehouseId": "wh-chosen"}, headers=TAB)
    client.post("/api/results", json={**result_request(result), "warehouseId": "wh-chosen"})
    assert state.warehouses == ["wh-chosen"] * 3
    state.warehouses.clear()
    client.post("/api/run", json=BODY, headers=TAB)
    assert state.warehouses == [None]


def test_unusable_warehouse_fails_and_never_falls_back(setup):
    """APP-COMPUTE-TARGET — no CAN_USE on the chosen warehouse is a visible error, not the default."""
    client, state = setup
    state.unusable.add("wh-denied")
    stream = client.post("/api/run-stream", json={**BODY, "warehouse_id": "wh-denied"}, headers=TAB)
    events = [json.loads(line[6:]) for line in stream.text.split("\n\n") if line.startswith("data: ")]
    assert events[-1]["stage"] == "error" and "no CAN_USE on warehouse wh-denied" in events[-1]["error"]
    reopened = client.post("/api/reopen", json={**REOPEN, "warehouseId": "wh-denied"}, headers=TAB)
    assert reopened.status_code == 502 and "wh-denied" in reopened.json()["error"]
    assert None not in state.warehouses and not state.writes


def test_warehouse_list_is_the_callers(setup):
    """APP-COMPUTE-TARGET — the listing route returns the env's caller-scoped list and default."""
    client, state = setup
    assert client.get("/api/warehouses").json() == state.listing

    def refuse():
        raise RuntimeError("SEC-OBO: signed-in caller required")
    app = FastAPI()
    app.include_router(runs.router(compiler=SimpleNamespace(), env=runs.CalculationEnv(
        user_execute=lambda w=None: None, user_run=lambda w=None, o=None: None, settings=lambda: None,
        warehouses=refuse, run_context=lambda w: None, run_log=lambda entry: None)))
    failed = TestClient(app).get("/api/warehouses")
    assert failed.status_code == 502 and "signed-in caller" in failed.json()["error"]


def test_realtime_warehouses_are_not_offered():
    """APP-COMPUTE-TARGET — a REALTIME warehouse cannot run the cql_* UC functions, so it is not listed."""
    from cql_databricks_designer import calculation_env
    listed = [{"id": "wh-rt", "name": "rt", "state": "STOPPED", "warehouse_type": "REALTIME"},
              {"id": "wh-pro", "name": "Default", "state": "RUNNING", "warehouse_type": "PRO"}]
    assert calculation_env._supported(listed) == [{"id": "wh-pro", "name": "Default", "state": "RUNNING"}]


def test_warehouse_choice_defaults_only_when_none_is_named(monkeypatch):
    """APP-COMPUTE-TARGET — None means the deployment default; an empty name is refused."""
    from cql_databricks_designer import calculation_env
    monkeypatch.setattr(calculation_env.config, "warehouse_id", lambda: "wh-default")
    assert calculation_env._chosen(None) == "wh-default"
    assert calculation_env._chosen("wh-chosen") == "wh-chosen"
    with pytest.raises(ValueError):
        calculation_env._chosen(" ")


def test_private_retirement_is_not_an_interactive_user_operation(setup):
    """RUN-CLIENT-MAINTENANCE — shared maintenance replaces per-user lifecycle routes."""
    client, state = setup
    request = {**SCOPE, "instance": "tab"}
    assert client.post("/api/calculation-session/archive", json=request).status_code == 404
    assert client.get("/api/calculation-storage/cleanup-preview").status_code == 404
    assert not any(sql.startswith(("DROP", "DELETE", "MERGE")) for sql in state.sql)


# --- APP-RUN-LOG ----------------------------------------------------------------------------------

def test_a_completed_run_is_logged_with_its_steps_and_their_statement_ids(setup):
    """APP-RUN-LOG — one run row and one step row; the step joins to the statement that computed it."""
    client, state = setup
    response = client.post("/api/run-stream", json={**BODY, "warehouse_id": "wh-chosen"})
    done = [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")][-1]
    [entry] = state.logged
    assert done["runId"] == entry.run_id
    run = entry.run
    assert (run["user"], run["library"], run["version"], run["data_source"], run["warehouse_id"]) == (
        "synthetic-user", "Synthetic", "1", "synthetic", "wh-chosen")
    assert (run["period_start"], run["period_end"], run["trigger"], run["mode"]) == (
        "2026-01-01", "2026-12-31", "automatic", "skip_if_computed")
    assert (run["status"], run["error"], run["defines_computed"], run["defines_reused"]) == ("completed", None, 1, 0)
    assert run["duration_ms"] >= run["compile_ms"] >= 0 and run["app_version"] == "synthetic-build"
    [step] = entry.steps
    ctas = next(i for i, sql in enumerate(state.sql) if sql.startswith("CREATE OR REPLACE")) + 1
    assert (step["define"], step["decision"], step["statement_id"]) == ("Output", "computed", f"stmt-{ctas}")
    assert step["result_table"] == ".".join(re.findall(r"`([^`]+)`", state.sql[ctas - 1])[:3])
    assert not any("designer" in sql for sql in state.sql), "the log is never written on the user's runner"


def test_a_reused_step_has_no_statement(setup):
    """APP-RUN-LOG — reuse sends no CTAS, so there is no statement ID or timing to record."""
    client, state = setup
    client.post("/api/run", json=BODY)
    client.post("/api/run", json=BODY)
    second = state.logged[-1]
    assert (second.run["defines_computed"], second.run["defines_reused"]) == (0, 1)
    assert [(s["decision"], s["statement_id"], s["started_at"]) for s in second.steps] == [("reused", None, None)]


def test_a_failed_run_is_logged_with_the_failing_step(setup):
    """APP-RUN-LOG — the run row says failed and why; the failed step keeps its statement ID."""
    client, state = setup
    state.failed = True
    response = client.post("/api/run", json={**BODY, "recompute": True, "trigger": "refresh"})
    assert response.status_code == 400
    [entry] = state.logged
    assert (entry.run["status"], entry.run["trigger"], entry.run["mode"]) == ("failed", "refresh", "recompute")
    assert "synthetic output failed" in entry.run["error"]
    [step] = entry.steps
    assert "synthetic output failed" in step["error"] and step["statement_id"].startswith("stmt-")


def test_a_run_that_fails_before_compiling_still_leaves_its_run_row(setup, monkeypatch):
    """APP-RUN-LOG, DEC-RUN-LOG-SHAPE — a compile error has no step, but the run is still logged."""
    client, state = setup

    def broken(**kwargs):
        raise ValueError("synthetic compile error")
    monkeypatch.setattr(runs, "prepare_calculation", broken)
    events = client.post("/api/run-stream", json=BODY).text
    [entry] = state.logged
    assert entry.run_id in events
    assert (entry.run["status"], entry.run["library"], entry.run["compile_ms"], entry.steps) == (
        "failed", None, None, [])


def test_a_log_failure_never_changes_the_run(setup, caplog):
    """DEC-RUN-LOG-FAILURE — the run completes; the failure is logged at ERROR with the run ID."""
    client, state = setup
    state.log_fails = True
    response = client.post("/api/run", json=BODY)
    assert response.status_code == 200 and response.json()["ok"]
    assert any(r.levelname == "ERROR" and "was not logged" in r.getMessage() for r in caplog.records)


def test_reopen_is_not_logged(setup):
    """DEC-RUN-LOG-SCOPE — reopening runs no define and is not a logged run."""
    client, state = setup
    client.post("/api/run", json=BODY)
    state.logged.clear()
    reopened = client.post("/api/reopen", json=REOPEN, headers=TAB)
    assert reopened.status_code == 200 and reopened.json()["found"]
    assert not state.logged


def test_a_run_must_say_what_started_it(setup):
    """APP-RUN-LOG — trigger is required: the log records it and nothing guesses it."""
    client, state = setup
    body = {k: v for k, v in BODY.items() if k != "trigger"}
    assert client.post("/api/run", json=body).status_code == 422
    assert client.post("/api/run", json={**BODY, "trigger": "reopen"}).status_code == 422
    assert not state.logged and not state.writes


def test_metrics_counts_each_define_in_the_cohort_the_client_names(setup, monkeypatch):
    """SPEC-POPULATION-CASCADE — the cohort travels with each define; a define without one is refused."""
    client, _ = setup
    request = result_request(client.post("/api/run", json=BODY).json())
    monkeypatch.setattr(runs, "resolve_data_source", lambda *a, **kw: SimpleNamespace(bindings={}))
    captured = []
    monkeypatch.setattr(runs, "build_measure_metrics",
                        lambda **kw: captured.append(kw) or SimpleNamespace(to_dict=lambda: {"ok": True}))
    ref = lambda name: {"name": name, "library": "Synthetic"}
    define = {"name": "Numerator", "library": "Synthetic", "resultType": "Boolean",
              "cohort": {"allOf": [ref("Initial Population"), ref("Denominator")],
                         "noneOf": [ref("Denominator Exclusions")]}}
    assert client.post("/api/libraries/Synthetic/1/metrics",
                       json={**request, "defines": [define]}).status_code == 200
    assert captured[0]["cohorts"] == {("Synthetic", "Numerator"): runs.Cohort(
        all_of=(("Synthetic", "Initial Population"), ("Synthetic", "Denominator")),
        none_of=(("Synthetic", "Denominator Exclusions"),))}
    no_cohort = {k: v for k, v in define.items() if k != "cohort"}
    assert client.post("/api/libraries/Synthetic/1/metrics",
                       json={**request, "defines": [no_cohort]}).status_code == 422
