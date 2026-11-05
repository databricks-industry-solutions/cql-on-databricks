"""The four roles a CQL run reads and writes through: data, terminology, knowledge and results (`CLIENT-RESOLVERS`).

Each role is an interface with methods made for it: :class:`DataResolver` (`CLIENT-DATA-RESOLVER`),
:class:`TerminologyResolver` (`CLIENT-TERMINOLOGY-RESOLVER`), :class:`KnowledgeResolver`
(`CLIENT-KNOWLEDGE-RESOLVER`) and :class:`ResultResolver` (`CLIENT-RESULT-RESOLVER`). Every implementation's name
ends in ``Resolver``. The client resolves everything through them, and nothing else. Reading and writing are separate
interfaces (`CLIENT-RESOLVER-READ-WRITE`): ``publish`` is only on a store. ``Overlay(dev, base)`` composes two
resolvers of one role (`CLIENT-RESOLVER-OVERLAY`).

A resolver binds by rule, one answer for every call site, or per site (`BIND-CALL-SITE`): a data resolver answers a
retrieve site's :class:`RetrieveRequest` (`BIND-RETRIEVE-REQUEST`) with a relation (`BIND-RELATION`), a name for
where the rows are, never a field mapping. A run whose resolvers all bind by rule makes one compiler call.

This module is part of the client core: it imports no platform SDK and no Spark.
"""
from __future__ import annotations

import abc
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Union

from cql_databricks.compiler.libraries import LibraryRef, LibrarySource

# --------------------------------------------------------------------------------------------------
# Requests, call sites and relations
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Code:
    """One literal code of a code-filtered retrieve."""
    system: str
    code: str


@dataclass(frozen=True, kw_only=True)
class RetrieveRequest:
    """BIND-RETRIEVE-REQUEST: what one retrieve in the compiled CQL asks for.

    Every field but ``fhir_type`` has a default, and a new field will too, so a resolver written against this
    class keeps working. A resolver may ignore every field but ``fhir_type``: the engine re-applies the filters
    (`BIND-CAPABILITY`). Whatever it returns must hold every resource of ``fhir_type`` that matches the request.
    ``parameters`` maps a CQL parameter name in the define's own library to its literal value: a string, or for an
    interval ``{"low", "lowClosed", "high", "highClosed"}``."""
    fhir_type: str
    value_set: str | None = None
    codes: tuple[Code, ...] = ()
    date_property: str | None = None
    date_low: str | None = None
    date_high: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "RetrieveRequest":
        """The request as ``describe`` sends it (camelCase). A key this client does not know is ignored, so a newer
        compiler's field never breaks an older client (`BUILD-CLIENT-COMPAT`)."""
        return RetrieveRequest(
            fhir_type=raw["fhirType"],
            value_set=raw.get("valueSet"),
            codes=tuple(Code(system=c["system"], code=c["code"]) for c in raw.get("codes") or ()),
            date_property=raw.get("dateProperty"),
            date_low=raw.get("dateLow"),
            date_high=raw.get("dateHigh"),
            parameters=dict(raw.get("parameters") or {}))


@dataclass(frozen=True, kw_only=True)
class ValueSetRef:
    """A value set the CQL uses: its canonical id (URL or OID) and an optional version."""
    id: str
    version: str | None = None


@dataclass(frozen=True, kw_only=True)
class CallSite:
    """BIND-CALL-SITE: one place the compiled CQL reads data or a value set, as ``describe`` reports it.

    ``kind`` is ``retrieve`` or ``valueset``. ``library``, ``version`` and ``define`` name the library the site is
    written in and the define it is in, never the root being compiled; all three are None for the context's own
    retrieve. ``request`` is a :class:`RetrieveRequest` for a retrieve and a :class:`ValueSetRef` for a value set."""
    id: str
    kind: str
    library: str | None
    version: str | None
    define: str | None
    request: RetrieveRequest | ValueSetRef

    RETRIEVE = "retrieve"
    VALUE_SET = "valueset"

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "CallSite":
        kind = raw["kind"]
        body = raw["request"]
        request = (RetrieveRequest.from_dict(body) if kind == CallSite.RETRIEVE
                   else ValueSetRef(id=body["valueSet"], version=body.get("version")))
        return CallSite(id=raw["id"], kind=kind, library=raw.get("library"), version=raw.get("version"),
                        define=raw.get("define"), request=request)


