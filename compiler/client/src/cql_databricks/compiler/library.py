"""Library resolution — the ``LIBRARY = load_library(...)`` the generated pipeline stubs call.

Resolves the CQL library's ELM (the interchange form, ``EMIT-INTERCHANGE``), the active compile provider,
and EVERY setting the compiler needs (context, FHIR catalog, conformed value-set table, version pins) by
the single `BUILD-RESOLVE` precedence: explicit kwarg > ``spark.cqldatabricks.*`` conf > ``CQL_DATABRICKS_*`` env >
loud FAIL. The resolved FHIR catalog / value-set table / versions are carried to the compiler as
`CompileRequest` overrides under the stable keys in ``config`` (the host-injected provider bakes the concrete
tables). The data a run reads is not a setting: the library's binder chooses it at compile time
(`CLIENT-EXEC-DISCOVERY`, :mod:`.binding`).

Spark conf and file reads are lazy so the module imports (and unit-tests) with no Spark present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Dict, Mapping, Optional, Sequence, Tuple

from . import config
from .period import PERIOD_POINT_TYPES, measurement_period_bounds  # noqa: F401  (re-exported)
from .libraries import (LibraryProvider, LibraryRef, LibrarySource, identity_of,
                        resolve_library_provider)
from .providers import CompileProvider, resolve_provider


def _cql_literal(value) -> str:
    """Render a Python parameter value as CQL expression text (`BUILD-CLIENT-SESSION`). ``bool`` →
    ``true``/``false``; ``int``/``float`` → its numeral; ``str`` → verbatim CQL (the caller supplies the
    literal syntax). ``None`` → ``null``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return str(value)


def measurement_period_literal(start: str, end: str, point_type: str = "DateTime") -> str:
    """The CQL literal for a measurement period, bound at the precision its DECLARED type calls for
    (`LANG-PARAM-INSTANT`). PUBLIC: this is the one place the widening rule lives, so a caller that
    builds the parameter itself (a host binding a scheduled run, a pipeline deploy) gets the same
    answer as [[Library.with_measurement_period]] instead of re-deriving it.

    A bare DATE widens to the instant it denotes — ``start`` to ``T00:00:00.000``, ``end`` to
    ``T23:59:59.999``; a value that already carries a time is used VERBATIM. Returns the closed
    ``Interval[@lo, @hi]`` of DateTimes the measures declare. ``point_type="Date"`` binds a period declared
    ``Interval<Date>`` instead (:func:`measurement_period_bounds`).

    Why this is load-bearing, not formatting. A parameter override is CQL EXPRESSION TEXT translated
    through the front end, so the LITERAL TYPE survives into the IR and out to the emitted SQL —
    ``@2026-01-01`` stays a day-precision Date even against an ``Interval<DateTime>`` parameter. Precision
    is ISO-STRING LENGTH, and a comparison whose operands share a prefix but differ in length is
    UNCERTAIN -> ``null`` -> the row fails its ``WHERE``. A 10-char bound against a 23-char resource
    instant therefore DROPS every boundary-day resource, silently and with no error. It also reaches the
    retrieve UDTF's pushdown hint, which is derived from this same value, so a coarse bound can narrow the
    SCAN before any predicate runs.

    `LANG-INTERVAL-PRECISION` independently makes a NAMED precision (`during day of`) truncate both
    operands, so a coarse bound is harmless *there*; this keeps it from arising in the first place, and
    covers the unqualified comparisons and the retrieve hint that the named-precision rule does not.
    """
    lo, hi = measurement_period_bounds(start, end, point_type)
    return f"Interval[@{lo}, @{hi}]"


