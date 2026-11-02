"""APP-RUN-LOG — the run log's rows, its inserts and its background writer. Synthetic runs only."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from cql_databricks.data_bindings import ExecutedStatement
from cql_databricks.result import StepRecord
from cql_databricks_designer import run_log

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONTEXT = run_log.RunContext(user="synthetic-user", warehouse_id="wh", app_version="synthetic-build")


def recorder() -> run_log.RunRecorder:
    ticks = iter(T0 + timedelta(seconds=n) for n in range(100))
    return run_log.RunRecorder(context=CONTEXT, environment="synthetic", period=("2026-01-01", "2026-12-31"),
                               trigger="refresh", mode="recompute", clock=lambda: next(ticks))


def computed(define: str, statement: str, error: str | None = None) -> StepRecord:
    return StepRecord(definition=define, library="Synthetic", define=define, decision="computed", reason="r",
                      table=("c", "s", define.lower()), statement=statement, started_at=T0,
                      ended_at=T0 + timedelta(milliseconds=1500), error=error)


def test_steps_join_to_the_statement_with_exactly_their_text():
    """APP-RUN-LOG — tag writes interleave on other threads, so only the step's own statement counts."""
    rec = recorder()
    rec.observe(ExecutedStatement("CTAS a", "id-a", "SUCCEEDED", T0, T0))
    rec.observe(ExecutedStatement("ALTER TABLE a SET TAGS", "id-tag", "SUCCEEDED", T0, T0))
    rec.observe(ExecutedStatement("CTAS b", "id-b", "FAILED", T0, T0))
    rec.step(computed("A", "CTAS a"))
    rec.step(computed("B", "CTAS b", error="RuntimeError: boom"))
    entry = rec.finish("RuntimeError: boom")
    assert [(s["define"], s["statement_id"], s["duration_ms"]) for s in entry.steps] == [
        ("A", "id-a", 1500), ("B", "id-b", 1500)]
    assert (entry.run["status"], entry.run["defines_computed"]) == ("failed", 1)


def test_the_run_row_times_compile_and_the_whole_run():
    rec = recorder()                       # started at T0
    rec.compiled("Synthetic", "1", T0)     # compiled at T0 + 1s
    entry = rec.finish(None)               # ended at T0 + 2s
    assert (entry.run["compile_ms"], entry.run["duration_ms"], entry.run["status"]) == (1000, 2000, "completed")
    assert entry.run["started_at"] == T0.isoformat() and entry.run["run_id"] == entry.run_id
    assert set(entry.run) == {name for name, _ in run_log.RUN_COLUMNS}


def test_a_long_error_is_truncated():
    entry = recorder().finish("x" * 10_000)
    assert len(entry.run["error"]) == 4000


def test_no_sql_text_reaches_a_row():
    """APP-RUN-LOG — no CQL or SQL in the log: identifiers, names, timings and errors only."""
    rec = recorder()
    rec.observe(ExecutedStatement("CREATE OR REPLACE TABLE secret AS SELECT 1", "id", "SUCCEEDED", T0, T0))
    rec.step(computed("A", "CREATE OR REPLACE TABLE secret AS SELECT 1"))
    entry = rec.finish(None)
    assert "SELECT" not in json.dumps(entry.run) + json.dumps(entry.steps)
    assert set(entry.steps[0]) == {name for name, _ in run_log.STEP_COLUMNS}


def test_one_insert_per_table_with_the_rows_as_one_parameter():
    """APP-RUN-LOG — batched: one statement per table, every column named, times cast from ISO text."""
    calls = []
    rec = recorder()
    rec.step(computed("A", "CTAS a"))
    run_log.write(lambda sql, params=None: calls.append((sql, params)) or [], "cat", rec.finish(None))
    assert [sql.split(" (")[0] for sql, _ in calls] == ["INSERT INTO `cat`.`designer`.`runs`",
                                                       "INSERT INTO `cat`.`designer`.`run_steps`"]
    runs_sql, runs_params = calls[0]
    assert "CAST(`started_at` AS TIMESTAMP)" in runs_sql and "CAST(`period_start` AS DATE)" in runs_sql
    assert "`duration_ms`: BIGINT" in runs_sql and "`user`: STRING" in runs_sql
    assert json.loads(runs_params["rows"])[0]["library"] is None
    assert len(json.loads(calls[1][1]["rows"])) == 1


def test_a_run_with_no_steps_writes_only_its_run_row():
    calls = []
    run_log.write(lambda sql, params=None: calls.append(sql) or [], "cat", recorder().finish("ValueError: x"))
    assert len(calls) == 1 and "`runs`" in calls[0]


def test_the_ddl_matches_the_columns_the_app_inserts():
    """APP-RUN-LOG — one column definition feeds the deploy's DDL and the App's inserts."""
    runs_ddl, steps_ddl = run_log.create_statements("cat")
    assert runs_ddl.startswith("CREATE TABLE IF NOT EXISTS `cat`.`designer`.`runs` (`run_id` STRING NOT NULL, ")
    assert all(f"`{name}` {kind}" in runs_ddl for name, kind in run_log.RUN_COLUMNS)
    assert all(f"`{name}` {kind}" in steps_ddl for name, kind in run_log.STEP_COLUMNS)
    assert "USING DELTA" in runs_ddl and "USING DELTA" in steps_ddl


def test_the_writer_runs_in_the_background_and_logs_a_failure_at_error(caplog):
    """DEC-RUN-LOG-FAILURE — submit returns before the write; a failed write is logged with the run ID."""
    release, wrote = threading.Event(), threading.Event()

    def slow(sql, params=None):
        release.wait(5)
        raise RuntimeError("synthetic warehouse failure")
    writer = run_log.RunLog(lambda: slow, lambda: "cat")
    entry = recorder().finish(None)
    writer.submit(entry)                     # returns while the write is still blocked
    release.set()
    writer._pool.submit(wrote.set)           # the single worker reaches this only after the write
    assert wrote.wait(5)
    [record] = [r for r in caplog.records if "was not logged" in r.getMessage()]
    assert record.levelname == "ERROR" and entry.run_id in record.getMessage()
