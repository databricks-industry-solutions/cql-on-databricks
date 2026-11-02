"""``cql()`` — the runtime-compiled CQL query surface for Spark (PHASE-10, BUILD-CLIENT).

``cql(source)`` (or ``client.cql(source)``) binds a CQL library — a raw CQL / ELM STRING, a packaged
``resource`` path, or a :class:`~.library.Library` — and returns a lazy :class:`CqlQuery` HANDLE. The handle
lets you inspect compile-time artifacts (the front-end ELM, the neutral define DAG) and, terminally,
``exec(*targets)`` the requested defines — compiling to a Spark SQL SCRIPT (`EXEC-SQL-BACKEND`), running it
via ``spark.sql``, and returning a lazy DataFrame per target. Execution + governance happen at the executor;
the compiler touches no data.

Source in the front, define-names in ``exec`` — a string is always SOURCE, never a target, so
``cql("Numerator")`` can never be mistaken for "compile the Numerator define". The compiled SQL is an
INTERNAL implementation detail (it may change or be eliminated) and is deliberately NOT exposed; what IS
exposed is the neutral DAG (`describe`/`dag`, what an SDP reads to build its own graph) and, per define, a
DataFrame whose native ``.explain()`` gives the Spark plan.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from . import config
from .binding import bound_request
from .library import Library
from .providers import CompileError, CompileRequest, LibraryGraph, PipelinePlan, SqlCompileResult


def cql(source=None, *, resource: Optional[str] = None, cql: Optional[str] = None,
        elm: Optional[str] = None, library: Optional[str] = None, context: Optional[str] = None,
        data_model: Optional[str] = None, spark=None, client=None,
        endpoint: Optional[str] = None, engine: Optional[str] = None,
        recompute: Optional[bool] = None) -> "CqlQuery":
    """Bind ``source`` and return a lazy :class:`CqlQuery` handle (`PIPE-RUNTIME-COMPILE`), peer to how
    ``spark.sql`` names a query — except selection of WHICH defines to evaluate happens terminally in
    ``exec(*targets)``, not here.

    ``source`` is a raw CQL / ELM STRING, a packaged ``resource`` path (``…​.cql`` / ``…​.elm.json``), or an
    already-bound :class:`~.library.Library` (equivalently pass ``resource=`` / ``cql=`` / ``elm=``). A string
    (or ``resource``/``cql``/``elm``) is bound against ``client`` if given, else a client built lazily from
    the `BUILD-RESOLVE` config surface (conf/env + provider plugins) — so with config present
    ``cql('define "Two": 2 + 2').exec()`` just works, and with none it FAILS LOUDLY
    (:class:`~.config.ConfigResolutionError`). ``context`` (`LANG-CONTEXT-DEFAULT`) and ``data_model``
    configure the bind (string form only).

    ``library=`` addresses a library by NAME (optionally ``"Name|version"``) through the session's
    :class:`~.libraries.LibraryProvider` — ``cql(library="HypertensionBPControl")``. It is a
    SEPARATE kwarg, never an overload of the ``source`` string: the string classification (path / ELM / raw
    CQL) is frozen (`BUILD-CLIENT-COMPAT`), so a bare name would otherwise be raw CQL forever.

    ``engine`` and ``recompute`` are the explicit tier of the session's settings
    (EVALUATOR-CLIENT-EVALUATE, `DEC-ENGINE-SESSION-OPTIONS`); a session sets them once and omits them here."""
    needs_bind = isinstance(source, str) or source is None or resource or cql or elm or library
    if needs_bind:
        c = client if client is not None else _lazy_client(spark=spark, endpoint=endpoint)
        return c.cql(source, resource=resource, cql=cql, elm=elm, library=library, context=context,
                     data_model=data_model, spark=spark, engine=engine, recompute=recompute)
    # An already-bound library value object (or a duck-typed one): wrap it directly, no client needed.
    if engine is not None or recompute is not None:
        raise TypeError("engine/recompute apply to a source STRING; bind them on the library instead")
    if context is not None or data_model is not None:
        raise TypeError("context/data_model apply to a source STRING; bind them on the library instead")
    return CqlQuery(source, spark=spark)


class CqlQuery:
    """A lazy, compile-once CQL query handle (`BUILD-CLIENT-QUERY`). Wraps a resolved
    :class:`~.library.Library` (the ELM/CQL, provider, context, config overrides, parameter bindings) plus an
    optional Spark session. Immutable-fluent: ``with_parameter`` / ``with_measurement_period`` (and their
    bare aliases ``parameter`` / ``measurement_period``) return a NEW handle, so one bound query can be
    reused and specialized. Terminal ``exec(*targets)`` runs it. Compile results are memoized (compile is
    pure + data-free) so inspecting then running does not double round-trip the service."""

    def __init__(self, library: Library, spark=None):
        self._lib = library
        self._spark = spark
        self._describe_cache: Optional[LibraryGraph] = None
        self._compile_cache: Dict[tuple, SqlCompileResult] = {}

    # -- immutable-fluent refinement -------------------------------------------------------------
    def with_parameter(self, name: str, value) -> "CqlQuery":
        """Bind CQL parameter ``name`` to ``value`` (returns a NEW handle, `BUILD-CLIENT-SESSION`)."""
        return CqlQuery(self._lib.with_parameter(name, value), self._spark)

    def with_measurement_period(self, start: str, end: str) -> "CqlQuery":
        """Bind ``"Measurement Period"`` to the closed ``Interval[@start, @end]`` (returns a NEW handle)."""
        return CqlQuery(self._lib.with_measurement_period(start, end), self._spark)

    # Bare-verb aliases — a chained value refinement reads fine without the `with_` prefix.
    parameter = with_parameter
    measurement_period = with_measurement_period

    # -- compile-time artifacts (no execution) ---------------------------------------------------
    def elm(self) -> str:
        """The front-end ELM JSON for this library (`EMIT-INTERCHANGE`) — the shared, portable IR. Empty
        when bound from raw CQL not yet translated (the service translates on compile; a dedicated
        translated-ELM egress endpoint is a deferred service capability)."""
        return self._lib.explain_elm()

    def describe(self, *, boundaries: Optional[Iterable[str]] = None,
                 grain_first: bool = False) -> LibraryGraph:
        """The NEUTRAL define DAG (`BUILD-META`): defines, dependency edges, cost-chosen materialization
        boundaries, the population defines, pinned versions — what an SDP reads to build its OWN dataset
        graph, and the index of which defines are materialized BARRIERS (``materialized_defines``). No
        execution needed. The default (no args) result is memoized."""
        if boundaries is None and not grain_first:
            if self._describe_cache is None:
                self._describe_cache = describe(self._lib)
            return self._describe_cache
        return describe(self._lib, boundaries=boundaries, grain_first=grain_first)

    dag = describe  # alias: the DAG IS the describe result

    def defines(self) -> List[str]:
        """The library's define names."""
        return list(self.describe().defines)

    def populations(self) -> List[str]:
        """The measure POPULATION defines present in this library (`BUILD-CLIENT-POPULATIONS`) — the default
        target set of :meth:`exec`. The intersection of the well-known eCQM population names with the actual
        defines (a convention; the authoritative source is a FHIR ``Measure``, a deferred service
        enrichment). Empty for an ad-hoc library with no populations."""
        return _population_defaults(self.describe().defines)

    def default_targets(self) -> List[str]:
        """What :meth:`exec` with no targets runs: the :meth:`populations`, else every define. For the
        session library, the same rule over only the defines of the fragment that bound it — a cell shows
        its own result, not the whole session's (`SVC-NOTEBOOK-SESSION-SCOPE`)."""
        own = getattr(self._lib, "fragment_defines", None)
        if own is not None:
            return _population_defaults(own) or list(own)
        return self.populations() or self.defines()

    def compile_pipeline(self, *, boundaries: Optional[Iterable[str]] = None,
                         grain_first: bool = False, durable: bool = False) -> PipelinePlan:
        """The compile-once :class:`~.providers.PipelinePlan` (`PIPE-SQL-DAG-REUSE`) — one dataset recipe per
        materialized define. What the reference SDP turns into ``@dp.materialized_view`` bodies."""
        return _pipeline(self._lib, boundaries=boundaries,
                         grain_first=grain_first, durable=durable)

    # -- terminal execution ----------------------------------------------------------------------
    def exec(self, *targets: str, boundaries: Optional[Iterable[str]] = None,
             persist_as: Optional[str] = None):
        """Evaluate ``targets`` (`EXEC-SQL-BACKEND`) and return a lazy DataFrame result.

        Result arity depends on the CALL, never on the library's contents (`BUILD-CLIENT-QUERY`):
        ``exec("X")`` — exactly ONE caller-named target — returns that define's DataFrame; ``exec()`` (the
        default population set, :meth:`populations`, else all defines) and ``exec("X", "Y")`` always return
        a ``dict`` mapping each define name to its DataFrame, EVEN when the set holds a single define. So a
        caller that writes ``for name, df in q.exec().items()`` keeps working on a one-population library,
        and a later switch to authoritative populations metadata cannot silently change the return TYPE.
        Each frame is independently ``.explain()``-able — the way to view a materialized BARRIER's plan.

        ``boundaries`` selects the compiler's RUNTIME cost-free mode; ``persist_as`` (single named target
        only, `BUILD-META`) also writes ``CREATE TABLE <persist_as> AS SELECT * FROM <target>``. The runtime
        must provide the ``retrieve_<Type>`` functions the plan references: the library's binder chooses
        them (`CLIENT-EXEC-DISCOVERY`), else they are the bare convention names."""
        single = len(targets) == 1                    # the CALL shape, not the resolved set's size
        session = getattr(self._lib, "fragment_defines", None) is not None
        if session and not targets:
            targets = tuple(self.default_targets())
            if not targets:
                raise ValueError("nothing to evaluate: the fragment declares no defines")
        engine = getattr(self._lib, "engine", config.LOCAL_ENGINE) or config.LOCAL_ENGINE
        if engine != config.LOCAL_ENGINE:
            # The engine compiles the closure itself, so it also picks the default targets: a local describe
            # would need the closure on this session's library path, which the engine does not use.
            frames = self._exec_on(engine, list(targets), boundaries=boundaries, persist_as=persist_as)
            return frames[targets[0]] if single else frames
        tset = list(targets) or self.default_targets()
        if not tset:
            raise ValueError("nothing to evaluate: the library has no defines")
        sp = self._spark or _active_spark()
        _ship_udf_catalog(sp, self._lib)
        if single and not session:
            result = self._compile(tset[0], boundaries)
            _run_statements(sp, result)
            return _select(sp, result.target, persist_as)
        if persist_as is not None and not single:
            raise TypeError("persist_as is only valid when exec() names exactly one target")
        # Multiple targets: compile the WHOLE library ONCE (shared view DAG), run it, then hand back a lazy
        # SELECT per requested define. Views are lazy, so a define is only computed when its frame is
        # actioned; the cost-chosen barriers (materialized once) are shared across the populations.
        # The session library always takes this path: every run re-creates every session view, so a view
        # built on a name a later fragment redefined is never stale (SVC-NOTEBOOK-SESSION-SCOPE).
        result = self._compile(None, boundaries)
        _run_statements(sp, result)
        if single:
            return _select(sp, _object_for(tset[0], result), persist_as)
        return {t: _select(sp, _object_for(t, result), None) for t in tset}

    def _exec_on(self, engine: str, targets: List[str], *, boundaries, persist_as) -> dict:
        """EVALUATOR-CLIENT-EVALUATE — run on another engine (`engines.engine_for`). It compiles and keeps its
        own results, so the local compile knobs do not apply to it and are refused rather than ignored."""
        from .engines import EngineRequest, engine_for

        if boundaries is not None or persist_as is not None:
            raise TypeError(f"boundaries/persist_as are the local engine's; the {engine!r} engine keeps its own "
                            f"results (DEC-ONE-VERB-ENGINE-OPTION)")
        source = getattr(self._lib, "cql", None)
        if not source:
            raise ValueError(f"the {engine!r} engine runs CQL text, and this query was bound from ELM; bind it "
                             f"from CQL (a source string, a .cql resource, or library= with CQL on the path)")
        request = EngineRequest(
            cql=source, targets=list(targets),
            measurement_period=getattr(self._lib, "measurement_period", None),
            recompute=bool(getattr(self._lib, "recompute", False)),
            settings=dict(getattr(self._lib, "settings", None) or {}), spark=self._spark or _active_spark())
        frames = engine_for(engine).exec(request)
        missing = [t for t in targets if t not in frames]
        if missing or not frames:
            raise RuntimeError(f"the {engine!r} engine returned no result for {missing or 'any define'}")
        return {t: frames[t] for t in targets} if targets else frames

    def with_recompute(self, recompute: bool = True) -> "CqlQuery":
        """RUN-RECOMPUTE-OVERRIDE — on an engine that keeps results, run every define this query names even
        where its stored rows are current (returns a NEW handle). The local engine keeps nothing."""
        from dataclasses import replace
        return CqlQuery(replace(self._lib, recompute=recompute), self._spark)

    def _compile(self, target: Optional[str], boundaries: Optional[Iterable[str]]) -> SqlCompileResult:
        # CLIENT-RESOLVER-IDENTITY: a binder whose choice can change keys the cache by its generation, so a query
        # compiled before the change recompiles against the new relations.
        generation = getattr(getattr(self._lib, "binder", None), "generation", None)
        key = (target, frozenset(boundaries) if boundaries is not None else None,
               generation() if generation is not None else None)
        cached = self._compile_cache.get(key)
        if cached is None:
            # BIND-CALL-SITE: the binder binds this compile's call sites.
            req = bound_request(self._lib, _request(self._lib, target=target, boundaries=boundaries))
            cached = self._lib.provider.compile_sql(req)
            self._compile_cache[key] = cached
        return cached