# The Spark-conf keys read to build a settings map (there is no public "get all by prefix").
_CONF_KEYS = [
    config.CONF_PROVIDER_KIND, config.CONF_PROVIDER_ENDPOINT, config.CONF_LIBRARY,
    config.CONF_LIBRARY_SOURCE, config.CONF_FHIR_CATALOG, config.CONF_VALUESET_TABLE,
    config.CONF_COMPILER_VERSION, config.CONF_TERMINOLOGY_VERSION, config.CONF_CONTEXT,
    config.CONF_DATA_MODEL,
    config.CONF_LIBRARY_PATH, config.CONF_LIBRARY_LAYOUT, config.CONF_LIBRARY_PROVIDER_KIND,
    config.CONF_OBJECT_PREFIX,
    config.CONF_AUTH_KIND, config.CONF_AUTH_TOKEN, config.CONF_AUTH_TOKEN_URL,
    config.CONF_AUTH_AUDIENCE, config.CONF_AUTH_SUBJECT_TOKEN, config.CONF_AUTH_SUBJECT_TOKEN_TYPE,
    config.CONF_AUTH_SCOPE,
    config.CONF_ENGINE, config.CONF_DATA_SOURCE, config.CONF_MEASUREMENT_PERIOD, config.CONF_RECOMPUTE,
]


