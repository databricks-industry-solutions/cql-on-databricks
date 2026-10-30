"""CLIENT-SESSION-STATE — what the client keeps for a notebook session, kept in that Spark session.

The libraries declared by ``%%cql`` cells (`SVC-NOTEBOOK-CELL-LIBRARY`) and the session mount
(`CLIENT-SESSION-MOUNT`) are rows of one temp view, ``__cqldatabricks_session``, beside the temp functions and
views the mount owns. So they end together: restarting Python loses neither, and ending the Spark session
loses both. They never disagree.

Rows are ``(kind, name, payload)``. A ``cell`` row's payload is its CQL text; the ``scope`` row's is the session
library that header-less CQL joins (`SVC-NOTEBOOK-SESSION-SCOPE`); the ``mount`` row's is the mount's manifest as
JSON; the ``version`` row's is a counter every write moves, so a compile cached against an older mount is not
reused (`CLIENT-SESSION-MOUNT`).

A write rebuilds the view from the rows: notebooks are single-threaded and the record is small. A read is one
small query. The client writes no session conf. With no Spark session there is no record: reading one
finds nothing, and writing one raises. There is no in-memory stand-in.
"""
from __future__ import annotations

import json
from typing import Dict, Iterator, List, Optional, Tuple

VIEW = "__cqldatabricks_session"
CELL, SCOPE, MOUNT, VERSION = "cell", "scope", "mount", "version"
_SCHEMA = "kind STRING, name STRING, payload STRING"

Row = Tuple[str, str, str]


class SessionRecord:
    """The record in one Spark session (`CLIENT-SESSION-STATE`). ``spark`` ``None`` means the active session,
    looked up at each use, so a record built before the session exists still finds it."""

    def __init__(self, spark=None) -> None:
        self._spark = spark

    def _session(self):
        """The session, or ``None`` when there is none."""
        if self._spark is not None:
            return self._spark
        try:
            from pyspark.sql import SparkSession  # lazy: the client imports without PySpark
        except ImportError:
            return None
        return SparkSession.getActiveSession()

    @property
    def spark(self):
        """The session, for a write. With none, raises."""
        active = self._session()
        if active is None:
            raise RuntimeError("the notebook session state lives in a Spark session, and there is none "
                               "(CLIENT-SESSION-STATE); run this where `spark` is defined")
        return active

    def rows(self) -> List[Row]:
        """Every row, in the order written. Empty when the session has no record yet, or there is no
        session: then there is no session state to read."""
        spark = self._session()
        if spark is None:
            return []
        try:
            found = spark.table(VIEW).collect()
        except Exception as error:  # noqa: BLE001 — only "no such view" means empty; anything else is raised
            if "TABLE_OR_VIEW_NOT_FOUND" in str(error):
                return []
            raise
        return [(r["kind"], r["name"], r["payload"]) for r in found]

    def write(self, rows: List[Row]) -> None:
        """Replace the record with ``rows``, moving its version."""
        body = [r for r in rows if r[0] != VERSION]
        body.append((VERSION, "", str(_version(rows) + 1)))
        self.spark.createDataFrame(body, _SCHEMA).createOrReplaceTempView(VIEW)

    def version(self) -> int:
        return _version(self.rows())

    def mount(self) -> Optional[dict]:
        """The mount's manifest, or ``None`` when nothing is mounted."""
        payload = next((p for k, _, p in self.rows() if k == MOUNT), None)
        return None if payload is None else json.loads(payload)

    def set_mount(self, manifest: Optional[dict]) -> None:
        rows = [r for r in self.rows() if r[0] != MOUNT]
        if manifest is not None:
            rows.append((MOUNT, "", json.dumps(manifest, sort_keys=True)))
        self.write(rows)

    def scope(self) -> str:
        """The session library header-less CQL has joined so far (`SVC-NOTEBOOK-SESSION-SCOPE`), or ``""``."""
        return next((p for k, _, p in self.rows() if k == SCOPE), "")

    def set_scope(self, cql: str) -> None:
        self.write([r for r in self.rows() if r[0] != SCOPE] + [(SCOPE, "", cql)])

    def drop(self) -> None:
        """Remove the record itself. Safe when there is none."""
        self.spark.catalog.dropTempView(VIEW)


def _version(rows: List[Row]) -> int:
    return next((int(p) for k, _, p in rows if k == VERSION), 0)


class CellStore:
    """The ``%%cql`` cells as a mapping of library name to :class:`~.libraries.LibrarySource`, read from and
    written to the session record (`SVC-NOTEBOOK-CELL-LIBRARY`, `CLIENT-SESSION-STATE`). The interface a
    :class:`~.session_libraries.SessionLibraryProvider` reads its cells through; a plain ``dict`` has it too."""

    def __init__(self, record: SessionRecord) -> None:
        self._record = record

    def _cells(self) -> Dict[str, "LibrarySource"]:
        from .libraries import LibrarySource, identity_of

        out: Dict[str, LibrarySource] = {}
        for kind, name, payload in self._record.rows():
            if kind == CELL:
                out[name] = LibrarySource(name=name, version=identity_of(payload)[1], cql=payload)
        return out

    def get(self, name: str, default=None):
        return self._cells().get(name, default)

    def __getitem__(self, name: str):
        return self._cells()[name]

    def __contains__(self, name: str) -> bool:
        return name in self._cells()

    def __setitem__(self, name: str, source) -> None:
        rows = [r for r in self._record.rows() if not (r[0] == CELL and r[1] == name)]
        rows.append((CELL, name, source.cql))
        self._record.write(rows)

    def values(self):
        return self._cells().values()

    def __iter__(self) -> Iterator[str]:
        return iter(self._cells())

    def __len__(self) -> int:
        return len(self._cells())

    def snapshot(self) -> Dict[str, "LibrarySource"]:
        """The cells as a plain dict, read once: for a walk that looks up many."""
        return self._cells()

    def clear(self) -> None:
        self._record.write([r for r in self._record.rows() if r[0] != CELL])
