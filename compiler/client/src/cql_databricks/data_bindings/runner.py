"""The one seam every resolver read goes through: a SQL runner.

A runner takes a statement with named parameter markers (``:name``) and returns rows as dicts keyed by
column name. Both Spark and Databricks SQL accept the same markers, so every query in this package is
written once and runs unchanged in a notebook (``spark_runner``) or on a SQL warehouse (``warehouse_session``).
That is what makes the resolver one implementation for every caller (``DEC-CONTENT-LOADERS-AND-RESOLVER``).

RUN-CLIENT-WAREHOUSE-CONNECTOR — every statement sent to a SQL warehouse goes through :class:`WarehouseSession`.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.parse import urlparse

log = logging.getLogger(__name__)


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


#: APP-RUN-LOG — receives every statement a warehouse session sent, on the thread that ran it, before the
#: call returns or raises. It must not raise: the statement's own outcome is what the call reports.
StatementObserver = Callable[[ExecutedStatement], None]


#: RUN-CLIENT-WAREHOUSE-CONNECTOR — a statement still running after this long fails (``STATEMENT_TIMEOUT``).
STATEMENT_TIMEOUT_SECONDS = 1800

#: RUN-CLIENT-SESSION-REUSE — a held connection idle longer than this is checked with ``SELECT 1`` before
#: its next statement: the warehouse may have ended its session meanwhile (idle expiry, a stop).
CHECK_AFTER_IDLE_SECONDS = 60.0


class _Connections:
    """RUN-CLIENT-SESSION-REUSE — a session's connections: one main connection, and side connections.

    The connector's connections are not thread-safe (DB-API ``threadsafety = 1``), so the main one serves one
    statement at a time, whichever thread sends it. It is the one that keeps the runtime functions' sandbox
    warm. Side connections serve statements off the run's thread (``RUN-CLIENT-ASYNC-CHECKPOINTS``): each is
    borrowed for one statement and returned, so those statements never wait on the main one.
    """

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect
        self._main_lock = threading.Lock()
        self._side_lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._main: Any = None
        self._used_at = 0.0
        self._idle: list[tuple[Any, float]] = []
        self._open: list[Any] = []
        self._closed = False

    @contextmanager
    def main(self, statement: str) -> Iterator[Any]:
        with self._main_lock:
            self._refuse_if_closed(statement)
            if self._main is not None and time.monotonic() - self._used_at > CHECK_AFTER_IDLE_SECONDS:
                self._check_main()
            if self._main is None:
                self._main = self._opened()
            try:
                yield self._main
            except BaseException:
                # A failure may mean the session ended under it: check before the next statement.
                self._used_at = float("-inf")
                raise
            self._used_at = time.monotonic()

    @contextmanager
    def side(self, statement: str) -> Iterator[Any]:
        stale, connection = [], None
        with self._side_lock:
            self._refuse_if_closed(statement)
            while self._idle and connection is None:
                candidate, used_at = self._idle.pop()
                # A side connection keeps no warm sandbox, so a new one is cheaper than checking an old one.
                if time.monotonic() - used_at > CHECK_AFTER_IDLE_SECONDS:
                    stale.append(candidate)
                else:
                    connection = candidate
        for candidate in stale:
            self._discard(candidate)
        connection = connection or self._opened()
        try:
            yield connection
        finally:
            with self._side_lock:
                closed = self._closed
                if not closed:
                    self._idle.append((connection, time.monotonic()))
            if closed:
                self._discard(connection)

    @contextmanager
    def locked(self, statement: str) -> Iterator[None]:
        """The main connection's lock, for a short call on a statement already started on it."""
        with self._main_lock:
            self._refuse_if_closed(statement)
            yield
            self._used_at = time.monotonic()

    def close(self) -> None:
        with self._main_lock, self._side_lock, self._open_lock:
            self._closed = True
            connections, self._open, self._idle, self._main = self._open, [], [], None
        for connection in connections:
            connection.close()

    def _refuse_if_closed(self, statement: str) -> None:
        if self._closed:
            raise ResolverError(f"the warehouse session is closed: {statement[:120]}")

    def _opened(self) -> Any:
        connection = self._connect()
        with self._open_lock:
            self._open.append(connection)
        return connection

    def _check_main(self) -> None:
        try:
            with self._main.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()
        except Exception as error:  # noqa: BLE001 — the caller's statement has not run; it opens a new session
            log.info("RUN-CLIENT-SESSION-REUSE: the held warehouse session ended (%s); opening a new one", error)
            self._discard(self._main)
            self._main = None

    def _discard(self, connection: Any) -> None:
        with self._open_lock:
            if connection in self._open:
                self._open.remove(connection)
        try:
            connection.close()
        except Exception as error:  # noqa: BLE001 — closing an ended session has nothing left to report
            log.info("RUN-CLIENT-SESSION-REUSE: closing an ended warehouse connection failed: %s", error)


