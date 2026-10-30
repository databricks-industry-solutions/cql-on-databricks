"""RUN-CLIENT-RESULT-IDENTITY, RUN-CLIENT-REUSE — the definitions and outcomes of a durable calculation.

The compiler issues each definition's whole identity, its ``fingerprint`` (`DEC-RESULT-IDENTITY`). Nothing
here adds to it: not the destination, the caller, the compiler version or the population label. Nothing is
inferred from SQL or table names.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from typing import Literal


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(timezone.utc)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _destination(value: tuple[str, str]) -> None:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("destination must be an explicit (catalog, schema) tuple")
    for component in value:
        _text(component, "destination component")


@dataclass(frozen=True, kw_only=True)
class Definition:
    """RUN-CLIENT-RESULT-IDENTITY — a compiler-issued fingerprint, reuse eligibility and declared reads.

    ``origin_json`` is compiler metadata (library, version, define) for the readable part of the table name
    and for reports. It is not part of the identity.
    """

    key: str
    fingerprint: str
    reusable: bool
    reads: tuple[str, ...] = ()
    origin_json: str = "{}"

    def __post_init__(self) -> None:
        _text(self.key, "definition key")
        _digest(self.fingerprint, "fingerprint")
        if not isinstance(self.reads, tuple) or len(set(self.reads)) != len(self.reads):
            raise ValueError("reads must be a tuple of unique definition keys")
        for dependency in self.reads:
            _text(dependency, "dependency key")
        if not isinstance(self.reusable, bool):
            raise ValueError("reusable must be a boolean")
        origin = json.loads(self.origin_json)
        if not isinstance(origin, dict):
            raise ValueError("origin_json must be a JSON object")
        object.__setattr__(self, "origin_json", _json(origin))


@dataclass(frozen=True, kw_only=True)
class CompletedResult:
    """RUN-CLIENT-COMPLETION — a successfully written result: its fingerprint, table and computation time.

    ``completed_at`` is when the computation started, recorded only after it succeeded. It is not a
    freshness guarantee: the data source serves as-of data (`DEC-RESULT-IDENTITY`).
    """

    fingerprint: str
    artifact: object
    completed_at: datetime

    def __post_init__(self) -> None:
        _digest(self.fingerprint, "fingerprint")
        object.__setattr__(self, "completed_at", _utc(self.completed_at, "completed_at"))


@dataclass(frozen=True, kw_only=True)
class Outcome:
    """RUN-CLIENT-REUSE — explain each selected definition's decision and result origin."""

    definition: str
    decision: Literal["computed", "reused"]
    reason: str
    result: CompletedResult


class ResultsUnavailable(RuntimeError):
    """RUN-CLIENT-PUBLISHED — a read-only resolution lacks a complete, usable result set."""


def _selected(definitions: Sequence[Definition], targets: Sequence[str]) -> list[Definition]:
    if isinstance(targets, str) or not targets:
        raise ValueError("targets must be a nonempty sequence of definition keys")
    by_key = {definition.key: definition for definition in definitions}
    if len(by_key) != len(definitions):
        raise ValueError("duplicate definition keys")
    pending = list(targets)
    selected: set[str] = set()
    while pending:
        key = pending.pop()
        if key not in by_key:
            raise ValueError(f"unknown definition {key!r}")
        if key not in selected:
            selected.add(key)
            pending.extend(by_key[key].reads)
    graph = {key: sorted(by_key[key].reads) for key in sorted(selected)}
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError("definition dependency cycle") from error
    return [by_key[key] for key in order]
