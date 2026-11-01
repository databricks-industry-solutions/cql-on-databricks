"""The four roles a CQL run reads and writes through: data, terminology, knowledge and results.

Each role is an interface with methods made for it (`CLIENT-DATA-PROVIDER`, `CLIENT-TERMINOLOGY-PROVIDER`,
`CLIENT-KNOWLEDGE-PROVIDER`, `CLIENT-RESULT-STORE`). The client resolves everything through them, and nothing
else. Reading and writing are separate interfaces (`CLIENT-PROVIDER-READ-WRITE`): code that reads takes a
source, ``publish`` is only on a store. ``Overlay(dev, base)`` composes two providers of one role
(`CLIENT-PROVIDER-OVERLAY`).

A data provider answers each retrieve's :class:`RetrieveRequest` (`BIND-RETRIEVE-REQUEST`) with a relation
(`BIND-RELATION`): a name for where the rows are, never a field mapping.

This module is part of the client core: it imports no platform SDK and no Spark.
"""
from __future__ import annotations

import abc
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Union

from .libraries import LibraryRef, LibrarySource

# --------------------------------------------------------------------------------------------------
# Requests and relations
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Code:
    """One literal code of a code-filtered retrieve."""
    system: str
    code: str


@dataclass(frozen=True, kw_only=True)
class RetrieveRequest:
    """BIND-RETRIEVE-REQUEST: what one retrieve in the compiled CQL asks for.

    Every field but ``fhir_type`` has a default, and a new field will too, so a provider written against
    this class keeps working. A provider may ignore every field but ``fhir_type``: the engine re-applies the
    filters (`BIND-CAPABILITY`). Whatever it returns must hold every resource of ``fhir_type`` that matches
    the request. ``parameters`` maps a CQL parameter name in the define's own library to its literal value:
    a string, or for an interval ``{"low", "lowClosed", "high", "highClosed"}``."""
    fhir_type: str
    value_set: str | None = None
    codes: tuple[Code, ...] = ()
    date_property: str | None = None
    date_low: str | None = None
    date_high: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "RetrieveRequest":
        """The request as ``describe`` sends it (camelCase). A key this client does not know is ignored, so a
        newer compiler's field never breaks an older client (`BUILD-CLIENT-COMPAT`)."""
        return RetrieveRequest(
            fhir_type=raw["fhirType"],
            value_set=raw.get("valueSet"),
            codes=tuple(Code(system=c["system"], code=c["code"]) for c in raw.get("codes") or ()),
            date_property=raw.get("dateProperty"),
            date_low=raw.get("dateLow"),
            date_high=raw.get("dateHigh"),
            parameters=dict(raw.get("parameters") or {}))

    def field_names(self) -> frozenset[str]:
        """The request field names a function relation may declare as parameters (`BIND-NAMED-ARGS`)."""
        return frozenset(f.name for f in fields(self))


@dataclass(frozen=True, kw_only=True)
class Retrieve:
    """One retrieve ``describe`` reports: its id in the compile request and its request
    (`BIND-PROVIDER-REQUEST`)."""
    id: str
    request: RetrieveRequest

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "Retrieve":
        return Retrieve(id=raw["id"], request=RetrieveRequest.from_dict(raw["request"]))


@dataclass(frozen=True, kw_only=True)
class TableRelation:
    """BIND-RELATION: a Delta table or UC view holding ``(__subject_id, __resource)``. Warehouse and Spark."""
    name: str


@dataclass(frozen=True, kw_only=True)
class FunctionRelation:
    """BIND-RELATION: a UC SQL table function returning ``(__subject_id, __resource)``, called with only the
    request fields it declares, by name (`BIND-NAMED-ARGS`). The client reads its parameters from the
    catalog. Warehouse and Spark."""
    name: str


@dataclass(frozen=True, kw_only=True)
class VolumeRelation:
    """BIND-RELATION: the resources of ``fhir_type`` in a volume folder, read with ``read_files``: the
    folder's ``<Type>.ndjson`` file (``layout="ndjson"``), or every FHIR Bundle in it (``layout="bundles"``).
    For value sets (``fhir_type="ValueSet"``) every ``.json`` file is one resource (``layout="files"``). The
    client reshapes it and registers it as a temporary view. Warehouse and Spark."""
    path: str
    fhir_type: str
    layout: str = "ndjson"

    def __post_init__(self):
        if not self.path.startswith("/Volumes/"):
            raise ValueError(f"a volume relation reads a /Volumes path, not {self.path!r}")
        if self.layout not in ("ndjson", "bundles", "files"):
            raise ValueError(f"a volume relation's layout is 'ndjson', 'bundles' or 'files', not {self.layout!r}")
        if (self.layout == "files") != (self.fhir_type == "ValueSet"):
            raise ValueError("a volume of value sets is read as files, one ValueSet per .json file")