class SessionRunner:
    """RUN-CLIENT-WAREHOUSE-CONNECTOR — a runner over a :class:`WarehouseSession`'s connections.

    ``__call__`` is a :class:`SqlRunner`: rows as dicts. :meth:`rows` is the results' ``Execute``: rows as tuples,
    by position, or None for no rows; a dict would drop a repeated column name. Rows come back with typed
    values (``int``, ``Decimal``, ``datetime`` …), as ``spark_runner``'s do. ARRAY, STRUCT and MAP values come
    back as JSON text.

    Every failure raises :class:`ResolverError` with the statement's text.

    APP-RUN-LOG — ``observer``, when given, receives each statement the warehouse accepted, with its ID, once it
    succeeds or fails.
    """

    def __init__(self, connections: _Connections, observer: StatementObserver | None = None, *,
                 side: bool = False) -> None:
        self._connections = connections
        self._observer = observer
        self._side = side

    def __call__(self, statement: str, params: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        columns, rows = self._execute(statement, params)
        return [dict(zip(columns, row)) for row in rows]

    def rows(self, statement: str) -> list[tuple] | None:
        _, rows = self._execute(statement, None)
        return [tuple(row) for row in rows] or None

    @property
    def background(self) -> SessionRunner:
        """RUN-CLIENT-ASYNC-CHECKPOINTS — this runner on side connections, for statements off the run's thread."""
        return SessionRunner(self._connections, self._observer, side=True)

    def start(self, statement: str) -> RunningStatement:
        """RUN-CLIENT-PARALLEL-DEFINES — submit ``statement`` on the main connection and return without waiting.

        For statements whose rows are not read (a define's ``CREATE OR REPLACE TABLE``). Several started
        statements run side by side in the one session, so they share its warm sandbox.
        """
        return _Started(self, statement)

    def _execute(self, statement: str, params: Mapping[str, str] | None) -> tuple[list[str], list]:
        connections = self._connections.side(statement) if self._side else self._connections.main(statement)
        try:
            with connections as connection, connection.cursor() as cursor:
                started = datetime.now(timezone.utc)
                state = "FAILED"
                try:
                    cursor.execute(statement, parameters=dict(params) if params else None)
                    columns = [column[0] for column in cursor.description or ()]
                    rows = cursor.fetchall() if columns else []
                    state = "SUCCEEDED"
                finally:
                    if self._observer is not None and cursor.query_id is not None:
                        self._observer(ExecutedStatement(statement=statement, statement_id=cursor.query_id,
                                                         state=state, started_at=started,
                                                         ended_at=datetime.now(timezone.utc)))
        except ResolverError:
            raise
        except Exception as error:  # the connector's errors, a failed download, a failed connect: all reported
            raise ResolverError(f"statement FAILED: {error}: {statement[:120]}") from error
        return columns, rows


class RunningStatement(Protocol):
    """RUN-CLIENT-PARALLEL-DEFINES — a statement started and not yet awaited."""

    def poll(self) -> bool:
        """True once the statement succeeded, False while it runs; raises :class:`ResolverError` if it failed.

        May wait up to about 5 s for a running statement: the warehouse answers when its state changes."""

    def cancel(self) -> None:
        """Stop the statement if it is still running. Never raises."""


class _Started:
    """RUN-CLIENT-PARALLEL-DEFINES — a statement submitted with ``execute_async`` on a session's main connection.

    ``poll`` asks the warehouse for its state on the same connection. The warehouse holds that request until the
    state changes or about 5 s pass, so a poll is also the wait: it returns as soon as the statement finishes,
    and a statement already finished answers at once. The status call raises the warehouse's own message for a
    failed statement. APP-RUN-LOG — the observer receives the statement once, when it succeeds, fails or is
    cancelled.
    """

    def __init__(self, runner: SessionRunner, statement: str) -> None:
        self._connections, self._observer, self.statement = runner._connections, runner._observer, statement
        self._started = datetime.now(timezone.utc)
        self._cursor: Any = None
        self._settled = False
        try:
            with self._connections.main(statement) as connection:
                self._cursor = connection.cursor()
                self._cursor.execute_async(statement)
        except Exception as error:
            self._settle("FAILED")
            if isinstance(error, ResolverError):
                raise
            raise ResolverError(f"statement FAILED: {error}: {statement[:120]}") from error

    def poll(self) -> bool:
        from databricks.sql.backend.types import CommandState

        try:
            with self._connections.locked(self.statement):
                state = self._cursor.get_query_state()
        except Exception as error:
            self._settle("FAILED")
            if isinstance(error, ResolverError):
                raise
            raise ResolverError(f"statement FAILED: {error}: {self.statement[:120]}") from error
        if state in (CommandState.PENDING, CommandState.RUNNING):
            return False
        self._settle(state.name)
        if state != CommandState.SUCCEEDED:
            raise ResolverError(f"statement {state.name}: {self.statement[:120]}")
        return True

    def cancel(self) -> None:
        if self._settled:
            return
        try:
            with self._connections.locked(self.statement):
                self._cursor.cancel()
        except Exception as error:  # noqa: BLE001 — cancelling is best effort; the run already failed
            log.info("RUN-CLIENT-PARALLEL-DEFINES: cancelling a statement failed: %s", error)
        self._settle("CANCELLED")

    def _settle(self, state: str) -> None:
        if self._settled:
            return
        self._settled = True
        query_id = self._cursor.query_id if self._cursor is not None else None
        if self._cursor is not None:
            try:
                with self._connections.locked(self.statement):
                    self._cursor.close()
            except Exception as error:  # noqa: BLE001 — the statement's own outcome is what is reported
                log.info("RUN-CLIENT-PARALLEL-DEFINES: closing a statement's cursor failed: %s", error)
        if self._observer is not None and query_id is not None:
            self._observer(ExecutedStatement(statement=self.statement, statement_id=query_id, state=state,
                                             started_at=self._started, ended_at=datetime.now(timezone.utc)))


class _Ran:
    """RUN-CLIENT-PARALLEL-DEFINES — a runner that cannot start a statement and return runs it to the end now."""

    def __init__(self, run: SqlRunner, statement: str) -> None:
        run(statement)

    def poll(self) -> bool:
        return True

    def cancel(self) -> None:
        pass


def start_statement(run: SqlRunner, statement: str) -> RunningStatement:
    """RUN-CLIENT-PARALLEL-DEFINES — start ``statement`` on ``run`` without waiting, where ``run`` can.

    A warehouse session submits it and returns; any other runner (``spark_runner``) runs it before returning,
    so its statements run one at a time.
    """
    return run.start(statement) if isinstance(run, SessionRunner) else _Ran(run, statement)


class WarehouseSession(SessionRunner):
    """RUN-CLIENT-SESSION-REUSE — a SQL warehouse session held like a Spark session.

    The runtime functions' sandbox starts once per session, so every statement after the first that calls them
    runs warm, across runs and measures, until the holder closes the session. A script or Job holds one for its
    whole run (``with warehouse_session(...) as run:``); an App holds one per user for its process
    (``cql_databricks_common.warehouse``).

    Its statements share one connection, one at a time; :attr:`background` runs on side connections that never
    start the sandbox. A connection opens on its first statement, so building a session connects nothing. A
    statement after :meth:`close` fails. :meth:`observed` gives one caller, a run, its own observer over the
    same connections.
    """

    def __init__(self, connect: Callable[[], Any], observer: StatementObserver | None = None) -> None:
        super().__init__(_Connections(connect), observer)

    def observed(self, observer: StatementObserver | None) -> SessionRunner:
        """APP-RUN-LOG — a runner over this session whose statements go to ``observer``."""
        return SessionRunner(self._connections, observer)

    def close(self) -> None:
        """End the session and every connection it holds; closing twice is harmless."""
        self._connections.close()

    def __enter__(self) -> WarehouseSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def background_runner(run: SqlRunner) -> SqlRunner:
    """RUN-CLIENT-ASYNC-CHECKPOINTS — the runner for ``run``'s statements off its thread.

    A warehouse session's side connections; any other runner as it is (a Spark session is thread-safe).
    """
    return run.background if isinstance(run, SessionRunner) else run


def warehouse_session(client, warehouse_id: str, *,
                      observer: StatementObserver | None = None) -> WarehouseSession:
    """RUN-CLIENT-WAREHOUSE-CONNECTOR — a :class:`WarehouseSession` over ``warehouse_id`` as ``client``'s identity.

    ``client`` is read again on every request the connection makes, so a holder that swaps in a fresh client
    (a newer forwarded token) moves the session to it. A statement still running after
    :data:`STATEMENT_TIMEOUT_SECONDS` fails. Cloud fetch is off and complex values come back as JSON text, so
    rows read the same whether or not ``pyarrow`` is installed.
    """
    if not warehouse_id:
        raise ResolverError("warehouse_session needs a SQL warehouse id; none is configured")

    def connect():
        from databricks import sql

        return sql.connect(server_hostname=urlparse(client.config.host).netloc,
                           http_path=f"/sql/1.0/warehouses/{warehouse_id}",
                           credentials_provider=lambda: lambda: client.config.authenticate(),
                           use_cloud_fetch=False, _use_arrow_native_complex_types=False,
                           session_configuration={"STATEMENT_TIMEOUT": str(STATEMENT_TIMEOUT_SECONDS)})

    return WarehouseSession(connect, observer)
