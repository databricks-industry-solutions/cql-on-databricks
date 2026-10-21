"""RUN-CLIENT-DURABLE — caller-owned durable bulk execution, independent of SDP and services."""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal

from cql_databricks.compiler.cql import CqlQuery
from cql_databricks.data_bindings.runner import SqlRunner, spark_runner
from cql_databricks.data_bindings.tags import parse_scopes

from ._catalog_reader import CatalogReader
from ._catalog_completion import Completion, search
from ._catalog_runtime import CatalogSelection, StepObserver, execute as _execute, request_identity, table_name
from ._durable import Outcome, ResultsUnavailable, _destination, _selected, _text, _utc
from ._durable_delta import table_sql
from ._durable_plan import PreparedPlan
from .calculation import PreparedCalculation
from .lifecycle import check_capacity
from .private import PrivateDestination, checked_destination


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


def materialize(query: CqlQuery | PreparedCalculation, *targets: str, spark=None, run: SqlRunner | None = None,
                destination: tuple[str, str] | PrivateDestination,
                mode: Literal["skip_if_computed", "recompute"] = "skip_if_computed",
                computed_since: datetime | None = None,
                session_id: str | None = None,
                purpose: Literal["private", "published"] = "private",
                capacity: int | None = None,
                on_step: StepObserver | None = None,
                catalog_scopes: Iterable[str] | None = None) -> MaterializedResults:
    """RUN-CLIENT-DURABLE, RUN-CLIENT-REUSE — physically compute or reuse the requested closure.

    An explicit result schema must already exist; ``ensure_private_destination`` provisions one
    automatically and returns a destination with ownership checks against accidental modification of
    unrelated objects. Source-data, terminology-content and runtime changes are the caller's concern.
    RUN-CLIENT-RESULT-IDENTITY — the compiler fingerprints each define from its SQL, parameter values, upstream
    fingerprints and bound data source; the fingerprint names its table. No source-change tracking.

    Repeating this call from a new session resolves persisted completions. The caller must restore
    the same CQL closure, parameters and bindings. Normal ``query.exec()`` remains unchanged.
    Supply exactly one of ``spark`` or a parameterized SQL ``run`` (for example ``warehouse_runner``).
    Retain an explicit ``computed_since`` cutoff in the request to resume a failed refresh.
    Recompute without a cutoff starts a new refresh; no hidden index saves request context.
    An explicit cutoff refreshes only results older than it. It is not a source-data snapshot.
    APP-RUN-LOG — ``on_step`` receives what was done for each definition (``StepRecord``), as it is done.

    CLIENT-DISCOVERY-SCOPE — a define the destination holds no reusable result for reuses one from the
    ``catalog.schema`` entries of ``catalog_scopes``, earliest first, under the same table name, so the same
    calculation. The scopes are the trust boundary: no table-owner or publisher check (DEC-RESULT-SCOPE).
    Whole-catalog entries and ``*`` are never read for results. Unset, ``catalog_scopes`` is the list
    ``connect(catalog_scopes=...)`` gave the query's client, else ``CQL_DATABRICKS_CATALOG_SCOPES``. Writes go only
    to ``destination``.
    """
    return _run(query, targets, spark=spark, run=run, destination=destination,
                mode=mode, session_id=session_id, purpose=purpose,
                capacity=capacity, computed_since=computed_since, on_step=on_step, catalog_scopes=catalog_scopes)


def resolve_results(query: CqlQuery | PreparedCalculation, *targets: str, spark=None, run: SqlRunner | None = None,
                    destination: tuple[str, str] | PrivateDestination,
                    session_id: str | None = None, computed_since: datetime | None = None,
                    purpose: Literal["private", "published"] = "published",
                    catalog_scopes: Iterable[str] | None = None) -> MaterializedResults:
    """RUN-CLIENT-PUBLISHED — read completed results as the reader, without any writes.

    Every metadata and artifact read uses the actual caller's grants. A shared destination must grant write
    access only to its publishers. Missing results fail; readers never calculate on a miss.
    ``catalog_scopes`` adds result scopes to read from, as for ``materialize``.
    """
    return _run(query, targets, spark=spark, run=run, destination=destination,
                mode="skip_if_computed", session_id=session_id, purpose=purpose, capacity=None,
                computed_since=computed_since, read_only=True, catalog_scopes=catalog_scopes)


