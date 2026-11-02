"""RUN-CLIENT-CATALOG-CHECKPOINTS, RUN-CLIENT-RESULT-IDENTITY — optimistic current-table execution and portable
descriptors.

No metadata-table writes, physical version inference, DESCRIBE, or result-count probes. The caller
serializes writers for its calculation namespace. UC grants authorize every actual SQL statement.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal

from cql_databricks.resolvers.interfaces import KeptResult, ResultKey, ResultResolver, ResultStore, ResultWrites, TableRef
from cql_databricks.data_bindings.runner import RunningStatement, SqlRunner, start_statement

from ._catalog_completion import WRITER_DIGITS, result_table_name
from ._durable import (CompletedResult, Definition, Outcome, ResultsUnavailable, _destination, _hash, _json,
                       _selected, _text, _utc)
from ._durable_delta import table_sql
from ._durable_plan import PreparedPlan


FORMAT = "cql-catalog-selection-v3"


@dataclass(frozen=True, kw_only=True)
class CurrentArtifact:
    """RUN-CLIENT-CATALOG-CHECKPOINTS — a current result table and its column descriptors.

    The table name carries the calculation's fingerprint (RUN-CLIENT-TABLE-NAMES), so the table is the result.
    """

    table: tuple[str, str, str]
    schema_ddl: str = ""

    def __post_init__(self) -> None:
        if (not isinstance(self.table, tuple) or len(self.table) != 3
                or any(not isinstance(part, str) or not part.strip() for part in self.table)):
            raise ValueError("artifact table must be an explicit (catalog, schema, table) tuple")
        if self.schema_ddl:
            columns = json.loads(self.schema_ddl)
            if (not isinstance(columns, list) or not columns
                    or any(not isinstance(c, list) or len(c) != 2
                           or any(not isinstance(v, str) or not v for v in c) for c in columns)
                    or len({c[0] for c in columns}) != len(columns)):
                raise ValueError("artifact column descriptors must be unique name/type pairs")


@dataclass(frozen=True, kw_only=True)
class StepRecord:
    """APP-RUN-LOG — what ``execute`` did for one definition: reused it, computed it, failed computing it, or
    cancelled it because another failed (``decision="cancelled"``, RUN-CLIENT-PARALLEL-DEFINES).

    ``statement`` is the exact SQL a computed step sent (None when reused), so a caller that observes its
    runner (``warehouse_session(...).observed(observer)``) can join the step to that statement's ID. ``started_at`` and
    ``ended_at`` bracket that statement; a reused step has neither. ``error`` is set only on a failed or cancelled step.
    """

    definition: str
    library: str
    define: str
    decision: Literal["computed", "reused", "cancelled"]
    reason: str
    table: tuple[str, str, str]
    statement: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None


StepObserver = Callable[[StepRecord], None]
PlanObserver = Callable[[tuple[str, ...]], None]
#: RUN-PROGRESS-TASKS — receives a definition's key when its statement starts.
StartObserver = Callable[[str], None]


def _step(definition: Definition, **fields) -> StepRecord:
    origin = json.loads(definition.origin_json)
    return StepRecord(definition=definition.key, library=origin.get("library", ""),
                      define=origin.get("define") or definition.key, **fields)


def result_key(definition: Definition) -> ResultKey:
    """RUN-CLIENT-TABLE-NAMES — the library and define from compiler metadata, and the fingerprint. A materialized
    retrieve has no define name; its compiler key names it instead."""
    origin = json.loads(definition.origin_json)
    if not origin.get("library"):
        raise ValueError(f"definition {definition.key!r} has no library in its compiler metadata")
    return ResultKey(library=origin["library"], define=origin.get("define") or definition.key,
                     fingerprint=definition.fingerprint)


def table_name(definition: Definition, writer: str) -> str:
    """RUN-CLIENT-TABLE-NAMES — ``definition``'s table name when the identity with digest ``writer`` writes it
    (`RUN-CLIENT-RESULT-OWNERSHIP`)."""
    key = result_key(definition)
    return result_table_name(key.library, key.define, key.fingerprint, writer)


def _stem(name: str) -> str:
    """A result table name without its writer's digest."""
    return name[:-(WRITER_DIGITS + 1)]


def _parts(table: TableRef) -> tuple[str, str, str]:
    return table.catalog, table.schema, table.table


