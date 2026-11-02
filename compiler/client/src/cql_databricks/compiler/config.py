"""Config keys + ONE-precedence setting resolution for the cql client (PHASE-10, BUILD-CLIENT / BUILD-RESOLVE).

Every setting resolves by a SINGLE precedence (`BUILD-RESOLVE`)::

    explicit call arg/kwarg  >  spark.cqldatabricks.* Spark conf  >  CQL_DATABRICKS_* env  >  compiled-in default  >  loud FAIL

Config is granular and explicit — one knob each, Spark-conf style; there is NO bespoke selector object,
and the client never reads a config *table* from a catalog. Compiled-in defaults are constants, never
fetched. A setting has no discovery tier: the data a run reads is chosen at compile time by the library's
binder (`CLIENT-EXEC-DISCOVERY`, :mod:`.binding`), never guessed from a conventional name.

This module owns the READING of config. The OSS core imports no platform SDK.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

# --------------------------------------------------------------------------------------------------
# Spark-conf keys (the `spark.cqldatabricks.*` block).
# --------------------------------------------------------------------------------------------------
CONF_PROVIDER_KIND = "spark.cqldatabricks.provider.kind"
CONF_PROVIDER_ENDPOINT = "spark.cqldatabricks.provider.endpoint"
CONF_LIBRARY = "spark.cqldatabricks.library"
CONF_LIBRARY_SOURCE = "spark.cqldatabricks.librarySource"
CONF_COMPILER_VERSION = "spark.cqldatabricks.compilerVersion"
CONF_TERMINOLOGY_VERSION = "spark.cqldatabricks.terminologyVersion"
CONF_CONTEXT = "spark.cqldatabricks.context"
CONF_DATA_MODEL = "spark.cqldatabricks.dataModel"
# BUILD-LIBRARY-PROVIDER: the library SOURCE seam. `libraryPath` is an `os.pathsep`-separated list of
# search roots (the plural successor to the single `librarySource` root, which still works as a
# one-element path); `libraryLayout` is a comma-separated subset of `libraries.LAYOUTS`;
# `libraryProvider.kind` selects `file` (built-in) or a plugin in the `cql_databricks.libraries` group.
CONF_LIBRARY_PATH = "spark.cqldatabricks.libraryPath"
CONF_LIBRARY_LAYOUT = "spark.cqldatabricks.libraryLayout"
CONF_LIBRARY_PROVIDER_KIND = "spark.cqldatabricks.libraryProvider.kind"
# EMIT-SQL-OBJECT-PREFIX: the session-object namespace prefix. UNSET (the default) emits BARE names — the
# long-standing form. `auto` derives a stable prefix from the library identity; any other value is used
# verbatim. Opt in when more than one library is lowered into a single Spark session.
CONF_OBJECT_PREFIX = "spark.cqldatabricks.objectPrefix"
# EVALUATOR-CLIENT-EVALUATE (`DEC-ONE-VERB-ENGINE-OPTION`, `DEC-ENGINE-SESSION-OPTIONS`): which engine runs a
# query, and the settings the evaluator engine needs. All are session settings like the rest of this block,
# so a session sets them once and every call is just `cql("…")`.
CONF_ENGINE = "spark.cqldatabricks.engine"                      # local (default) | evaluator
CONF_MEASUREMENT_PERIOD = "spark.cqldatabricks.measurementPeriod"  # "<start>/<end>", ISO 8601 interval form
CONF_RECOMPUTE = "spark.cqldatabricks.recompute"                # true: run what the evaluator would skip
# EXEC-UDF-LIBRARY (compiler/docs/execution-model.md): the opaque-UDF catalog JAR to ship to the
# Connect server (`spark.addArtifact`) on the OSS/local path, so emitted SQL referencing `cql_*` UDFs
# resolves. On Databricks the catalog is instead a UC-governed `CREATE FUNCTION … USING JAR` deployed
# once, so this is unset there (registration is a deploy step, not a per-session client action).
CONF_UDF_JAR = "spark.cqldatabricks.runtime.udfJar"
# EMIT-UDF-QUALIFY: the schema qualifying emitted `cql_*` UDF calls (`runtime.schema` on the wire),
# for a session whose search path does not include the schema the catalog was registered in.
CONF_UDF_SCHEMA = "spark.cqldatabricks.udfSchema"

# Auth (all standard; the provider is auth-mechanism-agnostic — it just gets headers).
CONF_AUTH_KIND = "spark.cqldatabricks.auth.kind"            # none | bearer | oauth2-token-exchange | <plugin>
CONF_AUTH_TOKEN = "spark.cqldatabricks.auth.token"          # static bearer
CONF_AUTH_TOKEN_URL = "spark.cqldatabricks.auth.tokenUrl"   # RFC 8693 token endpoint
CONF_AUTH_AUDIENCE = "spark.cqldatabricks.auth.audience"    # RFC 8693 audience (target service)
CONF_AUTH_SUBJECT_TOKEN = "spark.cqldatabricks.auth.subjectToken"
CONF_AUTH_SUBJECT_TOKEN_TYPE = "spark.cqldatabricks.auth.subjectTokenType"
CONF_AUTH_SCOPE = "spark.cqldatabricks.auth.scope"

# --------------------------------------------------------------------------------------------------
# Environment fallbacks (for a bare OSS run with no Spark conf plumbed).
# --------------------------------------------------------------------------------------------------
ENV_PROVIDER = "CQL_DATABRICKS_PROVIDER"
ENV_ENDPOINT = "CQL_DATABRICKS_COMPILE_ENDPOINT"
ENV_LIBRARY = "CQL_DATABRICKS_LIBRARY"
ENV_LIBRARY_SOURCE = "CQL_DATABRICKS_LIBRARY_SOURCE"
ENV_COMPILER_VERSION = "CQL_DATABRICKS_COMPILER_VERSION"
ENV_TERMINOLOGY_VERSION = "CQL_DATABRICKS_TERMINOLOGY_VERSION"
ENV_CONTEXT = "CQL_DATABRICKS_CONTEXT"
ENV_DATA_MODEL = "CQL_DATABRICKS_DATA_MODEL"
ENV_LIBRARY_PATH = "CQL_DATABRICKS_LIBRARY_PATH"
ENV_LIBRARY_LAYOUT = "CQL_DATABRICKS_LIBRARY_LAYOUT"
ENV_LIBRARY_PROVIDER = "CQL_DATABRICKS_LIBRARY_PROVIDER"
ENV_OBJECT_PREFIX = "CQL_DATABRICKS_OBJECT_PREFIX"
ENV_ENGINE = "CQL_DATABRICKS_ENGINE"
#: CLIENT-ENVIRONMENT: the environment a notebook or Job runs in; unset, the default environment.
ENV_ENVIRONMENT = "CQL_DATABRICKS_ENVIRONMENT"
ENV_MEASUREMENT_PERIOD = "CQL_DATABRICKS_MEASUREMENT_PERIOD"
ENV_RECOMPUTE = "CQL_DATABRICKS_RECOMPUTE"
ENV_UDF_JAR = "CQL_DATABRICKS_UDF_JAR"
ENV_UDF_SCHEMA = "CQL_DATABRICKS_UDF_SCHEMA"
ENV_AUTH_KIND = "CQL_DATABRICKS_AUTH_KIND"
ENV_AUTH_TOKEN = "CQL_DATABRICKS_AUTH_TOKEN"

# --------------------------------------------------------------------------------------------------
# Compiled-in defaults (NEVER fetched). Override any of them with the granular knob above.
# --------------------------------------------------------------------------------------------------
# NO compiled-in default context (`LANG-CONTEXT-DEFAULT`): the client must NOT impose one. When the caller
# sets no context (explicit arg / conf / env), the client sends UNSET and the COMPILER applies the
# spec-conformant resolution (library-declared `context`, else the model default, else `Unfiltered`).
# Hard-coding `Patient` here was non-conformant (it forced a Patient retrieve onto a context-free define).
#: EVALUATOR-CLIENT-EVALUATE — the convention tier of the engine setting: the caller's own compute, as today.
#: Not a fallback: an engine that fails never hands the query to another (`DEC-ONE-VERB-ENGINE-OPTION`).
LOCAL_ENGINE = "local"
DEFAULT_DATA_MODEL = "FHIR"                         # the CQL data model; engine is FHIR-only today (ARCH-DATAMODEL-TRAIT future)

# Well-known eCQM POPULATION define names, in canonical report order (`BUILD-CLIENT-POPULATIONS`). The
# default target set of `exec()` when the caller names none: the intersection of these with the library's
# actual defines. A convention (the authoritative source is a FHIR `Measure`'s populationCriteria — a
# deferred service-side enrichment of `describe`); an ad-hoc library with none falls back to its own defines.
POPULATION_DEFINE_NAMES = (
    "Initial Population",
    "Denominator",
    "Denominator Exclusions",
    "Denominator Exceptions",
    "Numerator",
    "Numerator Exclusions",
    "Measure Population",
    "Measure Population Exclusions",
    "Measure Observation",
)

# --------------------------------------------------------------------------------------------------
# Client -> host CARRY contract (`BUILD-RESOLVE`): resolved settings the client hands the compiler as
# `CompileRequest.overrides` under these stable keys. Data and value sets are bound by role in the request
# (`BIND-CALL-SITE`), never through an override.
# --------------------------------------------------------------------------------------------------
OVERRIDE_DATA_MODEL = "data.model"
OVERRIDE_COMPILER_VERSION = "compiler.version"
OVERRIDE_TERMINOLOGY_VERSION = "terminology.version"


class ConfigResolutionError(ValueError):
    """A loud FAIL when a REQUIRED setting cannot be resolved by any tier (`BUILD-RESOLVE`). The message
    names every way to set it so the fix is obvious. Subclasses ``ValueError`` — it IS a configuration
    error, and preserves the prior missing-endpoint contract."""

    def __init__(self, name: str, conf_key: Optional[str], env: Optional[str]):
        ways = [f"pass it explicitly"]
        if conf_key:
            ways.append(f"set Spark conf `{conf_key}`")
        if env:
            ways.append(f"set env `{env}`")
        super().__init__(
            f"cannot resolve {name}: " + ", or ".join(ways) + ".")
        self.name = name


def resolve_setting(
    name: str,
    *,
    explicit: Optional[str] = None,
    conf: Optional[Mapping[str, str]] = None,
    conf_key: Optional[str] = None,
    env: Optional[str] = None,
    default: Optional[str] = None,
    required: bool = False,
) -> Optional[str]:
    """Resolve one setting by the single `BUILD-RESOLVE` precedence:
    ``explicit > conf[conf_key] > os.environ[env] > default > FAIL``.

    ``default`` is a compiled-in default VALUE (used directly, e.g. the ``FHIR`` data model). Empty
    strings count as unset. When nothing resolves and ``required`` is set (and no ``default``), raises
    [[ConfigResolutionError]]; otherwise returns ``None``."""
    if explicit is not None and explicit != "":
        return explicit
    if conf is not None and conf_key is not None:
        v = conf.get(conf_key)
        if v is not None and v != "":
            return v
    if env is not None:
        ev = os.environ.get(env)
        if ev is not None and ev != "":
            return ev
    if default is not None and default != "":
        return default
    if required:
        raise ConfigResolutionError(name, conf_key, env)
    return None


def resolve(conf: Mapping[str, str], key: str, env: Optional[str] = None,
            default: Optional[str] = None) -> Optional[str]:
    """`conf[key]` (if non-empty) > `os.environ[env]` (if given) > `default`. The flat, discovery-free
    form used by the transport/auth resolution (endpoint has no discovery on OSS; auth defaults to
    ``none``). A thin shim over [[resolve_setting]]."""
    return resolve_setting(key, conf=conf, conf_key=key, env=env, default=default)
