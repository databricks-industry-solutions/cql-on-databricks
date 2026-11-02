"""CLIENT-RESULT-RESOLVER: kept results, found by fingerprint and written, in Delta.

:class:`DeltaResultResolver` finds results in two layers, in one statement (`RUN-CLIENT-RESULT-OWNERSHIP`):

1. **The caller's own**, in ``own`` schemas, in order: tables named with the caller's digest
   (`RUN-CLIENT-TABLE-NAMES`) that the caller owns. A table named with the caller's digest that another identity owns
   fails: it is neither reused nor written over.
2. **The official ones**, in ``official`` schemas: any writer's. The environment's ``results`` schema is official by
   construction; whoever the implementer grants write access decides what is there (`DEC-RESULT-SCOPE`).

A result is a Delta table named ``<library>_<define>_<16 fingerprint digits>_<8 writer digits>`` carrying the four
completion tags (`RUN-CLIENT-RESULT-IDENTITY`), and found only when its full ``cql_fingerprint`` tag matches.

:class:`DeltaResultStore` finds the same way, with its ``destination`` as its first own schema, and writes only there,
as the caller (`CLIENT-RESOLVER-READ-WRITE`). A store over an environment's ``results`` takes only calculations of the
environment's own content (`RUN-CLIENT-DRAFT-RESULTS`, :meth:`DeltaResultStore.admit`). A result is never written to
a folder.
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


class DeltaResultResolver(ResultResolver):
    """CLIENT-RESULT-RESOLVER: the caller's own results in ``own``, then any writer's in ``official``, read through
    ``run`` (or ``spark``) as the caller. Writes nothing."""

    def __init__(self, run: SqlRunner | None = None, *, spark=None, own: Sequence[Schema] = (),
                 official: Sequence[Schema] = ()) -> None:
        self.run = _transport(run, spark)
        self.own = _schemas(own, "own result schemas")
        self.official = _schemas(official, "official result schemas")
        if not (self.own or self.official):
            raise ValueError("a result resolver reads at least one schema")
        self._owner: str | None = None
        #: Tables seen in ``own`` under a requested name, complete or not: a store marks one before replacing it.
        self._seen: set[tuple[str, str, str]] = set()

    @property
    def destination(self) -> Schema:
        """The first own schema, else the first official one."""
        return (self.own or self.official)[0]

    def holds(self, table: TableRef) -> bool:
        return (table.catalog, table.schema) in (*self.own, *self.official)

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
        """The completed result of each key, own first, in ``own`` order, then the latest official one, in one
        statement. A result computed before ``computed_since`` is passed over."""
        if computed_since is not None:
            computed_since = _utc(computed_since, "computed_since")
        keys = list({key.fingerprint: key for key in keys}.values())
        for key in keys:
            _digest(key.fingerprint, "fingerprint")
        if not keys:
            return {}
        own = {result_table_name(k.library, k.define, k.fingerprint, self.writer): k for k in keys} if self.own else {}
        owned, official = self._lookup(own, {k.fingerprint[:NAME_DIGITS] for k in keys})
        found: dict[str, KeptResult] = {}
        for key in keys:
            candidates = [c for c in owned if c.table[2] in own and own[c.table[2]] is key]
            candidates.sort(key=lambda c: self.own.index(c.table[:2]))
            for completion in candidates:
                if completion.fingerprint != key.fingerprint:
                    raise ValueError(f"result table {table_sql(completion.table)} holds another calculation with the "
                                     f"same name (fingerprint {completion.fingerprint}, expected {key.fingerprint})")
            stem = result_table_name(key.library, key.define, key.fingerprint, "0" * WRITER_DIGITS)[:-WRITER_DIGITS]
            for completion in official:
                if completion.table[2].startswith(stem) and completion.fingerprint != key.fingerprint:
                    raise ValueError(f"result table {table_sql(completion.table)} holds another calculation with the "
                                     f"same name (fingerprint {completion.fingerprint}, expected {key.fingerprint})")
            others = [c for c in official if c.fingerprint == key.fingerprint]
            # The earlier official schema first, then the latest computation in it.
            others.sort(key=lambda c: (self.official.index(c.table[:2]), -c.computed_at.timestamp(), c.table))
            for completion in [*candidates, *others]:
                if computed_since is None or completion.computed_at >= computed_since:
                    found[key.fingerprint] = _kept(completion)
                    break
        return found

    def _lookup(self, own: Mapping[str, ResultKey], prefixes: set[str]) -> tuple[list[Completion], list[Completion]]:
        """One statement over both layers: the own tables named ``own`` (any state, with their owner), and the official
        tables whose fingerprint digits are in ``prefixes``, with their completion tags."""
        params: dict[str, str] = {}
        branches: list[str] = []
        tags = ", ".join(_literal(tag) for tag in TAGS)
        layers = (("own", self.own if own else ()), ("official", self.official))
        catalogs = dict.fromkeys(catalog for _, schemas in layers for catalog, _ in schemas)
        if own:
            params["names"] = json.dumps(sorted(own))
        if self.official:
            params["prefixes"] = json.dumps(sorted(prefixes))
        if len(json.dumps(params).encode("utf-8")) > 900_000:
            raise ValueError("result lookup exceeds the statement parameter budget")
        for i, catalog in enumerate(catalogs):
            info_tables = table_sql((catalog, "information_schema", "tables"))
            info_tags = table_sql((catalog, "information_schema", "table_tags"))
            for layer, schemas in layers:
                names = sorted(schema for c, schema in schemas if c == catalog)
                if not names:
                    continue
                params[f"{layer}_{i}"] = json.dumps(names)
                if layer == "own":
                    branches.append(
                        "SELECT 'own' AS layer, t.table_catalog AS catalog_name, t.table_schema AS schema_name, "
                        "t.table_name, t.table_owner, g.tag_name, g.tag_value "
                        f"FROM {info_tables} t LEFT JOIN {info_tags} g ON g.catalog_name = t.table_catalog "
                        "AND g.schema_name = t.table_schema AND g.table_name = t.table_name "
                        f"AND g.tag_name IN ({tags}) "
                        f"WHERE t.table_schema IN (SELECT explode(from_json(:own_{i}, 'ARRAY<STRING>'))) "
                        "AND t.table_name IN (SELECT explode(from_json(:names, 'ARRAY<STRING>')))")
                else:
                    branches.append(
                        "SELECT 'official' AS layer, g.catalog_name, g.schema_name, g.table_name, "
                        "CAST(NULL AS STRING) AS table_owner, g.tag_name, g.tag_value "
                        f"FROM {info_tags} g "
                        f"WHERE g.schema_name IN (SELECT explode(from_json(:official_{i}, 'ARRAY<STRING>'))) "
                        f"AND g.tag_name IN ({tags}) AND g.table_name RLIKE '{NAME_RLIKE}' "
                        f"AND substr(g.table_name, -{NAME_DIGITS + WRITER_DIGITS + 1}, {NAME_DIGITS}) "
                        "IN (SELECT explode(from_json(:prefixes, 'ARRAY<STRING>')))")
        values: dict[tuple[str, tuple[str, str, str]], dict[str, str]] = {}
        for row in self.run(" UNION ALL ".join(branches), params):
            table = (row["catalog_name"], row["schema_name"], row["table_name"])
            layer = row["layer"]
            if layer == "own":
                if table[:2] not in self.own or table[2] not in own:
                    raise ValueError("result lookup returned an unrequested result table")
                if row["table_owner"] != self.owner:
                    raise PermissionError(f"{'.'.join(table)} is named for your results but owned by "
                                          f"{row['table_owner']}; it is not reused or replaced "
                                          "(RUN-CLIENT-RESULT-OWNERSHIP)")
                self._seen.add(table)
            elif table[:2] not in self.official:
                raise ValueError("result lookup returned a table outside the official result schemas")
            held = values.setdefault((layer, table), {})
            tag = row["tag_name"]
            if tag is not None:
                if tag not in TAGS or tag in held:
                    raise ValueError("result lookup returned an unexpected or duplicate result tag")
                held[tag] = row["tag_value"]
        owned: list[Completion] = []
        official: list[Completion] = []
        for (layer, table), held in values.items():
            completion = Completion.from_tags(table, held)
            if completion is not None:
                (owned if layer == "own" else official).append(completion)
        return owned, official


class DeltaResultStore(DeltaResultResolver, ResultStore):
    """CLIENT-RESULT-RESOLVER: a :class:`DeltaResultResolver` that writes into ``destination``, its first own schema,
    as the caller. ``capacity`` caps the result tables the destination may hold (`RUN-CLIENT-LIFECYCLE`).
    ``requires`` are, by role (``data``, ``terminology``, ``knowledge``), the resolver identities a calculation may have
    bound for its results to be written here; None takes any calculation."""

    def __init__(self, destination: Schema, run: SqlRunner | None = None, *, spark=None, own: Sequence[Schema] = (),
                 official: Sequence[Schema] = (), capacity: int | None = None,
                 requires: Mapping[str, frozenset[str]] | None = None) -> None:
        (destination,) = _schemas([destination], "destination")
        super().__init__(run, spark=spark, own=(destination, *own), official=official)
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
        """RUN-CLIENT-RESULT-IDENTITY — the completed results in the destination, latest first, optionally one
        ``fingerprint``. Candidates, not a verified complete result set: read a calculation's with
        ``resolve_results``. ``completed_after`` filters when a computation started, not data freshness."""
        return tuple(_kept(c) for c in search(self.run, self.destination, fingerprint=fingerprint,
                                              computed_since=completed_after, limit=limit))


class _DeltaWrites(ResultWrites):
    """The writes of one run: ``CREATE OR REPLACE TABLE ... AS`` per result, then its completion tags, set on side
    connections in the order the results were written (`RUN-CLIENT-ASYNC-CHECKPOINTS`, `RUN-CLIENT-SESSION-REUSE`)."""

    def __init__(self, store: DeltaResultStore) -> None:
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
