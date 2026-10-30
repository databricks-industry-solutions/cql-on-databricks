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

from cql_databricks.data_bindings.runner import RunningStatement, SqlRunner, background_runner, start_statement

from ._catalog_completion import Completion, CompletionWriter, discover, discover_in_sources, result_table_name
from ._durable import (CompletedResult, Definition, Outcome, ResultsUnavailable, _destination, _hash, _json,
                       _selected, _text, _utc)
from ._durable_delta import table_sql
from ._durable_plan import PreparedPlan


FORMAT = "cql-catalog-selection-v2"
TABLE_FORMAT = "cql-catalog-delta-v1"


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


def table_name(definition: Definition) -> str:
    """RUN-CLIENT-TABLE-NAMES — the library and define from compiler metadata, plus fingerprint digits.

    A materialized retrieve has no define name; its compiler key names it instead.
    """
    origin = json.loads(definition.origin_json)
    if not origin.get("library"):
        raise ValueError(f"definition {definition.key!r} has no library in its compiler metadata")
    return result_table_name(origin["library"], origin.get("define") or definition.key, definition.fingerprint)


def _result(completion: Completion) -> CompletedResult:
    return CompletedResult(fingerprint=completion.fingerprint, completed_at=completion.computed_at,
                           artifact=CurrentArtifact(table=completion.table))


def _matching(definition: Definition, completion: Completion) -> None:
    """RUN-CLIENT-TABLE-NAMES — a name shares only 16 fingerprint digits; the full tag must match."""
    if completion.fingerprint != definition.fingerprint:
        raise ValueError(f"result table {table_sql(completion.table)} holds another calculation with the same "
                         f"name (fingerprint {completion.fingerprint}, expected {definition.fingerprint})")


def _columns(run: SqlRunner, outcomes: Sequence[Outcome]) -> tuple[Outcome, ...]:
    """RUN-CLIENT-PORTABLE-HANDLE — one bulk column-descriptor read per result schema, not per-table verification.
    A closure reusing results from another result scope (CLIENT-DISCOVERY-SCOPE) reads that schema too."""
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
    reason: str
    table: tuple[str, str, str]
    replaces: bool
    statement: str
    after: frozenset[str]