@dataclass(frozen=True, kw_only=True)
class IncludeSite:
    """BIND-CALL-SITE: one ``include`` the client resolves while it builds the closure: the library that declares
    it, by name and version. None for the root itself."""
    library: str
    version: str | None


@dataclass(frozen=True, kw_only=True)
class TableRelation:
    """BIND-RELATION: a Delta table or UC view returning the data contract (`BIND-CONTRACT`), or, for terminology,
    ``(valueset_id, version, system, code)``. Warehouse and Spark."""
    name: str


@dataclass(frozen=True, kw_only=True)
class VolumeRelation:
    """BIND-RELATION: the resources of ``fhir_type`` in a volume folder, read with ``read_files``: the folder's
    ``<Type>.ndjson`` file (``layout="ndjson"``), or every FHIR Bundle in it (``layout="bundles"``). For value sets
    (``fhir_type="ValueSet"``) every ``.json`` file is one resource (``layout="files"``). The client reshapes it and
    registers it as a temporary view. Warehouse and Spark."""
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
    """BIND-RELATION: a DataFrame of ``fhir_type`` resources, its fields as columns or already on the contract. The
    client reshapes it and registers it as a temporary view. Spark only: a SQL warehouse run fails before it starts,
    naming the resolver."""
    frame: Any
    fhir_type: str

    def __repr__(self) -> str:
        return f"DataFrameRelation(fhir_type={self.fhir_type!r})"


#: BIND-RELATION: what a data resolver returns for one retrieve site. There is no function relation.
Relation = Union[TableRelation, VolumeRelation, DataFrameRelation]

#: The relation kinds a SQL warehouse can read.
WAREHOUSE_RELATIONS = (TableRelation, VolumeRelation)


@dataclass(frozen=True, kw_only=True)
class Layered:
    """CLIENT-RESOLVER-OVERLAY for terminology: value-set relations in precedence order. A value set is read from
    the first that holds it, which is how the compile request's terminology lists are read (`BIND-CALL-SITE`)."""
    layers: tuple[Relation, ...]


def layers(relation: Relation | Layered) -> tuple[Relation, ...]:
    """BIND-CALL-SITE: a terminology answer as the request's ordered list of relations."""
    return relation.layers if isinstance(relation, Layered) else (relation,)


# --------------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------------


class DataResolver(abc.ABC):
    """CLIENT-DATA-RESOLVER: where a run's FHIR data comes from.

    A resolver binds by rule, returning from :meth:`rule` the schema whose views serve every type, or per site,
    answering :meth:`retrieve` for each retrieve site. ``reusable`` is false for a development resolver: a run that
    binds one is never reused and never kept (`CLIENT-RESOLVER-IDENTITY`)."""

    reusable: bool = True

    @abc.abstractmethod
    def identity(self) -> str:
        """What this resolver reads, never who reads it; part of a kept result's fingerprint."""

    def rule(self) -> str | None:
        """BIND-CALL-SITE: the ``catalog.schema`` whose view ``<type in lower case>`` serves every retrieve, or None
        for a resolver that binds per site."""
        return None

    def types(self) -> frozenset[str] | None:
        """The FHIR types it serves; a retrieved type outside them fails before the run. None for a resolver that
        binds by rule: a type its schema has no table or view for fails in the warehouse."""
        return None

    def retrieve(self, site: CallSite) -> Relation:
        """The relation holding every resource that matches the site's request (`BIND-RETRIEVE-REQUEST`). A resolver
        that binds by rule answers with its rule's view."""
        schema = self.rule()
        if schema is None:
            raise NotImplementedError(f"{type(self).__name__} binds neither by rule nor per site")
        return TableRelation(name=f"{schema}.{site.request.fhir_type.lower()}")

    def change_log(self) -> Relation | None:
        """DATA-CHANGE-LOG-PERIODS: the relation saying which evaluation periods changed, or none."""
        return None

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.identity()})"


# --------------------------------------------------------------------------------------------------
# Terminology
# --------------------------------------------------------------------------------------------------


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