def _result(kept: KeptResult) -> CompletedResult:
    return CompletedResult(fingerprint=kept.fingerprint, completed_at=kept.computed_at,
                           artifact=CurrentArtifact(table=_parts(kept.table)))


def _columns(run: SqlRunner, outcomes: Sequence[Outcome]) -> tuple[Outcome, ...]:
    """One bulk column-descriptor read per result schema, not per-table verification.
    A closure reusing results from another schema (an official result, `RUN-CLIENT-RESULT-OWNERSHIP`) reads that
    schema too."""
    columns: dict[tuple[str, str, str], list] = {}
    for schema in dict.fromkeys(outcome.result.artifact.table[:2] for outcome in outcomes):
        names = {o.result.artifact.table[2] for o in outcomes if o.result.artifact.table[:2] == schema}
        rows = run("SELECT table_name, column_name, full_data_type FROM "
                   f"{table_sql((schema[0], 'information_schema', 'columns'))} "
                   "WHERE table_schema = :schema AND table_name IN "
                   "(SELECT explode(from_json(:tables, 'ARRAY<STRING>'))) ORDER BY table_name, ordinal_position",
                   {"schema": schema[1], "tables": json.dumps(sorted(names))})
        found = {(*schema, name): [] for name in names}
        for row in rows:
            if (*schema, row["table_name"]) not in found:
                raise ValueError("catalog returned unrequested column descriptors")
            found[(*schema, row["table_name"])].append([row["column_name"], row["full_data_type"]])
        columns.update(found)
    if any(not value for value in columns.values()):
        raise ResultsUnavailable("catalog did not return every selected table's column descriptors")
    return tuple(replace(outcome, result=replace(outcome.result, artifact=replace(
        outcome.result.artifact, schema_ddl=_json(columns[outcome.result.artifact.table]))))
                 for outcome in outcomes)


#: RUN-CLIENT-PARALLEL-DEFINES — how many define statements a run keeps in flight, unless the caller says.
PARALLEL_DEFINES = 4

@dataclass(frozen=True)
class _Compute:
    """A definition this run computes: its table, its statement, and the computed definitions it reads."""
    definition: Definition
    key: ResultKey
    reason: str
    table: TableRef
    statement: str
    after: frozenset[str]