def execute(plan: PreparedPlan, targets: Sequence[str], *, destination: tuple[str, str], run: SqlRunner,
            mode: str = "skip_if_computed", computed_since: datetime | None = None,
            read_only: bool = False, on_step: StepObserver | None = None, on_plan: PlanObserver | None = None,
            on_start: StartObserver | None = None, sources: Sequence[tuple[str, str]] = (),
            parallelism: int = PARALLEL_DEFINES) -> tuple[Outcome, ...]:
    """RUN-CLIENT-ASYNC-CHECKPOINTS, RUN-CLIENT-RESULT-IDENTITY — one catalog lookup, one CTAS per miss.

    A define's table name is its identity. A complete result under that name is reused unless the define is not
    reusable (`DEC-RESULT-IDENTITY`) or was computed before ``computed_since``. The upstream results it read are
    not checked: freshness is the data source's.

    RUN-CLIENT-PARALLEL-DEFINES — every definition is decided first, in plan order. The ones to compute then
    start as soon as every definition they read that this run computes has finished, up to ``parallelism`` at
    once, so a long define delays only the defines that read it. On a warehouse session they run side by side
    in its one session (``start_statement``); on any other runner, one at a time. The first failure cancels
    every statement still running and starts nothing more.

    CLIENT-DISCOVERY-SCOPE — ``sources`` are the other result scopes, ``(catalog, schema)`` in order. A define
    the destination holds no reusable result for reuses the first one they hold under the same name (one more
    lookup), with no owner or publisher check: the scopes are the trust boundary. Every write goes to
    ``destination``.

    Pass the SAME explicit cutoff when retrying a failed refresh. A missing cutoff for recompute
    starts a new refresh; no hidden session index remembers it. Readers never execute on a miss.
    APP-RUN-LOG — ``on_step`` receives a ``StepRecord`` per definition as it is decided or finishes: the reused
    ones, then each computed one in the order it finished, the failed one, and each one cancelled because of it.
    RUN-PROGRESS-TASKS — ``on_plan`` receives every planned definition key, in plan order, once, before any of
    them runs; ``on_start`` receives a key each time its statement starts.
    """
    _destination(destination)
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
    names = {definition.key: table_name(definition) for definition in planned}
    found = discover(run, destination, {definition.fingerprint: names[definition.key] for definition in planned})
    sources = [source for source in dict.fromkeys(sources) if source != destination]
    reusable = sorted(names[definition.key] for definition in planned if definition.reusable)
    elsewhere = discover_in_sources(run, sources, reusable) if sources else {}
    outcomes: dict[str, Outcome] = {}
    artifacts: dict[str, CurrentArtifact] = {}
    computes: dict[str, _Compute] = {}
    submitted: set[tuple[str, str, str]] = set()

    def reused(definition: Definition, completion: Completion, reason: str) -> None:
        outcomes[definition.key] = Outcome(definition=definition.key, decision="reused", reason=reason,
                                           result=_result(completion))
        artifacts[definition.key] = outcomes[definition.key].result.artifact
        if on_step is not None:
            on_step(_step(definition, decision="reused", reason=reason, table=completion.table))

    # Even on foreground failure, prior successful writes must finish their completion tags.
    # RUN-CLIENT-SESSION-REUSE — on a warehouse session, the tag writes use its side connections.
    with CompletionWriter(background_runner(run)) as writer:
        for definition in planned:
            writer.check()
            existing = found.get(definition.fingerprint)
            candidate = existing.completion if existing else None
            reason = "no matching completed result"
            if candidate is not None:
                _matching(definition, candidate)
                if not definition.reusable:
                    reason = "definition is not reusable"
                elif computed_since is not None and candidate.computed_at < computed_since:
                    reason = "computed before computed_since"
                else:
                    reused(definition, candidate, "matching completed result")
                    continue
            if definition.reusable and names[definition.key] in elsewhere:
                passed_over = None
                for completion in elsewhere[names[definition.key]]:
                    _matching(definition, completion)
                    if computed_since is not None and completion.computed_at < computed_since:
                        passed_over = f"result in {'.'.join(completion.table[:2])} was computed before computed_since"
                        continue
                    reused(definition, completion, f"matching completed result in {'.'.join(completion.table[:2])}")
                    break
                if definition.key in outcomes:
                    continue
                if candidate is None:
                    reason = passed_over
            if read_only:
                raise ResultsUnavailable(f"result {definition.key!r} is unavailable: {reason}")
            table = (*destination, names[definition.key])
            artifacts[definition.key] = CurrentArtifact(table=table)
            query = plan.query_for(definition.key, {key: artifacts[key] for key in definition.reads})
            statement = (f"CREATE OR REPLACE TABLE {table_sql(table)} USING DELTA "
                         f"TBLPROPERTIES ('cql.durable.format' = '{TABLE_FORMAT}') AS {query}")
            computes[definition.key] = _Compute(definition, reason, table, existing is not None, statement,
                                                frozenset(key for key in definition.reads if key in computes))
        _compute(computes, run=run, writer=writer, outcomes=outcomes, submitted=submitted, on_step=on_step,
                 on_start=on_start, parallelism=parallelism)
    return _columns(run, tuple(outcomes[definition.key] for definition in planned))


