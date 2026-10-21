"""Value-set expansions the synthesizer generates from (SYNTH-PDS-TERMINOLOGY).

Loads the tall ``(valueset_id, system, code)`` expansion shape from deck-style NDJSON —
one ``valueset-<oid>.ndjson`` per set, the same VSAC-sourced rows the runtime binds
(``BIND-TERMINOLOGY-TABLE``) — so generator and engine can never disagree on membership.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import SynthesizerError


@dataclass(frozen=True)
class Coding:
    system: str
    code: str
    display: str | None = None


class ValueSetIndex:
    """Expansion members keyed by canonical value-set URL."""

    def __init__(self, members: dict[str, list[Coding]]):
        self._members = members
        self._sets: dict[str, set[tuple[str, str]]] = {
            url: {(c.system, c.code) for c in codings} for url, codings in members.items()
        }

    @classmethod
    def from_ndjson_dir(cls, path: str | Path) -> ValueSetIndex:
        """Read every ``valueset-*.ndjson`` under ``path``. A measure that declares no value set has none, so an
        existing folder with no expansion files is an empty index; asking it for a value set fails loud
        (``members``)."""
        root = Path(path)
        if not root.is_dir():
            raise SynthesizerError(f"value-set folder {root} does not exist")
        files = sorted(root.glob("valueset-*.ndjson"))
        members: dict[str, list[Coding]] = {}
        for file in files:
            with file.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    coding = Coding(system=row["system"], code=row["code"], display=row.get("display"))
                    members.setdefault(row["valueset_id"], []).append(coding)
        return cls(members)

    def members(self, url: str) -> list[Coding]:
        codings = self._members.get(url)
        if not codings:
            raise SynthesizerError(
                f"value set {url} has no loaded expansion — the synthesizer cannot pick a member "
                f"code without one (SYNTH-PDS-TERMINOLOGY)")
        return codings

    def contains(self, url: str, system: str, code: str) -> bool:
        return (system, code) in self._sets.get(url, set())

    def urls(self) -> list[str]:
        return sorted(self._members)
