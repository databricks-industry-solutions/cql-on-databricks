"""APP-RUN-LOG — the Designer's run log: one row per run and one per define step, written in the background.

Two Delta tables in ``<designer_catalog>.designer`` (`DEC-RUN-LOG-SHAPE`, `DEC-RUN-LOG-LOCATION`). The deploy
creates them from ``RUN_COLUMNS`` and ``STEP_COLUMNS`` (``scripts/deploy/provision-run-log.py``); the App only inserts,
as its service principal. No CQL, patient data or result values: identifiers, names, timings and errors.

A run's rows are handed to ``RunLog`` before its last frame is sent and written on a background thread, so
logging never delays a run (`DEC-RUN-LOG-FAILURE`). A failed write is logged at ERROR with the run ID and
changes nothing about the run.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from cql_databricks.data_bindings import ExecutedStatement, SqlRunner
from cql_databricks.result import StepRecord

log = logging.getLogger(__name__)

SCHEMA = "designer"
RUNS = "runs"
STEPS = "run_steps"

#: The one definition of each table's columns, in order: the deploy's DDL and the App's inserts read it.
RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"), ("user", "STRING"), ("library", "STRING"), ("version", "STRING"),
    ("data_source", "STRING"), ("period_start", "DATE"), ("period_end", "DATE"), ("warehouse_id", "STRING"),
    ("trigger", "STRING"), ("mode", "STRING"), ("started_at", "TIMESTAMP"), ("ended_at", "TIMESTAMP"),
    ("duration_ms", "BIGINT"), ("compile_ms", "BIGINT"), ("defines_computed", "INT"), ("defines_reused", "INT"),
    ("status", "STRING"), ("error", "STRING"), ("app_version", "STRING"),
)
STEP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"), ("library", "STRING"), ("define", "STRING"), ("decision", "STRING"),
    ("reason", "STRING"), ("result_table", "STRING"), ("statement_id", "STRING"), ("started_at", "TIMESTAMP"),
    ("ended_at", "TIMESTAMP"), ("duration_ms", "BIGINT"), ("error", "STRING"),
)

#: Long enough for a SQL error's first lines; an error message is not a place for a stack trace.
_MAX_ERROR = 4000


def table_sql(catalog: str, table: str) -> str:
    return f"`{catalog}`.`{SCHEMA}`.`{table}`"


def create_statements(catalog: str) -> tuple[str, str]:
    """APP-RUN-LOG — the two tables' DDL. Run by the deploy, as the deployer; never by the App."""
    def ddl(table: str, columns: tuple[tuple[str, str], ...], comment: str) -> str:
        cols = ", ".join(f"`{name}` {kind}{' NOT NULL' if name == 'run_id' else ''}" for name, kind in columns)
        return f"CREATE TABLE IF NOT EXISTS {table_sql(catalog, table)} ({cols}) USING DELTA COMMENT '{comment}'"
    return (ddl(RUNS, RUN_COLUMNS, "APP-RUN-LOG: one row per Designer run"),
            ddl(STEPS, STEP_COLUMNS, "APP-RUN-LOG: one row per define step of a Designer run"))


@dataclass(frozen=True)
class RunContext:
    """APP-RUN-LOG — who ran, on which warehouse, with which build. Captured on the request thread."""
    user: str | None
    warehouse_id: str
    app_version: str | None


@dataclass(frozen=True)
class RunLogEntry:
    """APP-RUN-LOG — one run's rows, ready to insert."""
    run_id: str
    run: dict
    steps: list[dict] = field(default_factory=list)


