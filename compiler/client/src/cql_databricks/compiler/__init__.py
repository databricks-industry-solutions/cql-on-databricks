"""cql-databricks — the runtime-compiled ``cql()`` plan builder for CQL on Spark (BUILD-CLIENT).

One distribution with two layers.

**The core** reaches the compiler over plain HTTP (:class:`RemoteProvider`) with standard OAuth, and
imports no platform SDK. Build it explicitly against a named endpoint. That is the local development and
test path: the conformance suite's end-to-end lane drives it against a local compile service::

    from cql_databricks.compiler import CqlClient
    client = (CqlClient.builder()
              .endpoint("http://localhost:8080").auth_bearer(token)
              .library_source("cql/").data_catalog("fhir").spark(spark).build())
    q = (client.cql("cql/HypertensionBPControl.cql")
              .with_measurement_period("2026-01-01", "2026-12-31"))
    q.dag()                # the neutral LibraryGraph
    q.exec("Initial Population")    # -> lazy DataFrame (single target)
    q.exec()               # -> {Initial Population: DataFrame, ...} (the default set)

**The Databricks layer** makes that zero-config on a workspace (`SVC-ZERO-CONFIG-NOTEBOOK`): it resolves
the Compiler Service App endpoint from the App registry, authenticates with the ambient identity by
serverless-to-App OAuth token exchange, finds each run's data source by its Unity Catalog tag
(`CLIENT-EXEC-DISCOVERY`), and discovers the UDF schema from ``information_schema``. So a notebook names no
endpoint, no token, and no catalog::

    from cql_databricks.compiler import cql
    cql('define "Two": 2 + 2').exec("Two").display()

or, holding the session explicitly and mounting a Git Folder of CQL as the library source
(`SVC-NOTEBOOK-LIBRARY-FOLDER`), so a published multi-library measure runs by NAME::

    from cql_databricks.compiler import connect
    client = connect(spark, library_path="/Workspace/Users/me/measure-content/input/cql")
    client.cql(library="HypertensionBPControl").exec("Initial Population")

``exec(*targets)`` is the ONLY terminal (returns DataFrame(s)); a string is always SOURCE, never a target.

This is one package, not a core plus a plugin (decision 10). It depends on ``databricks-sdk``. The
explicit :class:`CqlClient` + :class:`RemoteProvider` path above needs none of it. The no-Databricks-
dependency rule applies to the compiler and its runtime, enforced by ``./gradlew checkNeutrality``.
"""
#: The distribution version (`BUILD-CLIENT-COMPAT`). Stated so a host can gate on a client capability rather
#: than probing for an attribute, and so a bug report names a build.
__version__ = "0.1.1"

from .client import CqlClient, CqlClientBuilder
from .config import ConfigResolutionError, resolve_setting
# `cql_explicit` is the core's config-driven binder: it resolves an endpoint from explicit arg, conf, or
# env and FAILS LOUDLY when there is none. The exported `cql` below is the Databricks zero-config form,
# which is what a notebook wants; this one stays reachable under a name that says which it is, so the
# platform-free path is never only an implementation detail of the platform one.
from .cql import CqlQuery, compile_pipeline, describe
from .cql import cql as cql_explicit
from .libraries import (LAYOUTS, FileLibraryProvider, LibraryProvider, LibraryRef,
                        LibraryResolutionError, LibrarySource, includes_of)
from .library import Library, load_library, measurement_period_bounds, measurement_period_literal
# The Databricks layer (`SVC-DISCOVERY-PLUGIN`, `SVC-ZERO-CONFIG-NOTEBOOK`). `cql` here SHADOWS the core
# binder deliberately: on a workspace, zero-config is the correct default, and it still honors an explicit
# `client=` / `endpoint=` so nothing is taken away.
from .zeroconfig import connect, cql, publish_library, resolve_libraries, translate
from .provider import make_provider
from .session_libraries import register_cell
# CLIENT-SESSION-MOUNT / CLIENT-SESSION-RESET / BIND-RESHAPE: session data, and raw FHIR JSON as a source.
from .mount import mount, reset
from .reshape import FhirJson
# CLIENT-PUBLISH: a folder library into a tagged knowledge store.
from .library_publish import PublishFailed, PublishRefused, PublishSummary, publish_folder_library
# BIND-PUBLISH: a tagged data source from tables.
from .publish import PublishPlan, publish_data_source
from .providers import (
    RELATION,
    RESOURCE,
    RESOURCE_COLUMN,
    SCALAR,
    VALUE_COLUMN,
    CompileError,
    CompileProvider,
    CompileRequest,
    CredentialProvider,
    DatasetRecipe,
    DefineShape,
    LibraryGraph,
    LibraryInput,
    NoAuth,
    OAuth2TokenExchange,
    PipelinePlan,
    ReadCoordinates,
    RemoteProvider,
    ResourceSchema,
    SqlCompileResult,
    SqlStatement,
    StaticBearer,
    resolve_credentials,
    resolve_provider,
)

__all__ = [
    "__version__",
    "CqlClient", "CqlClientBuilder", "CqlQuery",
    "cql", "cql_explicit", "describe", "compile_pipeline", "load_library", "Library",
    # SVC-ZERO-CONFIG-NOTEBOOK: the Databricks surface.
    "connect", "publish_library", "resolve_libraries", "translate", "make_provider", "register_cell",
    "measurement_period_bounds",
    "measurement_period_literal",
    # BUILD-LIBRARY-PROVIDER: the library SOURCE seam. Implement `LibraryProvider` and inject it with
    # `CqlClientBuilder.library_provider(...)`, or register it under the `cql_databricks.libraries`
    # entry-point group and select it by `.library_provider_kind(name)`.
    "LibraryProvider", "FileLibraryProvider", "LibraryRef", "LibrarySource",
    "LibraryResolutionError", "LAYOUTS", "includes_of",
    # CLIENT-SESSION-MOUNT, CLIENT-SESSION-RESET, BIND-RESHAPE: FHIR data on the session, with no catalog writes.
    "mount", "reset", "FhirJson",
    # BIND-PUBLISH: write a tagged data source from tables, for review or to run.
    "publish_data_source", "PublishPlan",
    "publish_folder_library", "PublishSummary", "PublishRefused", "PublishFailed",
    "CompileProvider", "RemoteProvider", "CompileRequest", "CompileError",
    "SqlCompileResult", "SqlStatement",
    # CLIENT-RESOURCE-SCHEMA: the model's `__resource` struct per FHIR type (`CqlClient.resource_schema`).
    "ResourceSchema",
    # EMIT-SQL-VALUE-COLUMN: the uniform result shape contract.
    "DefineShape", "VALUE_COLUMN", "SCALAR", "RELATION",
    "PipelinePlan", "DatasetRecipe",
    # DATA-RETRIEVE-BARRIER-OPT: the materialized-read dataset shape + its provenance.
    "RESOURCE", "RESOURCE_COLUMN", "ReadCoordinates",
    "LibraryGraph", "CredentialProvider", "NoAuth", "StaticBearer", "OAuth2TokenExchange",
    "resolve_provider", "resolve_credentials",
    "resolve_setting", "ConfigResolutionError",
]


def load_ipython_extension(ipython) -> None:
    """``%load_ext cql_databricks.compiler`` — register the ``%%cql`` cell magic (`SVC-NOTEBOOK-CQL-MAGIC`).

    Imported lazily so the package stays importable without IPython, which is a notebook-only surface and
    deliberately not a dependency."""
    from .magic import load_ipython_extension as _load

    _load(ipython)
