"""Zero-config ``cql()`` for Databricks (`SVC-ZERO-CONFIG-NOTEBOOK`).

``connect()`` builds a ``cql-databricks`` :class:`~cql_databricks.compiler.CqlClient` wired for AUTO-DISCOVERY:
it resolves the deployed Compiler Service App endpoint from the workspace App registry and authenticates
by serverless→App OAuth token-exchange (the ``databricks-app`` provider this package contributes), while
each run reads the data source found by its Unity Catalog tag (`CLIENT-EXEC-DISCOVERY`). So a notebook
needs NO endpoint, NO token, and NO provider selection::

    from cql_databricks.compiler import cql
    cql('define "Two": 2 + 2').exec("Two").display()

or, holding the session explicitly (fluent) and mounting a Git Folder of CQL as the library source
(`SVC-NOTEBOOK-LIBRARY-FOLDER`), so a published multi-library measure runs by NAME::

    from cql_databricks.compiler import connect
    client = connect(spark, library_path="/Workspace/Users/me/measure-content/input/cql")
    client.cql(library="HypertensionBPControl") \\
          .with_measurement_period("2026-01-01", "2026-12-31").exec("Initial Population")

The endpoint + OAuth are resolved ON INIT (at ``connect()`` / the first ``cql()``), inside the notebook or
job where the workspace App registry + ambient identity are available. This is the thin platform wrapper
the OSS client anticipates; the OSS core still imports no platform SDK (`APP-DECOUPLE`).
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from cql_databricks.compiler import CqlClient, CqlQuery
from cql_databricks.compiler.libraries import resolve_library_provider

from ._udfs import register_runtime_udfs
from .compile_shim import CqlDatabricksCompileProvider, discover_udf_schema
from .discovery import DatabricksDataBinder
from .provider import CONF_APP_NAME, make_provider
from .session_libraries import session_provider
from .session_scope import SessionScope
from .session_state import SessionRecord


def connect(spark=None, *, app_name: Optional[str] = None, data_catalog: Optional[str] = None,
            valueset_table: Optional[str] = None, udf_schema: Optional[str] = None,
            library_path: Optional[str | Iterable[str]] = None, engine: Optional[str] = None,
            data_source: Optional[str] = None, measurement_period: Optional[tuple[str, str]] = None,
            recompute: Optional[bool] = None, catalog_scopes: Optional[Iterable[str]] = None) -> CqlClient:
    """A :class:`~cql_databricks.compiler.CqlClient` wired for Databricks auto-discovery. The Compiler Service App
    endpoint + OAuth are resolved ON INIT via this package's ``databricks-app`` provider (built directly,
    so it needs no ``provider.kind`` selection). Each run reads the data source found by its tag, in the
    session's catalog scopes (`CLIENT-EXEC-DISCOVERY`, :mod:`.discovery`): the one ``data_source`` names,
    else the only one in scope. Value sets come from the ``valuesets`` view in the ``udf_schema``, as for a
    durable calculation. Call inside a Databricks notebook/job.

    Emitted ``cql_*`` calls are schema-qualified through the OSS ``.udf_schema()`` knob
    (`EMIT-UDF-QUALIFY`) and the provider is wrapped in :class:`~.compile_shim.CqlDatabricksCompileProvider` so
    materialization barriers are serverless-safe (`SVC-NOTEBOOK-RUNTIME-BIND`) — the two things that
    otherwise make a measure fail on serverless, which is the compute a notebook gets by default.

    ``library_path`` mounts one or more directories of CQL as the session's library source
    (`SVC-NOTEBOOK-LIBRARY-FOLDER`) — typically a Databricks **Git Folder** under ``/Workspace``, or a UC
    Volume. Roots are ORDERED and earlier wins, so an override directory shadows a vendored content repo.
    With it set, ``client.cql(library="HypertensionBPControl")`` resolves that library BY NAME and ships its whole
    ``include`` closure with the compile (`BUILD-LIBRARY-PROVIDER`, `BUILD-COMPILE-LINK`). Omit it and the
    folder still resolves from ``spark.cqldatabricks.libraryPath`` / ``CQL_DATABRICKS_LIBRARY_PATH``, which is how the
    ``%%cql`` magic and the module-level :func:`cql`, which build their own client, see the same folder
    without being handed one.

    Ahead of that folder sit the libraries DECLARED BY ``%%cql`` CELLS (`SVC-NOTEBOOK-CELL-LIBRARY`), so a
    measure can be written across cells and a later cell's ``include`` finds an earlier cell's library. One
    lookup order, cells first — a cell shadows a published library of the same name, which is what makes it
    an override while you draft.

    CQL with NO ``library`` header — a ``%%cql`` cell or a ``client.cql("...")`` string — joins the session
    library instead (`SVC-NOTEBOOK-SESSION-SCOPE`): a later fragment sees an earlier one's defines unqualified,
    and redeclaring a name replaces it. Every client on the session shares it, like the cells.

    ``spark`` is the session runs execute against (else the active one). The keyword overrides are only for
    a NON-convention deployment: ``app_name`` (a Compiler Service App not named the default),
    ``data_catalog`` (a ``catalog.schema`` whose ``retrieve_<Type>`` functions every run reads, instead of
    finding a data source by tag), ``valueset_table`` (the value-set table, instead of the runtime view),
    ``udf_schema`` (the ``cql_*`` UC catalog's ``catalog.schema``, else discovered from UC).

    ``catalog_scopes`` is the ordered list of ``catalog``, ``catalog.schema`` or ``*`` entries this client discovers
    data sources, libraries and value sets in, and ``materialize`` reads results in (`CLIENT-DISCOVERY-SCOPE`).
    Unset, it is ``CQL_DATABRICKS_CATALOG_SCOPES``, else every catalog, with a warning. Pass it here rather than in
    the Spark conf, which serverless refuses to set (`BUILD-RESOLVE`).

    ``engine``, ``data_source``, ``measurement_period`` and ``recompute`` set the
    session's engine settings once (EVALUATOR-CLIENT-EVALUATE, `DEC-ENGINE-SESSION-OPTIONS`), so every later
    call is just ``client.cql("…")``. An ``engine`` other than the local one is a plugin in the
    ``cql_databricks.engines`` entry-point group; none ships since EVALUATOR-REMOVE retired the evaluator App's.
    Each also resolves from its ``spark.cqldatabricks.*`` conf or ``CQL_DATABRICKS_*`` env, which is how
    the module-level :func:`cql` and the ``%%cql`` magic see them."""
    conf = {CONF_APP_NAME: app_name} if app_name else {}
    if isinstance(catalog_scopes, str):
        raise TypeError("catalog_scopes is a list of entries, not one string: pass [\"cat\", \"cat.schema\"]")
    scopes = None if catalog_scopes is None else list(catalog_scopes)
    session = spark if spark is not None else _active_spark()
    provider = CqlDatabricksCompileProvider(make_provider(conf))
    # `.provider(...)` injects the discovered provider directly (bypasses endpoint/auth conf resolution).
    builder = CqlClient.builder().provider(provider)
    udf = udf_schema or discover_udf_schema(session)
    builder = builder.udf_schema(udf)
    if spark is not None:
        builder = builder.spark(spark)
    if data_catalog:
        builder = builder.data_catalog(data_catalog)
    else:
        builder = builder.data_binder(DatabricksDataBinder(
            lambda: _session_content(session, scopes), lambda: _spark_runner(session),
            terminology_table=f"{udf}.valuesets", record=SessionRecord(session), catalog_scopes=scopes))
    if valueset_table:
        builder = builder.valueset_table(valueset_table)
    if engine:
        builder = builder.engine(engine)
    if data_source:
        builder = builder.data_source(data_source)
    if measurement_period:
        builder = builder.measurement_period(*measurement_period)
    if recompute is not None:
        builder = builder.recompute(recompute)
    roots = _library_roots(library_path)
    if roots:
        # Recorded as ordinary session config too, so the mount stays visible on the client — the injected
        # provider below is built from these same roots, never from a second source of truth.
        builder = builder.library_path(*roots)
    builder = builder.library_provider(_library_provider(roots, spark, scopes))
    # SVC-NOTEBOOK-SESSION-SCOPE: header-less CQL joins the session library, kept beside the cells.
    builder = builder.session_scope(SessionScope(SessionRecord(spark)))
    client = builder.build()
    # SVC-RUNTIME-UDF: register the opaque-UDF catalog on a JVM-backed classic session. Since
    # SVC-NOTEBOOK-RUNTIME-BIND qualifies every emitted `cql_*` call against the UC catalog, nothing the
    # compiler emits calls a bare name — this is the legacy classic-cluster path kept until SVC-RUNTIME-UDF
    # is formally retired in favour of SVC-RUNTIME-UDF-UC. Best-effort no-op on Spark Connect (serverless).
    register_runtime_udfs(session)
    return client


def _library_provider(roots: list[str], spark=None, catalog_scopes: Optional[list[str]] = None):
    """SVC-NOTEBOOK-CELL-LIBRARY, CLIENT-KNOWLEDGE-INCLUDES — the session's library source: cells first, the
    mounted folder second, tagged knowledge in scope third.

    A :class:`~.session_libraries.SessionLibraryProvider` over the session's cells is always installed, so
    a library declared by a `%%cql` cell is includable by a later cell whether or not any folder is
    mounted. Its second root is the engine's own configured provider — ``roots`` when `connect()` was
    handed a ``library_path``, else whatever ``spark.cqldatabricks.libraryPath`` / ``CQL_DATABRICKS_LIBRARY_PATH``
    resolves to, which is how the front doors that build their own client see the same folder
    (`BUILD-RESOLVE`). The cells are shared with every other client; this folder is this client's own.
    Its third is the tagged knowledge in the session's catalog scopes, read through ``spark`` (else the active
    session) on first use (:mod:`.knowledge_libraries`)."""
    from .knowledge_libraries import FolderThenKnowledge, KnowledgeLibraryProvider

    knowledge = KnowledgeLibraryProvider(lambda: _session_content(spark, catalog_scopes))
    return session_provider(FolderThenKnowledge(resolve_library_provider({}, search_path=roots), knowledge),
                            spark)


def _session_content(spark=None, catalog_scopes: Optional[Iterable[str]] = None):
    """CLIENT-DISCOVERY-SCOPE — a resolver over this session's tagged content, as this user, in the session's
    catalog scopes: ``catalog_scopes``, else ``spark.cqldatabricks.catalogScopes``, else
    ``CQL_DATABRICKS_CATALOG_SCOPES``, else every catalog this user can see, with a warning."""
    from cql_databricks.data_bindings import ContentResolver, spark_runner

    if spark is None:
        from pyspark.sql import SparkSession  # lazy
        spark = SparkSession.active()
    scopes = session_catalog_scopes(spark, catalog_scopes)
    return ContentResolver(lambda: spark_runner(spark), scopes)


def session_catalog_scopes(spark=None, catalog_scopes: Optional[Iterable[str]] = None, *,
                           query=None) -> Optional[list[str]]:
    """CLIENT-DISCOVERY-SCOPE — the session's ordered catalog scopes: ``catalog_scopes``, else the list
    ``connect(catalog_scopes=...)`` gave ``query``'s client, else ``spark.cqldatabricks.catalogScopes`` on ``spark``
    (none read when ``spark`` is None), else ``CQL_DATABRICKS_CATALOG_SCOPES``, else ``None``, which is every
    catalog. Discovery and durable results read the same list."""
    from . import config

    if catalog_scopes is None and query is not None:
        binder = getattr(getattr(query, "_lib", None), "binder", None)
        if isinstance(binder, DatabricksDataBinder):
            catalog_scopes = binder.catalog_scopes
    conf = {} if spark is None else {config.CONF_CATALOG_SCOPES: _conf_or_empty(spark, config.CONF_CATALOG_SCOPES)}
    return config.resolve_catalog_scopes(None if catalog_scopes is None else list(catalog_scopes), conf=conf)


def _spark_runner(spark):
    from cql_databricks.data_bindings import spark_runner

    return spark_runner(spark)


def _conf_or_empty(spark, key: str) -> str:
    """``key`` from the session conf, or ``""`` when it is unset. Serverless raises CONFIG_NOT_AVAILABLE for an
    unset key that is not on its allowlist, even with a default, and that is "unset" too, as in
    ``library._spark_conf``. Unset scopes search every catalog, with a warning (CLIENT-DISCOVERY-SCOPE)."""
    try:
        return spark.conf.get(key, None) or ""
    except Exception:  # noqa: BLE001 — pyspark raises AnalysisException; the key is simply not set
        return ""


def _library_roots(library_path: Optional[str | Iterable[str]]) -> list[str]:
    """Normalize ``library_path`` to an ORDERED root list. A bare string is ONE root — iterating it would
    make ``library_path="/Workspace/cql"`` scan sixteen single-character roots and report every library as
    unresolvable, which is a confusing way to learn that a string is iterable."""
    if library_path is None:
        return []
    if isinstance(library_path, str):
        return [library_path]
    return [str(root) for root in library_path]


def _active_spark():
    """The ambient PySpark session, or ``None`` — resolved best-effort so UDF auto-registration can find a
    JVM-backed notebook session when ``connect()`` was called without an explicit ``spark``."""
    try:
        from pyspark.sql import SparkSession  # lazy: cql-databricks must import without pyspark present
        return SparkSession.getActiveSession()
    except Exception:
        return None


# A lazily-built, reused default client for the module-level `cql` shorthand (NOT a public/settable
# global — the confusing OSS `set_default_client` was removed; this is just the zero-config convenience).
_default_client: Optional[CqlClient] = None


def cql(source=None, *, spark=None, client=None, endpoint=None, **kwargs) -> CqlQuery:
    """Bind ``source`` and return a lazy :class:`~cql_databricks.compiler.CqlQuery` handle; ``.exec(*targets)``
    runs it. This is the package's exported ``cql`` (`SVC-ZERO-CONFIG-NOTEBOOK`).

    With neither ``client`` nor ``endpoint``, it AUTO-DISCOVERS: the client is built once by
    :func:`connect` — App-registry endpoint, ambient-identity OAuth, UC-discovered catalog / value-set
    table / UDF schema — and reused for later calls. That is the notebook path, where naming any of it
    by hand is the thing this package exists to avoid.

    Given an explicit ``client`` or ``endpoint``, it delegates to the core binder
    (:func:`cql_databricks.compiler.cql_explicit`) and NO discovery and no platform SDK is involved. This matters
    beyond convenience: it is the path the OSS end-to-end conformance lane drives, against a locally
    hosted compile service with no workspace anywhere, so it has to stay a first-class way in rather than
    a fallback. A bound :class:`~cql_databricks.compiler.Library` also needs no client at all and is wrapped
    directly.

    Remaining ``kwargs`` pass through unchanged (``resource=`` / ``cql=`` / ``elm=`` / ``library=`` /
    ``context=`` / ``data_model=``)."""
    from .cql import cql as _core_cql  # lazy: `.cql` imports this module's `connect` for nothing else

    # Anything the core can answer without discovery, the core answers — explicit wiring, or a value
    # object that needs no wiring. Discovery is the LAST resort, not the first.
    if client is not None or endpoint is not None or not (
            isinstance(source, str) or source is None or kwargs.get("resource")
            or kwargs.get("cql") or kwargs.get("elm") or kwargs.get("library")):
        return _core_cql(source, spark=spark, client=client, endpoint=endpoint, **kwargs)

    global _default_client
    if _default_client is None:
        _default_client = connect(spark=spark)
    return _default_client.cql(source, spark=spark, **kwargs)


def resolve_libraries(source: str, *, spark=None, catalog_scopes: Optional[Iterable[str]] = None,
                      bff_app_name: Optional[str] = None) -> list[str]:
    """The CQL sources of ``source``'s ``include`` closure from tagged knowledge, dependencies first
    (`KNOWLEDGE-RESOLVE-API`, `BUILD-RESOLVE-COMPILE`).

    Resolved in this session, as the notebook's user: the knowledge schemas are found by their UC tag and read
    through ``spark`` (the active session when omitted), so what resolves is what this user may read
    (`DEC-STATELESS-SERVICES`). Only the session's catalog scopes are searched (`CLIENT-DISCOVERY-SCOPE`):
    ``catalog_scopes``, else ``spark.cqldatabricks.catalogScopes``, else ``CQL_DATABRICKS_CATALOG_SCOPES``, else
    every catalog this user can see, with a warning. The resolver is :func:`cql_databricks.compiler.resolve.resolve`, the one the
    Designer and the SDP publish use, so all three resolve alike. An include not found raises, naming it.

    Before BUILD-RESOLVE-COMPILE this asked the Designer's App (``bff_app_name``), an edge against the feature
    order; that argument is refused rather than ignored."""
    if bff_app_name is not None:
        raise TypeError("resolve_libraries no longer calls the Designer's App: it resolves in this session from "
                        "tagged knowledge (BUILD-RESOLVE-COMPILE); drop bff_app_name")
    from .resolve import resolve

    return resolve(_session_content(spark, catalog_scopes), cql=source).require_complete().include_sources


def translate(source: str, *, includes: Sequence[str] = (), app_name: Optional[str] = None) -> str:
    """CQL → ELM JSON via the Compiler Service App's ``/translate`` endpoint (`EMIT-INTERCHANGE`).

    Returns the portable ELM interchange string the engine's front end produces from ``source`` — the IR
    an SDP / another CQL tool consumes, and the shared artifact behind the neutral DAG. The client's lazy
    ``CqlQuery.elm()`` is empty for raw inline CQL (the service translates on compile, no ELM egress), so
    this helper hits ``/translate`` directly through the SAME auto-discovered App endpoint + OAuth as
    :func:`connect` — no endpoint, token, or provider selection needed. Data-free (a pure front-end
    translate; no Spark session).

    ``includes`` is the CQL of every library ``source`` includes, FHIRHelpers among them: the compiler bundles
    no library, so an include no source provides fails the translation (`LANG-FHIRHELPERS-FROM-CONTENT`)."""
    import requests  # lazy: keep the package importable without requests

    conf = {CONF_APP_NAME: app_name} if app_name else {}
    provider = make_provider(conf)  # RemoteProvider: discovered App URL + OAuth token-exchange
    url = provider._base + "/translate"
    headers = {"Content-Type": "application/json", **provider._credentials.auth_headers()}
    resp = requests.post(url, json={"cql": source, "includes": list(includes)}, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"{url} returned {resp.status_code}: {resp.text}")
    return resp.json()["elm"]
