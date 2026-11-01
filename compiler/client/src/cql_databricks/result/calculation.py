"""RUN-CLIENT-MIGRATION — shared caller preparation for interactive and scheduled calculations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import CompileRequest, PipelinePlan
from cql_databricks.compiler.provider_binder import bind_request
from cql_databricks.compiler.resolve import ResolutionError, resolve
from cql_databricks.compiler.tagged import TaggedResolver

from ._durable_plan import PreparedPlan


@dataclass(frozen=True)
class PreparedCalculation:
    """RUN-CLIENT-RESULT-IDENTITY — one compiled closure, with targets supplied by compiler metadata.

    ``population`` is the name of the data source it binds to; the compiler hashes its identity, the source's
    ``catalog.schema`` (`CLIENT-PROVIDER-IDENTITY`)."""

    library: str
    version: str
    population: str
    targets: tuple[str, ...]
    plan: PipelinePlan
    request: dict | None = None

    def compile_pipeline(self, *, durable: bool = False) -> PipelinePlan:
        """RUN-CLIENT-DURABLE — feed the ordinary durable executor without recompiling."""
        if not durable:
            raise ValueError("prepared calculations support durable execution only")
        return self.plan


def tagged_request(request: CompileRequest, *, compiler, content, run, data_source: str,
                   durable: bool = False) -> CompileRequest:
    """CLIENT-DEFAULT-RESOLVER, BIND-PROVIDER-REQUEST: ``request`` bound to the data source tagged ``data_source`` and
    the tagged terminology in ``content``'s scopes, read through ``run``, as its identity."""
    resolver = TaggedResolver(content, run)
    bound, _ = bind_request(request, compiler.describe(request), data=resolver.data(data_source),
                            terminology=resolver.terminology(), run=run, schemas=compiler.resource_schemas,
                            durable=durable)
    return bound


def prepare_calculation(*, content, run, compiler, data_source: str,
                        runtime_catalog: str, runtime_schema: str = "runtime",
                        library: str | None = None, version: str | None = None,
                        cql: str | None = None, period: tuple[str | None, str | None] = (None, None),
                        targets: tuple[str, ...] | None = None, location: str | None = None,
                        files=None) -> PreparedCalculation:
    """RUN-CLIENT-DURABLE — resolve bindings as the caller and compile unchanged durable recipes.

    Both Job and interactive callers use this preparation. It creates no result objects, reads no
    change log and chooses no destination. Includes are calculated in the same selected closure.

    ``location`` (RUN-ADDRESS-LOCATION) is where the library and its includes are read first, and it is recorded in
    the request so a reopen resolves from the same place. ``files`` reads a folder location; it is not recorded.
    """
    if not runtime_catalog.strip() or not runtime_schema.strip():
        raise ValueError("runtime catalog and schema must be configured")
    resolution = resolve(content, library=library, version=version, cql=cql, location=location,
                         files=files).require_complete()
    if not resolution.name or not resolution.version:
        raise ResolutionError("a calculation needs a library name and version")
    # SVC-COMPILE-BATCH: the compiler translates/types the whole closure; the caller only discovers it.
    year = date.today().year
    request = CompileRequest(
        cql=resolution.source, durable=True,
        measurement_period=(period[0] or f"{year}-01-01", period[1] or f"{year}-12-31"),
        udf_schema=f"{runtime_catalog}.{runtime_schema}",
        deps=tuple(LibrarySource(name=inc.id.name, version=inc.id.version, cql=inc.source)
                   for inc in resolution.includes))
    # CLIENT-DEFAULT-RESOLVER: the named data source and every tagged terminology schema in scope, as the caller.
    request = tagged_request(request, compiler=compiler, content=content, run=run, data_source=data_source,
                             durable=True)
    plan = compiler.compile_pipeline(request)
    PreparedPlan(plan)  # Validate the compiler contract before any caller provisions storage.
    available = {f"{recipe.library}.{recipe.define}": recipe for recipe in plan.datasets
                 if recipe.define and recipe.retrieve_read is None}
    selected = tuple(available) if targets is None else targets
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("calculation requires distinct materialized definition targets")
    qualified = []
    for target in selected:
        matches = [name for name, recipe in available.items()
                   if name == target or (recipe.library == resolution.name and recipe.define == target)]
        if len(matches) != 1:
            raise ValueError(f"target {target!r} must identify exactly one materialized definition")
        qualified.append(matches[0])
    if len(set(qualified)) != len(qualified):
        raise ValueError("duplicate materialization targets")
    request = dict(data_source=data_source, runtime_catalog=runtime_catalog, runtime_schema=runtime_schema,
                   period=[period[0] or f"{year}-01-01", period[1] or f"{year}-12-31"],
                   targets=list(qualified))
    request.update({"cql": cql} if cql is not None else {"library": resolution.name, "version": resolution.version})
    if location is not None:
        request["location"] = str(resolution.location)
    return PreparedCalculation(resolution.name, resolution.version, data_source, tuple(qualified), plan, request)