def _ms(start: datetime | None, end: datetime | None) -> int | None:
    return None if start is None or end is None else int((end - start).total_seconds() * 1000)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _error(message: str | None) -> str | None:
    return message[:_MAX_ERROR] if message else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunRecorder:
    """APP-RUN-LOG — collects one run as it happens: its statements, its steps and its compile time.

    ``observe`` is the run's ``warehouse_runner`` observer and ``step`` its ``on_step``. A computed step's
    statement ID is the one the runner observed for exactly that step's statement text. Completion-tag
    writes run on other threads at the same time, so "the last statement" would not be the step's.
    """

    def __init__(self, *, context: RunContext, data_source: str | None, period: tuple[str | None, str | None],
                 trigger: str, mode: str, clock: Callable[[], datetime] = _now) -> None:
        self.run_id = str(uuid4())
        self._context, self._data_source, self._period = context, data_source, period
        self._trigger, self._mode, self._clock = trigger, mode, clock
        self._started = clock()
        self._library: str | None = None
        self._version: str | None = None
        self._compile_ms: int | None = None
        self._statements: dict[str, str] = {}
        self._steps: list[StepRecord] = []
        self._lock = Lock()

    def observe(self, statement: ExecutedStatement) -> None:
        with self._lock:
            self._statements[statement.statement] = statement.statement_id

    def step(self, step: StepRecord) -> None:
        self._steps.append(step)

    def compiled(self, library: str, version: str, started: datetime) -> None:
        self._library, self._version = library, version
        self._compile_ms = _ms(started, self._clock())

    def finish(self, error: str | None) -> RunLogEntry:
        """The run's rows; ``error`` is None for a completed run."""
        ended = self._clock()
        with self._lock:
            statements = dict(self._statements)
        done = [s for s in self._steps if s.error is None]
        run = {"run_id": self.run_id, "user": self._context.user, "library": self._library,
               "version": self._version, "data_source": self._data_source, "period_start": self._period[0],
               "period_end": self._period[1], "warehouse_id": self._context.warehouse_id,
               "trigger": self._trigger, "mode": self._mode, "started_at": _iso(self._started),
               "ended_at": _iso(ended), "duration_ms": _ms(self._started, ended), "compile_ms": self._compile_ms,
               "defines_computed": sum(s.decision == "computed" for s in done),
               "defines_reused": sum(s.decision == "reused" for s in done),
               "status": "failed" if error else "completed", "error": _error(error),
               "app_version": self._context.app_version}
        steps = [{"run_id": self.run_id, "library": s.library, "define": s.define, "decision": s.decision,
                  "reason": s.reason, "result_table": ".".join(s.table),
                  "statement_id": statements.get(s.statement) if s.statement else None,
                  "started_at": _iso(s.started_at), "ended_at": _iso(s.ended_at),
                  "duration_ms": _ms(s.started_at, s.ended_at), "error": _error(s.error)}
                 for s in self._steps]
        return RunLogEntry(self.run_id, run, steps)


def _insert(run: SqlRunner, table: str, columns: tuple[tuple[str, str], ...], rows: list[dict]) -> None:
    """One INSERT for all rows. They travel as one JSON parameter; dates and times are cast from ISO text."""
    fields = ", ".join(f"`{name}`: {kind if kind in ('BIGINT', 'INT') else 'STRING'}" for name, kind in columns)
    select = ", ".join(f"CAST(`{name}` AS {kind})" if kind in ("DATE", "TIMESTAMP") else f"`{name}`"
                       for name, kind in columns)
    names = ", ".join(f"`{name}`" for name, _ in columns)
    run(f"INSERT INTO {table} ({names}) SELECT {select} FROM "
        f"(SELECT inline(from_json(:rows, 'ARRAY<STRUCT<{fields}>>')))",
        {"rows": json.dumps(rows)})


def write(run: SqlRunner, catalog: str, entry: RunLogEntry) -> None:
    """APP-RUN-LOG — insert one run's rows: the run row, then its steps (if any)."""
    _insert(run, table_sql(catalog, RUNS), RUN_COLUMNS, [entry.run])
    if entry.steps:
        _insert(run, table_sql(catalog, STEPS), STEP_COLUMNS, entry.steps)


class RunLog:
    """APP-RUN-LOG — the background writer, one per process. ``submit`` returns at once.

    ``run`` builds the App service principal's runner (``app_sql_runner``) and ``catalog`` names the log's
    catalog; both are read on the writer thread, so a missing setting fails the write, not the run.
    """

    def __init__(self, run: Callable[[], SqlRunner], catalog: Callable[[], str]) -> None:
        self._run, self._catalog = run, catalog
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="designer-run-log")

    def submit(self, entry: RunLogEntry) -> None:
        self._pool.submit(self._write, entry)

    def _write(self, entry: RunLogEntry) -> None:
        try:
            write(self._run(), self._catalog(), entry)
        except Exception:  # DEC-RUN-LOG-FAILURE — never changes the run; logged at ERROR, never silent.
            log.exception("APP-RUN-LOG: run %s was not logged", entry.run_id)
