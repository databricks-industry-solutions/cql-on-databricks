"""Zero-config ``cql()`` for Databricks (`SVC-ZERO-CONFIG-NOTEBOOK`).

``connect()`` builds a ``cql-databricks`` :class:`~cql_databricks.compiler.CqlClient` wired for AUTO-DISCOVERY:
it resolves the deployed Compiler Service App endpoint from the workspace App registry and authenticates
by serverless→App OAuth token-exchange (the ``databricks-app`` provider this package contributes), while
each run reads the default environment, found by its Unity Catalog catalog tags (`CLIENT-ENVIRONMENT`). So a notebook
needs NO endpoint, NO token, and NO environment::

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

from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from typing import Iterable, Optional, Sequence

from cql_databricks.compiler import CqlClient, CqlQuery
from cql_databricks.compiler.libraries import resolve_library_provider

from cql_databricks.resolvers.knowledge import library_provider
from .resolver_binder import ResolverBinder
from ._udfs import register_runtime_udfs
from .compile_shim import CqlDatabricksCompileProvider, discover_udf_schema
from .provider import CONF_APP_NAME, make_provider
from .session_libraries import session_provider
from .session_scope import SessionScope
from .session_state import SessionRecord, held_record, session_resolvers, set_session_resolvers


def connect(spark=None, *, app_name: Optional[str] = None, udf_schema: Optional[str] = None,
            library_path: Optional[str | Iterable[str]] = None, engine: Optional[str] = None,
            environment: Optional[str] = None, measurement_period: Optional[tuple[str, str]] = None,
            recompute: Optional[bool] = None, data=None, terminology=None, knowledge=None,
            results=None) -> CqlClient:
    """A :class:`~cql_databricks.compiler.CqlClient` wired for Databricks. The Compiler Service App endpoint + OAuth
    are resolved ON INIT via this package's ``databricks-app`` provider (built directly, so it needs no
    ``provider.kind`` selection). Call inside a Databricks notebook/job.

    Every run is in one environment (`CLIENT-ENVIRONMENT`): the one ``environment`` names, else
    ``CQL_DATABRICKS_ENVIRONMENT``, else the default environment, found by its catalog tags as this user
    (`CLIENT-ENVIRONMENT-RESOLVER`): the lookup starts here, in the background, and the first compile waits for it
    (`CLIENT-DISCOVERY-PARALLEL`). Its published views serve the data and value sets, bound by rule, so a run
    makes one compiler call and reads no catalog (`BIND-PUBLISHED-VIEW`). ``data``, ``terminology`` and ``knowledge``
    replace the environment's resolver for that role (`CLIENT-RESOLVERS`); a resolver that binds per call site makes
    the run ask ``describe`` first (`BIND-CALL-SITE`). ``results`` replaces the environment's result resolver, through
    which ``exec()`` reads kept results in place of computing them (`CLIENT-QUERY-REUSE`).

    Emitted ``cql_*`` calls are schema-qualified through the OSS ``.udf_schema()`` knob (`EMIT-UDF-QUALIFY`) and the
    provider is wrapped in :class:`~.compile_shim.CqlDatabricksCompileProvider` so materialization barriers are
    serverless-safe (`SVC-NOTEBOOK-RUNTIME-BIND`).

    ``library_path`` mounts one or more directories of CQL as the session's library source
    (`SVC-NOTEBOOK-LIBRARY-FOLDER`), typically a Databricks **Git Folder** under ``/Workspace``, or a UC Volume. Roots
    are ORDERED and earlier wins. Omit it and the folder still resolves from ``spark.cqldatabricks.libraryPath`` /
    ``CQL_DATABRICKS_LIBRARY_PATH``. Ahead of that folder sit the libraries DECLARED BY ``%%cql`` CELLS
    (`SVC-NOTEBOOK-CELL-LIBRARY`), and after it the environment's knowledge (`CLIENT-KNOWLEDGE-INCLUDES`). CQL with NO
    ``library`` header joins the session library instead (`SVC-NOTEBOOK-SESSION-SCOPE`).

    ``engine``, ``measurement_period`` and ``recompute`` set the session's engine settings once
    (`DEC-ENGINE-SESSION-OPTIONS`); ``recompute=True`` reads no kept result."""
    conf = {CONF_APP_NAME: app_name} if app_name else {}
    session = spark if spark is not None else _active_spark()
    env = _environment(session, environment)
    # CLIENT-DISCOVERY-PARALLEL: the environments and the runtime's location are read on the session in the
    # background while the compiler App's endpoint is resolved here, so connect() waits for the slowest, not the sum.
    # The environments are waited for on first use, and fail there, as when they were read then.
    discovery = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cql-discovery")
    try:
        if session is not None and any(role is None for role in (data, terminology, knowledge, results)):
            env.prefetch(discovery, session)
        udf_found = None if udf_schema else discovery.submit(copy_context().run, discover_udf_schema, session)
        provider = CqlDatabricksCompileProvider(make_provider(conf))
        udf = udf_schema or udf_found.result()
    finally:
        discovery.shutdown(wait=False)
    # `.provider(...)` injects the discovered provider directly (bypasses endpoint/auth conf resolution).
    builder = CqlClient.builder().provider(provider)
    builder = builder.udf_schema(udf)
    if spark is not None:
        builder = builder.spark(spark)
    # CLIENT-RESOLVERS: the environment's resolvers, each replaced by the one given for its role.
    builder = builder.data_binder(ResolverBinder(
        data=data if data is not None else lambda: env.resolver().data(env.get()),
        terminology=terminology if terminology is not None else lambda: env.resolver().terminology(env.get()),
        run=lambda: _spark_runner(session), spark=session, schemas=provider.resource_schemas,
        results=results if results is not None else lambda: env.resolver().results(env.get())))
    if engine:
        builder = builder.engine(engine)
    if measurement_period:
        builder = builder.measurement_period(*measurement_period)
    if recompute is not None:
        builder = builder.recompute(recompute)
    roots = _library_roots(library_path)
    if roots:
        # Recorded as ordinary session config too, so the mount stays visible on the client — the injected
        # provider below is built from these same roots, never from a second source of truth.
        builder = builder.library_path(*roots)
    # CLIENT-KNOWLEDGE-RESOLVER: a knowledge resolver given replaces the default order (cells, folder, environment).
    builder = builder.library_provider(library_provider(knowledge) if knowledge is not None
                                       else _library_provider(roots, spark, env))
    # SVC-NOTEBOOK-SESSION-SCOPE: header-less CQL joins the session library, kept beside the cells.
    builder = builder.session_scope(SessionScope(SessionRecord(spark)))
    client = builder.build()
    # SVC-RUNTIME-UDF: register the opaque-UDF catalog on a JVM-backed classic session. Since
    # SVC-NOTEBOOK-RUNTIME-BIND qualifies every emitted `cql_*` call against the UC catalog, nothing the
    # compiler emits calls a bare name. This is the classic-cluster path; the UC form is SVC-RUNTIME-UDF-UC.
    # Best-effort no-op on Spark Connect (serverless).
    register_runtime_udfs(session)
    return client


class _SessionEnvironment:
    """CLIENT-ENVIRONMENT — the environment a session runs in, found as this user on first use and kept.

    CLIENT-DISCOVERY-PARALLEL — :meth:`prefetch` starts finding the environments in the background at ``connect()``;
    first use waits for that discovery and raises what it raised, on the caller's thread."""

    def __init__(self, spark, name: Optional[str]) -> None:
        self._spark, self.name = spark, name
        self._resolver = None
        self._env = None
        self._found: Optional[Future] = None
        self._run = None

    def prefetch(self, pool: ThreadPoolExecutor, session) -> None:
        from cql_databricks.resolvers.environment import discover_environments

        self._run = _spark_runner(session)
        self._found = pool.submit(copy_context().run, discover_environments, self._run)

    def resolver(self):
        if self._resolver is None:
            from cql_databricks.resolvers.environment import EnvironmentResolver

            if self._found is not None:
                found = self._found
                self._resolver = EnvironmentResolver(self._run, found=lambda: found.result().emit())
            else:
                self._resolver = EnvironmentResolver(_spark_runner(_session(self._spark)))
        return self._resolver

    def get(self):
        if self._env is None:
            self._env = self.resolver().resolve(self.name)
        return self._env

    def content(self):
        return self.resolver().content(self.get())


