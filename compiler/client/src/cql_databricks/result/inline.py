"""CLIENT-INLINE-WAREHOUSE — run CQL on a SQL warehouse session and keep nothing.

The inline path ``cql(...).exec()`` takes for a Spark session, over a warehouse runner instead: compile
the whole library through ``/compile-sql``, create its temporary views in the caller's session, count
what is asked, then drop the views. No table is written and no result is tagged or reused. Every run
reads its data source as it is now (`DEC-RESULT-IDENTITY`: freshness is the data source's).

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
from dataclasses import dataclass, field
from datetime import date

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import CompileRequest, SqlCompileResult
from cql_databricks.compiler.resolve import resolve, resolve_data_source_routines

from .insights.score import POPULATIONS, MeasureScore, measure_score, subjects_sql
from .relation import SUBJECT_ID, quote


@dataclass
class InlineRun:
    """CLIENT-INLINE-WAREHOUSE — one inline run's temporary views, readable until :meth:`close`.

    ``cql`` is the exact root CQL that ran (`AGENT-CQL-REVIEW`). ``defines`` are the root library's
    defines, in compiler order. Use it as a context manager so the views are dropped."""

    library: str
    version: str
    data_source: str
    period: tuple[str, str]
    cql: str
    defines: tuple[str, ...]
    _relations: dict[str, str]
    _objects: tuple[str, ...]
    _run: object = field(repr=False)
    _columns: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    def relation(self, define: str) -> str:
        if define not in self._relations:
            raise KeyError(f"{define!r} is not a define of {self.library or 'this library'}")
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


def run_inline(*, content, run, compiler, data_source: str, cql: str, runtime_catalog: str,
               runtime_schema: str = "runtime", period: tuple[str | None, str | None] = (None, None),
               location: str | None = None, files=None) -> InlineRun:
    """CLIENT-INLINE-WAREHOUSE — compile ``cql`` with its include closure and run it inline on ``run``.

    ``run`` is a warehouse session runner (``WarehouseSession``), ``compiler`` a provider whose
    ``/compile-sql`` renders barriers as views. Includes resolve from tagged knowledge, as for a durable
    calculation (`CLIENT-KNOWLEDGE-INCLUDES`), first from ``location`` when given (RUN-ADDRESS-LOCATION), a folder
    read through ``files``. The period defaults to the current calendar year.
    """
    if not runtime_catalog.strip() or not runtime_schema.strip():
        raise ValueError("runtime catalog and schema must be configured")
    resolution = resolve(content, cql=cql, location=location, files=files).require_complete()
    layout = resolve_data_source_routines(content, run, data_source)
    year = date.today().year
    bounds = (period[0] or f"{year}-01-01", period[1] or f"{year}-12-31")
    compiled: SqlCompileResult = compiler.compile_sql(CompileRequest(
        cql=resolution.source, measurement_period=bounds, data_source=layout,
        terminology_table=f"{runtime_catalog}.{runtime_schema}.valuesets",
        udf_schema=f"{runtime_catalog}.{runtime_schema}",
        object_prefix=f"inline_{uuid.uuid4().hex[:12]}",
        deps=tuple(LibrarySource(name=inc.id.name, version=inc.id.version, cql=inc.source)
                   for inc in resolution.includes)))
    barriers = [s.name for s in compiled.statements if s.kind != "view"]
    if barriers:
        raise ValueError(f"inline runs create views only; the compiler returned {barriers}. Compile through "
                         "CqlDatabricksCompileProvider (SVC-RUN-SERVERLESS-BARRIER)")
    relations = _relations(compiled)
    objects: list[str] = []
    inline = InlineRun(library=resolution.name, version=resolution.version, data_source=data_source,
                       period=bounds, cql=cql, defines=tuple(compiled.defines), _relations=relations,
                       _objects=(), _run=run)
    try:
        for statement in compiled.statements:
            run(statement.sql)
            objects.append(statement.name)
    except BaseException:
        inline._objects = tuple(objects)
        inline.close()
        raise
    inline._objects = tuple(objects)
    return inline


def _relations(compiled: SqlCompileResult) -> Mapping[str, str]:
    """Each root define's view, from the compiler's published shapes (`EMIT-SQL-VALUE-COLUMN`)."""
    missing = [d for d in compiled.defines if not (compiled.shape(d) and compiled.shape(d).obj)]
    if missing:
        raise ValueError(f"the compiler published no object for {missing}; deploy a compiler service from "
                         "the same release as this client")
    return {d: compiled.shape(d).obj for d in compiled.defines}
