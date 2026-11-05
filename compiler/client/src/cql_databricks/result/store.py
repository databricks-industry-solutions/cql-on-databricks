"""CLIENT-RESULT-RESOLVER: kept results in one schema, found by fingerprint and written, in Delta.

:class:`SchemaResultResolver` finds results in one schema, whose results it reuses one of two ways
(`RUN-CLIENT-RESULT-OWNERSHIP`):

- ``reuse="own"``: only the caller's own, tables named with the caller's digest (`RUN-CLIENT-TABLE-NAMES`) that the
  caller owns. A table named with the caller's digest that another identity owns fails: it is neither reused nor
  written over.
- ``reuse="official"``: any writer's, the latest first. The environment's ``results`` schema is official by
  construction; whoever the implementer grants write access decides what is there (`RUN-CLIENT-PUBLISHED`).

:class:`SchemaResultStore` finds the caller's own results in its schema and writes only there, as the caller
(`CLIENT-RESOLVER-READ-WRITE`). A store given ``requires`` takes only calculations that bound those resolvers
(`RUN-CLIENT-DRAFT-RESULTS`, :meth:`SchemaResultStore.admit`). Layers compose with
:class:`~cql_databricks.resolvers.Overlay`, which an environment's result resolvers are (`CLIENT-RESOLVER-OVERLAY`).

A result is a Delta table named ``<library>_<define>_<16 fingerprint digits>_<8 writer digits>`` carrying the four
completion tags (`RUN-CLIENT-RESULT-IDENTITY`), and found only when its full ``cql_fingerprint`` tag matches. A
result is never written to a folder.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from cql_databricks.resolvers.interfaces import KeptResult, ResultKey, ResultResolver, ResultStore, ResultWrites, TableRef
from cql_databricks.data_bindings.runner import SqlRunner, background_runner, spark_runner

from ._catalog_completion import (NAME_DIGITS, NAME_RLIKE, TABLE_FORMAT, TAGS, WRITER_DIGITS, Completion,
                                  CompletionWriter, _literal, result_table_name, search, writer_digest)
from ._durable import _digest, _utc
from ._durable_delta import table_sql
from .lifecycle import check_capacity

log = logging.getLogger(__name__)

Schema = tuple[str, str]


def _schemas(value: Sequence[Schema], name: str) -> tuple[Schema, ...]:
    out = []
    for schema in value:
        if (not isinstance(schema, tuple) or len(schema) != 2
                or any(not isinstance(part, str) or not part.strip() for part in schema)):
            raise ValueError(f"{name} must be explicit (catalog, schema) tuples")
        out.append(schema)
    return tuple(dict.fromkeys(out))


def _transport(run: SqlRunner | None, spark) -> SqlRunner:
    if (spark is None) == (run is None):
        raise ValueError("supply exactly one of spark or run")
    return spark_runner(spark) if spark is not None else run


def current_principal(run: SqlRunner) -> str:
    """RUN-CLIENT-RESULT-OWNERSHIP — the principal ``run`` acts as, which owns what it writes."""
    rows = run("SELECT current_user() AS principal")
    if not rows or not rows[0]["principal"]:
        raise PermissionError("the SQL session reports no current user")
    return rows[0]["principal"]


def _ref(table: tuple[str, str, str]) -> TableRef:
    return TableRef(catalog=table[0], schema=table[1], table=table[2])


def _kept(completion: Completion) -> KeptResult:
    return KeptResult(table=_ref(completion.table), fingerprint=completion.fingerprint,
                      computed_at=completion.computed_at)


#: RUN-CLIENT-RESULT-OWNERSHIP — whose results a schema's resolver reuses.
OWN, OFFICIAL = "own", "official"


class SchemaResultResolver(ResultResolver):
    """CLIENT-RESULT-RESOLVER: kept results in ``schema``, read through ``run`` (or ``spark``) as the caller, in one
    statement. ``reuse`` is whose: ``"own"``, the caller's alone, or ``"official"``, any writer's
    (`RUN-CLIENT-RESULT-OWNERSHIP`). Writes nothing."""

    def __init__(self, schema: Schema, run: SqlRunner | None = None, *, spark=None, reuse: str = OWN) -> None:
        self.run = _transport(run, spark)
        (self.schema,) = _schemas([schema], "result schema")
        if reuse not in (OWN, OFFICIAL):
            raise ValueError(f"reuse is {OWN!r} or {OFFICIAL!r}, not {reuse!r}")
        self.reuse = reuse
        self._owner: str | None = None
        #: Tables seen under a requested name, complete or not: a store marks one before replacing it.
        self._seen: set[tuple[str, str, str]] = set()

    @property
    def destination(self) -> Schema:
        """The schema it reads, which a run over it is addressed by (`RUN-CLIENT-RESULT-SETS`)."""
        return self.schema

    def holds(self, table: TableRef) -> bool:
        return (table.catalog, table.schema) == self.schema

    @property
    def owner(self) -> str:
        """RUN-CLIENT-RESULT-OWNERSHIP — the principal ``run`` acts as, read once."""
        if self._owner is None:
            self._owner = current_principal(self.run)
        return self._owner

    @property
    def writer(self) -> str:
        """RUN-CLIENT-TABLE-NAMES — the caller's digest, which ends every table name it writes."""
        return writer_digest(self.owner)

    def find(self, keys: Sequence[ResultKey], *, computed_since: datetime | None = None) -> Mapping[str, KeptResult]:
        """The completed result of each key in the schema, in one statement: the caller's own table, or for
        ``reuse="official"`` the latest of any writer's. A result computed before ``computed_since`` is passed over."""
        if computed_since is not None:
            computed_since = _utc(computed_since, "computed_since")
        keys = list({key.fingerprint: key for key in keys}.values())
        for key in keys:
            _digest(key.fingerprint, "fingerprint")
        if not keys:
            return {}
        if self.reuse == OWN:
            names = {result_table_name(k.library, k.define, k.fingerprint, self.writer): k for k in keys}
            held = self._lookup_own(names)
        else:
            held = self._lookup_official({k.fingerprint[:NAME_DIGITS] for k in keys})
        found: dict[str, KeptResult] = {}
        for key in keys:
            if self.reuse == OWN:
                candidates = [c for c in held if names[c.table[2]] is key]
                clashing = candidates
            else:
                stem = result_table_name(key.library, key.define, key.fingerprint, "0" * WRITER_DIGITS)[:-WRITER_DIGITS]
                clashing = [c for c in held if c.table[2].startswith(stem)]
                # The latest computation first.
                candidates = sorted((c for c in held if c.fingerprint == key.fingerprint),
                                    key=lambda c: (-c.computed_at.timestamp(), c.table))
            for completion in clashing:
                if completion.fingerprint != key.fingerprint:
                    raise ValueError(f"result table {table_sql(completion.table)} holds another calculation with the "
                                     f"same name (fingerprint {completion.fingerprint}, expected {key.fingerprint})")
            for completion in candidates:
                if computed_since is None or completion.computed_at >= computed_since:
                    found[key.fingerprint] = _kept(completion)
                    break
        return found

    def _lookup_own(self, names: Mapping[str, ResultKey]) -> list[Completion]:
        """The tables named ``names`` (any state, with their owner) and their completion tags."""
        catalog, schema = self.schema
        params = {"own_0": json.dumps([schema]), "names": json.dumps(sorted(names))}
        sql = ("SELECT 'own' AS layer, t.table_catalog AS catalog_name, t.table_schema AS schema_name, "
               "t.table_name, t.table_owner, g.tag_name, g.tag_value "
               f"FROM {table_sql((catalog, 'information_schema', 'tables'))} t "
               f"LEFT JOIN {table_sql((catalog, 'information_schema', 'table_tags'))} g "
               "ON g.catalog_name = t.table_catalog AND g.schema_name = t.table_schema AND g.table_name = t.table_name "
               f"AND g.tag_name IN ({self._tags()}) "
               "WHERE t.table_schema IN (SELECT explode(from_json(:own_0, 'ARRAY<STRING>'))) "
               "AND t.table_name IN (SELECT explode(from_json(:names, 'ARRAY<STRING>')))")
        rows = self.run(sql, _budget(params))
        for row in rows:
            table = (row["catalog_name"], row["schema_name"], row["table_name"])
            if table[:2] != self.schema or table[2] not in names:
                raise ValueError("result lookup returned an unrequested result table")
            if row["table_owner"] != self.owner:
                raise PermissionError(f"{'.'.join(table)} is named for your results but owned by "
                                      f"{row['table_owner']}; it is not reused or replaced "
                                      "(RUN-CLIENT-RESULT-OWNERSHIP)")
            self._seen.add(table)
        return _completions(rows)

    def _lookup_official(self, prefixes: set[str]) -> list[Completion]:
        """Any writer's tables whose fingerprint digits are in ``prefixes``, with their completion tags."""
        catalog, schema = self.schema
        params = {"official_0": json.dumps([schema]), "prefixes": json.dumps(sorted(prefixes))}
        sql = ("SELECT 'official' AS layer, g.catalog_name, g.schema_name, g.table_name, "
               "CAST(NULL AS STRING) AS table_owner, g.tag_name, g.tag_value "
               f"FROM {table_sql((catalog, 'information_schema', 'table_tags'))} g "
               "WHERE g.schema_name IN (SELECT explode(from_json(:official_0, 'ARRAY<STRING>'))) "
               f"AND g.tag_name IN ({self._tags()}) AND g.table_name RLIKE '{NAME_RLIKE}' "
               f"AND substr(g.table_name, -{NAME_DIGITS + WRITER_DIGITS + 1}, {NAME_DIGITS}) "
               "IN (SELECT explode(from_json(:prefixes, 'ARRAY<STRING>')))")
        rows = self.run(sql, _budget(params))
        for row in rows:
            if (row["catalog_name"], row["schema_name"]) != self.schema:
                raise ValueError("result lookup returned a table outside the official result schema")
        return _completions(rows)

    @staticmethod
    def _tags() -> str:
        return ", ".join(_literal(tag) for tag in TAGS)