def _request(lib, *, target: Optional[str] = None, boundaries: Optional[Iterable[str]] = None,
             grain_first: bool = False, durable: bool = False) -> CompileRequest:
    """Build the :class:`~.providers.CompileRequest` for ``lib`` — the ONE place a Library's fields are
    mapped onto the wire, so ``compile_sql`` / ``describe`` / ``compile_pipeline`` cannot drift apart (the
    `BUILD-COMPILE-LINK` closure in particular must ride on ALL THREE: a describe that silently omits the
    deps reports a DAG missing every cross-library define).

    Every field is read with ``getattr`` defaults so a DUCK-TYPED library (the SDP stubs hold one, and
    tests fake one) keeps working as new fields are added — the append-only rule of `BUILD-CLIENT-COMPAT`
    applied inward as well as on the wire."""
    return CompileRequest(
        elm=lib.elm,
        cql=lib.cql,
        target=target,
        boundaries=set(boundaries) if boundaries is not None else None,
        context=lib.context,
        overrides=lib.overrides,
        parameters=lib.parameters,
        grain_first=grain_first,
        durable=durable,
        deps=tuple(getattr(lib, "deps", ()) or ()),
        object_prefix=getattr(lib, "object_prefix", None),
        udf_schema=getattr(lib, "udf_schema", None),
        session_parameters=getattr(lib, "session_parameters", None) or {})


