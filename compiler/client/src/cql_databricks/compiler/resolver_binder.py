"""CLIENT-RESOLVERS, BIND-CALL-SITE: the binder that binds each compile through resolvers.

At each compile it binds the request through :func:`~.relations.bind`. When every resolver binds by rule, the request
carries the rules and the compile is one compiler call. When one binds per site, it first asks ``describe``, with the
compile's own request, for the call sites, and sends that resolver's answers by site id. A durable compile refuses a
resolver that is not reusable: its results are never kept (`CLIENT-RESOLVER-IDENTITY`). Each resolver may be given as
itself or as a factory, called on the first compile, so building a client touches no workspace.

Part of the client core: it imports no platform SDK.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from .providers import CompileError, CompileRequest, LibraryGraph
from .relations import BoundResolvers, ResolverError, bind, binds_by_rule
from .resolvers import DataResolver, TerminologyResolver


class ResolverBinder:
    """Binds every compile to ``data`` and ``terminology``. ``run`` builds the SQL runner of the session that runs the
    compiled SQL; ``spark`` is that session when it is Spark, else None; ``schemas`` returns the model's resource
    schema per FHIR type, for the reshape."""

    def __init__(self, *, data: DataResolver | Callable[[], DataResolver] | None = None,
                 terminology: TerminologyResolver | Callable[[], TerminologyResolver | None] | None = None,
                 run: Callable[[], Any], spark=None,
                 schemas: Callable[[Sequence[str]], Any]) -> None:
        self._data, self._terminology = data, terminology
        self._run_factory, self._run = run, None
        self.spark = spark
        self._schemas = schemas
        self.last: BoundResolvers | None = None

    @property
    def data(self) -> DataResolver | None:
        if self._data is not None and not isinstance(self._data, DataResolver):
            self._data = self._data()
        return self._data

    @property
    def terminology(self) -> TerminologyResolver | None:
        if self._terminology is not None and not isinstance(self._terminology, TerminologyResolver):
            self._terminology = self._terminology()
        return self._terminology

    def generation(self):
        """A compile is cached only against reusable resolvers, by their identities: a development resolver's frames
        can change under the same identity, so its compiles are never reused."""
        data = self.data
        if data is not None and not data.reusable:
            return object()
        terminology = self.terminology
        return (data.identity() if data else None, terminology.identity() if terminology else None)

    def bind_request(self, library, request: CompileRequest, describe: Callable[[], LibraryGraph], *,
                     durable: bool) -> CompileRequest:
        """``request`` bound through its resolvers."""
        if self._run is None:
            self._run = self._run_factory()
        bound_request, self.last = bind_request(request, describe, data=self.data, terminology=self.terminology,
                                                run=self._run, spark=self.spark, schemas=self._schemas,
                                                durable=durable)
        return bound_request


def bind_request(request: CompileRequest, describe: Callable[[], LibraryGraph], *, data: DataResolver | None,
                 terminology: TerminologyResolver | None, run, schemas, spark=None,
                 durable: bool = False) -> tuple[CompileRequest, BoundResolvers]:
    """BIND-CALL-SITE: ``request`` with the ``data`` and ``terminology`` its resolvers give. ``describe`` (of this very
    request) is called only when a resolver binds per site, so a run bound by rule makes one compiler call. A durable
    compile refuses a resolver that is not reusable (`CLIENT-RESOLVER-IDENTITY`). Every caller binds a compile here."""
    sites = None
    if not binds_by_rule(data, terminology):
        graph = describe()
        if graph.sites is None:
            raise CompileError("the compiler service does not report call sites (BIND-CALL-SITE); deploy a compiler "
                               "service from the same release as this client")
        sites = graph.sites
    bound = bind(sites, data=data, terminology=terminology, run=run, spark=spark, schemas=schemas)
    if durable and not bound.reusable:
        raise ResolverError(f"{data} is not reusable, so its results are never kept (CLIENT-RESOLVER-IDENTITY): run "
                            f"it with exec(), or bind a reusable resolver")
    return replace(request, data=bound.data, terminology=bound.terminology), bound
