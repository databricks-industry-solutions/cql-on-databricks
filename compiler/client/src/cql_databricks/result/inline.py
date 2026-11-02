"""CLIENT-INLINE-WAREHOUSE — run CQL on a SQL warehouse session and keep nothing.

The inline path ``cql(...).exec()`` takes for a Spark session, over a warehouse runner instead: create the library's
temporary views in the caller's session, count what is asked, then drop the views. No table is written and nothing is
tagged.

**REUSE, READ-ONLY.** Given a result resolver, and binding only reusable resolvers, a run compiles the durable plan,
finds every define's kept result in one lookup (the caller's own, then the environment's official ones,
`RUN-CLIENT-RESULT-OWNERSHIP`), reads each one found from its table, and creates a view only for the rest, reading the
found tables in place of their defines (`CLIENT-RESULT-RESOLVER`). Without one, or with a resolver that is not reusable,
it compiles the whole library through ``/compile-sql``. A kept result is as fresh as when it was computed
(`DEC-RESULT-IDENTITY`: freshness is the data source's); a view reads the data as it is now.

**ONE PREFIX PER RUN** (`EMIT-SQL-OBJECT-PREFIX`). An App holds one warehouse session per user
(`RUN-CLIENT-SESSION-REUSE`), so two runs by one user share a temporary namespace. Each run compiles
under its own prefix, so neither replaces the other's views.

**BARRIERS MUST BE VIEWS.** A warehouse rejects ``CACHE TABLE ... AS SELECT``. Compile through
``CqlDatabricksCompileProvider`` (`SVC-RUN-SERVERLESS-BARRIER`); a ``cache_table`` statement here
raises rather than being rewritten a second time.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import CompileRequest, SqlCompileResult
from cql_databricks.compiler.resolve import resolve
from cql_databricks.resolvers.interfaces import Resolvers
from cql_databricks.resolvers.value_set_digest import complete_fingerprints

from ._catalog_runtime import result_key
from ._durable_delta import table_sql
from ._durable_plan import PreparedPlan
from .calculation import bind_resolvers
from .insights.score import POPULATIONS, MeasureScore, measure_score, subjects_sql
from .relation import SUBJECT_ID, quote


@dataclass
class InlineRun:
    """CLIENT-INLINE-WAREHOUSE — one inline run's temporary views, readable until :meth:`close`.

    ``cql`` is the exact root CQL that ran (`AGENT-CQL-REVIEW`). ``defines`` are the root library's
    defines, in compiler order. ``reused`` maps each root define read from a kept result to its table
    (`CLIENT-RESULT-RESOLVER`). Use it as a context manager so the views are dropped; kept results are never dropped."""

    library: str
    version: str
    environment: str
    period: tuple[str, str]
    cql: str
    defines: tuple[str, ...]
    _relations: dict[str, str]
    _objects: tuple[str, ...]
    _run: object = field(repr=False)
    reused: dict[str, str] = field(default_factory=dict)
    _columns: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    def relation(self, define: str) -> str:
        if define not in self._relations:
            raise KeyError(f"{define!r} is not a define of {self.library or 'this library'}")
        if define in self.reused:
            return self._relations[define]
        return quote(self._relations[define])

    def columns(self, define: str) -> dict[str, str]:
        """The view's columns and types, read once with ``DESCRIBE``."""
        if define not in self._columns:
            rows = self._run.rows(f"DESCRIBE {self.relation(define)}") or []
            self._columns[define] = {str(r[0]): str(r[1]) for r in rows
                                     if r and r[0] and not str(r[0]).startswith("#")}
        return self._columns[define]

    def rows(self, sql: str) -> list:
        """Run one read over this run's views, on the session they live in."""
        return self._run.rows(sql) or []

    def subjects(self, define: str) -> str:
        """SQL selecting the distinct subjects who met ``define``."""
        return subjects_sql(self.relation(define), self.columns(define))

    def counts(self, defines: tuple[str, ...] | None = None) -> dict[str, int]:
        """Distinct subjects who met each define, as written: no cascade. Defines with no subject column
        (an unfiltered-context value) are left out, never counted as zero."""
        names = [d for d in (defines or self.defines) if SUBJECT_ID in self.columns(d)]
        if not names:
            return {}
        sql = "\nUNION ALL\n".join(
            f"SELECT {i} AS d, COUNT(DISTINCT {SUBJECT_ID}) AS n FROM ({self.subjects(name)})"
            for i, name in enumerate(names))
        rows = self._run.rows(sql) or []
        return {names[int(r[0])]: int(r[1] or 0) for r in rows}

    def score(self, scoring: str | None) -> MeasureScore:
        """RUN-CLIENT-MEASURE-SCORE — the score over this run's populations."""
        present = [d for d in self.defines if d in POPULATIONS]
        return measure_score(scoring=scoring, declared=self.defines,
                             subjects={d: self.subjects(d) for d in present}, execute=self._run.rows)

    def close(self) -> None:
        """Drop every view this run created, last first."""
        for name in reversed(self._objects):
            self._run(f"DROP VIEW IF EXISTS {quote(name)}")
        self._objects = ()

    def __enter__(self) -> InlineRun:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def run_inline(*, content, run, compiler, resolvers: Resolvers, cql: str, runtime_catalog: str,
               runtime_schema: str = "runtime", period: tuple[str | None, str | None] = (None, None),
               location: str | None = None, files=None) -> InlineRun:
    """CLIENT-INLINE-WAREHOUSE — compile ``cql`` with its include closure and run it inline on ``run``.

    ``run`` is a warehouse session runner (``WarehouseSession``), ``compiler`` a provider whose
    ``/compile-sql`` renders barriers as views. Includes resolve from ``content``, as for a durable calculation
    (`CLIENT-KNOWLEDGE-INCLUDES`), first from ``location`` when given (RUN-ADDRESS-LOCATION), a folder read through
    ``files``. ``resolvers`` give the data and value sets, and, when ``resolvers.results`` is set, the kept results
    the run reads in place of computing (`CLIENT-RESULT-RESOLVER`). The period defaults to the current calendar year.
    """
    if not runtime_catalog.strip() or not runtime_schema.strip():
        raise ValueError("runtime catalog and schema must be configured")
    resolution = resolve(content, cql=cql, location=location, files=files).require_complete()
    year = date.today().year
    bounds = (period[0] or f"{year}-01-01", period[1] or f"{year}-12-31")
    prefix = f"inline_{uuid.uuid4().hex[:12]}"
    request = CompileRequest(
        cql=resolution.source, measurement_period=bounds,
        udf_schema=f"{runtime_catalog}.{runtime_schema}", object_prefix=prefix,
        deps=tuple(LibrarySource(name=inc.id.name, version=inc.id.version, cql=inc.source)
                   for inc in resolution.includes))
    inline = InlineRun(library=resolution.name, version=resolution.version, environment=resolvers.environment or "",
                       period=bounds, cql=cql, defines=(), _relations={}, _objects=(), _run=run)
    if resolvers.results is not None and resolvers.data.reusable:
        statements = _reusing(inline, request, prefix, compiler=compiler, resolvers=resolvers, run=run)
    else:
        statements = _compiled(inline, request, compiler=compiler, resolvers=resolvers, run=run)
    objects: list[str] = []
    try:
        for name, sql in statements:
            run(sql)
            objects.append(name)
    except BaseException:
        inline._objects = tuple(objects)
        inline.close()
        raise
    inline._objects = tuple(objects)
    return inline