def _environment(spark, name: Optional[str]) -> _SessionEnvironment:
    """CLIENT-ENVIRONMENT, BUILD-RESOLVE — the environment ``name`` names, else ``CQL_DATABRICKS_ENVIRONMENT``, else the
    default; found on first use."""
    from . import config as settings

    return _SessionEnvironment(spark, settings.resolve_setting("environment", explicit=name,
                                                               env=settings.ENV_ENVIRONMENT))


def _library_provider(roots: list[str], spark, env: _SessionEnvironment):
    """SVC-NOTEBOOK-CELL-LIBRARY, CLIENT-KNOWLEDGE-INCLUDES — the session's library source: cells first, the mounted
    folder second, the environment's knowledge third, read on first use (:mod:`.knowledge_libraries`).

    A :class:`~.session_libraries.SessionLibraryProvider` over the session's cells is always installed, so a library
    declared by a `%%cql` cell is includable by a later cell whether or not any folder is mounted. Its second root is
    the engine's own configured provider: ``roots`` when `connect()` was handed a ``library_path``, else whatever
    ``spark.cqldatabricks.libraryPath`` / ``CQL_DATABRICKS_LIBRARY_PATH`` resolves to (`BUILD-RESOLVE`)."""
    from .knowledge_libraries import FolderThenKnowledge, KnowledgeLibraryProvider

    knowledge = KnowledgeLibraryProvider(env.content)
    return session_provider(FolderThenKnowledge(resolve_library_provider({}, search_path=roots), knowledge),
                            spark)