def _population_defaults(defines: Iterable[str]) -> List[str]:
    """The well-known eCQM population defines present in ``defines``, in canonical order
    (`BUILD-CLIENT-POPULATIONS`)."""
    present = set(defines)
    return [n for n in config.POPULATION_DEFINE_NAMES if n in present]


def _object_for(define: str, result: SqlCompileResult) -> str:
    """The runtime object a whole-library compile produced for ``define``. Internal name resolution — the
    SQL statement names are NEVER exposed to the caller.

    Read from the compiler's PUBLISHED per-define shape (`EMIT-SQL-VALUE-COLUMN`: ``DefineShape.obj``),
    which exists precisely so a consumer never re-derives ``SqlNames.sanitize``. That mapping is
    deliberately one-way and NOT injective, and it is now prefix-namespaced (`EMIT-SQL-OBJECT-PREFIX`), so
    re-deriving it in Python would silently miss both the prefix and any collision the compiler resolved.

    The sanitize fallback applies ONLY when talking to an older compile service that publishes no shapes
    (`BUILD-CLIENT-COMPAT`); it cannot know about a prefix, so it is used only when the statement names
    confirm the bare form."""
    shape = result.shape(define)
    if shape is not None and shape.obj:
        return shape.obj
    import re  # pre-shapes service only
    sanitized = re.sub(r"[^A-Za-z0-9]", "_", define)
    return sanitized if sanitized in {s.name for s in result.statements} else define