def _compiled(inline: InlineRun, request: CompileRequest, *, compiler, resolvers: Resolvers,
              run) -> list[tuple[str, str]]:
    """The whole library through ``/compile-sql``: one view per statement."""
    compiled: SqlCompileResult = compiler.compile_sql(
        bind_resolvers(request, compiler=compiler, resolvers=resolvers, run=run)[0])
    barriers = [s.name for s in compiled.statements if s.kind != "view"]
    if barriers:
        raise ValueError(f"inline runs create views only; the compiler returned {barriers}. Compile through "
                         "CqlDatabricksCompileProvider (SVC-RUN-SERVERLESS-BARRIER)")
    inline.defines = tuple(compiled.defines)
    inline._relations = dict(_relations(compiled))
    return [(s.name, s.sql) for s in compiled.statements]


def _reusing(inline: InlineRun, request: CompileRequest, prefix: str, *, compiler, resolvers: Resolvers,
             run) -> list[tuple[str, str]]:
    """CLIENT-RESULT-RESOLVER — the durable plan, every define's kept result found in one lookup, and one view per
    define not found, in plan order, reading found tables and earlier views in place of the defines they read."""
    # RUN-CLIENT-RESULT-IDENTITY: no object prefix, which would change the emitted SQL and so every fingerprint; this
    # run's views take the prefix as their names instead.
    request, bound = bind_resolvers(replace(request, durable=True, object_prefix=None), compiler=compiler,
                                    resolvers=resolvers, run=run, durable=True)
    plan = PreparedPlan(complete_fingerprints(compiler.compile_pipeline(request), bound.terminology_tables, run))
    keys = {d.key: result_key(d) for d in plan.definitions}
    found = resolvers.results.find([keys[d.key] for d in plan.definitions if d.reusable])
    statements: list[tuple[str, str]] = []
    relations: dict[str, _Relation] = {}
    for i, definition in enumerate(plan.definitions):
        kept = found.get(definition.fingerprint) if definition.reusable else None
        if kept is not None:
            relations[definition.key] = _Relation((kept.table.catalog, kept.table.schema, kept.table.table))
            continue
        name = f"{prefix}_{i}"
        query = plan.query_for(definition.key, {k: relations[k] for k in definition.reads})
        statements.append((name, f"CREATE TEMPORARY VIEW {quote(name)} AS {query}"))
        relations[definition.key] = _Relation((name,))
    root = [(d, keys[d.key]) for d in plan.definitions if keys[d.key].library == inline.library]
    inline.defines = tuple(key.define for _, key in root)
    for definition, key in root:
        relation = relations[definition.key]
        if len(relation.table) == 3:
            inline.reused[key.define] = table_sql(relation.table)
            inline._relations[key.define] = table_sql(relation.table)
        else:
            inline._relations[key.define] = relation.table[0]
    return statements


@dataclass(frozen=True)
class _Relation:
    """Where a define's rows are, for ``PreparedPlan.query_for``: a kept result's table, or a view this run made."""
    table: tuple[str, ...]


def _relations(compiled: SqlCompileResult) -> Mapping[str, str]:
    """Each root define's view, from the compiler's published shapes (`EMIT-SQL-VALUE-COLUMN`)."""
    missing = [d for d in compiled.defines if not (compiled.shape(d) and compiled.shape(d).obj)]
    if missing:
        raise ValueError(f"the compiler published no object for {missing}; deploy a compiler service from "
                         "the same release as this client")
    return {d: compiled.shape(d).obj for d in compiled.defines}
