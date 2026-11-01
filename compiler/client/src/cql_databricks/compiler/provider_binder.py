"""CLIENT-DATA-PROVIDER, CLIENT-TERMINOLOGY-PROVIDER, BIND-PROVIDER-REQUEST: the binder that binds each compile
through providers.

At each compile it asks ``describe``, with the compile's own request, for the retrieves and value sets, binds
them through :func:`~.relations.bind`, and sends the relations in the request. A durable compile refuses a
provider that is not reusable: its results are never kept (`CLIENT-PROVIDER-IDENTITY`). Each provider may be
given as itself or as a factory, called on the first compile, so building a client touches no workspace.

Part of the client core: it imports no platform SDK.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from .providers import CompileError, CompileRequest, LibraryGraph
from .relations import BoundProviders, ProviderError, bind
from .roles import DataProvider, TerminologySource


class ProviderBinder:
    """Binds every compile to ``data`` and ``terminology``. ``run`` builds the SQL runner of the session that runs
    the compiled SQL; ``spark`` is that session when it is Spark, else None; ``schemas`` returns the model's
    resource schema per FHIR type, for the reshape."""

    def __init__(self, *, data: DataProvider | Callable[[], DataProvider] | None = None,
                 terminology: TerminologySource | Callable[[], TerminologySource | None] | None = None,
                 run: Callable[[], Any], spark=None,
                 schemas: Callable[[Sequence[str]], Any]) -> None:
        self._data, self._terminology = data, terminology
        self._run_factory, self._run = run, None
        self.spark = spark
        self._schemas = schemas
        self.last: BoundProviders | None = None

    @property
    def data(self) -> DataProvider | None:
        if self._data is not None and not isinstance(self._data, DataProvider):
            self._data = self._data()
        return self._data

    @property
    def terminology(self) -> TerminologySource | None:
        if self._terminology is not None and not isinstance(self._terminology, TerminologySource):
            self._terminology = self._terminology()
        return self._terminology

    def generation(self):
        """A compile is cached only against reusable providers, by their identities: a development provider's
        frames can change under the same identity, so its compiles are never reused."""
        data = self.data
        if data is not None and not data.reusable:
            return object()
        terminology = self.terminology
        return (data.identity() if data else None, terminology.identity() if terminology else None)

    def bind_request(self, library, request: CompileRequest, describe: Callable[[], LibraryGraph], *,
                     durable: bool) -> CompileRequest:
        """``request`` with the relations its providers return for each retrieve and value set it makes."""
        if request.sources or request.data_source:
            raise ProviderError("a client with providers binds no per-type sources or data source "
                                "(BIND-PROVIDER-REQUEST)")
        if self._run is None:
            self._run = self._run_factory()
        bound_request, self.last = bind_request(request, describe(), data=self.data, terminology=self.terminology,
                                                run=self._run, spark=self.spark, schemas=self._schemas,
                                                durable=durable)
        return bound_request


def bind_request(request: CompileRequest, graph: LibraryGraph, *, data: DataProvider | None,
                 terminology: TerminologySource | None, run, schemas, spark=None,
                 durable: bool = False) -> tuple[CompileRequest, BoundProviders]:
    """BIND-PROVIDER-REQUEST: ``request`` with the relations ``data`` and ``terminology`` return for each retrieve
    and value set ``graph`` (``describe`` of this very request) reports. A durable compile refuses a provider that
    is not reusable (`CLIENT-PROVIDER-IDENTITY`). Every caller binds a compile here."""
    if graph.retrieves is None:
        raise CompileError("the compiler service does not report the retrieves a library makes "
                           "(BIND-PROVIDER-REQUEST); deploy a compiler service from the same release as this client")
    bound = bind(graph.retrieves, graph.value_sets, data=data, terminology=terminology, run=run, spark=spark,
                 schemas=schemas)
    if durable and not bound.reusable:
        raise ProviderError(f"{data} is not reusable, so its results are never kept (CLIENT-PROVIDER-IDENTITY): run "
                            f"it with exec(), or bind a reusable provider")
    return replace(request, data=bound.data, terminology=bound.terminology, terminology_table=None), bound