def describe(library: Library, boundaries: Optional[Iterable[str]] = None,
             grain_first: bool = False) -> LibraryGraph:
    """Return the library's NEUTRAL graph metadata (defines, dependency edges, cost-chosen boundaries,
    pinned versions) via the active provider — what an external pipeline reads to build its own dataset
    graph. This repo DESCRIBES a pipeline; it does not generate one. No Spark session or execution needed."""
    return library.provider.describe(
        _request(library, boundaries=boundaries, grain_first=grain_first))


def compile_pipeline(library: Library, boundaries: Optional[Iterable[str]] = None,
                     grain_first: bool = False, *, durable: bool = False) -> PipelinePlan:
    """Return the library's compile-once :class:`PipelinePlan` (`PIPE-SQL-DAG-REUSE`) via the active
    provider — one dataset recipe per materialized define, cut from a single whole-library lowering. What
    the reference pipeline turns into one ``@dp.materialized_view`` per recipe (no per-node ``cql()``). No
    Spark session or execution needed (pure compile). The library's binder binds its call sites
    (`BIND-CALL-SITE`)."""
    return _pipeline(library, boundaries=boundaries, grain_first=grain_first, durable=durable)


def _pipeline(library, *, boundaries, grain_first: bool, durable: bool) -> PipelinePlan:
    """The one pipeline compile, bound by the library's binder (`BIND-CALL-SITE`)."""
    request = _request(library, boundaries=boundaries, grain_first=grain_first, durable=durable)
    return library.provider.compile_pipeline(bound_request(library, request, durable=durable))


