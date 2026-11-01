"""CLIENT-DEFAULT-RESOLVER: providers found by Unity Catalog schema tag, in the caller's catalog scopes, as the
caller.

| Tag on a schema | Provider |
|---|---|
| ``cql_databricks_data_source = <name>`` | :class:`TaggedDataSource`, over its ``retrieve_<Type>`` tables, views and SQL functions |
| ``cql_databricks_terminology = <name>`` | :class:`DeltaTerminology`, over its ``valueset`` table |
| ``cql_databricks_knowledge = <name>`` | a Delta knowledge store over its ``library`` and ``measure`` tables |

A run's terminology is every tagged terminology schema in scope, earliest scope first, read at each compile
(:class:`TaggedTerminology`). The tag search is the :class:`~cql_databricks.data_bindings.ContentResolver`'s,
cached until it reloads (`CONTENT-RESOLVER`); every read goes through the caller's SQL runner, so a notebook and
an App run one implementation as whoever owns the runner (`SEC-OBO`).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from .data_source import CHANGE_LOG, RETRIEVE_PREFIX, DataSourceLayoutError
from .reshape import quote_name
from .roles import (CodeMatch, DataProvider, FunctionRelation, Layered, PublishReport, Relation, RetrieveRequest,
                    TableRelation, TerminologySource, TerminologyStore, ValueSet, ValueSetRef)

log = logging.getLogger(__name__)

_OBJECTS = """
SELECT 'function' AS kind, routine_name AS name
  FROM system.information_schema.routines
 WHERE routine_catalog = :catalog AND routine_schema = :schema
UNION ALL
SELECT 'table' AS kind, table_name AS name
  FROM system.information_schema.tables
 WHERE table_catalog = :catalog AND table_schema = :schema