def execute(plan: PreparedPlan, targets: Sequence[str], *, results: ResultResolver, run: SqlRunner,
            mode: str = "skip_if_computed", computed_since: datetime | None = None, read_only: bool = False,
            on_step: StepObserver | None = None, on_plan: PlanObserver | None = None,
            on_start: StartObserver | None = None, parallelism: int = PARALLEL_DEFINES) -> tuple[Outcome, ...]:
    """RUN-CLIENT-ASYNC-CHECKPOINTS, RUN-CLIENT-RESULT-IDENTITY — one result lookup, one statement per miss.

    CLIENT-RESULT-RESOLVER — every result is found through ``results``, which owns the whole lookup (the caller's own,
    then the official ones), and, unless ``read_only``, written through it, a :class:`ResultStore`, which names each
    table and gives its statement. A read-only run never writes: a miss raises ``ResultsUnavailable``.

    A complete result is reused unless the define is not reusable (`DEC-RESULT-IDENTITY`) or was computed before
    ``computed_since``. The upstream results it read are not checked: freshness is the data source's.

    RUN-CLIENT-PARALLEL-DEFINES — every definition is decided first, in plan order. The ones to compute then
    start as soon as every definition they read that this run computes has finished, up to ``parallelism`` at
    once, so a long define delays only the defines that read it. On a warehouse session they run side by side
    in its one session (``start_statement``); on any other runner, one at a time. The first failure cancels
    every statement still running and starts nothing more.

    RUN-CLIENT-RESULT-OWNERSHIP — two runs of one identity computing one result write the same rows to the same table,
    so just before each write, and again if the write fails, the result is looked up once more, and reused if another
    run finished it: a collision costs duplicate work, never a wrong result or a failed run.

    Pass the SAME explicit cutoff when retrying a failed refresh. A missing cutoff for recompute
    starts a new refresh; no hidden session index remembers it.
    APP-RUN-LOG — ``on_step`` receives a ``StepRecord`` per definition as it is decided or finishes: the reused
    ones, then each computed one in the order it finished, the failed one, and each one cancelled because of it.
    RUN-PROGRESS-TASKS — ``on_plan`` receives every planned definition key, in plan order, once, before any of
    them runs; ``on_start`` receives a key each time its statement starts.
    """
    if not read_only and not isinstance(results, ResultStore):
        raise TypeError(f"writing results needs a ResultStore, not {type(results).__name__}")
    if mode not in ("skip_if_computed", "recompute"):
        raise ValueError(f"unsupported reuse mode {mode!r}")
    if read_only and mode != "skip_if_computed":
        raise ValueError("read-only resolution cannot request recompute")
    if type(parallelism) is not int or parallelism < 1:
        raise ValueError("parallelism must be a positive number of define statements")
    if computed_since is not None:
        computed_since = _utc(computed_since, "computed_since")
        if computed_since > datetime.now(timezone.utc):
            raise ValueError("computed_since cannot be in the future")
    elif mode == "recompute":
        computed_since = datetime.now(timezone.utc)
    planned = _selected(plan.definitions, targets)
    if on_plan is not None:
        on_plan(tuple(definition.key for definition in planned))
    keys = {definition.key: result_key(definition) for definition in planned}
    found = results.find(list(keys.values()), computed_since=computed_since)
    destination = getattr(results, "destination", None)
    outcomes: dict[str, Outcome] = {}
    artifacts: dict[str, CurrentArtifact] = {}
    computes: dict[str, _Compute] = {}
    submitted: set[TableRef] = set()

    def done_meanwhile(compute: _Compute) -> KeptResult | None:
        """RUN-CLIENT-RESULT-OWNERSHIP — the result ``compute`` failed to write, if another run finished it since the
        first lookup and it may be reused."""
        if not compute.definition.reusable:
            return None
        return results.find([compute.key], computed_since=computed_since).get(compute.key.fingerprint)

    def reused(definition: Definition, kept: KeptResult, reason: str) -> None:
        outcomes[definition.key] = Outcome(definition=definition.key, decision="reused", reason=reason,
                                           result=_result(kept))
        artifacts[definition.key] = outcomes[definition.key].result.artifact
        if on_step is not None:
            on_step(_step(definition, decision="reused", reason=reason, table=_parts(kept.table)))

    def decide(writes: ResultWrites | None) -> None:
        for definition in planned:
            if writes is not None:
                writes.check()
            kept = found.get(definition.fingerprint) if definition.reusable else None
            if kept is not None:
                where = (kept.table.catalog, kept.table.schema)
                reused(definition, kept, "matching completed result" if where == destination
                       else f"matching completed result in {'.'.join(where)}")
                continue
            if not definition.reusable:
                reason = "definition is not reusable"
            elif computed_since is not None:
                reason = f"no matching result computed since {computed_since.isoformat()}"
            else:
                reason = "no matching completed result"
            if writes is None:
                raise ResultsUnavailable(f"result {definition.key!r} is unavailable: {reason}")
            key = keys[definition.key]
            table = writes.table(key)
            artifacts[definition.key] = CurrentArtifact(table=_parts(table))
            query = plan.query_for(definition.key, {k: artifacts[k] for k in definition.reads})
            computes[definition.key] = _Compute(definition, key, reason, table, writes.statement(table, query),
                                                frozenset(k for k in definition.reads if k in computes))

    if read_only:
        decide(None)
    else:
        # Even on foreground failure, prior successful writes must finish their completion marks.
        with results.writes(list(keys.values())) as writes:
            decide(writes)
            _compute(computes, run=run, writes=writes, outcomes=outcomes, submitted=submitted, on_step=on_step,
                     on_start=on_start, parallelism=parallelism, done_meanwhile=done_meanwhile, reused=reused)
    return _columns(run, tuple(outcomes[definition.key] for definition in planned))


