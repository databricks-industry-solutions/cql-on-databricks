"""RUN-CLIENT-MIGRATION — shared caller preparation for interactive and scheduled calculations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from collections.abc import Mapping

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.providers import CompileRequest, PipelinePlan
from cql_databricks.compiler.relations import BoundResolvers
from cql_databricks.compiler.resolve import ResolutionError, resolve
from cql_databricks.resolvers.interfaces import Resolvers
from cql_databricks.compiler.resolver_binder import bind_request
from cql_databricks.resolvers.value_set_digest import complete_fingerprints

from ._durable_plan import PreparedPlan


@dataclass(frozen=True)
class PreparedCalculation:
    """RUN-CLIENT-RESULT-IDENTITY — one compiled closure, with targets supplied by compiler metadata.

    ``population`` is the name of the environment its resolvers came from, a label; the compiler hashes the identities
    of its resolvers (`CLIENT-RESOLVER-IDENTITY`). ``identities`` are the identities it bound, by role (``data``,
    ``terminology``, ``knowledge``), which a store over official results checks (`RUN-CLIENT-DRAFT-RESULTS`)."""

    library: str
    version: str
    population: str
    targets: tuple[str, ...]
    plan: PipelinePlan
    request: dict | None = None
    identities: Mapping[str, str] | None = None

    def compile_pipeline(self, *, durable: bool = False) -> PipelinePlan:
        """RUN-CLIENT-DURABLE — feed the ordinary durable executor without recompiling."""
        if not durable:
            raise ValueError("prepared calculations support durable execution only")
        return self.plan


def bind_resolvers(request: CompileRequest, *, compiler, resolvers: Resolvers, run,
                   durable: bool = False) -> tuple[CompileRequest, BoundResolvers]:
    """CLIENT-RESOLVERS, BIND-CALL-SITE: ``request`` bound to ``resolvers``' data and terminology, read through ``run``,
    as its identity. Resolvers that bind by rule, as an environment's do, need no ``describe`` call and no catalog
    query (`BIND-PUBLISHED-VIEW`)."""
    return bind_request(request, lambda: compiler.describe(request), data=resolvers.data,
                        terminology=resolvers.terminology, run=run, schemas=compiler.resource_schemas,
                        durable=durable)


def prepare_calculation(*, content, run, compiler, resolvers: Resolvers,
                        runtime_catalog: str, runtime_schema: str = "runtime",
                        library: str | None = None, version: str | None = None,
                        cql: str | None = None, period: tuple[str | None, str | None] = (None, None),
                        targets: tuple[str, ...] | None = None, location: str | None = None,
                        files=None) -> PreparedCalculation:
    """RUN-CLIENT-DURABLE — resolve bindings as the caller and compile unchanged durable recipes.

    Both Job and interactive callers use this preparation. It creates no result objects, reads no
    change log and chooses no destination. Includes are calculated in the same selected closure. ``content`` is
    the knowledge libraries and includes resolve from; ``resolvers`` give the data and value sets (`CLIENT-RESOLVERS`).
    Each kept result's fingerprint is completed with the codes of its value sets, read in one query
    (`CLIENT-RESOLVER-IDENTITY`).

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
    request, bound = bind_resolvers(request, compiler=compiler, resolvers=resolvers, run=run, durable=True)
    plan = complete_fingerprints(compiler.compile_pipeline(request), bound.terminology_tables, run)
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
    request = dict(environment=resolvers.environment, runtime_catalog=runtime_catalog, runtime_schema=runtime_schema,
                   period=[period[0] or f"{year}-01-01", period[1] or f"{year}-12-31"],
                   targets=list(qualified))
    request.update({"cql": cql} if cql is not None else {"library": resolution.name, "version": resolution.version})
    if location is not None:
        request["location"] = str(resolution.location)
    # RUN-CLIENT-DRAFT-RESULTS: what the knowledge came from: CQL text, a location, or the content by name.
    knowledge = ("cql" if cql is not None else str(Location.parse(str(location))) if location is not None
                 else content.identity())
    identities = {"data": resolvers.data.identity(), "terminology": resolvers.terminology.identity(),
                  "knowledge": knowledge}
    return PreparedCalculation(resolution.name, resolution.version, resolvers.environment or "", tuple(qualified),
                               plan, request, identities)