@dataclass(frozen=True)
class Library:
    """A resolved CQL library: its ELM (or raw CQL text), the compile provider it reaches, the CQL context,
    the resolved config overrides carried to the compiler (`BUILD-RESOLVE`), and any parameter-value
    overrides (`BUILD-CLIENT-SESSION`). Held by the pipeline stubs as ``LIBRARY`` and passed to every
    ``cql()`` call. Immutable: the fluent ``with_*`` refinements return a NEW ``Library`` (Spark-builder
    ergonomics), so a bound library can be reused and specialized without mutation."""
    resource: str
    elm: str
    provider: CompileProvider
    context: Optional[str] = None
    overrides: Dict[str, str] = field(default_factory=dict)
    cql: Optional[str] = None
    parameters: Dict[str, str] = field(default_factory=dict)
    # BIND-MANIFEST: the retrieve binding table carried to the compiler (fhirType -> {function, catalog?}),
    # naming the SQL UDTF each retrieve lowers to (BIND-RETRIEVE-UDF). Empty → every type is convention-bound.
    sources: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # BUILD-LIBRARY-PROVIDER / BUILD-COMPILE-LINK: the resolved include CLOSURE shipped with the root, so a
    # multi-library eCQM compiles. Empty for a single self-contained library (the unchanged path).
    deps: Tuple[LibrarySource, ...] = ()
    # EMIT-SQL-OBJECT-PREFIX: the session-object namespace, or None for BARE names (the default).
    object_prefix: Optional[str] = None
    # BIND-TERMINOLOGY-TABLE: the value-set expansion table FQN, sent in `sources.valuesets.table` where
    # the service actually reads it. (It is ALSO carried in `overrides` under the stable client↔host key
    # for a platform host that bakes tables from there — the two consumers read different places.)
    valueset_table: Optional[str] = None
    # EMIT-UDF-QUALIFY: schema qualifying emitted `cql_*` UDF calls, sent in `sources.udfs.schema`.
    udf_schema: Optional[str] = None
    # EXEC-UDF-LIBRARY: the opaque-UDF catalog JAR to ship to the session. Resolved HERE, through the one
    # `BUILD-RESOLVE` precedence, rather than read out of `overrides` at exec time — `overrides` carries
    # only the five compiler-override keys, so the `spark.cqldatabricks.runtime.udfJar` CONF was silently
    # dropped and only the env var ever worked.
    udf_jar: Optional[str] = None
    # EVALUATOR-CLIENT-EVALUATE (`DEC-ENGINE-SESSION-OPTIONS`): which engine runs this library, and what the
    # evaluator engine needs, resolved by the same `BUILD-RESOLVE` precedence as every setting above.
    engine: str = config.LOCAL_ENGINE
    data_source: Optional[str] = None
    #: The raw ``(start, end)`` of the measurement period, as the caller or the session gave it.
    measurement_period: Optional[Tuple[str, str]] = None
    recompute: bool = False
    #: BUILD-CLIENT-SESSION-PARAMETERS — session values the compiler applies only where the library declares them.
    session_parameters: Dict[str, str] = field(default_factory=dict)
    #: The session's resolved settings, for an engine's own keys (`engines.EngineRequest.settings`).
    settings: Dict[str, str] = field(default_factory=dict)
    #: CLIENT-EXEC-DISCOVERY — chooses the data each compile binds to (:class:`~.binding.DataBinder`), or
    #: ``None`` for the bare ``retrieve_<Type>`` convention. Not compared: two bindings of one library are
    #: the same library.
    binder: Optional[object] = field(default=None, compare=False)
    #: SVC-NOTEBOOK-SESSION-SCOPE — when this is the session library, the defines of the fragment that bound
    #: it: what ``exec()`` with no targets runs. ``None`` for any other library.
    fragment_defines: Optional[Tuple[str, ...]] = None

    def with_parameter(self, name: str, value) -> "Library":
        """Bind CQL parameter ``name`` to ``value`` (`BUILD-CLIENT-SESSION`, extends `LANG-PARAMETER`).
        ``value`` is a CQL EXPRESSION-TEXT override the compiler inlines as a literal: a Python ``bool`` →
        ``true``/``false``, an ``int``/``float`` → its numeral, and a ``str`` is used VERBATIM as CQL (so a
        date/interval/quantity/code literal — ``"@2026-01-01"``, ``"Interval[@2026-01-01, @2026-12-31]"``,
        ``"40 'mg'"`` — passes through unquoted; wrap a CQL string in its own quotes: ``"'active'"``)."""
        return replace(self, parameters={**self.parameters, name: _cql_literal(value)})

    def with_measurement_period(self, start: str, end: str) -> "Library":
        """Convenience for the ubiquitous ``"Measurement Period"`` parameter: bind it to the closed
        ``Interval[@start, @end]`` of **DateTimes** (`LANG-PARAM-INSTANT`). ``start``/``end`` are ISO date
        or dateTime strings (``"2026-01-01"``, or ``"2026-01-01T00:00:00.000"`` to pin the instant).

        A bare DATE is widened to the instant it denotes — ``start`` to ``T00:00:00.000``, ``end`` to
        ``T23:59:59.999`` — so both bounds are DateTimes at MILLISECOND precision, matching the
        ``Interval<DateTime>`` the measures declare.

        Load-bearing, not cosmetic. A parameter override is CQL EXPRESSION TEXT translated through the
        front end (`CompileService.overrideExprs`), so the LITERAL TYPE you write survives into the IR and
        out to the emitted SQL: ``@2026-01-01`` stays a day-precision Date even against a parameter
        declared ``Interval<DateTime>``. Precision in the temporal model is ISO-STRING LENGTH, and a
        comparison whose operands share a prefix but differ in length is UNCERTAIN -> ``null`` -> the row
        fails its ``WHERE``. A 10-char bound against a 23-char resource instant therefore DROPS every
        boundary-day resource silently.

        Widening fixes the BINDING side. `LANG-INTERVAL-PRECISION` fixes the other side (an Allen relation
        that NAMES a precision now truncates both operands to it), so a coarse bound is harmless there too.
        """
        bound = self.with_parameter("Measurement Period", measurement_period_literal(start, end))
        # EVALUATOR-CLIENT-EVALUATE: the evaluator engine takes the raw bounds and applies the declared type.
        return replace(bound, measurement_period=(start, end))

    def explain_elm(self) -> str:
        """The front-end ELM for this library (`BUILD-META`, `EMIT-INTERCHANGE`) — the shared, portable IR.
        Empty when the library is bound from raw CQL not yet translated (the service translates on compile)."""
        return self.elm or ""


