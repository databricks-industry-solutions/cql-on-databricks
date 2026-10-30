"""RUN-ADDRESS-LOCATION — where a library is read from: a tagged knowledge store or a folder.

A library's address is ``(location, name, version)``. The location decides where the library itself is read
and where its includes resolve first:

- ``store:<catalog>.<schema>`` — a schema tagged ``cql_databricks_knowledge``. Includes resolve from that store
  first, then the other tagged stores in scope order (`CLIENT-DISCOVERY-SCOPE`).
- ``folder:<path>`` — a folder of CQL, laid out as a measure package is (`cql/<Name>-<version>.cql`) or flat.
  Includes resolve from the folder first, sibling-first, then tagged knowledge (`CLIENT-KNOWLEDGE-INCLUDES`).

No location means the whole of tagged knowledge, in scope order — what every address meant before locations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["store", "folder"]


@dataclass(frozen=True)
class Location:
    """A tagged knowledge store or a folder. ``str()`` is the wire form, ``parse`` reads it back."""
    kind: Kind
    path: str

    def __post_init__(self) -> None:
        if self.kind not in ("store", "folder"):
            raise ValueError(f"a location is a store or a folder, not {self.kind!r}")
        if not self.path or self.path != self.path.strip():
            raise ValueError(f"a {self.kind} location needs a path, got {self.path!r}")
        if self.kind == "store" and len(self.path.split(".")) != 2:
            raise ValueError(f"a store location is <catalog>.<schema>, got {self.path!r}")
        if self.kind == "folder" and not self.path.startswith("/"):
            raise ValueError(f"a folder location is an absolute path, got {self.path!r}")

    @property
    def catalog_schema(self) -> tuple[str, str]:
        """A store's ``(catalog, schema)``."""
        if self.kind != "store":
            raise ValueError(f"{self} is not a store")
        catalog, schema = self.path.split(".")
        return catalog, schema

    def __str__(self) -> str:
        return f"{self.kind}:{self.path.rstrip('/') if self.kind == 'folder' else self.path}"

    @staticmethod
    def parse(text: str) -> "Location":
        """``store:<catalog>.<schema>`` or ``folder:<absolute path>``."""
        kind, sep, path = (text or "").partition(":")
        if not sep:
            raise ValueError(f"a location is store:<catalog>.<schema> or folder:<path>, got {text!r}")
        if kind == "folder":
            path = path.rstrip("/") or "/"
        return Location(kind, path)  # type: ignore[arg-type]
