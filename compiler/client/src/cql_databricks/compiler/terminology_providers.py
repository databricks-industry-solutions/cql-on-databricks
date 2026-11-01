"""CLIENT-TERMINOLOGY-PROVIDER: FHIR ``ValueSet`` JSON as a terminology source.

A folder of ``ValueSet`` resources, one per ``.json`` file. In a volume it is read with ``read_files`` at each
run, on a SQL warehouse or Spark; in a workspace folder it is read on the driver and handed to Spark as a
DataFrame, so it runs on Spark only. Every value set must carry its expansion (``expansion.contains``, two
levels deep): one with only ``compose`` fails, naming it, because the engine expands nothing
(`LANG-TERMINOLOGY`). The Delta store is :class:`~.tagged.DeltaTerminology`.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from typing import Any

from .roles import (Code, CodeMatch, DataFrameRelation, Relation, TerminologySource, ValueSet, ValueSetRef, VolumeRelation)


class ValueSetFolder(TerminologySource):
    """CLIENT-TERMINOLOGY-PROVIDER: FHIR ``ValueSet`` JSON files in ``path``. A ``/Volumes`` folder runs on both
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

    def expansions(self, refs: Sequence[ValueSetRef]) -> Relation:
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


def parse_value_set(resource: dict[str, Any], where: str) -> ValueSet:
    """A FHIR ``ValueSet`` resource as a :class:`~.roles.ValueSet`. One with no expansion fails, naming it."""
    if resource.get("resourceType") != "ValueSet":
        raise ValueError(f"{where} is a {resource.get('resourceType')!r}, not a ValueSet")
    url = resource.get("url") or resource.get("id")
    if not url:
        raise ValueError(f"{where} has no url")
    expansion = resource.get("expansion")
    if not expansion or "contains" not in expansion:
        raise ValueError(f"value set {url} ({where}) has no expansion, only compose; expand it first: the engine "
                         f"expands nothing (CLIENT-TERMINOLOGY-PROVIDER)")
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