class TerminologyResolver(abc.ABC):
    """CLIENT-TERMINOLOGY-RESOLVER: where a run's value-set expansions come from. It binds by rule, returning from
    :meth:`rule` the relations every value set is read from, or per value-set site through :meth:`expansions`. Each
    relation holds ``(valueset_id, version, system, code)``."""

    @abc.abstractmethod
    def identity(self) -> str:
        """What this resolver reads; part of a kept result's fingerprint (`CLIENT-RESOLVER-IDENTITY`)."""

    def rule(self) -> Relation | Layered | None:
        """BIND-CALL-SITE: the relations every value set is read from, or None for a resolver that binds per site."""
        return None

    def expansions(self, site: CallSite) -> Relation | Layered:
        """The relations for one value-set site. A resolver that binds by rule answers with its rule."""
        relation = self.rule()
        if relation is None:
            raise NotImplementedError(f"{type(self).__name__} binds neither by rule nor per site")
        return relation

    @abc.abstractmethod
    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        """The value sets that contain ``code``, in ``system`` when given (`AGENT-VALUE-SET-LOOKUP`)."""


class TerminologyStore(TerminologyResolver):
    """A terminology resolver that can be written: ``publish`` writes the store tables (`CLIENT-RESOLVER-READ-WRITE`,
    `BIND-PUBLISHED-VIEW`)."""

    @abc.abstractmethod
    def publish(self, value_sets: Sequence[ValueSet]) -> PublishReport:
        """Write ``value_sets``, each with its expansion."""


# --------------------------------------------------------------------------------------------------
# Knowledge
# --------------------------------------------------------------------------------------------------


class KnowledgeResolver(abc.ABC):
    """CLIENT-KNOWLEDGE-RESOLVER: where libraries and measures come from, looked up at compile time. A resolver only
    answers lookups; the include closure is resolved once, in the client core, which passes the include site that
    asks (`BIND-CALL-SITE`)."""

    @abc.abstractmethod
    def identity(self) -> str:
        """What this resolver reads."""

    @abc.abstractmethod
    def library(self, name: str, version: str | None, site: IncludeSite | None = None) -> LibrarySource | None:
        """The library's CQL and ELM, or None. A version pin matches exactly (`KNOWLEDGE-RESOLVE-API`)."""

    @abc.abstractmethod
    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        """The Measure resource, or None."""

    @abc.abstractmethod
    def libraries(self) -> Iterable[LibraryRef]:
        """Every library it holds."""


class KnowledgeStore(KnowledgeResolver):
    """A knowledge resolver that can be written (`CLIENT-RESOLVER-READ-WRITE`)."""

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


@dataclass(frozen=True, kw_only=True)
class ResultKey:
    """One result a run needs: its fingerprint (`RUN-CLIENT-RESULT-IDENTITY`), and the library and define it computes,
    which name its table but are not part of its identity."""
    library: str
    define: str
    fingerprint: str


@dataclass(frozen=True, kw_only=True)
class KeptResult:
    """A completed result: its table, its full fingerprint, and when its computation started."""
    table: TableRef
    fingerprint: str
    computed_at: datetime


class ResultResolver(abc.ABC):
    """CLIENT-RESULT-RESOLVER: where kept results are found. A resolver owns its whole lookup, and a run never
    composes result layers itself: an environment's is an :class:`Overlay` of the caller's own results over the
    official ones, then none, so the run computes (`RUN-CLIENT-RESULT-OWNERSHIP`)."""

    @abc.abstractmethod
    def find(self, keys: Sequence[ResultKey], *,
             computed_since: datetime | None = None) -> Mapping[str, KeptResult]:
        """The completed result of each key that may be reused, by fingerprint. A result computed before
        ``computed_since`` is not returned. A key with no result is left out."""

    @property
    @abc.abstractmethod
    def destination(self) -> tuple[str, str]:
        """The ``(catalog, schema)`` a run over this resolver is addressed by (`RUN-CLIENT-RESULT-SETS`): where a store
        writes, or the first schema a resolver reads."""

    @abc.abstractmethod
    def holds(self, table: TableRef) -> bool:
        """Whether a result read from ``table`` could have come from this resolver."""

    @abc.abstractmethod
    def columns(self, tables: Sequence[TableRef]) -> Mapping[TableRef, Sequence[tuple[str, str]]]:
        """CLIENT-RESULT-RESOLVER — each of ``tables``' columns, ``(name, type)`` in order, every one a table it holds.
        A table it reports no columns for is left out."""