"""

#: CLIENT-TERMINOLOGY-PROVIDER: the value-set table of a tagged terminology schema (`BIND-TERMINOLOGY-TABLE`).
VALUESET_TABLE = "valueset"

#: Rows per INSERT when publishing value sets.
_PUBLISH_BATCH = 500


class TaggedDataSource(DataProvider):
    """CLIENT-DEFAULT-RESOLVER: a schema tagged ``cql_databricks_data_source``, serving each FHIR type from its
    ``retrieve_<Type>`` table, view or SQL function (`BIND-DATA-SOURCE-LAYOUT`). Its identity is its
    ``catalog.schema``. The layout is read once, on first use. Unity Catalog stores names in lower case, so
    :meth:`types` are lower case and a type is matched without case."""

    def __init__(self, name: str, catalog: str, schema: str, run) -> None:
        self.name, self.catalog, self.schema = name, catalog, schema
        self._run = run
        self._layout: dict[str, Relation] | None = None
        self._change_log: Relation | None = None

    def identity(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def types(self) -> frozenset[str]:
        return frozenset(self._objects())

    def retrieve(self, request: RetrieveRequest) -> Relation:
        return self._objects()[request.fhir_type.lower()]

    def change_log(self) -> Relation | None:
        self._objects()
        return self._change_log

    def _objects(self) -> dict[str, Relation]:
        if self._layout is None:
            layout: dict[str, Relation] = {}
            problems: list[str] = []
            for row in self._run(_OBJECTS, {"catalog": self.catalog, "schema": self.schema}):
                name, kind = row["name"], row["kind"]
                if kind == "table" and name.lower() == CHANGE_LOG:
                    self._change_log = TableRelation(name=f"{self.catalog}.{self.schema}.{CHANGE_LOG}")
                if not name.lower().startswith(RETRIEVE_PREFIX):
                    continue
                fhir_type = name[len(RETRIEVE_PREFIX):].lower()
                if fhir_type in layout:
                    problems.append(f"{fhir_type} has both a table or view and a function")
                    continue
                fqn = f"{self.catalog}.{self.schema}.{name}"
                layout[fhir_type] = FunctionRelation(name=fqn) if kind == "function" else TableRelation(name=fqn)
            if not layout:
                problems.append(f"no {RETRIEVE_PREFIX}<Type> table, view or function")
            if problems:
                raise DataSourceLayoutError(f"data source {self.name!r} ({self.catalog}.{self.schema}) does not "
                                            f"match the data-source layout: {'; '.join(sorted(problems))}")
            self._layout = dict(sorted(layout.items()))
        return self._layout


class DeltaTerminology(TerminologyStore):
    """CLIENT-TERMINOLOGY-PROVIDER: the ``valueset(valueset_id, system, code, version)`` table of one schema
    (`BIND-TERMINOLOGY-TABLE`), named ``catalog.schema``. A store: :meth:`publish` writes value sets into it, as
    the caller."""

    def __init__(self, schema: str, run) -> None:
        parts = schema.split(".")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"a terminology store is named catalog.schema, not {schema!r}")
        self.catalog, self.schema = parts
        self._run = run

    @property
    def table(self) -> str:
        return f"{self.catalog}.{self.schema}.{VALUESET_TABLE}"

    def identity(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def expansions(self, refs: Sequence[ValueSetRef]) -> Relation:
        return TableRelation(name=self.table)

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        """AGENT-VALUE-SET-LOOKUP: the value sets in this table that contain ``code``, sorted by id."""
        where = "code = :code" + (" AND system = :system" if system else "")
        params = {"code": code, **({"system": system} if system else {})}
        rows = self._run(f"SELECT DISTINCT valueset_id, version, system FROM {quote_name(self.table)} "
                         f"WHERE {where} ORDER BY valueset_id, version, system", params)
        return [CodeMatch(id=r["valueset_id"], version=r["version"], system=r["system"]) for r in rows]

    def publish(self, value_sets: Sequence[ValueSet]) -> PublishReport:
        """Replace each value set's rows, by ``(valueset_id, version)``, with its expansion. A value set with no
        codes is refused before anything is written: an expansion that lists nothing is not one."""
        empty = [vs.id for vs in value_sets if not vs.codes]
        if empty:
            raise ValueError(f"value sets with no expansion: {', '.join(empty)} (CLIENT-TERMINOLOGY-PROVIDER)")
        table = quote_name(self.table)
        written = []
        for vs in value_sets:
            self._run(f"DELETE FROM {table} WHERE valueset_id = :id AND version <=> :version",
                      {"id": vs.id, "version": vs.version})
            for start in range(0, len(vs.codes), _PUBLISH_BATCH):
                rows = ", ".join(f"({_literal(vs.id)}, {_literal(c.system)}, {_literal(c.code)}, "
                                 f"{_literal(vs.version)})" for c in vs.codes[start:start + _PUBLISH_BATCH])
                self._run(f"INSERT INTO {table} (valueset_id, system, code, version) VALUES {rows}")
            written.append(vs.id)
        log.info("published %d value sets to %s", len(written), self.table)
        return PublishReport(written=tuple(written))


class TaggedTerminology(TerminologySource):
    """CLIENT-DEFAULT-RESOLVER: every tagged terminology schema in scope, earliest scope first. A value set is
    read from the first that holds it (`BIND-PROVIDER-REQUEST`)."""

    def __init__(self, stores: Sequence[DeltaTerminology]) -> None:
        if not stores:
            raise ValueError("no terminology schema is in scope")
        self.stores = list(stores)

    def identity(self) -> str:
        return ",".join(s.identity() for s in self.stores)

    def expansions(self, refs: Sequence[ValueSetRef]) -> Relation | Layered:
        if len(self.stores) == 1:
            return self.stores[0].expansions(refs)
        return Layered(layers=tuple(s.expansions(refs) for s in self.stores))

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        found: dict[str, list[ValueSetRef]] = {}
        for store in self.stores:
            held: dict[str, list[ValueSetRef]] = {}
            for ref in store.find_code(code, system):
                held.setdefault(ref.id, []).append(ref)
            for valueset_id, refs in held.items():
                found.setdefault(valueset_id, refs)  # the earliest store holding a value set answers for it
        return [ref for refs in found.values() for ref in refs]


class TaggedResolver:
    """CLIENT-DEFAULT-RESOLVER: providers by schema tag. ``content`` is the
    :class:`~cql_databricks.data_bindings.ContentResolver` holding the caller's catalog scopes; ``run`` is the
    caller's SQL runner. Each data source is built once and reused until ``content`` reloads."""

    def __init__(self, content, run) -> None:
        self.content = content
        self._run = run
        self._sources: dict[str, TaggedDataSource] = {}
        self._generation: int | None = None

    def data_sources(self) -> list[TaggedDataSource]:
        """Every data source in scope, by name."""
        self._refresh()
        for tagged in self.content.data_source_schemas():
            if tagged.name not in self._sources:
                self._sources[tagged.name] = TaggedDataSource(tagged.name, tagged.catalog, tagged.schema, self._run)
        return [self._sources[t.name] for t in sorted(self.content.data_source_schemas(), key=lambda t: t.name)]

    def data(self, name: str) -> TaggedDataSource:
        """The data source tagged ``name``. One not in scope fails, naming those that are."""
        sources = {s.name: s for s in self.data_sources()}
        if name not in sources:
            scopes = ", ".join(str(s) for s in self.content.scopes)
            raise LookupError(f"no data source is tagged {name!r} in catalog scopes {scopes}; in scope: "
                              f"{', '.join(sorted(sources)) or 'none'}")
        return sources[name]

    def terminology(self) -> TaggedTerminology | None:
        """Every tagged terminology schema in scope, earliest first; None when there is none."""
        found = self.content.terminology_sources()
        if not found:
            return None
        ordered = sorted(found, key=lambda t: t.position)
        return TaggedTerminology([DeltaTerminology(f"{t.catalog}.{t.schema}", self._run) for t in ordered])

    def _refresh(self) -> None:
        generation = self.content.generation
        if self._generation is not None and generation != self._generation:
            self._sources.clear()
        self._generation = generation


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
