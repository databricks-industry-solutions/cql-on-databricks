"""CLIENT-DATA-PROVIDER: the data providers the client ships.

| Provider | Answers each retrieve with | Runs on |
|---|---|---|
| :class:`Tables` | the Delta table or UC view named for its type | Warehouse and Spark |
| :class:`Functions` | the UC SQL table function named for its type, called with the request by name | Warehouse and Spark |
| :class:`VolumeFolder` | the type's ``<Type>.ndjson`` file, or the Bundles, in a volume folder | Warehouse and Spark |
| :class:`Frames` | the DataFrame given for its type; for development, never reused or kept | Spark only |

A table, view or function must return the contract ``(__subject_id, __resource)`` (`BIND-CONTRACT`). A volume
folder or DataFrame holds FHIR resources, which the client checks and reshapes (`BIND-RELATION`). Each provider
ignores every request field but the type, which is correct: the engine re-applies the filters
(`BIND-CAPABILITY`). A schema found by its tag is :class:`~.tagged.TaggedDataSource`.

A workspace folder is never a data provider.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from .reshape import table_name
from .roles import (DataFrameRelation, DataProvider, FunctionRelation, Relation, RetrieveRequest, TableRelation,
                    VolumeRelation)


class Tables(DataProvider):
    """CLIENT-DATA-PROVIDER: a Delta table or UC view per FHIR type, ``{"Patient": "acme.fhir.patient", ...}``. Its
    identity is the tables it reads, so a kept result read through it is reused only through the same tables."""

    def __init__(self, tables: Mapping[str, str]) -> None:
        if not tables:
            raise ValueError("Tables needs at least one FHIR type and its table")
        self._tables = {t: table_name(name) for t, name in sorted(tables.items())}

    def identity(self) -> str:
        return "tables:" + ",".join(f"{t}={name}" for t, name in self._tables.items())

    def types(self) -> frozenset[str]:
        return frozenset(self._tables)

    def retrieve(self, request: RetrieveRequest) -> Relation:
        return TableRelation(name=self._tables[request.fhir_type])


class Functions(DataProvider):
    """CLIENT-DATA-PROVIDER: a UC SQL table function per FHIR type. Each is called with the request fields it
    declares, by name (`BIND-NAMED-ARGS`), so a function that branches on them reads only what a retrieve needs."""

    def __init__(self, functions: Mapping[str, str]) -> None:
        if not functions:
            raise ValueError("Functions needs at least one FHIR type and its function")
        self._functions = {t: table_name(name) for t, name in sorted(functions.items())}

    def identity(self) -> str:
        return "functions:" + ",".join(f"{t}={name}" for t, name in self._functions.items())

    def types(self) -> frozenset[str]:
        return frozenset(self._functions)

    def retrieve(self, request: RetrieveRequest) -> Relation:
        return FunctionRelation(name=self._functions[request.fhir_type])


class VolumeFolder(DataProvider):
    """CLIENT-DATA-PROVIDER: FHIR resources in a volume folder, read with ``read_files`` at each run. With
    ``layout="ndjson"`` each type is a ``<Type>.ndjson`` file of one resource per line, and the types are the
    files found, unless ``types`` names them; with ``layout="bundles"`` the folder holds FHIR Bundles, and
    ``types`` must name the types they hold."""

    def __init__(self, path: str, *, layout: str = "ndjson", types: Sequence[str] | None = None) -> None:
        if layout not in ("ndjson", "bundles"):
            raise ValueError(f"a volume folder of FHIR data is 'ndjson' or 'bundles', not {layout!r}")
        VolumeRelation(path=path, fhir_type="Patient", layout=layout)  # checks the path
        if layout == "bundles" and not types:
            raise ValueError("a folder of Bundles needs types=: the types are not in the file names")
        self.path = path.rstrip("/")
        self.layout = layout
        self._types = frozenset(types) if types else None

    def identity(self) -> str:
        return f"volume:{self.path}:{self.layout}"

    def types(self) -> frozenset[str]:
        if self._types is None:
            try:
                names = os.listdir(self.path)
            except OSError as e:
                raise ValueError(f"cannot list {self.path} to find its <Type>.ndjson files; pass types= to name "
                                 f"them") from e
            self._types = frozenset(n[:-len(".ndjson")] for n in names if n.endswith(".ndjson"))
        return self._types

    def retrieve(self, request: RetrieveRequest) -> Relation:
        return VolumeRelation(path=self.path, fhir_type=request.fhir_type, layout=self.layout)


class Frames(DataProvider):
    """CLIENT-DATA-PROVIDER: a DataFrame per FHIR type, for developing a mapping on Spark. Its fields are FHIR's
    as columns, or the contract. It is never reused or kept (``reusable`` is false), and a SQL warehouse run
    refuses it. ``name`` tells two in one session apart."""

    reusable = False

    def __init__(self, frames: Mapping[str, object], *, name: str = "dev") -> None:
        if not frames:
            raise ValueError("Frames needs at least one FHIR type and its DataFrame")
        self._frames = dict(frames)
        self.name = name

    def identity(self) -> str:
        return f"dataframes:{self.name}"

    def types(self) -> frozenset[str]:
        return frozenset(self._frames)

    def retrieve(self, request: RetrieveRequest) -> Relation:
        return DataFrameRelation(frame=self._frames[request.fhir_type], fhir_type=request.fhir_type)