def _lazy_client(*, spark=None, endpoint: Optional[str] = None):
    """Build a client from the `BUILD-RESOLVE` config surface (conf/env + provider plugins) for a bare
    ``cql("…")`` with no explicit client. There is NO ambient global (that was confusing): a platform host
    contributes its provider as an entry-point PLUGIN, so the lazily-built client resolves to it without any
    registration; a hand-built client is passed explicitly (``cql(src, client=…)`` or ``client.cql(src)``)."""
    from .client import CqlClient  # lazy: avoid a client<->cql import cycle
    b = CqlClient.builder()
    if endpoint is not None:
        b = b.endpoint(endpoint)
    if spark is not None:
        b = b.spark(spark)
    return b.build()


def _active_spark():
    from pyspark.sql import SparkSession  # lazy
    return SparkSession.active()


#: `(id(session), jar)` pairs already shipped, so the per-`exec` upload hook is idempotent. Keyed by
#: session identity rather than globally: two sessions in one process each need their own upload.
_SHIPPED_UDF_JARS = set()


def _ship_udf_catalog(spark, lib) -> None:
    """EXEC-UDF-LIBRARY (compiler/docs/execution-model.md): ship the opaque-UDF catalog JAR to the
    Connect server (``spark.addArtifact``) so emitted SQL that references ``cql_*`` UDFs (EXEC-OPAQUE-UDF)
    resolves. This is the OSS/local half — the client's genuine responsibility is UPLOADING the artifact.

    The functions must then be REGISTERED by name. On Databricks that is a governed, one-time deploy step
    (UC ``CREATE FUNCTION … USING JAR``), so the JAR path is unset there and this is a no-op. On OSS/local
    the JVM side registers the Scala catalog (a Scala lambda cannot be registered by name over a *Python*
    Connect client), so a pure-Python OSS run currently relies on that registration already being in place.
    Wiring finalizes with Phase 2, when the emit begins referencing these UDFs.

    The JAR path comes from ``spark.cqldatabricks.runtime.udfJar`` (conf/override) or ``CQL_DATABRICKS_UDF_JAR``
    (env); absent → no-op (the pre-UDF status quo)."""
    jar = getattr(lib, "udf_jar", None)
    if not jar:
        return
    add = getattr(spark, "addArtifact", None)
    if add is None:
        # A Spark build with no `addArtifact` (a classic non-Connect session) cannot ship an artifact at
        # all — the status quo before this hook existed. Not an error: the JAR is then a deploy-step
        # concern, exactly as it is on Databricks (`CREATE FUNCTION … USING JAR`).
        return
    # ONCE PER (session, jar). This hook runs on EVERY `exec()`, and Spark Connect rejects re-adding an
    # artifact it already holds — so without this guard the second query in a session would fail on a
    # duplicate upload that is not a real problem at all.
    key = (id(spark), jar)
    if key in _SHIPPED_UDF_JARS:
        return
    # A genuinely FAILED first upload is LOUD (`CORE-NO-SILENT-MISLOWER`). Swallowing it turned a missing
    # artifact into a cryptic "function cql_temporal_cmp not found" thousands of lines into the emitted
    # SQL, with no trace back to this hook.
    try:
        add(jar)
    except Exception as e:
        raise CompileError(
            f"failed to ship the opaque-UDF catalog JAR '{jar}' to the Spark session ({e}). The emitted "
            f"SQL references `cql_*` UDFs that will not resolve without it. Fix the path "
            f"(`{config.CONF_UDF_JAR}` conf / `{config.ENV_UDF_JAR}` env), or unset it if the catalog is "
            f"already registered at the server as a deploy step.") from e
    _SHIPPED_UDF_JARS.add(key)


def _run_statements(spark, result: SqlCompileResult) -> None:
    """Run a compiled SQL script (EMIT-SQL-EXECUTE): execute each statement via ``spark.sql`` in dependency
    order (each `CREATE OR REPLACE TEMP VIEW` / `CACHE TABLE` registers a name). The script itself is an
    internal detail and is never returned; only the resulting DataFrames are exposed."""
    for stmt in result.statements:
        spark.sql(stmt.sql)


def _select(spark, target: str, persist_as: Optional[str] = None):
    """A lazy DataFrame over the (identifier-quoted) ``target`` object, so a define name with spaces reads
    correctly. ``persist_as`` (`BUILD-META`) additionally materializes it into a managed table
    ``CREATE TABLE <persist_as> AS SELECT * FROM <target>`` (dotted, each part quoted) and returns that."""
    quoted = "`" + target.replace("`", "``") + "`"
    if persist_as is not None:
        dest = ".".join("`" + p.replace("`", "``") + "`" for p in persist_as.split("."))
        spark.sql(f"CREATE TABLE {dest} AS SELECT * FROM {quoted}")
        return spark.table(persist_as)
    return spark.sql(f"SELECT * FROM {quoted}")
