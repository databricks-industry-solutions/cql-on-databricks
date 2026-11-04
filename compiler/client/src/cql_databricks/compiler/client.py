"""``CqlClient`` — the fluent client session (``BUILD-CLIENT-SESSION``).

Configured like a ``SparkSession``: one session holds the transpiler endpoint / auth / catalog once, and
every ``cql()`` / ``describe()`` inherits it (no per-call kwarg sprawl). Each builder call is the top
("explicit") tier of the ``BUILD-RESOLVE`` precedence, so an omitted option still falls through to
``spark.cqldatabricks.*`` conf > ``CQL_DATABRICKS_*`` env > convention auto-discovery > loud FAIL.

Thin sugar over :mod:`cql_databricks.compiler.config` / :mod:`~.library` / :mod:`~.providers`: the session builds
a resolved conf map + provider, and ``client.cql(source)`` returns a lazy :class:`~.cql.CqlQuery` handle you
inspect (``elm``/``dag``) or ``exec`` terminally. KISS: ONE client, ONE server, NO
multi-version routing — a client pins one compiler/terminology version (``BUILD-VERSION-PIN``); multiple
versions ⇒ separate workspaces.

The platform (accelerator) wrapper SUBCLASSES this client and auto-discovers the App endpoint on init; it
contributes its provider as an entry-point PLUGIN, so a bare ``cql("…")`` resolves to it with no global
registration. The OSS core imports no platform SDK.
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, Optional, Union

from . import config
from .cql import CqlQuery, describe as _describe
from .libraries import LibraryProvider, LibraryRef, identity_of, resolve_library_provider
from .library import Library, load_library
from .providers import CompileError, CompileProvider, LibraryGraph, ResourceSchema, resolve_provider


class CqlClient:
    """A configured CQL session. Build one with :meth:`builder`, then ``client.cql(source)`` to get a lazy
    :class:`~.cql.CqlQuery` handle (inspect with ``elm``/``dag``, run with ``exec``), or :meth:`describe`
    a library's neutral graph directly."""

    def __init__(self, conf: Dict[str, str], *, provider: Optional[CompileProvider] = None,
                 spark=None, endpoint: Optional[str] = None,
                 library_provider: Optional[LibraryProvider] = None, binder=None, session_scope=None):
        self._conf = dict(conf)
        self._spark = spark
        # Resolve the provider ONCE at build time (a single server — KISS), so every call reuses it.
        self._provider = provider or resolve_provider(self._conf, endpoint=endpoint)
        # BUILD-LIBRARY-PROVIDER: the library SOURCE seam, likewise resolved once. `None` is legitimate —
        # a session that only binds inline CQL needs no source; the loud failure comes at first use.
        self._library_provider = (library_provider if library_provider is not None
                                  else resolve_library_provider(self._conf))
        # BIND-CALL-SITE: binds each compile's call sites, or None for a client that binds nothing.
        self._binder = binder
        # SVC-NOTEBOOK-SESSION-SCOPE: the session library header-less CQL joins, or None for a client whose
        # every string is its own library.
        self._session_scope = session_scope
        # CLIENT-RESOURCE-SCHEMA: the model's schema is fixed for a compiler version, so it is read once.
        self._resource_schemas: Optional[Dict[str, ResourceSchema]] = None

    @property
    def library_provider(self) -> Optional[LibraryProvider]:
        """The session's :class:`~.libraries.LibraryProvider`, or ``None`` when no source is configured.
        Exposed so a host can inspect or delegate to it (``client.library_provider.search("Measure*")``)."""
        return self._library_provider

    def libraries(self, pattern: Optional[str] = None) -> list:
        """Discover the libraries this session can bind by NAME (`BUILD-LIBRARY-PROVIDER`), optionally
        narrowed by a substring or glob. Returns :class:`~.libraries.LibraryRef`s."""
        if self._library_provider is None:
            raise config.ConfigResolutionError("a library search path", config.CONF_LIBRARY_PATH,
                                               config.ENV_LIBRARY_PATH)
        return self._library_provider.search(pattern)

    def resource_schema(self, fhir_type: Optional[str] = None):
        """The compiler's model of FHIR data (`CLIENT-RESOURCE-SCHEMA`): the ``resource`` struct a
        data view of that type returns, as a :class:`~.providers.ResourceSchema`. With no type, every
        modeled type's, keyed by type. Read from the compiler once per client, never derived here. An
        unmodeled type raises, naming it."""
        if self._resource_schemas is None:
            self._resource_schemas = self._provider.resource_schemas(self._provider.resource_types())
        if fhir_type is None:
            return dict(self._resource_schemas)
        schema = self._resource_schemas.get(fhir_type)
        if schema is None:
            raise CompileError(f"the compiler does not model FHIR type {fhir_type!r}")
        return schema

    @staticmethod
    def builder() -> "CqlClientBuilder":
        """Start configuring a client (Spark-builder ergonomics)."""
        return CqlClientBuilder()

    # -- the query surface -----------------------------------------------------------------------
    def cql(self, source: Union[Library, str, None] = None, *, resource: Optional[str] = None,
            cql: Optional[str] = None, elm: Optional[str] = None, library: Optional[str] = None,
            context: Optional[str] = None, data_model: Optional[str] = None, spark=None,
            engine: Optional[str] = None,
            recompute: Optional[bool] = None) -> CqlQuery:
        """Bind ``source`` against this session and return a lazy :class:`~.cql.CqlQuery` handle
        (`PIPE-RUNTIME-COMPILE`). ``source`` is a raw CQL / ELM STRING, a packaged ``resource`` path
        (``…​.cql`` / ``…​.elm.json``), or an already-bound :class:`~.library.Library` (equivalently pass
        ``resource=`` / ``cql=`` / ``elm=``). With no source at all, it is the session library that header-less CQL
        joined (`SVC-NOTEBOOK-SESSION-SCOPE`). Inspect the handle (``.elm()`` / ``.dag()``) or ``.exec(*targets)``
        it — selection of WHICH defines to evaluate is terminal, never a string here (so ``cql("Numerator")``
        can never be mistaken for a target).

        ``library=`` addresses a library by NAME through the session's
        :class:`~.libraries.LibraryProvider` and ships its resolved ``include`` closure with the compile —
        the only form that makes a real multi-library eCQM work.

        ``engine`` and ``recompute`` override the session's for this one query; a session that set them once
        passes none of them."""
        lib = self._bind(source, resource=resource, cql=cql, elm=elm, library=library, context=context,
                         data_model=data_model, engine=engine, recompute=recompute)
        return CqlQuery(lib, spark=spark or self._spark)

    def with_library(self, resource: Optional[str] = None, *, cql: Optional[str] = None,
                     elm: Optional[str] = None, library: Optional[str] = None,
                     context: Optional[str] = None,
                     data_model: Optional[str] = None, engine: Optional[str] = None,
                     recompute: Optional[bool] = None) -> Library:
        """Bind and return the immutable :class:`~.library.Library` value object directly (the interchange
        form the SDP path holds), resolving provider/context/data-model/overrides from this session
        (`BUILD-RESOLVE`). Most callers want :meth:`cql` (the runnable handle) instead.

        ``context`` is the explicit tier of `LANG-CONTEXT-DEFAULT`; when omitted here AND in conf/env, the
        client sends UNSET and the compiler resolves it (declared context → Unfiltered) — never a forced
        ``Patient``."""
        return load_library(resource, cql=cql, elm=elm, library=library, conf=self._conf,
                            provider=self._provider, library_provider=self._library_provider,
                            context=context, data_model=data_model,
                            engine=engine, recompute=recompute,
                            binder=self._binder)

    def _bind(self, source, *, resource, cql, elm, library, context, data_model,
              engine=None, recompute=None) -> Library:
        """Coerce ``source`` + explicit body kwargs to a bound :class:`~.library.Library`. A ``Library`` is
        returned as-is; otherwise a STRING is classified — a ``…​.cql`` / ``…​.elm.json`` / ``…​.json`` path is a
        ``resource``, a leading ``{`` is ``elm``, anything else is raw ``cql`` text.

        This classification is FROZEN (`BUILD-CLIENT-COMPAT`): a released string form can never change
        meaning. A library NAME is therefore addressed by the explicit ``library=`` kwarg and is never
        inferred from a bare string — otherwise ``cql("HypertensionBPControl")`` would have to change from "raw CQL that
        fails to translate" into "resolve a library", which is exactly the kind of silent re-binding this
        rule exists to prevent."""
        # Two bodies is a MISTAKE, not a precedence question — silently honoring one and dropping the
        # other is exactly the class of quiet wrong answer this surface must not have.
        if source is not None and library is not None:
            raise ValueError(
                "cql() got both a source and library=; pass one. A library NAME goes in library=, "
                "a CQL/ELM string or resource path goes in the positional source.")
        if source is not None and not isinstance(source, str):
            if engine is not None or recompute is not None:
                raise TypeError("engine/recompute apply to a source STRING; a bound Library "
                                "carries its own")
            return source  # an already-bound Library value object (or a duck-typed one) — used as-is
        if resource is None and cql is None and elm is None and library is None:
            if source is None:
                if self._session_scope is not None:
                    # SVC-NOTEBOOK-SESSION-SCOPE: no source is the session library header-less CQL joined.
                    return self._session_scope.library(lambda text: self.with_library(
                        cql=text, context=context, data_model=data_model, engine=engine, recompute=recompute))
                raise ValueError("cql() needs a source: a CQL/ELM string, a resource path, or one of "
                                 "resource=/library=/cql=/elm=")
            if source.endswith((".cql", ".elm.json", ".json")):
                resource = source
            elif source.lstrip().startswith("{"):
                elm = source
            else:
                cql = source
        if self._session_scope is not None and cql is not None and identity_of(cql)[0] is None:
            # SVC-NOTEBOOK-SESSION-SCOPE: header-less CQL is a fragment of the session library.
            return self._session_scope.join(cql, self._provider, lambda text: self.with_library(
                resource, cql=text, context=context, data_model=data_model, engine=engine,
                recompute=recompute))
        return self.with_library(resource, cql=cql, elm=elm, library=library, context=context,
                                 data_model=data_model, engine=engine,
                                 recompute=recompute)

    def describe(self, library: Union[Library, CqlQuery], *,
                 boundaries: Optional[Iterable[str]] = None,
                 grain_first: bool = False) -> LibraryGraph:
        """The neutral :class:`~.providers.LibraryGraph` DAG for ``library`` (`BUILD-META`) — defines,
        dependency edges, cost-chosen boundaries, materialized barriers, pinned versions — what an SDP reads
        to build its own dataset graph. Accepts a :class:`~.library.Library` or a :class:`~.cql.CqlQuery`.
        No session or execution needed."""
        if isinstance(library, CqlQuery):
            return library.describe(boundaries=boundaries, grain_first=grain_first)
        return _describe(library, boundaries=boundaries, grain_first=grain_first)