class ResultWrites(abc.ABC):
    """The writes of one run into a :class:`ResultStore`, opened by :meth:`ResultStore.writes`. The store names each
    table, gives the statement that writes it and marks it complete; the run decides when each statement runs
    (`RUN-CLIENT-PARALLEL-DEFINES`). Leaving it waits for every completion mark, also after a failure."""

    @abc.abstractmethod
    def table(self, key: ResultKey) -> TableRef:
        """Where ``key``'s result is written."""

    @abc.abstractmethod
    def statement(self, table: TableRef, query: str) -> str:
        """The one SQL statement that writes the rows of ``query`` to ``table``."""

    @abc.abstractmethod
    def starting(self, table: TableRef) -> None:
        """Called just before ``table``'s statement runs. An existing table stops being found as complete."""

    @abc.abstractmethod
    def completed(self, result: KeptResult, *, after: Sequence[TableRef] = ()) -> None:
        """Mark ``result`` complete once its statement succeeded, after the results in ``after`` this run wrote."""

    @abc.abstractmethod
    def check(self) -> None:
        """Raise the first completion mark that failed."""

    @abc.abstractmethod
    def close(self) -> None:
        """Wait for every completion mark and raise the first that failed."""

    def __enter__(self) -> "ResultWrites":
        return self

    def __exit__(self, kind, error, traceback) -> bool:
        try:
            self.close()
        except BaseException:
            if error is None:
                raise
        return False


class ResultStore(ResultResolver):
    """A result resolver that can be written (`CLIENT-RESOLVER-READ-WRITE`): it finds as its resolver does, and writes
    only into its own destination."""

    @abc.abstractmethod
    def admit(self, calculation: Any) -> None:
        """Raise ``PermissionError``, naming why, when this store does not take ``calculation``'s results."""

    @abc.abstractmethod
    def writes(self, keys: Sequence[ResultKey]) -> ResultWrites:
        """Open the writes of one run, which may write any of ``keys``."""


# --------------------------------------------------------------------------------------------------
# One run's resolvers
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Resolvers:
    """CLIENT-RESOLVERS: what one run reads its data and value sets through, and where it finds kept results. A run
    takes these and nothing else; an environment builds them (`CLIENT-ENVIRONMENT-RESOLVER`), and so may a notebook.
    ``environment`` is the name of the environment they came from, a label a run records, never read back as an
    address."""
    data: DataResolver
    terminology: TerminologyResolver
    results: ResultResolver | None = None
    environment: str | None = None


# --------------------------------------------------------------------------------------------------
# Overlay
# --------------------------------------------------------------------------------------------------


class Overlay:
    """CLIENT-RESOLVER-OVERLAY: ``Overlay(dev, base)`` serves each thing from ``dev`` when it has it, else from
    ``base``: per FHIR type for data, per value set for terminology, per library for knowledge, per result key for
    results. Both layers must play the same role; the result plays it too. Its ``reusable`` is false when any layer's
    is, and its identity is its layers' identities in order. A results overlay over a store writes into the store."""

    def __new__(cls, dev, base):
        if cls is not Overlay:
            return super().__new__(cls)
        for role, overlay in ((DataResolver, _DataOverlay), (TerminologyResolver, _TerminologyOverlay),
                              (KnowledgeResolver, _KnowledgeOverlay), (ResultResolver, _ResultOverlay)):
            if isinstance(dev, role) and isinstance(base, role):
                if overlay is _ResultOverlay and isinstance(dev, ResultStore):
                    overlay = _ResultStoreOverlay
                return super().__new__(overlay)
        raise TypeError(f"Overlay needs two resolvers of one role, got {type(dev).__name__} and "
                        f"{type(base).__name__}")

    def __init__(self, dev, base):
        self.dev = dev
        self.base = base

    def identity(self) -> str:
        return f"overlay({self.dev.identity()},{self.base.identity()})"