def _session(spark):
    if spark is None:
        from pyspark.sql import SparkSession  # lazy
        spark = SparkSession.active()
    return spark


def _spark_runner(spark):
    from cql_databricks.data_bindings import spark_runner

    return spark_runner(spark)


def _conf_or_empty(spark, key: str) -> str:
    """``key`` from the session conf, or ``""`` when it is unset. Serverless raises CONFIG_NOT_AVAILABLE for an
    unset key that is not on its allowlist, even with a default, and that is "unset" too, as in
    ``library._spark_conf``."""
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
# global; this is just the zero-config convenience).
# `_default_resolvers` is the session's resolvers it was built with (CLIENT-SESSION-RESOLVERS): once they are
# replaced, the next `cql()` builds a new one.
_default_client: Optional[CqlClient] = None
_default_resolvers = None


def use_resolvers(*, data=None, terminology=None, knowledge=None, results=None) -> None:
    """CLIENT-SESSION-RESOLVERS — set the resolvers this notebook's ``cql()`` calls and ``%%cql`` cells run through::

        from cql_databricks.compiler import use_resolvers
        from cql_databricks.resolvers import FolderTerminologyResolver, VolumeDataResolver
        use_resolvers(data=VolumeDataResolver("/Volumes/main/cql/tests/fhir"),
                      terminology=FolderTerminologyResolver("/Volumes/main/cql/tests/valuesets"))

    Each role given replaces the environment's resolver for that role; each role not given is the environment's
    (`CLIENT-RESOLVERS`). ``results`` is where ``exec()`` reads kept results (`CLIENT-QUERY-REUSE`). A call sets every
    role, so ``use_resolvers()`` returns to the environment. Nothing is read
    here: the next ``cql()`` or cell builds its client with them. ``reset(spark)`` drops them
    (`CLIENT-SESSION-RESET`). A client from :func:`connect` is not changed by them."""
    from cql_databricks.resolvers.interfaces import (DataResolver, KnowledgeResolver, ResultResolver,
                                                      TerminologyResolver)

    roles = {"data": (data, DataResolver), "terminology": (terminology, TerminologyResolver),
             "knowledge": (knowledge, KnowledgeResolver), "results": (results, ResultResolver)}
    for role, (given, kind) in roles.items():
        if given is not None and not isinstance(given, kind):
            raise TypeError(f"{role}= takes a {kind.__name__}, not {type(given).__name__} (CLIENT-SESSION-RESOLVERS)")
    set_session_resolvers({role: given for role, (given, _) in roles.items() if given is not None})