def _budget(params: dict[str, str]) -> dict[str, str]:
    if len(json.dumps(params).encode("utf-8")) > 900_000:
        raise ValueError("result lookup exceeds the statement parameter budget")
    return params


def _completions(rows) -> list[Completion]:
    """Each table's completion, from its tag rows; a table with no complete set of tags is a miss."""
    values: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        held = values.setdefault((row["catalog_name"], row["schema_name"], row["table_name"]), {})
        tag = row["tag_name"]
        if tag is not None:
            if tag not in TAGS or tag in held:
                raise ValueError("result lookup returned an unexpected or duplicate result tag")
            held[tag] = row["tag_value"]
    return [c for c in (Completion.from_tags(table, held) for table, held in values.items()) if c is not None]


class SchemaResultStore(SchemaResultResolver, ResultStore):
    """CLIENT-RESULT-RESOLVER: the caller's own results in ``schema``, found as a ``reuse="own"``
    :class:`SchemaResultResolver` finds them and written there, as the caller. ``capacity`` caps the result tables the
    schema may hold (`RUN-CLIENT-LIFECYCLE`). ``requires`` are, by role (``data``, ``terminology``, ``knowledge``), the
    resolver identities a calculation may have bound for its results to be written here; None takes any calculation."""

    def __init__(self, schema: Schema, run: SqlRunner | None = None, *, spark=None, capacity: int | None = None,
                 requires: Mapping[str, frozenset[str]] | None = None) -> None:
        super().__init__(schema, run, spark=spark, reuse=OWN)
        if capacity is not None and (type(capacity) is not int or capacity < 1):
            raise ValueError("capacity must be a positive number of result tables")
        self.capacity = capacity
        self.requires = {role: frozenset(allowed) for role, allowed in requires.items()} if requires is not None else None

    def admit(self, calculation) -> None:
        """RUN-CLIENT-DRAFT-RESULTS — refuse a calculation that bound anything but ``requires``, naming what differs."""
        if self.requires is None:
            return
        bound = getattr(calculation, "identities", None)
        where = ".".join(self.destination)
        if not isinstance(bound, Mapping):
            raise PermissionError(f"{where} holds only results of the environment's own published content, and this "
                                  "query's knowledge, terminology or data may come from cells, folders, volumes, "
                                  "DataFrames or custom resolvers: prepare it with prepare_calculation(library=..., "
                                  "version=...) over the environment's resolvers, or write it to draft_results")
        differs = [f"{role} is {bound.get(role) or 'unbound'}, not {' or '.join(sorted(allowed))}"
                   for role, allowed in sorted(self.requires.items()) if bound.get(role) not in allowed]
        if differs:
            raise PermissionError(f"{where} holds only results of the environment's own published content, and this "
                                  f"calculation's {'; '.join(differs)}: write it to draft_results")

    def writes(self, keys: Sequence[ResultKey]) -> ResultWrites:
        if self.capacity is not None:
            check_capacity(self.run, self.destination,
                           {result_table_name(k.library, k.define, k.fingerprint, self.writer) for k in keys},
                           self.capacity)
        return _DeltaWrites(self)

    def search(self, *, fingerprint: str | None = None, completed_after: datetime | None = None,
               limit: int = 100) -> tuple[KeptResult, ...]:
        """RUN-CLIENT-RESULT-IDENTITY — the completed results in the schema, latest first, optionally one
        ``fingerprint``. Candidates, not a verified complete result set: read a calculation's with
        ``resolve_results``. ``completed_after`` filters when a computation started, not data freshness."""
        return tuple(_kept(c) for c in search(self.run, self.destination, fingerprint=fingerprint,
                                              computed_since=completed_after, limit=limit))


