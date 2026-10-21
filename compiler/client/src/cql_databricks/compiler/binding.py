"""The data a compile binds to, chosen at compile time (`CLIENT-EXEC-DISCOVERY`).

A :class:`DataBinder` on a :class:`~.library.Library` is asked, at each compile, which data this run reads:
it is told the resource types the library retrieves (`EMIT-DESCRIBE-RETRIEVE-TYPES`) and answers with a
:class:`BoundData`, the bindings the request carries. The open-source core installs none, so an unbound type
compiles to the bare ``retrieve_<Type>`` convention (`BIND-MANIFEST`). The Databricks layer installs one that
finds a tagged data source (:mod:`.discovery`).

An explicit binding on the client (``source_binding``) beats any binder: the binder is not asked.

Platform-free: nothing here imports Spark or a platform SDK.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Mapping, Optional, Protocol, Sequence

from .providers import CompileError, CompileRequest


@dataclass(frozen=True, kw_only=True)
class BoundData:
    """What a binder chose for one compile. Each field maps onto the :class:`~.providers.CompileRequest` field
    of the same name: ``data_source`` is a tagged schema's routines (`SVC-COMPILE-BATCH`), ``sources`` an
    explicit per-type manifest (`BIND-MANIFEST`). At most one of the two is set; the service refuses both.
    ``terminology_table`` is the value-set table (`BIND-TERMINOLOGY-TABLE`)."""
    data_source: Optional[Mapping] = None
    sources: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    terminology_table: Optional[str] = None


class DataBinder(Protocol):
    """Chooses the data a compile binds to (`CLIENT-EXEC-DISCOVERY`)."""

    def bind(self, library, retrieve_types: Sequence[str], *, durable: bool) -> BoundData:
        """The bindings for compiling ``library``, which retrieves ``retrieve_types`` (empty for data-free
        CQL). ``durable`` is set for a compile whose results are kept (`RUN-CLIENT-DURABLE`)."""
        ...


def bound_request(library, request: CompileRequest, retrieve_types: Callable[[], Optional[Sequence[str]]], *,
                  durable: bool = False) -> CompileRequest:
    """``request`` with the bindings ``library``'s binder chooses, or unchanged when it has none or the
    client bound its sources explicitly.

    ``retrieve_types`` is called only when there is a binder to ask, so a binder-free compile makes no
    describe call. The value-set table the client named explicitly is kept over the binder's."""
    binder = getattr(library, "binder", None)
    if binder is None or request.sources:
        return request
    types = retrieve_types()
    if types is None:
        raise CompileError("the compiler service does not report which resource types a library retrieves "
                           "(EMIT-DESCRIBE-RETRIEVE-TYPES), so the client cannot choose its data; deploy a "
                           "compiler service from the same release as this client")
    bound = binder.bind(library, list(types), durable=durable)
    return replace(request, data_source=bound.data_source, sources=dict(bound.sources),
                   terminology_table=request.terminology_table or bound.terminology_table)