def load_library(resource: Optional[str] = None, *, elm: Optional[str] = None,
                 cql: Optional[str] = None,
                 library: Optional[str] = None,
                 conf: Optional[Mapping[str, str]] = None,
                 provider: Optional[CompileProvider] = None,
                 library_provider: Optional[LibraryProvider] = None,
                 resolve_closure: bool = True,
                 endpoint: Optional[str] = None,
                 context: Optional[str] = None,
                 data_model: Optional[str] = None,
                 fhir_catalog: Optional[str] = None,
                 data_catalog: Optional[str] = None,
                 valueset_table: Optional[str] = None,
                 udf_schema: Optional[str] = None,
                 library_source: Optional[str] = None,
                 compiler_version: Optional[str] = None,
                 terminology_version: Optional[str] = None,
                 sources: Optional[Mapping[str, Mapping[str, str]]] = None,
                 engine: Optional[str] = None,
                 data_source: Optional[str] = None,
                 recompute: Optional[bool] = None,
                 binder=None) -> Library:
    """Resolve a library and every compiler setting by the `BUILD-RESOLVE` precedence.

    The library body comes from exactly one of: ``resource`` (the packaged CQL path, e.g.
    ``"cql/HypertensionBPControl.cql"``, resolved under ``library_source`` when set and relative — its ELM is read from
    the sibling ``.elm.json``), ``library`` (a NAME, or ``"Name|version"``, resolved through the
    :class:`~.libraries.LibraryProvider` seam, `BUILD-LIBRARY-PROVIDER`), an explicit ``elm`` string, or
    raw ``cql`` TEXT (translated to ELM by the service, `BUILD-CLIENT-SESSION`). ``conf`` overrides the
    Spark conf (mainly for tests). Each keyword is the explicit (top-precedence) tier for its knob;
    anything omitted falls to conf > env > FAIL.

    ``resolve_closure`` (default on) walks the root's ``include``s through the library provider and ships
    the dependency closure with the compile (`BUILD-COMPILE-LINK`) — what makes a real multi-library eCQM
    compile. This covers raw ``cql`` TEXT as well: an inline body is a root like any other, so a
    multi-library measure typed into a cell resolves its deps off the search path. A headerless body has no
    identity, but its ``include``s resolve the same way (`SVC-NOTEBOOK-SESSION-SCOPE`). Set it off to send the root
    alone (an under-supplied closure then fails loudly at the service, never silently).
    ``library_provider`` injects a provider directly, bypassing config resolution — the seam a host or a
    test supplies its own source from. ``binder`` chooses the data each compile binds to
    (`CLIENT-EXEC-DISCOVERY`)."""
    # `library=` names its own body, so it is exclusive. `resource` may still ACCOMPANY an explicit
    # `elm`/`cql` body — there it is a LABEL (it lands on `Library.resource` for provenance), which is a
    # long-standing calling form and stays valid.
    if library is not None and (resource is not None or cql is not None or elm is not None):
        raise ValueError("library= names the library itself; it is exclusive with resource=/cql=/elm=")
    if library is None and (resource is None) == (cql is None) and elm is None:
        raise ValueError("load_library needs exactly one of resource=, library=, elm=, or cql=")
    # BUILD-RESOLVE: a session's own settings take precedence over the Spark conf. This ensures
    # `spark.cqldatabricks.engine` set in a notebook takes effect.
    resolved_conf = {**_spark_conf_if_any(), **conf} if conf is not None else _spark_conf()

    src = config.resolve_setting("library source", explicit=library_source, conf=resolved_conf,
                                 conf_key=config.CONF_LIBRARY_SOURCE, env=config.ENV_LIBRARY_SOURCE)
    # The library-source seam: injected > configured (`libraryProvider.kind` / search path / the legacy
    # single `library_source` root) > absent. Absent is fine until something needs it by NAME.
    lib_prov = library_provider if library_provider is not None else resolve_library_provider(
        resolved_conf, library_source=library_source)

    # Body precedence, unchanged for the pre-existing forms: an explicit `cql` wins, then an explicit
    # `elm`, then a provider-resolved `library`, then the `resource` file.
    root: Optional[LibrarySource] = None
    if cql is not None:
        elm_text = ""                       # the service translates CQL → ELM (BUILD-CLIENT-SESSION)
        # An inline body is a ROOT like any other (BUILD-COMPILE-LINK): synthesize a source from the text
        # so the same closure walk below asks the mounted provider for its `include`s. Without this a
        # multi-library measure typed into a cell compiled with ZERO deps and failed at translate with
        # "Could not load source for library <X>". No `origin` — there is no directory to scope siblings
        # to, so the file provider falls through to the wider search path.
        name, version = identity_of(cql)
        if name:
            root = LibrarySource(name=name, version=version, cql=cql)
    elif elm is not None:
        elm_text = elm
    elif library is not None:
        if lib_prov is None:
            raise config.ConfigResolutionError(
                f"a library search path (to resolve library='{library}')",
                config.CONF_LIBRARY_PATH, config.ENV_LIBRARY_PATH)
        root = lib_prov.resolve(LibraryRef.parse(library))
        # Exactly ONE body form, ELM preferred — never both. `CompileService.elmOf` prefers `cql` and
        # re-translates it, so sending both alongside an ELM closure would translate the root with no
        # sources for its `include`s and fail with "Could not load source for library …" even though the
        # ELM was already resolved. (`_read_resource` applies the same rule.)
        elm_text, cql = (root.elm, None) if root.elm else ("", root.cql)
    else:
        elm_text, cql, root = _read_resource(_under(src, resource))

    # BUILD-COMPILE-LINK: resolve the include closure so a multi-library measure compiles. Runs for EVERY
    # body form that yields an identified root — provider, file, or inline text. A root with an `origin`
    # scopes its `include`s sibling-first; one without (inline) resolves them on the search path alone.
    deps: Tuple[LibrarySource, ...] = ()
    if resolve_closure and root is not None and lib_prov is not None:
        deps = tuple(lib_prov.closure(root))
    elif resolve_closure and cql is not None and lib_prov is not None:
        # A headerless body is walked under a placeholder name; it is never sent as a dependency itself.
        deps = tuple(lib_prov.closure(LibrarySource(name="<inline>", cql=cql)))

    prov = provider or resolve_provider(resolved_conf, endpoint=endpoint)
    # NO compiled-in default (`LANG-CONTEXT-DEFAULT`): an unset context resolves to None and is sent UNSET,
    # so the COMPILER applies the spec resolution (library-declared context → model default → Unfiltered).
    ctx = config.resolve_setting("CQL context", explicit=context, conf=resolved_conf,
                                 conf_key=config.CONF_CONTEXT, env=config.ENV_CONTEXT)

    # `data_catalog` is the preferred, model-neutral name; `fhir_catalog` is the deprecated alias.
    overrides = _resolve_overrides(
        resolved_conf, data_model=data_model,
        fhir_catalog=fhir_catalog if fhir_catalog is not None else data_catalog,
        valueset_table=valueset_table, compiler_version=compiler_version,
        terminology_version=terminology_version)

    # EMIT-SQL-OBJECT-PREFIX: DEFAULT OFF — unset emits bare object names, the long-standing form. `auto`
    # derives a stable prefix from the library identity so two libraries in one session cannot collide.
    prefix = config.resolve_setting("object prefix", conf=resolved_conf,
                                    conf_key=config.CONF_OBJECT_PREFIX, env=config.ENV_OBJECT_PREFIX)
    if prefix == "auto":
        prefix = _auto_prefix(root, resource, elm_text, cql)

    # EVALUATOR-CLIENT-EVALUATE, BUILD-CLIENT-SESSION-PARAMETERS — the engine and its session settings.
    from .engines import parse_flag, parse_period
    engine_name = config.resolve_setting("CQL engine", explicit=engine, conf=resolved_conf,
                                         conf_key=config.CONF_ENGINE, env=config.ENV_ENGINE,
                                         default=config.LOCAL_ENGINE)
    period = parse_period(config.resolve_setting(
        "measurement period", conf=resolved_conf, conf_key=config.CONF_MEASUREMENT_PERIOD,
        env=config.ENV_MEASUREMENT_PERIOD))
    session_parameters = ({"Measurement Period": measurement_period_literal(*period)} if period else {})
    recompute_flag = recompute if recompute is not None else parse_flag(config.resolve_setting(
        "recompute", conf=resolved_conf, conf_key=config.CONF_RECOMPUTE, env=config.ENV_RECOMPUTE))

    return Library(resource=resource or (library or "<inline>"), elm=elm_text, provider=prov, context=ctx,
                   overrides=overrides, cql=cql,
                   sources={t: dict(b) for t, b in (sources or {}).items()},
                   deps=deps, object_prefix=prefix,
                   valueset_table=overrides.get(config.OVERRIDE_VALUESET_TABLE),
                   udf_schema=config.resolve_setting(
                       "UDF schema", explicit=udf_schema, conf=resolved_conf,
                       conf_key=config.CONF_UDF_SCHEMA, env=config.ENV_UDF_SCHEMA),
                   udf_jar=config.resolve_setting(
                       "UDF catalog JAR", conf=resolved_conf,
                       conf_key=config.CONF_UDF_JAR, env=config.ENV_UDF_JAR),
                   engine=engine_name,
                   data_source=config.resolve_setting(
                       "data source", explicit=data_source, conf=resolved_conf,
                       conf_key=config.CONF_DATA_SOURCE, env=config.ENV_DATA_SOURCE),
                   measurement_period=period,
                   recompute=recompute_flag,
                   session_parameters=session_parameters,
                   settings=dict(resolved_conf),
                   binder=binder)