def open_result_set(query: CqlQuery | PreparedCalculation, *targets: str, **options) -> MaterializedResults:
    """RUN-CLIENT-REQUEST-REOPEN — reconstruct the calculation and discover current completion tags.

    A session ID or serialized plan alone cannot reopen results. Supply the same query/prepared
    request, targets and destination as for ``resolve_results``. This never writes.
    """
    if not callable(getattr(query, "compile_pipeline", None)):
        raise ValueError("reopening requires the calculation request, not a session ID or serialized plan")
    return resolve_results(query, *targets, **options)


def archive_session(session_id: str, *, destination: tuple[str, str] | PrivateDestination,
                    spark=None, run: SqlRunner | None = None) -> None:
    """RUN-CLIENT-PORTABLE-HANDLE — a portable reference is released by discarding it in its owner."""
    raise ValueError("portable handles have no server-side session to archive; discard the client reference")


def search_results(*, destination: tuple[str, str] | PrivateDestination, spark=None, run: SqlRunner | None = None,
                   fingerprint: str | None = None, completed_after: datetime | None = None,
                   limit: int = 100) -> tuple[Completion, ...]:
    """RUN-CLIENT-RESULT-IDENTITY — list completed results in ``destination``, optionally one ``fingerprint``.

    Results are candidates, not a verified complete result set. Use ``resolve_results`` to read a calculation's
    results. ``completed_after`` filters successful computation start time, not data freshness or source-read time.
    """
    if (spark is None) == (run is None):
        raise ValueError("supply exactly one of spark or run")
    execute = spark_runner(spark) if spark is not None else run
    destination, _ = checked_destination(destination, execute)
    if (not isinstance(destination, tuple) or len(destination) != 2
            or any(not isinstance(part, str) or not part.strip() for part in destination)):
        raise ValueError("destination must be an explicit (catalog, schema) tuple")
    return search(execute, destination, fingerprint=fingerprint, computed_since=completed_after, limit=limit)


def _result_sources(spark, catalog_scopes, destination: tuple[str, str], query=None) -> tuple[tuple[str, str], ...]:
    """CLIENT-DISCOVERY-SCOPE, DEC-RESULT-SCOPE — the ``catalog.schema`` scopes results are read from, in order,
    after the destination. A whole catalog is never a result scope: a table owner can hand a table to anyone."""
    from cql_databricks.compiler.zeroconfig import session_catalog_scopes

    entries = session_catalog_scopes(spark, catalog_scopes, query=query)
    if entries is None:
        return ()
    return tuple(source for source in dict.fromkeys((scope.catalog, scope.schema) for scope in parse_scopes(entries)
                                                     if scope.schema is not None) if source != destination)


def _run(query, targets, *, spark, run, destination,
         mode, session_id, purpose, capacity, computed_since, read_only=False, on_step=None,
         catalog_scopes=None):
    if (spark is None) == (run is None):
        raise ValueError("supply exactly one of spark or run")
    if capacity is not None and (type(capacity) is not int or capacity < 1):
        raise ValueError("capacity must be a positive number of result tables")
    if session_id is not None:
        _text(session_id, "session_id")
    if computed_since is not None:
        computed_since = _utc(computed_since, "computed_since")
        if computed_since > datetime.now(timezone.utc):
            raise ValueError("computed_since cannot be in the future")
    execute = spark_runner(spark) if spark is not None else run
    destination, private = checked_destination(destination, execute)
    _destination(destination)
    sources = _result_sources(spark, catalog_scopes, destination, query)
    if private is not None and purpose == "published":
        raise PermissionError("automatic private destinations cannot be used for Job publishing")
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
    if session_id is None:
        session_id = request_identity(prepared.definitions, selected, destination)
    if purpose not in ("private", "published"):
        raise ValueError("purpose must be private or published")
    if mode == "recompute" and computed_since is None:
        computed_since = datetime.now(timezone.utc)
    try:
        if capacity is not None:
            check_capacity(execute, destination, {table_name(definition) for definition in planned}, capacity)
        outcomes = _execute(prepared, tuple(selected.values()), destination=destination, run=execute, mode=mode,
                            computed_since=computed_since, read_only=read_only, on_step=on_step, sources=sources)
        selection = CatalogSelection.completed(session_id, destination, selected, prepared.definitions, outcomes,
                                               computed_since=computed_since, purpose=purpose)
        backend = CatalogReader(destination, execute, spark, sources)
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