class CqlClientBuilder:
    """Accumulates ``spark.cqldatabricks.*`` options and an optional explicit provider/session, then
    :meth:`build`s a :class:`CqlClient`. Every setter returns ``self`` for chaining."""

    def __init__(self):
        self._conf: Dict[str, str] = {}
        self._provider: Optional[CompileProvider] = None
        self._library_provider: Optional[LibraryProvider] = None
        self._session_scope = None
        self._spark = None
        self._endpoint: Optional[str] = None
        self._binder = None

    def _set(self, key: str, value: Optional[str]) -> "CqlClientBuilder":
        if value is not None:
            self._conf[key] = value
        return self

    # -- transport -------------------------------------------------------------------------------
    def endpoint(self, url: str) -> "CqlClientBuilder":
        """The compile/transpiler service URL (the local ``OssCompileHost`` or a hosted App)."""
        self._endpoint = url
        return self._set(config.CONF_PROVIDER_ENDPOINT, url)

    def provider(self, provider: CompileProvider) -> "CqlClientBuilder":
        """Supply a fully-built provider directly (bypasses endpoint/auth resolution) — e.g. an embedded
        py4j provider or a platform plugin instance."""
        self._provider = provider
        return self

    def provider_kind(self, kind: str) -> "CqlClientBuilder":
        """Select a registered provider plugin by name (entry-point group ``cql_databricks.providers``)."""
        return self._set(config.CONF_PROVIDER_KIND, kind)

    # -- auth (standard, provider-neutral) -------------------------------------------------------
    def auth_none(self) -> "CqlClientBuilder":
        return self._set(config.CONF_AUTH_KIND, "none")

    def auth_bearer(self, token: str) -> "CqlClientBuilder":
        """A fixed OAuth 2.0 Bearer token (RFC 6750)."""
        self._set(config.CONF_AUTH_KIND, "bearer")
        return self._set(config.CONF_AUTH_TOKEN, token)

    def auth_token_exchange(self, token_url: str, subject_token: str, audience: str,
                            scope: Optional[str] = None) -> "CqlClientBuilder":
        """OAuth 2.0 Token Exchange (RFC 8693) — the provider-neutral core of the serverless→App recipe."""
        self._set(config.CONF_AUTH_KIND, "oauth2-token-exchange")
        self._set(config.CONF_AUTH_TOKEN_URL, token_url)
        self._set(config.CONF_AUTH_SUBJECT_TOKEN, subject_token)
        self._set(config.CONF_AUTH_AUDIENCE, audience)
        return self._set(config.CONF_AUTH_SCOPE, scope)

    # -- library + data binding ------------------------------------------------------------------
    def library_source(self, path: str) -> "CqlClientBuilder":
        """The root under which packaged ``resource`` CQL/ELM paths resolve. Also seeds the library
        SEARCH PATH as a single entry when no :meth:`library_path` is set, so existing config keeps
        working unchanged (`BUILD-LIBRARY-PROVIDER`)."""
        return self._set(config.CONF_LIBRARY_SOURCE, path)

    # -- library source seam (BUILD-LIBRARY-PROVIDER) ---------------------------------------------
    def library_provider(self, provider: "LibraryProvider") -> "CqlClientBuilder":
        """Supply a fully-built :class:`~.libraries.LibraryProvider` directly — the EXTERNAL injection
        point for a custom source (a FHIR server, a package registry, an object store, a test double).
        Bypasses all config resolution, exactly as :meth:`provider` does for the transport seam.

        Implement :class:`~.libraries.LibraryProvider`: ``resolve(ref)`` is the only required method;
        ``closure(root)`` has a working default that walks ``include``s via ``resolve`` (override it only
        when the backing store can answer a whole closure in one round trip), and ``search(pattern)`` is
        optional. A provider distributed as a package can instead register under the
        ``cql_databricks.libraries`` entry-point group and be selected by
        :meth:`library_provider_kind`, with no code change here at all."""
        self._library_provider = provider
        return self

    def session_scope(self, scope) -> "CqlClientBuilder":
        """Make header-less CQL join ``scope``, a session library (`SVC-NOTEBOOK-SESSION-SCOPE`), instead of
        each string being its own library. :func:`~cql_databricks.compiler.connect` sets this; a client built
        without it keeps every string separate."""
        self._session_scope = scope
        return self

    def library_provider_kind(self, kind: str) -> "CqlClientBuilder":
        """Select a registered library-provider plugin by name (entry-point group
        ``cql_databricks.libraries``). ``file`` is the built-in default."""
        return self._set(config.CONF_LIBRARY_PROVIDER_KIND, kind)

    def library_path(self, *roots: str) -> "CqlClientBuilder":
        """The ORDERED search roots the built-in ``file`` provider scans — earlier roots win, so a local
        override directory shadows a vendored content repo. Any directory works, including a Unity Catalog
        Volume (``/Volumes/...``): it is an ordinary path, so no platform code is involved."""
        return self._set(config.CONF_LIBRARY_PATH, os.pathsep.join(roots))

    def library_layout(self, *layouts: str) -> "CqlClientBuilder":
        """Which of :data:`~.libraries.LAYOUTS` the ``file`` provider scans (``flat``, ``ig``,
        ``fhir-library``, ``nested``). Layouts are ADDITIVE; the default scans all four."""
        return self._set(config.CONF_LIBRARY_LAYOUT, ",".join(layouts))

    def object_prefix(self, prefix: str) -> "CqlClientBuilder":
        """Namespace every emitted session object as ``<prefix>__<name>`` (`EMIT-SQL-OBJECT-PREFIX`).
        DEFAULT OFF — bare define names are emitted, the long-standing form.

        Turn it on when ONE Spark session lowers MORE THAN ONE library: emitted objects are bare sanitized
        define names, so two libraries both produce a view named `Numerator`, `CREATE OR REPLACE TEMP VIEW`
        rebinds silently, and because DataFrames are lazy the first handle then returns the second
        library's rows. Pass ``"auto"`` to derive a stable prefix from each library's own identity."""
        return self._set(config.CONF_OBJECT_PREFIX, prefix)

    def udf_schema(self, schema: str) -> "CqlClientBuilder":
        """Qualify every emitted `cql_*` UDF call as ``<schema>.cql_fn(…)`` (`EMIT-UDF-QUALIFY`) — needed
        when the session's search path does not include the schema the opaque-UDF catalog was registered
        in. Unset emits bare names."""
        return self._set(config.CONF_UDF_SCHEMA, schema)

    def data_binder(self, binder) -> "CqlClientBuilder":
        """Bind each compile's call sites with ``binder``, a :class:`~.resolver_binder.ResolverBinder`
        (`CLIENT-RESOLVERS`, `BIND-CALL-SITE`). Unset, nothing is bound: CQL that retrieves nothing compiles, and a
        retrieve or a value-set membership fails, naming its site."""
        self._binder = binder
        return self

    def context(self, name: str) -> "CqlClientBuilder":
        """The session default CQL context (``Patient`` / ``Unfiltered`` / ``Population`` / …). This is the
        explicit tier of `LANG-CONTEXT-DEFAULT`; leave it UNSET to get the spec default (the library's
        declared context, else ``Unfiltered``) rather than a forced ``Patient``."""
        return self._set(config.CONF_CONTEXT, name)

    def data_model(self, name: str) -> "CqlClientBuilder":
        """The CQL data model carried to the compiler (the ``data.model`` override). Defaults to ``FHIR``;
        the engine is FHIR-only today, so a non-FHIR value is reserved for future `ARCH-DATAMODEL-TRAIT`."""
        return self._set(config.CONF_DATA_MODEL, name)

    # -- the engine and its session settings (DEC-ENGINE-SESSION-OPTIONS) --
    def engine(self, name: str) -> "CqlClientBuilder":
        """Which engine runs this session's queries: ``local`` (the default: this session's compute, nothing
        kept) or ``evaluator`` (the evaluator's compute; results kept and reused). Never a fallback."""
        return self._set(config.CONF_ENGINE, name)

    def measurement_period(self, start: str, end: str) -> "CqlClientBuilder":
        """The session's measurement period, bound in every query whose library declares one
        (`BUILD-CLIENT-SESSION-PARAMETERS`); ``with_measurement_period`` on a query overrides it."""
        return self._set(config.CONF_MEASUREMENT_PERIOD, f"{start}/{end}")

    def recompute(self, recompute: bool = True) -> "CqlClientBuilder":
        """RUN-RECOMPUTE-OVERRIDE — on the evaluator engine, run every define even where its rows are current."""
        return self._set(config.CONF_RECOMPUTE, "true" if recompute else "false")

    # -- determinism pins (single version — KISS) ------------------------------------------------
    def compiler_version(self, version: str) -> "CqlClientBuilder":
        return self._set(config.CONF_COMPILER_VERSION, version)

    def terminology_version(self, version: str) -> "CqlClientBuilder":
        return self._set(config.CONF_TERMINOLOGY_VERSION, version)

    # -- session + escape hatch ------------------------------------------------------------------
    def spark(self, session) -> "CqlClientBuilder":
        """Bind the Spark session used for execution + ``default-name`` Catalog-API discovery (else the
        active session at call time)."""
        self._spark = session
        return self

    def conf(self, key: str, value: str) -> "CqlClientBuilder":
        """Set any ``spark.cqldatabricks.*`` key directly (escape hatch for a knob without a named setter)."""
        return self._set(key, value)

    def option(self, key: str, value: str) -> "CqlClientBuilder":
        """Public alias of :meth:`conf` (`BUILD-CLIENT-SESSION`) — the generic bare-verb setter, reading
        nicer than ``.conf()`` (``builder.option("spark.cqldatabricks.context", "Patient")``)."""
        return self._set(key, value)

    def build(self) -> CqlClient:
        return CqlClient(self._conf, provider=self._provider, spark=self._spark, endpoint=self._endpoint,
                         library_provider=self._library_provider, binder=self._binder,
                         session_scope=self._session_scope)
