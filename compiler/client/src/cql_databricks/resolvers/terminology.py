"""CLIENT-TERMINOLOGY-RESOLVER: the terminology resolvers the client ships.

| Resolver | Reads | A store |
|---|---|---|
| :class:`TableTerminologyResolver` | a ``valueset(valueset_id, system, code, version)`` table or view; an environment's ``knowledge.valueset`` | yes, given its store table |
| :class:`FolderTerminologyResolver` | FHIR ``ValueSet`` JSON, one per ``.json`` file, in a volume or workspace folder | no |
| :class:`DataFrameTerminologyResolver` | a DataFrame of ``(valueset_id, version, system, code)`` rows; for development, Spark only | no |

All bind by rule: one relation serves every value set (`BIND-CALL-SITE`). A folder in a volume is read with
``read_files`` at each run, on a SQL warehouse or Spark; a workspace folder is read on the driver and handed to Spark
as a DataFrame, so it runs on Spark only. Every value set must carry its expansion (``expansion.contains``, two levels
deep): one with only ``compose`` fails, naming it, because the engine expands nothing (`LANG-TERMINOLOGY`).
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Sequence
from typing import Any

from cql_databricks.compiler.reshape import quote_name, table_name
from .interfaces import (Code, CodeMatch, DataFrameRelation, PublishReport, Relation, TableRelation, TerminologyStore,
                        TerminologyResolver, ValueSet, ValueSetRef, VolumeRelation)

log = logging.getLogger(__name__)

#: Rows per ``INSERT`` when a value set is published.
_PUBLISH_BATCH = 500


class TableTerminologyResolver(TerminologyStore):
    """CLIENT-TERMINOLOGY-RESOLVER: the ``valueset(valueset_id, system, code, version)`` relation ``table``, read
    through ``run``, as its identity. It is a store: :meth:`publish` writes ``table`` (`BIND-PUBLISHED-VIEW`), so a
    ``table`` that is a view fails the publish in the warehouse, naming it. ``identity`` is ``table``'s schema unless
    given."""

    def __init__(self, table: str, run, *, identity: str | None = None) -> None:
        self.table = table_name(table)
        self._run = run
        self._identity = identity or self.table.rsplit(".", 1)[0]

    def identity(self) -> str:
        return self._identity

    def rule(self) -> Relation:
        return TableRelation(name=self.table)

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        """AGENT-VALUE-SET-LOOKUP: the value sets in this relation that contain ``code``, sorted by id."""
        where = "code = :code" + (" AND system = :system" if system else "")
        params = {"code": code, **({"system": system} if system else {})}
        rows = self._run(f"SELECT DISTINCT valueset_id, version, system FROM {quote_name(self.table)} "
                         f"WHERE {where} ORDER BY valueset_id, version, system", params)
        return [CodeMatch(id=r["valueset_id"], version=r["version"], system=r["system"]) for r in rows]

    def publish(self, value_sets: Sequence[ValueSet]) -> PublishReport:
        """Replace each value set's rows in ``table``, by ``(valueset_id, version)``, with its expansion. A value set
        with no codes is refused before anything is written: an expansion that lists nothing is not one."""
        empty = [vs.id for vs in value_sets if not vs.codes]
        if empty:
            raise ValueError(f"value sets with no expansion: {', '.join(empty)} (CLIENT-TERMINOLOGY-RESOLVER)")
        store = quote_name(self.table)
        written = []
        for vs in value_sets:
            self._run(f"DELETE FROM {store} WHERE valueset_id = :id AND version <=> :version",
                      {"id": vs.id, "version": vs.version})
            for start in range(0, len(vs.codes), _PUBLISH_BATCH):
                rows = ", ".join(f"({_literal(vs.id)}, {_literal(c.system)}, {_literal(c.code)}, "
                                 f"{_literal(vs.version)})" for c in vs.codes[start:start + _PUBLISH_BATCH])
                self._run(f"INSERT INTO {store} (valueset_id, system, code, version) VALUES {rows}")
            written.append(vs.id)
        log.info("published %d value sets to %s", len(written), self.table)
        return PublishReport(written=tuple(written))


class FolderTerminologyResolver(TerminologyResolver):
    """CLIENT-TERMINOLOGY-RESOLVER: FHIR ``ValueSet`` JSON files in ``path``. A ``/Volumes`` folder runs on both
    computes; a ``/Workspace`` folder needs ``spark``, the session the run uses."""

    def __init__(self, path: str, *, spark=None) -> None:
        self.path = path.rstrip("/")
        if self.path.startswith("/Volumes/"):
            self._spark = None
        elif self.path.startswith("/Workspace/"):
            if spark is None:
                raise ValueError(f"a workspace folder of value sets is read on Spark: pass spark= for {self.path}")
            self._spark = spark
        else:
            raise ValueError(f"value sets are read from a /Volumes or /Workspace folder, not {path!r}")
        self._sets: list[ValueSet] | None = None

    def identity(self) -> str:
        return f"valuesets:{self.path}"

    def rule(self) -> Relation:
        if self._spark is None:
            return VolumeRelation(path=self.path, fhir_type="ValueSet", layout="files")
        rows = [(vs.id, vs.version, c.system, c.code) for vs in self.value_sets() for c in vs.codes]
        frame = self._spark.createDataFrame(rows, "valueset_id STRING, version STRING, system STRING, code STRING")
        return DataFrameRelation(frame=frame, fhir_type="ValueSet")

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        """AGENT-VALUE-SET-LOOKUP: read on the driver, from either folder kind."""
        return [CodeMatch(id=vs.id, version=vs.version, system=s) for vs in self.value_sets()
                for s in sorted({c.system for c in vs.codes if c.code == code and system in (None, c.system)})]

    def value_sets(self) -> list[ValueSet]:
        """Every value set in the folder with its expansion, read once."""
        if self._sets is None:
            self._sets = [parse_value_set(resource, where) for where, resource in _files(self.path)]
        return self._sets


class DataFrameTerminologyResolver(TerminologyResolver):
    """CLIENT-TERMINOLOGY-RESOLVER: value-set expansions in a DataFrame, one row per ``(valueset_id, version, system,
    code)``, for development on Spark, as :class:`~.data.DataFrameDataResolver` is for data. ``version`` may
    be missing, then null. ``name`` tells two in one session apart."""

    def __init__(self, frame, *, name: str = "dev") -> None:
        missing = sorted({"valueset_id", "system", "code"} - set(frame.columns))
        if missing:
            raise ValueError(f"a value-set DataFrame needs the columns valueset_id, system and code; it has no "
                             f"{', '.join(missing)}")
        if "version" not in frame.columns:
            from pyspark.sql import functions as F  # lazy: the client imports without PySpark

            frame = frame.withColumn("version", F.lit(None).cast("string"))
        self._frame = frame.select("valueset_id", "version", "system", "code")
        self.name = name

    def identity(self) -> str:
        return f"valueset-dataframe:{self.name}"

    def rule(self) -> Relation:
        return DataFrameRelation(frame=self._frame, fhir_type="ValueSet")

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        """AGENT-VALUE-SET-LOOKUP: the value sets in the frame that contain ``code``, sorted by id."""
        rows = self._frame.filter(self._frame.code == code)
        if system is not None:
            rows = rows.filter(rows.system == system)
        found = {(r["valueset_id"], r["version"], r["system"]) for r in rows.collect()}
        return [CodeMatch(id=i, version=v, system=s) for i, v, s in sorted(found, key=lambda t: tuple(map(str, t)))]


def parse_value_set(resource: dict[str, Any], where: str) -> ValueSet:
    """A FHIR ``ValueSet`` resource as a :class:`~.resolvers.ValueSet`. One with no expansion fails, naming it."""
    if resource.get("resourceType") != "ValueSet":
        raise ValueError(f"{where} is a {resource.get('resourceType')!r}, not a ValueSet")
    url = resource.get("url") or resource.get("id")
    if not url:
        raise ValueError(f"{where} has no url")
    expansion = resource.get("expansion")
    if not expansion or "contains" not in expansion:
        raise ValueError(f"value set {url} ({where}) has no expansion, only compose; expand it first: the engine "
                         f"expands nothing (CLIENT-TERMINOLOGY-RESOLVER)")
    codes = tuple(Code(system=c["system"], code=c["code"]) for c in _contains(expansion["contains"])
                  if c.get("code") and c.get("system"))
    return ValueSet(id=url, version=resource.get("version"), name=resource.get("name"), codes=codes)


def _contains(entries: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for entry in entries:
        yield entry
        yield from _contains(entry.get("contains") or [])


def _files(path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    try:
        names = sorted(os.listdir(path))
    except OSError as e:
        raise ValueError(f"cannot list {path}; it must be readable from this process") from e
    for name in names:
        if name.endswith(".json"):
            where = f"{path}/{name}"
            with open(where, encoding="utf-8") as f:
                yield where, json.load(f)


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
