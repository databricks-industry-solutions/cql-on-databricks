"""RUN-CLIENT-DURABLE — caller-owned durable bulk execution, independent of SDP and services."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal

from cql_databricks.compiler.cql import CqlQuery
from cql_databricks.resolvers.interfaces import ResultResolver, ResultStore
from cql_databricks.data_bindings.runner import SqlRunner, spark_runner

from ._catalog_reader import CatalogReader
from ._catalog_runtime import (PARALLEL_DEFINES, CatalogSelection, PlanObserver, StartObserver, StepObserver,
                               execute as _execute, request_identity)
from ._durable import Outcome, ResultsUnavailable, _selected, _text, _utc
from ._durable_delta import table_sql
from ._durable_plan import PreparedPlan
from .calculation import PreparedCalculation


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterializedResults:
    """RUN-CLIENT-RESUME — named completed outputs and a decision for every required definition."""

    results: Mapping[str, Outcome]
    outcomes: tuple[Outcome, ...]
    _backend: CatalogReader = field(repr=False, compare=False)
    selection: CatalogSelection
    request: dict | None = None

    def read(self, name: str):
        """RUN-CLIENT-CATALOG-CHECKPOINTS — current Spark frame or warehouse rows under caller grants."""
        sql = self.sql(name)
        if self._backend.spark is not None:
            return self._backend.spark.sql(sql)
        return self._backend.run(sql)

    def sql(self, name: str) -> str:
        """RUN-CLIENT-OPTIMISTIC — validate local descriptors and expose the current table read."""
        artifact = self.results[name].result.artifact
        if not self._backend.usable(artifact):
            raise ResultsUnavailable(f"completed result {name!r} is no longer available")
        return f"SELECT * FROM {table_sql(artifact.table)}"


def materialize(query: CqlQuery | PreparedCalculation, *targets: str, results: ResultStore, spark=None,
                run: SqlRunner | None = None,
                mode: Literal["skip_if_computed", "recompute"] = "skip_if_computed",
                computed_since: datetime | None = None,
                session_id: str | None = None,
                on_step: StepObserver | None = None,
                on_plan: PlanObserver | None = None,
                on_start: StartObserver | None = None,
                parallelism: int = PARALLEL_DEFINES) -> MaterializedResults:
    """RUN-CLIENT-DURABLE, RUN-CLIENT-REUSE — physically compute or reuse the requested closure.

    CLIENT-RESULT-RESOLVER — every result is found and written through ``results``, a :class:`ResultStore`: it finds
    the caller's own results, then the environment's official ones, and writes the rest into its destination. A store
    over an environment's ``results`` takes only a calculation of the environment's own content, and refuses anything
    else before it computes (`RUN-CLIENT-DRAFT-RESULTS`). Source-data and runtime changes are the caller's concern.
    RUN-CLIENT-RESULT-IDENTITY — the compiler fingerprints each define from its SQL, parameter values, upstream
    fingerprints and bound data source; the fingerprint names its table. No source-change tracking.

    Repeating this call from a new session resolves persisted completions. The caller must restore
    the same CQL closure, parameters and bindings. Normal ``query.exec()`` remains unchanged.
    Supply exactly one of ``spark`` or a parameterized SQL ``run`` (for example ``warehouse_session``), acting as the
    same identity as ``results``.
    Retain an explicit ``computed_since`` cutoff in the request to resume a failed refresh.
    Recompute without a cutoff starts a new refresh; no hidden index saves request context.
    An explicit cutoff refreshes only results older than it. It is not a source-data snapshot.
    APP-RUN-LOG — ``on_step`` receives what was done for each definition (``StepRecord``), as it is done.
    RUN-PROGRESS-TASKS — ``on_plan`` receives the keys of every definition ``on_step`` will report, once, first;
    ``on_start`` receives a key when its statement starts.
    RUN-CLIENT-PARALLEL-DEFINES — up to ``parallelism`` define statements run at once, each as soon as the defines
    it reads are done; on a warehouse session they share its one session. The first failure cancels the rest.
    """
    if not isinstance(results, ResultStore):
        raise TypeError(f"materialize writes through a ResultStore, not {type(results).__name__}; read with "
                        "resolve_results")
    return _run(query, targets, spark=spark, run=run, results=results, mode=mode, session_id=session_id,
                computed_since=computed_since, on_step=on_step, on_plan=on_plan, on_start=on_start,
                parallelism=parallelism)


def resolve_results(query: CqlQuery | PreparedCalculation, *targets: str, results: ResultResolver, spark=None,
                    run: SqlRunner | None = None, session_id: str | None = None,
                    computed_since: datetime | None = None) -> MaterializedResults:
    """RUN-CLIENT-REQUEST-REOPEN — read completed results through ``results``, without any writes.

    Every metadata and artifact read uses the actual caller's grants. ``results`` finds the caller's own results, then
    the official ones (`RUN-CLIENT-RESULT-OWNERSHIP`). Missing results fail; readers never calculate on a miss.
    """
    return _run(query, targets, spark=spark, run=run, results=results, mode="skip_if_computed",
                session_id=session_id, computed_since=computed_since, read_only=True)


def open_result_set(query: CqlQuery | PreparedCalculation, *targets: str, **options) -> MaterializedResults:
    """RUN-CLIENT-REQUEST-REOPEN — reconstruct the calculation and discover current completion tags.

    A session ID or serialized plan alone cannot reopen results. Supply the same query/prepared
    request, targets and result resolver as for ``resolve_results``. This never writes.
    """
    if not callable(getattr(query, "compile_pipeline", None)):
        raise ValueError("reopening requires the calculation request, not a session ID or serialized plan")
    return resolve_results(query, *targets, **options)


def archive_session(session_id: str, *, destination: tuple[str, str],
                    spark=None, run: SqlRunner | None = None) -> None:
    """RUN-CLIENT-PORTABLE-HANDLE — a portable reference is released by discarding it in its owner."""
    raise ValueError("portable handles have no server-side session to archive; discard the client reference")


def _run(query, targets, *, spark, run, results, mode, session_id, computed_since, read_only=False, on_step=None,
         on_plan=None, on_start=None, parallelism=PARALLEL_DEFINES):
    if (spark is None) == (run is None):
        raise ValueError("supply exactly one of spark or run")
    if session_id is not None:
        _text(session_id, "session_id")
    if computed_since is not None:
        computed_since = _utc(computed_since, "computed_since")
        if computed_since > datetime.now(timezone.utc):
            raise ValueError("computed_since cannot be in the future")
    execute = spark_runner(spark) if spark is not None else run
    if not read_only:
        results.admit(query)
    if mode not in ("skip_if_computed", "recompute"):
        raise ValueError(f"unsupported reuse mode {mode!r}")
    if not targets or any(not isinstance(target, str) or not target.strip() for target in targets):
        raise ValueError("materialize requires explicit definition targets")
    if len(set(targets)) != len(targets):
        raise ValueError("duplicate materialization targets")
    compiled = query.compile_pipeline(durable=True)
    prepared = PreparedPlan(compiled)
    selected = {}
    for target in targets:
        matches = [recipe.name for recipe in compiled.datasets
                   if recipe.define == target or f"{recipe.library}.{recipe.define}" == target]
        if len(matches) != 1:
            raise ValueError(f"target {target!r} must identify exactly one materialized definition")
        selected[target] = matches[0]
    planned = _selected(prepared.definitions, tuple(selected.values()))
    if read_only and any(not definition.reusable for definition in planned):
        raise ResultsUnavailable("request contains non-reusable definitions; open an explicit result set to inspect it")
    destination = tuple(results.destination)
    if session_id is None:
        session_id = request_identity(prepared.definitions, selected, destination)
    if mode == "recompute" and computed_since is None:
        computed_since = datetime.now(timezone.utc)
    try:
        outcomes = _execute(prepared, tuple(selected.values()), results=results, run=execute, mode=mode,
                            computed_since=computed_since, read_only=read_only, on_step=on_step, on_plan=on_plan,
                            on_start=on_start, parallelism=parallelism)
        selection = CatalogSelection.completed(session_id, destination, selected, prepared.definitions, outcomes,
                                               computed_since=computed_since)
        backend = CatalogReader(results, execute, spark)
    except BaseException as error:
        if computed_since is not None:
            _failure_note(error, f"Retry with computed_since={computed_since.isoformat()} to reuse successful "
                                 "checkpoints; the cutoff is not saved in a server-side index.")
        raise
    by_key = {outcome.definition: outcome for outcome in outcomes}
    return MaterializedResults(MappingProxyType({name: by_key[key] for name, key in selected.items()}),
                               outcomes, backend, selection, getattr(query, "request", None))


def _failure_note(error: BaseException, message: str) -> None:
    """BUILD-CLIENT-COMPAT — retain/log recovery context without Python 3.11-only add_note().

    Python 3.10 stores the same exception attribute but does not render it in tracebacks, so log
    the context too. Do not replace the original exception or interrupt work-token release.
    """
    error.__notes__ = [*getattr(error, "__notes__", ()), message]
    log.warning("%s", message)