def _compute(computes: Mapping[str, _Compute], *, run: SqlRunner, writes: ResultWrites,
             outcomes: dict[str, Outcome], submitted: set[TableRef], on_step: StepObserver | None,
             on_start: StartObserver | None, parallelism: int,
             done_meanwhile: Callable[[_Compute], KeptResult | None],
             reused: Callable[[Definition, KeptResult, str], None]) -> None:
    """RUN-CLIENT-PARALLEL-DEFINES — run ``computes`` in dependency order, up to ``parallelism`` at once.

    A definition starts once every definition in its ``after`` has finished. One thread does it all: it starts every
    definition that is ready, then polls the running ones in turn. A poll waits until that statement finishes or
    about 5 s pass, and a finished one answers at once, so the polls are the run's only wait. The first failure is
    reported, every statement still running is cancelled and reported, and the failure is raised; nothing more
    starts.

    RUN-CLIENT-RESULT-OWNERSHIP — when a definition's statement fails, ``done_meanwhile`` looks its result up again; one
    another run finished is ``reused`` instead of failing. Nothing is looked up before a write: two runs of one identity
    computing one result both write it, the same rows to the same table.
    ``writes`` marks each table before its statement starts and each result complete after it finishes.
    """
    waiting = list(computes)
    running: dict[str, tuple[RunningStatement, datetime]] = {}  # in the order they were last polled
    finished: set[str] = set()
    cause = "the run stopped"

    def finish(key: str, started: datetime) -> None:
        compute = computes[key]
        kept = KeptResult(table=compute.table, fingerprint=compute.definition.fingerprint, computed_at=started)
        writes.completed(kept, after=tuple(computes[k].table for k in compute.after
                                           if computes[k].table in submitted))
        submitted.add(compute.table)
        outcomes[key] = Outcome(definition=key, decision="computed", reason=compute.reason, result=_result(kept))
        finished.add(key)
        if on_step is not None:
            on_step(_step(compute.definition, decision="computed", reason=compute.reason, table=_parts(compute.table),
                          statement=compute.statement, started_at=started, ended_at=datetime.now(timezone.utc)))

    def finished_elsewhere(key: str, when: str) -> bool:
        kept = done_meanwhile(computes[key])
        if kept is None:
            return False
        reused(computes[key].definition, kept, f"completed by another run {when}")
        finished.add(key)
        return True

    def failed(key: str, started: datetime, error: BaseException) -> None:
        nonlocal cause
        cause = f"{key} failed"
        running.pop(key, None)
        if on_step is not None:
            compute = computes[key]
            on_step(_step(compute.definition, decision="computed", reason=compute.reason, table=_parts(compute.table),
                          statement=compute.statement, started_at=started, ended_at=datetime.now(timezone.utc),
                          error=f"{type(error).__name__}: {error}"))

    try:
        while waiting or running:
            writes.check()
            for key in [k for k in waiting if computes[k].after <= finished][:parallelism - len(running)]:
                waiting.remove(key)
                compute = computes[key]
                writes.starting(compute.table)
                if on_start is not None:
                    on_start(key)
                started = datetime.now(timezone.utc)
                try:
                    running[key] = (start_statement(run, compute.statement), started)
                except Exception as error:
                    if finished_elsewhere(key, "while this one wrote it"):
                        continue
                    failed(key, started, error)
                    raise
            if not running:
                continue
            key = next(iter(running))
            statement, started = running.pop(key)
            try:
                done = statement.poll()
            except Exception as error:
                if finished_elsewhere(key, "while this one wrote it"):
                    continue
                failed(key, started, error)
                raise
            if done:
                finish(key, started)
            else:
                running[key] = (statement, started)  # to the back: the others are polled first
    except BaseException:
        for key, (statement, started) in list(running.items()):
            statement.cancel()
            if on_step is not None:
                compute = computes[key]
                on_step(_step(compute.definition, decision="cancelled", reason=compute.reason, table=_parts(compute.table),
                              statement=compute.statement, started_at=started, ended_at=datetime.now(timezone.utc),
                              error=f"cancelled: {cause}"))
        running.clear()
        raise


def request_identity(definitions: Sequence[Definition], targets: Mapping[str, str],
                     destination: tuple[str, str]) -> str:
    """RUN-CLIENT-RESULT-SETS — identify a requested closure without execution or session identity."""
    planned = _selected(definitions, tuple(targets.values()))
    return _hash({"destination": list(destination), "targets": dict(targets),
                  "definitions": [asdict(definition) for definition in planned]})