def _compute(computes: Mapping[str, _Compute], *, run: SqlRunner, writer: CompletionWriter,
             outcomes: dict[str, Outcome], submitted: set[tuple[str, str, str]], on_step: StepObserver | None,
             on_start: StartObserver | None, parallelism: int) -> None:
    """RUN-CLIENT-PARALLEL-DEFINES — run ``computes`` in dependency order, up to ``parallelism`` at once.

    A definition starts once every definition in its ``after`` has finished. One thread does it all: it starts every
    definition that is ready, then polls the running ones in turn. A poll waits until that statement finishes or
    about 5 s pass, and a finished one answers at once, so the polls are the run's only wait. The first failure is
    reported, every statement still running is cancelled and reported, and the failure is raised; nothing more
    starts.
    """
    waiting = list(computes)
    running: dict[str, tuple[RunningStatement, datetime]] = {}  # in the order they were last polled
    finished: set[str] = set()
    cause = "the run stopped"

    def finish(key: str, started: datetime) -> None:
        compute = computes[key]
        completion = Completion(table=compute.table, fingerprint=compute.definition.fingerprint, computed_at=started)
        writer.submit(completion, after=tuple(computes[k].table for k in compute.after
                                              if computes[k].table in submitted))
        submitted.add(compute.table)
        outcomes[key] = Outcome(definition=key, decision="computed", reason=compute.reason,
                                result=_result(completion))
        finished.add(key)
        if on_step is not None:
            on_step(_step(compute.definition, decision="computed", reason=compute.reason, table=compute.table,
                          statement=compute.statement, started_at=started, ended_at=datetime.now(timezone.utc)))

    def failed(key: str, started: datetime, error: BaseException) -> None:
        nonlocal cause
        cause = f"{key} failed"
        running.pop(key, None)
        if on_step is not None:
            compute = computes[key]
            on_step(_step(compute.definition, decision="computed", reason=compute.reason, table=compute.table,
                          statement=compute.statement, started_at=started, ended_at=datetime.now(timezone.utc),
                          error=f"{type(error).__name__}: {error}"))

    try:
        while waiting or running:
            writer.check()
            for key in [k for k in waiting if computes[k].after <= finished][:parallelism - len(running)]:
                waiting.remove(key)
                compute = computes[key]
                if compute.replaces:
                    writer.invalidate(compute.table)
                if on_start is not None:
                    on_start(key)
                started = datetime.now(timezone.utc)
                try:
                    running[key] = (start_statement(run, compute.statement), started)
                except Exception as error:
                    failed(key, started, error)
                    raise
            if not running:
                continue
            key = next(iter(running))
            statement, started = running.pop(key)
            try:
                done = statement.poll()
            except Exception as error:
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
                on_step(_step(compute.definition, decision="cancelled", reason=compute.reason, table=compute.table,
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
    """RUN-CLIENT-PORTABLE-HANDLE — self-contained descriptors; reads address current result tables.

    RUN-CLIENT-RESULT-IDENTITY — every result's table is named for its definition's fingerprint, in the
    destination or a result scope it was reused from, so a reader checks each name without recomputing it.
    """

    session_id: str
    selection_id: str
    destination: tuple[str, str]
    targets: Mapping[str, str]
    definitions: tuple[Definition, ...]
    outcomes: tuple[Outcome, ...]
    published_at: datetime
    computed_since: datetime | None = None
    purpose: str = "private"

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        _text(self.selection_id, "selection_id")
        _destination(self.destination)
        if self.purpose not in ("private", "published"):
            raise ValueError("selection purpose must be private or published")
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
                    or result.fingerprint != definition.fingerprint or artifact.table[2] != table_name(definition)):
                raise ValueError("selection identity or descriptors do not match")
            if self.computed_since is not None and result.completed_at < self.computed_since:
                raise ValueError("selection contains a result computed before computed_since")
            if artifact.table in tables:
                raise ValueError("selection definitions must have independent result tables")
            tables.add(artifact.table)

    @classmethod
    def completed(cls, session_id: str, destination: tuple[str, str], targets: Mapping[str, str],
                  definitions: Sequence[Definition], outcomes: tuple[Outcome, ...], *,
                  computed_since: datetime | None = None, purpose: str = "private") -> CatalogSelection:
        """RUN-CLIENT-PORTABLE-HANDLE — return a handle only after all checkpoint writes finish.

        The selection ID moves whenever any result in it is recomputed, here or in a result scope."""
        selection_id = _hash({"destination": list(destination), "targets": dict(targets),
                              "results": {o.definition: [list(o.result.artifact.table),
                                                         o.result.completed_at.isoformat()] for o in outcomes}})
        return cls(session_id, selection_id, destination, targets,
                   tuple(_selected(definitions, tuple(targets.values()))), outcomes,
                   datetime.now(timezone.utc), computed_since, purpose)

    def to_dict(self) -> dict:
        """RUN-CLIENT-PORTABLE-HANDLE — no tokens, SQL, or lookup-table foreign keys."""
        return {"format": FORMAT, "session_id": self.session_id, "selection_id": self.selection_id,
                "destination": list(self.destination), "targets": dict(self.targets),
                "definitions": [asdict(d) for d in self.definitions],
                "outcomes": [{"definition": o.definition, "decision": o.decision, "reason": o.reason,
                              "result": {"fingerprint": o.result.fingerprint,
                                         "artifact": asdict(o.result.artifact),
                                         "completed_at": o.result.completed_at.isoformat()}}
                             for o in self.outcomes],
                "published_at": self.published_at.isoformat(), "purpose": self.purpose,
                "computed_since": self.computed_since.isoformat() if self.computed_since else None}

    def to_json(self) -> str:
        """RUN-CLIENT-PORTABLE-HANDLE — canonical, portable serialization."""
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
        """RUN-CLIENT-PORTABLE-HANDLE — validate locally without recompiling or reading a metadata table."""
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
