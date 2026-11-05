"""CLIENT-SESSION-RESULTS: kept results that live as long as one SQL session, in temporary tables.

A :class:`SessionResultStore` writes each result it computes as a temporary table, read as ``system.session.<name>``
and created by its one-part name, which is all a temporary table's creation takes, named as any kept result is
(`RUN-CLIENT-TABLE-NAMES`), and records each completed one in another temporary table of the same
session, ``system.session.__cqldatabricks_results``: its name, fingerprint, when its computation started (ISO
text with its offset, so both computes read it back alike) and its columns. Temporary tables carry no Unity Catalog tags and are not in ``information_schema``, so that record is what
:meth:`~SessionResultStore.find` and :meth:`~SessionResultStore.columns` read. Every statement runs on the session the
store was given, never a side connection: a temporary table is visible only in the session that made it. When the
session ends, its results go with it.

It composes with the schema resolvers through :class:`~cql_databricks.resolvers.Overlay` (`CLIENT-RESOLVER-OVERLAY`):
``Overlay(SessionResultStore(...), official)`` reuses the official results and keeps the rest for the session.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from cql_databricks.data_bindings.runner import SqlRunner
from cql_databricks.resolvers.interfaces import KeptResult, ResultKey, ResultStore, ResultWrites, TableRef

from ._catalog_completion import result_table_name, writer_digest
from ._durable import _digest, _utc
from ._durable_delta import table_sql
from .store import _transport, current_principal

log = logging.getLogger(__name__)

#: CLIENT-SESSION-RESULTS — where a session's temporary tables are addressed.
SESSION: tuple[str, str] = ("system", "session")
#: CLIENT-SESSION-RESULTS — the temporary table recording each completed result of the session.
RECORD = "__cqldatabricks_results"
#: The error class a second ``CREATE TEMPORARY TABLE`` of the record fails with: the session has it already.
_EXISTS = "TABLE_OR_VIEW_ALREADY_EXISTS"


class SessionResultStore(ResultStore):
    """CLIENT-SESSION-RESULTS: results kept for the SQL session ``run`` (or ``spark``) holds, as temporary tables, and
    reused only in it. It takes any calculation: what a session computes is the session's own."""

    def __init__(self, run: SqlRunner | None = None, *, spark=None) -> None:
        self.run = _transport(run, spark)
        self._owner: str | None = None
        self._recorded = False
        #: Result tables the record listed under a requested name: a write marks one pending before replacing it.
        self._seen: set[str] = set()

    @property
    def destination(self) -> tuple[str, str]:
        return SESSION

    def holds(self, table: TableRef) -> bool:
        return (table.catalog, table.schema) == SESSION

    @property
    def writer(self) -> str:
        """RUN-CLIENT-TABLE-NAMES — the session's principal's digest, which ends every table name it writes."""
        if self._owner is None:
            self._owner = current_principal(self.run)
        return writer_digest(self._owner)

    def admit(self, calculation) -> None:
        """Every calculation: the session's results are never reused outside it."""

    def find(self, keys: Sequence[ResultKey], *, computed_since: datetime | None = None) -> Mapping[str, KeptResult]:
        """The completed result of each key this session recorded, in one read of the record."""
        if computed_since is not None:
            computed_since = _utc(computed_since, "computed_since")
        keys = list({key.fingerprint: key for key in keys}.values())
        for key in keys:
            _digest(key.fingerprint, "fingerprint")
        if not keys:
            return {}
        names = {result_table_name(k.library, k.define, k.fingerprint, self.writer): k for k in keys}
        self._record()
        rows = self.run(f"SELECT name, fingerprint, computed_at FROM {table_sql((*SESSION, RECORD))} "
                        "WHERE name IN (SELECT explode(from_json(:names, 'ARRAY<STRING>')))",
                        {"names": json.dumps(sorted(names))})
        found: dict[str, KeptResult] = {}
        for row in rows:
            key = names.get(row["name"])
            if key is None:
                raise ValueError("the session's result record returned an unrequested result table")
            self._seen.add(row["name"])
            if row["fingerprint"] != key.fingerprint:
                raise ValueError(f"session result {row['name']} holds another calculation with the same name "
                                 f"(fingerprint {row['fingerprint']}, expected {key.fingerprint})")
            computed_at = _utc(datetime.fromisoformat(row["computed_at"]), "computed_at")
            if computed_since is None or computed_at >= computed_since:
                found[key.fingerprint] = KeptResult(table=_ref(row["name"]), fingerprint=key.fingerprint,
                                                    computed_at=computed_at)
        return found

    def columns(self, tables: Sequence[TableRef]) -> Mapping[TableRef, Sequence[tuple[str, str]]]:
        """Each table's columns, as the record holds them, in one read."""
        if any(not self.holds(t) for t in tables):
            raise ValueError("columns were asked for a table outside the session")
        if not tables:
            return {}
        self._record()
        by_name = {t.table: t for t in tables}
        rows = self.run(f"SELECT name, columns FROM {table_sql((*SESSION, RECORD))} "
                        "WHERE name IN (SELECT explode(from_json(:names, 'ARRAY<STRING>')))",
                        {"names": json.dumps(sorted(by_name))})
        return {by_name[row["name"]]: [tuple(column) for column in json.loads(row["columns"])] for row in rows}

    def writes(self, keys: Sequence[ResultKey]) -> ResultWrites:
        self._record()
        return _SessionWrites(self)

    def _record(self) -> None:
        """Make the session's record once; a session that already has it keeps it."""
        if self._recorded:
            return
        try:
            self.run(f"CREATE TEMPORARY TABLE {_one_part(RECORD)} "
                     "(name STRING, fingerprint STRING, computed_at STRING, columns STRING)")
        except Exception as error:  # noqa: BLE001 — only an existing record is expected; anything else is raised
            if _EXISTS not in str(error):
                raise
        self._recorded = True