def cql(source=None, *, spark=None, client=None, endpoint=None, **kwargs) -> CqlQuery:
    """Bind ``source`` and return a lazy :class:`~cql_databricks.compiler.CqlQuery` handle; ``.exec(*targets)``
    runs it. This is the package's exported ``cql`` (`SVC-ZERO-CONFIG-NOTEBOOK`).

    With neither ``client`` nor ``endpoint``, it AUTO-DISCOVERS: the client is built once by
    :func:`connect` — App-registry endpoint, ambient-identity OAuth, the environment, the UDF schema — with the
    session's resolvers (:func:`use_resolvers`, `CLIENT-SESSION-RESOLVERS`), and reused for later calls until they
    change. That is the notebook path, where naming any of it
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

    global _default_client, _default_resolvers
    resolvers = session_resolvers()
    if _default_client is None or resolvers is not _default_resolvers:
        _default_client = connect(spark=spark, **resolvers)
        _default_resolvers = resolvers
    # CLIENT-SESSION-STATE: binding reads the session record once, however many cells it resolves.
    with held_record():
        return _default_client.cql(source, spark=spark, **kwargs)


def resolve_libraries(source: str, *, spark=None, environment: Optional[str] = None,
                      bff_app_name: Optional[str] = None) -> list[str]:
    """The CQL sources of ``source``'s ``include`` closure from the environment's knowledge, dependencies first
    (`KNOWLEDGE-RESOLVE-API`, `BUILD-RESOLVE-COMPILE`).

    Resolved in this session, as the notebook's user, in the environment ``environment`` names, else
    ``CQL_DATABRICKS_ENVIRONMENT``, else the default (`CLIENT-ENVIRONMENT`). The resolver is
    :func:`cql_databricks.compiler.resolve.resolve`, the one the Designer uses, so both resolve alike. An include not
    found raises, naming it.

    Before BUILD-RESOLVE-COMPILE this asked the Designer's App (``bff_app_name``), an edge against the feature
    order; that argument is refused rather than ignored."""
    if bff_app_name is not None:
        raise TypeError("resolve_libraries no longer calls the Designer's App: it resolves in this session from "
                        "the environment's knowledge (BUILD-RESOLVE-COMPILE); drop bff_app_name")
    from .resolve import resolve

    return resolve(_environment(spark, environment).content(), cql=source).require_complete().include_sources


def publish_library(name: str, version: str, *, folder: str, base: str, spark=None,
                    environment: Optional[str] = None, app_name: Optional[str] = None):
    """Publish library ``name`` ``version`` from the workspace ``folder`` into the knowledge store of the environment
    ``environment`` names, else ``CQL_DATABRICKS_ENVIRONMENT``, else the default, as this notebook's or Job's user
    (`CLIENT-PUBLISH`, `BIND-PUBLISHED-VIEW`).

    The library's includes held in ``folder`` go with it, dependencies first, and only what changed is written; see
    :func:`cql_databricks.compiler.library_publish.publish_folder_library`, which the Designer's Publish calls too.
    The folder is read from this session's ``/Workspace`` file system, includes not in it resolve from the
    environment's knowledge, the CQL compiles through the Compiler Service App (as :func:`translate` does), and the
    rows are written through ``spark``, then read back through the ``knowledge`` view. ``base`` is the canonical base
    for a library whose folder holds no ``Library`` JSON with a URL. Returns the
    :class:`~cql_databricks.compiler.library_publish.PublishSummary`; raises ``PublishRefused`` when nothing was
    written and ``PublishFailed`` when a write failed after others."""
    from cql_databricks.resolvers.environment import KNOWLEDGE, KNOWLEDGE_STORE
    from .libraries import LocalLibraryFiles
    from .library_publish import publish_folder_library

    session = _session(spark)
    env = _environment(session, environment)
    provider = make_provider({CONF_APP_NAME: app_name} if app_name else {})
    held = env.get()
    return publish_folder_library(env.content(), LocalLibraryFiles(), folder, name, version,
                                  store=held.address(KNOWLEDGE_STORE), view=held.address(KNOWLEDGE),
                                  run=_spark_runner(session), translate=provider.translate_result, base=base)


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