class _DataOverlay(Overlay, DataResolver):
    """Per FHIR type: a type ``dev`` serves comes from ``dev``. It binds per site, so a run over it asks
    ``describe``; ``dev`` must name its types."""

    def __init__(self, dev, base):
        super().__init__(dev, base)
        if dev.types() is None:
            raise TypeError(f"the development layer of an overlay names the types it serves; {dev} binds by rule")

    @property
    def reusable(self) -> bool:
        return self.dev.reusable and self.base.reusable

    def types(self) -> frozenset[str] | None:
        base = self.base.types()
        return None if base is None else self.dev.types() | base

    def retrieve(self, site: CallSite) -> Relation:
        layer = self.dev if site.request.fhir_type in self.dev.types() else self.base
        return layer.retrieve(site)

    def change_log(self) -> Relation | None:
        """None: two layers' change logs do not combine into one, so every open period refreshes."""
        return None


class _TerminologyOverlay(Overlay, TerminologyResolver):
    """Per value set: a set ``dev`` holds comes from ``dev``. The layers are read in order, the first holding a set
    serving it (:class:`Layered`)."""

    def rule(self) -> Layered | None:
        dev, base = self.dev.rule(), self.base.rule()
        return None if dev is None or base is None else Layered(layers=layers(dev) + layers(base))

    def expansions(self, site: CallSite) -> Layered:
        return Layered(layers=layers(self.dev.expansions(site)) + layers(self.base.expansions(site)))

    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]:
        found = list(self.dev.find_code(code, system))
        seen = {ref.id for ref in found}
        return found + [ref for ref in self.base.find_code(code, system) if ref.id not in seen]


class _KnowledgeOverlay(Overlay, KnowledgeResolver):
    """Per library: a library ``dev`` holds comes from ``dev``."""

    def library(self, name: str, version: str | None, site: IncludeSite | None = None) -> LibrarySource | None:
        found = self.dev.library(name, version, site)
        return found if found is not None else self.base.library(name, version, site)

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        found = self.dev.measure(url, version)
        return found if found is not None else self.base.measure(url, version)

    def libraries(self) -> Iterable[LibraryRef]:
        found = list(self.dev.libraries())
        seen = set(found)
        return found + [ref for ref in self.base.libraries() if ref not in seen]


class _ResultOverlay(Overlay, ResultResolver):
    """Per result key: a key ``dev`` finds comes from ``dev``, and ``base`` is asked only for the rest
    (`RUN-CLIENT-RESULT-OWNERSHIP`). A run is addressed by ``dev``'s destination."""

    def find(self, keys: Sequence[ResultKey], *,
             computed_since: datetime | None = None) -> Mapping[str, KeptResult]:
        found = dict(self.dev.find(keys, computed_since=computed_since))
        rest = [key for key in keys if key.fingerprint not in found]
        if rest:
            found.update((fingerprint, kept) for fingerprint, kept in
                         self.base.find(rest, computed_since=computed_since).items() if fingerprint not in found)
        return found

    @property
    def destination(self) -> tuple[str, str]:
        return self.dev.destination

    def holds(self, table: TableRef) -> bool:
        return self.dev.holds(table) or self.base.holds(table)

    def columns(self, tables: Sequence[TableRef]) -> Mapping[TableRef, Sequence[tuple[str, str]]]:
        """Each table's columns, from the layer that holds it."""
        mine = [t for t in tables if self.dev.holds(t)]
        rest = [t for t in tables if not self.dev.holds(t)]
        if any(not self.base.holds(t) for t in rest):
            raise ValueError("columns were asked for a table neither layer holds")
        found = dict(self.dev.columns(mine)) if mine else {}
        if rest:
            found.update(self.base.columns(rest))
        return found


class _ResultStoreOverlay(_ResultOverlay, ResultStore):
    """A results overlay whose ``dev`` is a store: it admits and writes as ``dev`` does, and lists ``dev``'s results
    (``search``)."""

    def admit(self, calculation: Any) -> None:
        self.dev.admit(calculation)

    def writes(self, keys: Sequence[ResultKey]) -> ResultWrites:
        return self.dev.writes(keys)

    def search(self, **options) -> tuple[KeptResult, ...]:
        return self.dev.search(**options)