class _DeltaWrites(ResultWrites):
    """The writes of one run: ``CREATE OR REPLACE TABLE ... AS`` per result, then its completion tags, set on side
    connections in the order the results were written (`RUN-CLIENT-ASYNC-CHECKPOINTS`, `RUN-CLIENT-SESSION-REUSE`)."""

    def __init__(self, store: SchemaResultStore) -> None:
        self._store = store
        self._tags = CompletionWriter(background_runner(store.run))

    def table(self, key: ResultKey) -> TableRef:
        return TableRef(catalog=self._store.destination[0], schema=self._store.destination[1],
                        table=result_table_name(key.library, key.define, key.fingerprint, self._store.writer))

    def statement(self, table: TableRef, query: str) -> str:
        self._mine(table)
        return (f"CREATE OR REPLACE TABLE {table_sql((table.catalog, table.schema, table.table))} USING DELTA "
                f"TBLPROPERTIES ('cql.durable.format' = '{TABLE_FORMAT}') AS {query}")

    def starting(self, table: TableRef) -> None:
        """RUN-CLIENT-CATALOG-CHECKPOINTS — a table the lookup saw is marked pending before it is replaced."""
        parts = self._mine(table)
        if parts in self._store._seen:
            self._tags.invalidate(parts)

    def completed(self, result: KeptResult, *, after: Sequence[TableRef] = ()) -> None:
        parts = self._mine(result.table)
        self._tags.submit(Completion(table=parts, fingerprint=result.fingerprint, computed_at=result.computed_at),
                          after=tuple((t.catalog, t.schema, t.table) for t in after))
        log.info("wrote %s", ".".join(parts))

    def check(self) -> None:
        self._tags.check()

    def close(self) -> None:
        self._tags.finish()

    def __exit__(self, kind, error, traceback) -> bool:
        return self._tags.__exit__(kind, error, traceback)

    def _mine(self, table: TableRef) -> tuple[str, str, str]:
        if (table.catalog, table.schema) != self._store.destination:
            raise ValueError(f"{table} is not in this store's destination {'.'.join(self._store.destination)}")
        return table.catalog, table.schema, table.table
