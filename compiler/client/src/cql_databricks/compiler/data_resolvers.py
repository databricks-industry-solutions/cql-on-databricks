"""CLIENT-DATA-RESOLVER: the data resolvers the client ships.

| Resolver | Binds | Answers each retrieve with | Runs on |
|---|---|---|---|
| :class:`SchemaDataResolver` | by rule | the view named for its type in one schema; an environment's ``data`` | Warehouse and Spark |
| :class:`TableDataResolver` | per site | the Delta table or UC view named for its type | Warehouse and Spark |
| :class:`VolumeDataResolver` | per site | the type's ``<Type>.ndjson`` file, or the Bundles, in a volume folder | Warehouse and Spark |
| :class:`DataFrameDataResolver` | per site | the DataFrame given for its type; for development, never reused or kept | Spark only |

A table or view must return the contract ``(__subject_id, __resource)`` (`BIND-CONTRACT`). A volume folder or
DataFrame holds FHIR resources, which the client checks and reshapes (`BIND-RELATION`). Each resolver ignores every
request field but the type, which is correct: the engine re-applies the filters (`BIND-CAPABILITY`). There is no
function resolver: a table function is not a relation.

A workspace folder is never a data resolver.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from .reshape import table_name
from .resolvers import CallSite, DataFrameRelation, DataResolver, Relation, TableRelation, VolumeRelation


class SchemaDataResolver(DataResolver):
    """CLIENT-DATA-RESOLVER, BIND-PUBLISHED-VIEW: every retrieve from the view ``<schema>.<type in lower case>``,
    bound by rule, so a run over it makes one compiler call and reads no catalog. ``identity`` is the schema unless
    given."""

    def __init__(self, schema: str, *, identity: str | None = None, change_log: str | None = None) -> None:
        parts = schema.split(".")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"a data schema is named catalog.schema, not {schema!r}")
        self.schema = schema
        self._identity = identity or schema
        self._change_log = change_log

    def identity(self) -> str:
        return self._identity

    def rule(self) -> str:
        return self.schema

    def change_log(self) -> Relation | None:
        return TableRelation(name=self._change_log) if self._change_log else None


class TableDataResolver(DataResolver):
    """CLIENT-DATA-RESOLVER: a Delta table or UC view per FHIR type, ``{"Patient": "acme.fhir.patient", ...}``. Its
    identity is the tables it reads, so a kept result read through it is reused only through the same tables."""

    def __init__(self, tables: Mapping[str, str]) -> None:
        if not tables:
            raise ValueError("TableDataResolver needs at least one FHIR type and its table")
        self._tables = {t: table_name(name) for t, name in sorted(tables.items())}

    def identity(self) -> str:
        return "tables:" + ",".join(f"{t}={name}" for t, name in self._tables.items())

    def types(self) -> frozenset[str]:
        return frozenset(self._tables)

    def retrieve(self, site: CallSite) -> Relation:
        return TableRelation(name=self._tables[site.request.fhir_type])


class VolumeDataResolver(DataResolver):
    """CLIENT-DATA-RESOLVER: FHIR resources in a volume folder, read with ``read_files`` at each run. With
    ``layout="ndjson"`` each type is a ``<Type>.ndjson`` file of one resource per line, and the types are the files
    found, unless ``types`` names them; with ``layout="bundles"`` the folder holds FHIR Bundles, and ``types`` must
    name the types they hold."""

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

    def retrieve(self, site: CallSite) -> Relation:
        return VolumeRelation(path=self.path, fhir_type=site.request.fhir_type, layout=self.layout)


class DataFrameDataResolver(DataResolver):
    """CLIENT-DATA-RESOLVER: a DataFrame per FHIR type, for developing a mapping on Spark. Its fields are FHIR's as
    columns, or the contract. It is never reused or kept (``reusable`` is false), and a SQL warehouse run refuses it.
    ``name`` tells two in one session apart."""

    reusable = False

    def __init__(self, frames: Mapping[str, object], *, name: str = "dev") -> None:
        if not frames:
            raise ValueError("DataFrameDataResolver needs at least one FHIR type and its DataFrame")
        self._frames = dict(frames)
        self.name = name

    def identity(self) -> str:
        return f"dataframes:{self.name}"

    def types(self) -> frozenset[str]:
        return frozenset(self._frames)

    def retrieve(self, site: CallSite) -> Relation:
        return DataFrameRelation(frame=self._frames[site.request.fhir_type], fhir_type=site.request.fhir_type)