@dataclass(frozen=True, kw_only=True)
class DataFrameRelation:
    """BIND-RELATION: a DataFrame of ``fhir_type`` resources, its fields as columns or already on the contract.
    The client reshapes it and registers it as a temporary view. Spark only: a SQL warehouse run fails before
    it starts, naming the provider."""
    frame: Any
    fhir_type: str

    def __repr__(self) -> str:
        return f"DataFrameRelation(fhir_type={self.fhir_type!r})"


#: BIND-RELATION: what a data provider returns for one retrieve.
Relation = Union[TableRelation, FunctionRelation, VolumeRelation, DataFrameRelation]

#: The relation kinds a SQL warehouse can read.
WAREHOUSE_RELATIONS = (TableRelation, FunctionRelation, VolumeRelation)


@dataclass(frozen=True, kw_only=True)
class Layered:
    """CLIENT-PROVIDER-OVERLAY for terminology: value-set relations in precedence order. A value set is read
    from the first that holds it, which is how the compile request's ``terminology`` list is read
    (`BIND-PROVIDER-REQUEST`). Only a terminology overlay returns one."""
    layers: tuple[Relation, ...]


# --------------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------------


class DataProvider(abc.ABC):
    """CLIENT-DATA-PROVIDER: where a run's FHIR data comes from.

    ``reusable`` is false for a development provider: a run that binds one is never reused and never kept
    (`CLIENT-PROVIDER-IDENTITY`)."""

    reusable: bool = True

    @abc.abstractmethod
    def identity(self) -> str:
        """What this provider reads, never who reads it; part of a kept result's fingerprint."""

    @abc.abstractmethod
    def types(self) -> frozenset[str]:
        """The FHIR types it serves. A retrieved type outside them fails before the run."""

    @abc.abstractmethod
    def retrieve(self, request: RetrieveRequest) -> Relation:
        """The relation holding every resource that matches ``request`` (`BIND-RETRIEVE-REQUEST`)."""

    def change_log(self) -> Relation | None:
        """DATA-CHANGE-LOG-PERIODS: the relation saying which evaluation periods changed, or none."""
        return None

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.identity()})"


# --------------------------------------------------------------------------------------------------
# Terminology
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ValueSetRef:
    """A value set the CQL uses: its canonical id (URL or OID) and an optional version."""
    id: str
    version: str | None = None


@dataclass(frozen=True, kw_only=True)
class CodeMatch(ValueSetRef):
    """AGENT-VALUE-SET-LOOKUP: a value set that contains a code, and the code system it holds the code in."""
    system: str | None = None


