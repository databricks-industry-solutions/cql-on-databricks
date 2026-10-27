"""CONTENT-RESOLVER — the one seam every resolver read goes through: a SQL runner.

A runner takes a statement with named parameter markers (``:name``) and returns rows as dicts keyed by
column name. Both Spark and Databricks SQL accept the same markers, so every query in this package is
written once and runs unchanged in a notebook (``spark_runner``) or an App (``warehouse_runner``).
That is what makes the resolver one implementation for every caller (``DEC-CONTENT-LOADERS-AND-RESOLVER``).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse


class SqlRunner(Protocol):
    def __call__(self, statement: str, params: Mapping[str, str] | None = None) -> list[dict[str, Any]]: ...


class ResolverError(RuntimeError):
    """Every resolver failure. Raised, never swallowed: a missing source is not an empty one."""


def spark_runner(spark) -> SqlRunner:
    """A runner over a Spark session (``spark.sql`` with named parameters)."""
    def run(statement: str, params: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        df = spark.sql(statement, args=dict(params)) if params else spark.sql(statement)
        return [row.asDict(recursive=True) for row in df.collect()]
    return run


@dataclass(frozen=True)
class ExecutedStatement:
    """APP-RUN-LOG — one warehouse statement once it settled: its ID, its final state and its wall clock.

    ``statement`` is the SQL text the runner sent, so a caller can tell its own statements apart.
    """
    statement: str
    statement_id: str
    state: str | None
    started_at: datetime
    ended_at: datetime


#: APP-RUN-LOG — receives every statement a ``warehouse_runner`` sent, on the thread that ran it, before
#: ``run`` returns or raises. It must not raise: the statement's own outcome is what ``run`` reports.
StatementObserver = Callable[[ExecutedStatement], None]


#: The Statement Execution API caps one wait at 50s; polling continues to this total budget.
_POLL_SECONDS = 2.0
_POLL_BUDGET_SECONDS = 600.0


def warehouse_runner(client, warehouse_id: str, *,
                     fetch: Callable[[str], list[list[Any]]] | None = None,
                     observer: StatementObserver | None = None) -> SqlRunner:
    """A runner over a SQL warehouse, through the Statement Execution API.

    Results come back as EXTERNAL_LINKS, not inline. The corpus read carries every library's CQL in one
    result, and inline results are capped at 25 MiB; an inline read would fail at exactly the size the
    corpus is growing toward. ``fetch`` downloads one link's JSON rows (injected in tests).

    APP-RUN-LOG — ``observer``, when given, receives each statement the warehouse accepted, with its ID,
    once it succeeds, fails or runs out of polling budget. A statement the API refused has no ID and is
    not observed; its error is raised as before.
    """
    if not warehouse_id:
        raise ResolverError("warehouse_runner needs a SQL warehouse id; none is configured")
    from databricks.sdk.service.sql import Disposition, Format, StatementParameterListItem

    def _download(url: str) -> list[list[Any]]:
        import requests
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        return response.json()

    get_rows = fetch or _download

    def run(statement: str, params: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        api = client.statement_execution
        started = datetime.now(timezone.utc)
        response = api.execute_statement(
            warehouse_id=warehouse_id, statement=statement, wait_timeout="30s",
            disposition=Disposition.EXTERNAL_LINKS, format=Format.JSON_ARRAY,
            parameters=[StatementParameterListItem(name=k, value=v) for k, v in (params or {}).items()])
        try:
            response = _settle(api, response, statement)
        finally:
            if observer is not None:
                observer(ExecutedStatement(statement=statement, statement_id=response.statement_id,
                                           state=_state(response), started_at=started,
                                           ended_at=datetime.now(timezone.utc)))
        state = _state(response)
        if state != "SUCCEEDED":
            error = response.status.error if response.status else None
            raise ResolverError(f"statement {state}: {error.message if error else 'unknown'}: "
                                f"{statement[:120]}")
        columns = [c.name for c in response.manifest.schema.columns]
        rows: list[dict[str, Any]] = []
        chunk = response.result
        while chunk is not None:
            next_index = None
            for link in chunk.external_links or []:
                rows.extend(dict(zip(columns, values)) for values in get_rows(link.external_link))
                next_index = link.next_chunk_index
            chunk = (api.get_statement_result_chunk_n(response.statement_id, next_index)
                     if next_index is not None else None)
        return rows

    return run


class WarehouseSession:
    """RUN-CLIENT-SESSION-REUSE — a runner that gives each thread of a run its own SQL session.

    The runtime functions' sandbox starts once per session, so a run that keeps its sessions pays that start
    once per thread, not once per statement. The run's own statements are on one thread, so they share
    one session; the completion writer's tag statements run on its worker threads
    (``RUN-CLIENT-ASYNC-CHECKPOINTS``), each in its own session, so they never wait on the run's.

    Build it with :func:`warehouse_session`; use it as a context manager, or call :meth:`close`, at the end
    of the run. A thread's session opens on its first statement, so building one connects nothing. A
    statement after ``close`` fails.

    Rows come back with typed values (``int``, ``Decimal``, ``datetime`` …), as ``spark_runner``'s do, not
    as the JSON strings of ``warehouse_runner``.

    APP-RUN-LOG — ``observer``, when given, receives each statement the warehouse accepted, with its ID,
    once it succeeds or fails, as ``warehouse_runner``'s does.
    """

    def __init__(self, connect: Callable[[], Any], observer: StatementObserver | None = None) -> None:
        self._connect = connect
        self._observer = observer
        self._local = threading.local()
        self._connections: list[Any] = []
        self._closed = False

    def __call__(self, statement: str, params: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        from databricks.sql.exc import Error

        if self._closed:
            raise ResolverError(f"the warehouse session is closed: {statement[:120]}")
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = self._connect()
            self._connections.append(connection)
        started = datetime.now(timezone.utc)
        with connection.cursor() as cursor:
            state = "FAILED"
            try:
                cursor.execute(statement, parameters=dict(params) if params else None)
                columns = [column[0] for column in cursor.description or ()]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()] if columns else []
                state = "SUCCEEDED"
            except Error as error:
                raise ResolverError(f"statement {state}: {error}: {statement[:120]}") from error
            finally:
                if self._observer is not None and cursor.query_id is not None:
                    self._observer(ExecutedStatement(statement=statement, statement_id=cursor.query_id,
                                                     state=state, started_at=started,
                                                     ended_at=datetime.now(timezone.utc)))
        return rows

    def close(self) -> None:
        """End every thread's session. Call it once the run's threads are done; closing twice is harmless."""
        self._closed = True
        connections, self._connections = self._connections, []
        for connection in connections:
            connection.close()

    def __enter__(self) -> WarehouseSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def warehouse_session(client, warehouse_id: str, *,
                      observer: StatementObserver | None = None) -> WarehouseSession:
    """RUN-CLIENT-SESSION-REUSE — a :class:`WarehouseSession` over ``warehouse_id`` as ``client``'s identity.

    A statement still running after the same budget ``warehouse_runner`` polls for fails
    (``STATEMENT_TIMEOUT``).
    """
    if not warehouse_id:
        raise ResolverError("warehouse_session needs a SQL warehouse id; none is configured")

    def connect():
        from databricks import sql

        return sql.connect(server_hostname=urlparse(client.config.host).netloc,
                           http_path=f"/sql/1.0/warehouses/{warehouse_id}",
                           credentials_provider=lambda: client.config.authenticate,
                           session_configuration={"STATEMENT_TIMEOUT": str(int(_POLL_BUDGET_SECONDS))})

    return WarehouseSession(connect, observer)


def _settle(api, response, statement: str):
    """Poll until the statement leaves PENDING/RUNNING; raise once the polling budget is spent."""
    waited = 0.0
    while _state(response) in ("PENDING", "RUNNING"):
        if waited >= _POLL_BUDGET_SECONDS:
            raise ResolverError(f"statement did not finish within {int(waited)}s: {statement[:120]}")
        time.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS
        response = api.get_statement(response.statement_id)
    return response


def _state(response) -> str | None:
    return response.status.state.value if response.status and response.status.state else None