@dataclass(frozen=True)
class CatalogSelection:
    """Self-contained descriptors; reads address current result tables.

    RUN-CLIENT-RESULT-IDENTITY — every result's table is named for its definition's fingerprint, in the
    destination or the schema it was reused from, so a reader checks each name without recomputing it.
    """

    session_id: str
    selection_id: str
    destination: tuple[str, str]
    targets: Mapping[str, str]
    definitions: tuple[Definition, ...]
    outcomes: tuple[Outcome, ...]
    published_at: datetime
    computed_since: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        _text(self.selection_id, "selection_id")
        _destination(self.destination)
        object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        if self.computed_since is not None:
            object.__setattr__(self, "computed_since", _utc(self.computed_since, "computed_since"))
        if not self.targets:
            raise ValueError("selection requires explicit targets")
        for name in self.targets:
            _text(name, "target name")
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))
        planned = _selected(self.definitions, tuple(self.targets.values()))
        records = {outcome.definition: outcome.result for outcome in self.outcomes}
        if (len(planned) != len(self.definitions) or len(records) != len(self.outcomes)
                or set(records) != {definition.key for definition in planned}):
            raise ValueError("selection must contain exactly its complete dependency closure")
        tables = set()
        for definition in planned:
            result = records[definition.key]
            artifact = result.artifact
            if (not isinstance(artifact, CurrentArtifact) or not artifact.schema_ddl
                    or result.fingerprint != definition.fingerprint
                    or _stem(artifact.table[2]) != _stem(table_name(definition, '0' * WRITER_DIGITS))):
                raise ValueError("selection identity or descriptors do not match")
            if self.computed_since is not None and result.completed_at < self.computed_since:
                raise ValueError("selection contains a result computed before computed_since")
            if artifact.table in tables:
                raise ValueError("selection definitions must have independent result tables")
            tables.add(artifact.table)

    @classmethod
    def completed(cls, session_id: str, destination: tuple[str, str], targets: Mapping[str, str],
                  definitions: Sequence[Definition], outcomes: tuple[Outcome, ...], *,
                  computed_since: datetime | None = None) -> CatalogSelection:
        """Return a handle only after all checkpoint writes finish.

        The selection ID moves whenever any result in it is recomputed, here or in the schema it was reused from."""
        selection_id = _hash({"destination": list(destination), "targets": dict(targets),
                              "results": {o.definition: [list(o.result.artifact.table),
                                                         o.result.completed_at.isoformat()] for o in outcomes}})
        return cls(session_id, selection_id, destination, targets,
                   tuple(_selected(definitions, tuple(targets.values()))), outcomes,
                   datetime.now(timezone.utc), computed_since)

    def to_dict(self) -> dict:
        """No tokens, SQL, or lookup-table foreign keys."""
        return {"format": FORMAT, "session_id": self.session_id, "selection_id": self.selection_id,
                "destination": list(self.destination), "targets": dict(self.targets),
                "definitions": [asdict(d) for d in self.definitions],
                "outcomes": [{"definition": o.definition, "decision": o.decision, "reason": o.reason,
                              "result": {"fingerprint": o.result.fingerprint,
                                         "artifact": asdict(o.result.artifact),
                                         "completed_at": o.result.completed_at.isoformat()}}
                             for o in self.outcomes],
                "published_at": self.published_at.isoformat(),
                "computed_since": self.computed_since.isoformat() if self.computed_since else None}

    def to_json(self) -> str:
        """Canonical, portable serialization."""
        return _json(self.to_dict())

    @property
    def request_hash(self) -> str:
        """RUN-CLIENT-RESULT-SETS — request compatibility is independent of when results were computed."""
        return request_identity(self.definitions, self.targets, self.destination)

    def validate(self, backend) -> None:
        """RUN-CLIENT-OPTIMISTIC — local descriptor checks only; SQL enforces actual access."""
        for outcome in self.outcomes:
            if not backend.usable(outcome.result.artifact):
                raise ResultsUnavailable("selected artifact is unavailable")

    @classmethod
    def from_json(cls, payload: str) -> CatalogSelection:
        """Validate locally without recompiling or reading a metadata table."""
        raw = json.loads(payload)
        if raw.pop("format") != FORMAT:
            raise ValueError("unsupported portable result handle format")
        raw["destination"] = tuple(raw["destination"])
        raw["definitions"] = tuple(Definition(**(d | {"reads": tuple(d["reads"])})) for d in raw["definitions"])
        outcomes = []
        for item in raw["outcomes"]:
            result = item["result"]
            artifact = result["artifact"]
            result["artifact"] = CurrentArtifact(**(artifact | {"table": tuple(artifact["table"])}))
            result["completed_at"] = datetime.fromisoformat(result["completed_at"])
            if item["decision"] not in ("computed", "reused"):
                raise ValueError("invalid outcome decision")
            outcomes.append(Outcome(**(item | {"result": CompletedResult(**result)})))
        raw["outcomes"] = tuple(outcomes)
        raw["published_at"] = datetime.fromisoformat(raw["published_at"])
        if raw["computed_since"] is not None:
            raw["computed_since"] = datetime.fromisoformat(raw["computed_since"])
        return cls(**raw)