def _auto_prefix(root: Optional[LibrarySource], resource: Optional[str], elm: str,
                 cql: Optional[str]) -> Optional[str]:
    """A stable session-object prefix from the library's own IDENTITY (`EMIT-SQL-OBJECT-PREFIX`, ``auto``):
    ``<name>_<version>`` when the library declares one, else the resource stem. Identity-derived rather
    than random so re-running the same library in a session REBINDS its own views instead of leaking a new
    set each call. ``None`` when nothing identifies the library — an unnamed inline snippet gets bare
    names rather than a meaningless prefix."""
    name = version = None
    if root is not None:
        name, version = root.name, root.version
    elif elm:
        name, version = identity_of(elm, elm=True)
    elif cql:
        name, version = identity_of(cql)
    if not name and resource:
        name = os.path.basename(resource).split(".")[0]
    if not name:
        return None
    return f"{name}_{version}" if version else name


def _resolve_overrides(conf: Mapping[str, str], *, data_model, fhir_catalog, valueset_table,
                       compiler_version, terminology_version) -> Dict[str, str]:
    """Resolve the settings CARRIED to the compiler as `CompileRequest.overrides` (the client↔host
    contract). None has a discovery tier: an unset catalog or value-set table is left to the library's
    binder (`CLIENT-EXEC-DISCOVERY`), and versions are optional pins. The data model is an explicit knob with a compiled-in ``FHIR`` default
    (the engine is FHIR-only today, `ARCH-DATAMODEL-TRAIT` future). Each is carry-only (``required=False``)
    — a data-free library needs none, so an unresolved knob is simply omitted, not a FAIL (a platform
    provider raises at the point of use if it truly needs one)."""
    out: Dict[str, str] = {}

    model = config.resolve_setting("data model", explicit=data_model, conf=conf,
                                   conf_key=config.CONF_DATA_MODEL, env=config.ENV_DATA_MODEL,
                                   default=config.DEFAULT_DATA_MODEL)
    if model:
        out[config.OVERRIDE_DATA_MODEL] = model

    catalog = config.resolve_setting(
        "FHIR catalog", explicit=fhir_catalog, conf=conf, conf_key=config.CONF_FHIR_CATALOG,
        env=config.ENV_FHIR_CATALOG)
    if catalog:
        out[config.OVERRIDE_FHIR_CATALOG] = catalog

    valueset = config.resolve_setting(
        "conformed value-set table", explicit=valueset_table, conf=conf,
        conf_key=config.CONF_VALUESET_TABLE, env=config.ENV_VALUESET_TABLE)
    if valueset:
        out[config.OVERRIDE_VALUESET_TABLE] = valueset

    cver = config.resolve_setting("compiler version pin", explicit=compiler_version, conf=conf,
                                  conf_key=config.CONF_COMPILER_VERSION, env=config.ENV_COMPILER_VERSION)
    if cver:
        out[config.OVERRIDE_COMPILER_VERSION] = cver

    tver = config.resolve_setting("terminology version pin", explicit=terminology_version, conf=conf,
                                  conf_key=config.CONF_TERMINOLOGY_VERSION,
                                  env=config.ENV_TERMINOLOGY_VERSION)
    if tver:
        out[config.OVERRIDE_TERMINOLOGY_VERSION] = tver

    return out