class _SessionWrites(ResultWrites):
    """The writes of one run: ``CREATE OR REPLACE TEMPORARY TABLE ... AS`` per result, then its record, on the
    session's own connection, after the statement that wrote it."""

    def __init__(self, store: SessionResultStore) -> None:
        self._store = store

    def table(self, key: ResultKey) -> TableRef:
        return _ref(result_table_name(key.library, key.define, key.fingerprint, self._store.writer))

    def statement(self, table: TableRef, query: str) -> str:
        self._mine(table)
        return f"CREATE OR REPLACE TEMPORARY TABLE {_one_part(table.table)} AS {query}"

    def starting(self, table: TableRef) -> None:
        """A table the record listed stops being complete before it is replaced."""
        self._mine(table)
        if table.table in self._store._seen:
            self._store.run(f"DELETE FROM {table_sql((*SESSION, RECORD))} WHERE name = :name", {"name": table.table})

    def completed(self, result: KeptResult, *, after: Sequence[TableRef] = ()) -> None:
        """Record ``result`` with its columns, read once from the table just written. ``after`` needs no wait: every
        record is written in order on the one connection."""
        self._mine(result.table)
        name = result.table.table
        columns = _described(self._store.run(f"DESCRIBE TABLE {table_sql((*SESSION, name))}"))
        record = table_sql((*SESSION, RECORD))
        self._store.run(f"DELETE FROM {record} WHERE name = :name", {"name": name})
        self._store.run(f"INSERT INTO {record} VALUES (:name, :fingerprint, :computed_at, :columns)",
                        {"name": name, "fingerprint": result.fingerprint,
                         "computed_at": result.computed_at.isoformat(), "columns": json.dumps(columns)})
        log.info("kept %s for the session", name)

    def check(self) -> None:
        """Nothing runs in the background."""

    def close(self) -> None:
        """Nothing runs in the background."""

    def _mine(self, table: TableRef) -> None:
        if (table.catalog, table.schema) != SESSION:
            raise ValueError(f"{table} is not a temporary table of the session")


def _one_part(name: str) -> str:
    """A temporary table's name as its creation takes it: one quoted part."""
    return "`" + name.replace("`", "``") + "`"


def _ref(name: str) -> TableRef:
    return TableRef(catalog=SESSION[0], schema=SESSION[1], table=name)


def _described(rows) -> list[list[str]]:
    """``DESCRIBE TABLE``'s columns, in order: the rows before the first blank or ``#`` section."""
    columns = []
    for row in rows:
        name = row["col_name"]
        if not name or name.startswith("#"):
            break
        columns.append([name, row["data_type"]])
    if not columns:
        raise ValueError("DESCRIBE TABLE returned no columns")
    return columns