@dataclass(frozen=True, kw_only=True)
class ValueSet:
    """A value set with its expansion, as published into a terminology store."""
    id: str
    version: str | None = None
    name: str | None = None
    codes: tuple[Code, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PublishReport:
    """What a ``publish`` wrote and what it left because it was unchanged."""
    written: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


class TerminologySource(abc.ABC):
    """CLIENT-TERMINOLOGY-PROVIDER: where a run's value-set expansions come from."""

    @abc.abstractmethod
    def identity(self) -> str:
        """What this source reads; part of a kept result's fingerprint (`CLIENT-PROVIDER-IDENTITY`)."""

    @abc.abstractmethod
    def expansions(self, refs: Sequence[ValueSetRef]) -> Relation | Layered:
        """A relation of ``(valueset_id, version, system, code)`` holding the value sets in ``refs``."""

    @abc.abstractmethod
    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        """The value sets that contain ``code``, in ``system`` when given (`AGENT-VALUE-SET-LOOKUP`)."""


class TerminologyStore(TerminologySource):
    """A terminology source that can be written (`CLIENT-PROVIDER-READ-WRITE`)."""

    @abc.abstractmethod
    def publish(self, value_sets: Sequence[ValueSet]) -> PublishReport:
        """Write ``value_sets``, each with its expansion."""


# --------------------------------------------------------------------------------------------------
# Knowledge
# --------------------------------------------------------------------------------------------------


class KnowledgeSource(abc.ABC):
    """CLIENT-KNOWLEDGE-PROVIDER: where libraries and measures come from, looked up at compile time. A source
    only answers lookups; the include closure is resolved once, in the client core."""

    @abc.abstractmethod
    def identity(self) -> str:
        """What this source reads."""

    @abc.abstractmethod
    def library(self, name: str, version: str | None) -> LibrarySource | None:
        """The library's CQL and ELM, or None. A version pin matches exactly (`KNOWLEDGE-RESOLVE-API`)."""

    @abc.abstractmethod
    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        """The Measure resource, or None."""

    @abc.abstractmethod
    def libraries(self) -> Iterable[LibraryRef]:
        """Every library it holds."""


class KnowledgeStore(KnowledgeSource):
    """A knowledge source that can be written (`CLIENT-PROVIDER-READ-WRITE`)."""

    @abc.abstractmethod
    def publish(self, closure: Any) -> Any:
        """Write a folder library and the folder includes that changed (`CLIENT-PUBLISH`)."""


# --------------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class TableRef:
    """A Delta table holding one kept result."""
    catalog: str
    schema: str
    table: str

    def __str__(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.table}"


class ResultStore(abc.ABC):
    """CLIENT-RESULT-STORE: where kept results are found and written. Delta only."""

    @abc.abstractmethod
    def find(self, fingerprint: str) -> TableRef | None:
        """The table holding the result with ``fingerprint``, or None."""

    @abc.abstractmethod
    def write(self, recipe: Any, relation: Any) -> TableRef:
        """Write ``recipe``'s result from ``relation`` and return its table."""


# --------------------------------------------------------------------------------------------------
# Overlay
# --------------------------------------------------------------------------------------------------


class Overlay:
    """CLIENT-PROVIDER-OVERLAY: ``Overlay(dev, base)`` serves each thing from ``dev`` when it has it, else from
    ``base``: per FHIR type for data, per value set for terminology, per library for knowledge. Both layers
    must play the same role; the result plays it too. Its ``reusable`` is false when any layer's is, and its
    identity is its layers' identities in order."""

    def __new__(cls, dev, base):
        if cls is not Overlay:
            return super().__new__(cls)
        for role, overlay in ((DataProvider, _DataOverlay), (TerminologySource, _TerminologyOverlay),
                              (KnowledgeSource, _KnowledgeOverlay)):
            if isinstance(dev, role) and isinstance(base, role):
                return super().__new__(overlay)
        raise TypeError(f"Overlay needs two providers of one role, got {type(dev).__name__} and "
                        f"{type(base).__name__}")

    def __init__(self, dev, base):
        self.dev = dev
        self.base = base

    def identity(self) -> str:
        return f"overlay({self.dev.identity()},{self.base.identity()})"


class _DataOverlay(Overlay, DataProvider):
    """Per FHIR type: a type ``dev`` serves comes from ``dev``."""

    @property
    def reusable(self) -> bool:
        return self.dev.reusable and self.base.reusable

    def types(self) -> frozenset[str]:
        return self.dev.types() | self.base.types()

    def retrieve(self, request: RetrieveRequest) -> Relation:
        layer = self.dev if request.fhir_type in self.dev.types() else self.base
        return layer.retrieve(request)

    def change_log(self) -> Relation | None:
        """None: two layers' change logs do not combine into one, so every open period refreshes."""
        return None


class _TerminologyOverlay(Overlay, TerminologySource):
    """Per value set: a set ``dev`` holds comes from ``dev``. The layers are read in order, the first holding
    a set serving it (:class:`Layered`)."""

    def expansions(self, refs: Sequence[ValueSetRef]) -> Layered:
        return Layered(layers=_layers(self.dev.expansions(refs)) + _layers(self.base.expansions(refs)))

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        found = list(self.dev.find_code(code, system))
        seen = {ref.id for ref in found}
        return found + [ref for ref in self.base.find_code(code, system) if ref.id not in seen]


class _KnowledgeOverlay(Overlay, KnowledgeSource):
    """Per library: a library ``dev`` holds comes from ``dev``."""

    def library(self, name: str, version: str | None) -> LibrarySource | None:
        found = self.dev.library(name, version)
        return found if found is not None else self.base.library(name, version)

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        found = self.dev.measure(url, version)
        return found if found is not None else self.base.measure(url, version)

    def libraries(self) -> Iterable[LibraryRef]:
        found = list(self.dev.libraries())
        seen = set(found)
        return found + [ref for ref in self.base.libraries() if ref not in seen]


def _layers(relation: Relation | Layered) -> tuple[Relation, ...]:
    return relation.layers if isinstance(relation, Layered) else (relation,)


def terminology_relations(relation: Relation | Layered) -> tuple[Relation, ...]:
    """BIND-PROVIDER-REQUEST: a terminology source's expansions as the request's ordered ``terminology``
    list."""
    return _layers(relation)
