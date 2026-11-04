"""CLIENT-RESOLVERS, BIND-CALL-SITE: the binder that binds each compile through resolvers.

At each compile it binds the request through :func:`~.relations.bind`. When every resolver binds by rule, the request
carries the rules and the compile is one compiler call. When one binds per site, it first asks ``describe``, with the
compile's own request, for the call sites, and sends that resolver's answers by site id. A durable compile refuses a
resolver that is not reusable: its results are never kept (`CLIENT-RESOLVER-IDENTITY`). Each resolver may be given as
itself or as a factory, called on first use, so building a client touches no workspace. ``results`` is where a run finds
kept results (`CLIENT-QUERY-REUSE`); the binder never reads it itself. The binder keeps the data views' shapes it reads,
so a session reads an environment's shapes once (`DATA-MODEL-SUBSET`); :meth:`forget_shapes` reads them again.

Part of the client core: it imports no platform SDK.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from .providers import CompileError, CompileRequest, LibraryGraph
from .relations import BoundResolvers, ResolverError, bind, binds_by_rule
from cql_databricks.resolvers.interfaces import DataResolver, ResultResolver, TerminologyResolver


class ResolverBinder:
    """Binds every compile to ``data`` and ``terminology``. ``run`` builds the SQL runner of the session that runs the
    compiled SQL; ``spark`` is that session when it is Spark, else None; ``schemas`` returns the model's resource
    schema per FHIR type, for the reshape. ``results`` is the run's result resolver, or None for a run that reads no
    kept result."""

    def __init__(self, *, data: DataResolver | Callable[[], DataResolver] | None = None,
                 terminology: TerminologyResolver | Callable[[], TerminologyResolver | None] | None = None,
                 run: Callable[[], Any], spark=None,
                 schemas: Callable[[Sequence[str]], Any],
                 results: ResultResolver | Callable[[], ResultResolver] | None = None) -> None:
        self._data, self._terminology, self._results = data, terminology, results
        self._run_factory, self._run = run, None
        self.spark = spark
        self._schemas = schemas
        self._shapes: dict[str, dict[str, str]] = {}
        self.last: BoundResolvers | None = None

    def forget_shapes(self) -> None:
        """DATA-MODEL-SUBSET — read the data views' shapes again at the next compile, after a view changed."""
        self._shapes.clear()

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

    @property
    def results(self) -> ResultResolver | None:
        if self._results is not None and not isinstance(self._results, ResultResolver):
            self._results = self._results()
        return self._results

    @property
    def reusable(self) -> bool:
        """CLIENT-RESOLVER-IDENTITY — whether a run over the data resolver may be kept, and so found."""
        return self.data is None or self.data.reusable

    @property
    def run(self):
        """The SQL runner of the session that runs the compiled SQL, built on first use."""
        if self._run is None:
            self._run = self._run_factory()
        return self._run

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
        bound_request, self.last = bind_request(request, describe, data=self.data, terminology=self.terminology,
                                                run=self.run, spark=self.spark, schemas=self._schemas,
                                                durable=durable, shape_cache=self._shapes)
        return bound_request


def bind_request(request: CompileRequest, describe: Callable[[], LibraryGraph], *, data: DataResolver | None,
                 terminology: TerminologyResolver | None, run, schemas, spark=None, durable: bool = False,
                 shape_cache: dict[str, dict[str, str]] | None = None) -> tuple[CompileRequest, BoundResolvers]:
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
    bound = bind(sites, data=data, terminology=terminology, run=run, spark=spark, schemas=schemas,
                 shape_cache=shape_cache)
    if durable and not bound.reusable:
        raise ResolverError(f"{data} is not reusable, so its results are never kept (CLIENT-RESOLVER-IDENTITY): run "
                            f"it with exec(), or bind a reusable resolver")
    return replace(request, data=bound.data, terminology=bound.terminology), bound