def _under(source: Optional[str], resource: str) -> str:
    """Resolve ``resource`` under the ``library_source`` root when the root is set and the resource is a
    relative path; an absolute resource (or no root) is used as-is."""
    if source and not os.path.isabs(resource):
        return os.path.join(source, resource)
    return resource


def _read_resource(resource: str) -> Tuple[str, Optional[str], Optional[LibrarySource]]:
    """Read a ``resource`` path into ``(elm, cql, source)``.

    ELM is PREFERRED when it is there — a ``.cql`` resource still reads its sibling ``.elm.json`` exactly
    as before. But a ``.cql`` file with NO sibling ELM now falls back to its own CQL TEXT (which the
    service translates) instead of raising ``FileNotFoundError``. That sharp edge made
    ``client.cql("measures/Foo.cql")`` fail for every checkout of published content, since content repos
    ship CQL and not pre-translated ELM.

    The returned [[LibrarySource]] carries the file's origin, which is what lets the include closure be
    scoped sibling-first to this resource's own directory."""
    if resource.endswith(".elm.json") or resource.endswith(".json"):
        text = _read_text(resource)
        name, version = identity_of(text, elm=True)
        return text, None, LibrarySource(name=name or os.path.basename(resource), version=version,
                                         elm=text, origin=resource)
    elm_path = resource[:-4] + ".elm.json" if resource.endswith(".cql") else resource + ".elm.json"
    cql_path = resource if resource.endswith(".cql") else resource + ".cql"
    elm_text = _read_text(elm_path) if os.path.exists(elm_path) else ""
    cql_text = _read_text(cql_path) if os.path.exists(cql_path) else None
    if not elm_text and cql_text is None:
        raise FileNotFoundError(
            f"no CQL library at '{resource}': looked for '{cql_path}' and '{elm_path}'")
    name, version = identity_of(cql_text) if cql_text else identity_of(elm_text, elm=True)
    origin = cql_path if cql_text is not None else elm_path
    return (elm_text, None if elm_text else cql_text,
            LibrarySource(name=name or os.path.basename(origin).split(".")[0], version=version,
                          cql=cql_text, elm=elm_text or None, origin=origin))


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _spark_conf() -> Dict[str, str]:
    from pyspark.sql import SparkSession  # lazy
    spark = SparkSession.active()
    out: Dict[str, str] = {}
    for k in _CONF_KEYS:
        try:
            v = spark.conf.get(k, None)
        except Exception:
            v = None
        if v is not None:
            out[k] = v
    return out


def _spark_conf_if_any() -> Dict[str, str]:
    """:func:`_spark_conf`, or ``{}`` where there is no Spark session to read (a unit test, a plain Python
    process): the Spark-conf tier is then simply empty."""
    try:
        return _spark_conf()
    except Exception:  # noqa: BLE001 — no pyspark, or no active session: that tier holds nothing
        return {}
