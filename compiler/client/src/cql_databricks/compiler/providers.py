"""Compile providers + credential providers (PHASE-10, BUILD-CLIENT / BUILD-COMPILE-SERVICE).

How the client REACHES the compiler and how it AUTHENTICATES are two orthogonal, pluggable seams:

* ``CompileProvider.compile_sql(request) -> SqlCompileResult`` — the transport. ``RemoteProvider`` is the
  OSS default (HTTP to a compile endpoint: the local ``OssCompileHost`` or any hosted service).
* ``CredentialProvider.auth_headers() -> dict`` — standard auth. OSS ships ``NoAuth``, ``StaticBearer``
  (RFC 6750), and a generic ``OAuth2TokenExchange`` (RFC 8693). Nothing here is platform-specific.

Platform hosts (a Databricks App, Microsoft Fabric, …) contribute a provider/credential implementation
selected by KIND through ``resolve_provider``
discovers it by name. The OSS package never imports a platform SDK.
"""
from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from . import config
from .libraries import LibrarySource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibraryInput:
    """One root library of a `PIPE-MULTI-LIB` request: the root as ELM JSON plus its include closure as
    ELM JSON strings. The service links every root into one merged plan and deduplicates the libraries
    they share, so a shared define is computed once."""
    elm: str
    deps: Sequence[str] = ()


@dataclass(frozen=True, kw_only=True)
class CompileRequest:
    """Data-free compile input: the CQL library, the define to build, the fixed boundary set (RUNTIME
    cost-free mode when present), the CQL context, config overrides, and parameter-value overrides.

    KEYWORD-ONLY by construction (`BUILD-CLIENT-COMPAT`). This is a PUBLIC type a third-party provider
    plugin both constructs and consumes, so field ORDER must never be part of the contract: a new field
    can then land anywhere without silently re-binding a positional argument in someone else's plugin.

    ``context`` is OPTIONAL (`LANG-CONTEXT-DEFAULT`): ``None`` means the caller specified none, so the
    compiler resolves it (library-declared ``context`` → model default → ``Unfiltered``). The client never
    imposes ``Patient``. It serializes to JSON ``null`` (the wire "unset").

    The library is given as ELM JSON (``elm``, the interchange form) OR as raw CQL TEXT (``cql``) which the
    service translates to ELM (`BUILD-CLIENT-SESSION`); exactly one is non-empty. ``parameters`` maps a CQL
    parameter name to a CQL EXPRESSION-TEXT override value (e.g. ``"40"``, ``"Interval[@2026-01-01,
    @2026-12-31]"``) the compiler inlines as a literal (extends `LANG-PARAMETER`)."""
    elm: str = ""
    target: Optional[str] = None
    boundaries: Optional[Set[str]] = None
    context: Optional[str] = None
    overrides: Mapping[str, str] = field(default_factory=dict)
    grain_first: bool = False
    cql: Optional[str] = None
    parameters: Mapping[str, str] = field(default_factory=dict)
    # BIND-MANIFEST: the retrieve binding table — per-FHIR-type map to the SQL UDTF that retrieves it
    # (``fhirType -> {"function": ..., "catalog"?: ...}``, BIND-RETRIEVE-UDF). An entry without a
    # ``function`` is ignored server-side; an unmapped type falls back to the convention
    # ``retrieve_<Type>``. Empty (the default) → every type is convention-bound.
    sources: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    # BUILD-COMPILE-LINK: the ROOT's include CLOSURE, resolved by the caller (`BUILD-LIBRARY-PROVIDER`).
    # The service resolves NOTHING — it merges what it is handed — so a multi-library eCQM is only
    # compilable when this carries every non-FHIRHelpers dependency. Each entry ships as ELM when it has
    # it, else as CQL TEXT the service registers as a translation source.
    deps: Sequence["LibrarySource"] = ()
    # EMIT-SQL-OBJECT-PREFIX: session-object namespace. Empty (the default) emits BARE names.
    object_prefix: Optional[str] = None
    # BIND-TERMINOLOGY-TABLE / EMIT-UDF-QUALIFY: the fully-qualified value-set expansion table and the
    # schema qualifying emitted `cql_*` UDF calls. Both ride in `sources` on the wire (`valuesets.table`
    # / `udfs.schema`) rather than in `overrides` — that is where the service reads them.
    terminology_table: Optional[str] = None
    udf_schema: Optional[str] = None
    # PIPE-MULTI-LIB: several roots in one request. When set the service ignores ``elm``/``cql``/``deps``,
    # links every root, and qualifies each root define as ``{libId}__{define}`` in the recipe's ``target``.
    libraries: Sequence[LibraryInput] = ()
    # PIPE-EVAL-SCOPED-READ: the evaluation set a pipeline plan writes; its recipes then read only that
    # evaluation's rows of each published sibling (`RUN-RESULTS-BY-EVAL`). Unset: bare reads.
    eval_id: Optional[str] = None
    # BUILD-CLIENT-SESSION-PARAMETERS: values set for the whole session. The service applies the ones the
    # library declares, beneath ``parameters``, and leaves the rest, so one session period binds every
    # measure that declares one and no library that does not.
    session_parameters: Mapping[str, str] = field(default_factory=dict)
    durable: bool = False
    # SVC-COMPILE-BATCH: service-owned period typing and binding validation, no preparation HTTP calls.
    measurement_period: tuple[str, str] | None = None
    data_source: Mapping | None = None

    def to_json(self) -> dict:
        return {
            "elm": self.elm,
            "cql": self.cql,
            "target": self.target,
            # sorted for a deterministic request body
            "boundaries": sorted(self.boundaries) if self.boundaries is not None else None,
            "context": self.context,
            "overrides": dict(self.overrides),
            "grainFirst": self.grain_first,
            # sorted for a deterministic request body
            "parameters": {k: self.parameters[k] for k in sorted(self.parameters)},
            # Every optional key below is OMITTED when unset, so the body a caller sends today is
            # byte-identical to the pre-existing one and an older host never sees a key it must ignore
            # (`BUILD-CLIENT-COMPAT`).
            **({"deps": [self._dep_entry(d, root_is_cql=bool(self.cql)) for d in self.deps]}
               if self.deps else {}),
            **({"objectPrefix": self.object_prefix} if self.object_prefix else {}),
            **({"evalId": self.eval_id} if self.eval_id else {}),
            **({"durable": True} if self.durable else {}),
            **({"measurementPeriod": list(self.measurement_period)} if self.measurement_period else {}),
            **({"dataSource": dict(self.data_source)} if self.data_source is not None else {}),
            **({"sessionParameters": {k: self.session_parameters[k] for k in sorted(self.session_parameters)}}
               if self.session_parameters else {}),
            **({"sources": self._sources_json()} if self._sources_json() else {}),
            **({"libraries": [{"elm": lib.elm, "deps": list(lib.deps)} for lib in self.libraries]}
               if self.libraries else {}),
        }

    def _sources_json(self) -> dict:
        """The `BIND-MANIFEST` retrieve bindings plus the two reserved non-type entries the service reads
        out of the same object: ``valuesets.table`` (`BIND-TERMINOLOGY-TABLE`) and ``udfs.schema``
        (`EMIT-UDF-QUALIFY`). Only entries that carry a function are emitted; keys are sorted for a
        deterministic body."""
        out = {t: self._source_entry(self.sources[t]) for t in sorted(self.sources)
               if self.sources[t].get("function")}
        if self.terminology_table:
            out["valuesets"] = {"table": self.terminology_table}
        if self.udf_schema:
            out["udfs"] = {"schema": self.udf_schema}
        return out

    @staticmethod
    def _dep_entry(dep: "LibrarySource", *, root_is_cql: bool):
        """One dependency on the wire: an ELM JSON string, or a ``{"cql": …}`` object the service
        translates. The string form is what `BUILD-COMPILE-LINK` has always accepted, so an ELM-only
        closure is wire-identical to a hand-built one.

        WHICH form depends on the ROOT, not just on what the dependency happens to carry. When the root
        ships as CQL the service must TRANSLATE it, and translation needs the closure as CQL SOURCES —
        an ELM-only dependency leaves the root's `include` unresolvable ("Could not load source for
        library …") even though its ELM was right there. So a CQL root takes each dependency's CQL
        whenever it has one; an ELM root prefers ELM and translates nothing."""
        prefer_cql = root_is_cql and dep.cql
        if dep.elm and not prefer_cql:
            return dep.elm
        return {"cql": dep.cql, "name": dep.name, **({"version": dep.version} if dep.version else {})}

    @staticmethod
    def _source_entry(entry: Mapping[str, str]) -> dict:
        out = {"function": entry["function"]}
        if entry.get("catalog"):
            out["catalog"] = entry["catalog"]
        return out


#: EMIT-SQL-VALUE-COLUMN — the payload column EVERY define result carries, whatever its row shape
#: (mirrors the compiler's ``SystemColumns.Val``). A scalar define publishes ``(__subject_id, __val)``; a
#: grain-first relation define publishes ``(__subject_id, __ordinal, __val)``. The define's own NAME is not
#: part of its result schema, so a reader never re-derives the ``SqlNames.sanitize`` convention to find it.
VALUE_COLUMN = "__val"

#: EMIT-SQL-VALUE-COLUMN — the two row shapes a define result can have (:attr:`DefineShape.kind`).
SCALAR = "scalar"
RELATION = "relation"

#: DATA-RETRIEVE-BARRIER-OPT — the row shape of a materialized READ dataset: ``(__subject_id,
#: __resource)``, one row per matching resource, payload in :data:`RESOURCE_COLUMN`. A read is the mount's
#: rows post-filter, BEFORE any grain-first projection, so it carries no ``__ordinal`` and its payload is
#: the resource struct rather than ``__val``. Only ever seen on a :class:`DatasetRecipe`, never on a define.
RESOURCE = "resource"

#: The payload column of a :data:`RESOURCE`-kind dataset (the FHIR resource struct, carrying the
#: ``__resource_type`` / ``__resource_id`` identity lineage).
RESOURCE_COLUMN = "__resource"


@dataclass(frozen=True)
class DefineShape:
    """The READ SHAPE of one define's result (`EMIT-SQL-VALUE-COLUMN`) — what a consumer needs to read it
    without re-deriving a naming convention or probing the produced schema.

    ``define`` is the RAW ELM name ("Initial Population" — spaces and casing preserved); ``obj`` is the
    object the script produces. ``kind`` is :data:`SCALAR` (one row per subject, ``(__subject_id,
    __val)``) or :data:`RELATION` (grain-first, one row per list ELEMENT in ``__ordinal`` order). A list
    carried as a per-subject ARRAY is :data:`SCALAR` with an array-typed value, so ``kind`` describes the
    ROW shape and nothing else. ``value_column`` is always :data:`VALUE_COLUMN` — published rather than
    assumed, so the convention lives in the contract instead of in every consumer."""
    define: str
    obj: str
    kind: str
    value_column: str = VALUE_COLUMN


@dataclass(frozen=True)
class SqlStatement:
    """One statement of a compiled SQL script (EMIT-SQL-EXECUTE): the object it defines, its KIND
    (``view`` | ``cache_table`` | ``table``), and the rendered single-statement SQL (no trailing ``;``)."""
    name: str
    kind: str
    sql: str


@dataclass(frozen=True)
class SqlCompileResult:
    """The compiled SQL result (EMIT-SQL-EXECUTE): the ordered ``statements`` to run via ``spark.sql`` in
    turn, the ``target`` object to ``SELECT`` afterward, and the define names. A small, auditable DAG of
    views/CTEs by name — the honest alternative to the share-free proto.

    ``shapes`` is the per-define READ CONTRACT (`EMIT-SQL-VALUE-COLUMN`) — one :class:`DefineShape` per
    entry of ``defines``, in the same order. Empty when talking to an older compile service that does not
    emit it, in which case a consumer falls back to :data:`VALUE_COLUMN`."""
    statements: List[SqlStatement]
    target: str
    defines: List[str]
    shapes: List[DefineShape] = field(default_factory=list)

    def shape(self, define: str) -> Optional[DefineShape]:
        """The :class:`DefineShape` for ``define`` (by RAW ELM name), or ``None`` when the service emitted
        no shapes. The supported way to learn a result's row kind and value column."""
        return next((s for s in self.shapes if s.define == define), None)

    @property
    def script(self) -> str:
        """The whole script as one ``;``-terminated text blob, for dumping / pasting into a SQL editor."""
        return "".join(s.sql + ";\n\n" for s in self.statements)


def _durable_identity(raw: Optional[dict]) -> Optional["DurableIdentity"]:
    """RUN-CLIENT-RESULT-IDENTITY — an older compiler's logic and shape hashes are refused, not half-read."""
    if raw is None:
        return None
    if "fingerprint" not in raw:
        raise CompileError(f"the compiler service returned durable identity format {raw.get('format')!r} without a "
                           "fingerprint; deploy a compiler service from the same release as this client")
    return DurableIdentity(format=raw["format"], fingerprint=raw["fingerprint"], reusable=raw["reusable"],
                           parameters=raw.get("parameters", {}))


def _read_coordinates(raw: Optional[dict]) -> Optional["ReadCoordinates"]:
    """Parse a recipe's ``retrieveRead`` object (DATA-RETRIEVE-BARRIER-OPT), or ``None`` for a define
    recipe — including when talking to an older compile service that does not emit the key at all."""
    if not raw:
        return None
    return ReadCoordinates(
        resource_type=raw["resourceType"],
        profile=raw.get("profile"),
        code_property=raw.get("codeProperty"),
        code_filter_kind=raw.get("codeFilterKind", "none"),
        value_set_id=raw.get("valueSetId"),
        codes=[(c["system"], c["code"]) for c in raw.get("codes", [])],
        date_property=raw.get("dateProperty"),
        date_filtered=bool(raw.get("dateFiltered", False)),
        period_property=raw.get("periodProperty"),
        period_low_inclusive=raw.get("periodLowInclusive"),
        period_high_exclusive=raw.get("periodHighExclusive"),
        consumed_by=list(raw.get("consumedBy", [])),
        call_sites=int(raw.get("callSites", 0)))


@dataclass(frozen=True)
class ReadCoordinates:
    """DATA-RETRIEVE-BARRIER-OPT — what a materialized READ dataset holds.

    This is the provenance vehicle for a dataset that has no define to name it. A read's published name is
    deliberately OPAQUE (content-addressed over its coordinates), so everything a human or a report needs
    to say about the dataset comes off these fields — exactly as a define recipe is correlated by
    ``(library, library_version, define)`` rather than by parsing the object name.

    ``code_filter_kind`` is ``"valueset"`` | ``"codes"`` | ``"other"`` | ``"none"``. ``"other"`` means the
    bracket is filtered by something the compiler cannot name statically (a parameter, an inlined operand):
    a filtered read is NEVER reported as unfiltered (``CORE-NO-SILENT-MISLOWER``).

    ``date_filtered`` reports only the PRESENCE of a folded date range, not the range: after pushdown it is
    an arbitrary expression, commonly measurement-period derived, so only its property is statically known.
    The coarse ``period_*`` window IS literal and is published as such.

    ``consumed_by`` / ``call_sites`` are the reuse figures — ``call_sites`` is what
    ``materialize.retrieves = costly`` thresholds."""
    resource_type: str
    profile: Optional[str] = None
    code_property: Optional[str] = None
    code_filter_kind: str = "none"
    value_set_id: Optional[str] = None
    codes: List[Tuple[str, str]] = field(default_factory=list)
    date_property: Optional[str] = None
    date_filtered: bool = False
    period_property: Optional[str] = None
    period_low_inclusive: Optional[str] = None
    period_high_exclusive: Optional[str] = None
    consumed_by: List[str] = field(default_factory=list)
    call_sites: int = 0


@dataclass(frozen=True, kw_only=True)
class DurableIdentity:
    """RUN-CLIENT-RESULT-IDENTITY — compiler-issued identity; absent on non-opt-in plans.

    ``fingerprint`` is the result's whole identity: the identity format, the emitted SQL, the parameter values
    it uses, its upstream fingerprints and the bound data source's address (`DEC-RESULT-IDENTITY`)."""

    format: str
    fingerprint: str
    reusable: bool
    parameters: Mapping[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetRecipe:
    """One dataset RECIPE of a :class:`PipelinePlan` (`PIPE-SQL-DAG-REUSE`): the self-contained sub-script
    that produces ONE materialized define, cut out of the single whole-library lowering.

    ``name`` is the PUBLISHED dataset name (the ``@dp.materialized_view(name=…)`` this recipe becomes AND the
    name a dependent recipe reads it by — the single naming source of truth, from the compiler). ``target``
    is the LOCAL object the recipe's own ``statements`` produce (what the node ``SELECT``s / returns).
    ``reads`` are the published names of the upstream materialized SIBLINGS this recipe depends on — the
    DECLARED dataset reads (the SDP dependency edges). ``statements`` is the DDL form (``CREATE VIEW``/``CACHE
    TABLE``) the interactive ``cql()`` / ``spark.sql`` path runs; ``query`` is the SAME sub-script collapsed
    to ONE declarative ``WITH … SELECT * FROM <target>`` (no DDL) that a Spark Declarative Pipeline dataset
    body returns — SDP rejects ``CREATE VIEW``/``CACHE TABLE`` commands in a body, and traces the
    ``FROM cql_<sibling>`` reads in the query as the dependency edges.

    ``library``, ``library_version``, and ``define`` carry the **original ELM coordinates** — the fully
    qualified identifier a client can join on without knowing the ``SqlNames.sanitize`` / ``datasetName``
    convention that produced ``name``. Defaults are empty strings for backward compatibility with older
    compile services that do not emit them.

    ``sql_file`` is the deploy-ready SDP SQL statement (``PIPE-EMIT-SQL``): ``CREATE OR REFRESH
    MATERIALIZED VIEW`` for materialized defines, ``CREATE OR REFRESH VIEW`` for non-materialized. The
    SQL needs no runtime compilation and no client library. Empty when the service does not emit it."""
    name: str
    target: str
    reads: List[str]
    statements: List[SqlStatement]
    query: str
    library: str = ""
    library_version: str = ""
    define: str = ""
    sql_file: str = ""
    # EMIT-SQL-VALUE-COLUMN: the published dataset's ROW shape (:data:`SCALAR` | :data:`RELATION` |
    # :data:`RESOURCE`) and its payload column. Stated on the recipe so a pipeline reading the dataset never
    # probes its schema.
    kind: str = SCALAR
    value_column: str = VALUE_COLUMN
    # DATA-RETRIEVE-BARRIER-OPT: set ONLY on a materialized retrieve-READ recipe (``None`` on a define
    # recipe), so the field also tells the two kinds of recipe apart without parsing a name. Named
    # ``retrieve_read`` rather than ``read`` so it cannot be confused with the sibling-dependency ``reads``.
    retrieve_read: Optional[ReadCoordinates] = None
    durable: Optional[DurableIdentity] = None

    @property
    def is_retrieve_read(self) -> bool:
        """True when this recipe publishes a materialized retrieve READ rather than a define."""
        return self.retrieve_read is not None


@dataclass(frozen=True)
class PipelinePlan:
    """The compile-once PIPELINE PLAN (`PIPE-SQL-DAG-REUSE`): the whole measure lowered ONCE, then cut at
    each materialized define into one :class:`DatasetRecipe` per materialized define. The reference pipeline
    turns each recipe into one ``@dp.materialized_view`` — with NO per-node ``cql()`` — so a materialized
    sibling is computed once, as its own dataset, and dependents read it back by name. Results are IDENTICAL
    to the fully re-inlined baseline; only the structure differs.

    ``compiler_version`` is the service's `SVC-COMPILER-IDENTITY`, the key a deployment pins its plan to
    (`BUILD-VERSION-PIN`); empty from a service that does not report one."""
    datasets: List[DatasetRecipe]
    compiler_version: str = ""


@dataclass(frozen=True)
class LibraryGraph:
    """A NEUTRAL, pipeline-agnostic description of a library's compiled graph (the ``/describe`` result) —
    what an external pipeline reads to build and drive its OWN dataset graph. Carries no pipeline concepts.

    ``boundaries`` is the materialization set (raw seam keys) to pass as ``cql(define, boundaries=…)`` at
    runtime — populations plus every define that filters (``EXEC-MATERIALIZE-FILTER``);
    ``materialized_defines`` is the convenience "persist this define" hint derived from it;
    ``dependencies`` maps each define to the defines it references (the DAG edges).

    ``define_kinds`` + ``value_column`` are the READ contract (`EMIT-SQL-VALUE-COLUMN`): every define carries
    its payload in ``value_column``, and ``define_kinds[name]`` is :data:`SCALAR` or :data:`RELATION` — the
    only thing that differs between two results. Published here so ``describe`` alone is enough to plan how
    to read each define, with no second compile call and no schema probe."""
    defines: List[str]
    dependencies: Dict[str, List[str]]
    boundaries: List[str]
    materialized_defines: List[str]
    compiler_version: str
    terminology_version: Optional[str] = None
    # EMIT-DESCRIBE-ENCODING: the het-Choice carrier encoding ("variant"|"struct") the library was
    # lowered against, and the per-resource demanded fields (the minimum a typed-subset binding must
    # supply, BIND-CAPABILITY-OVERLAP; ["*"] = whole-body/het-Choice). The runtime re-lowers against the
    # described encoding, so a mismatch is a correctness bug.
    encoding: str = "struct"
    retrieve_demand: Dict[str, List[str]] = field(default_factory=dict)
    # EMIT-SQL-VALUE-COLUMN: per-define row shape, and the payload column shared by every define.
    define_kinds: Dict[str, str] = field(default_factory=dict)
    value_column: str = VALUE_COLUMN
    # EMIT-DESCRIBE-RETRIEVE-TYPES: every resource type the library retrieves, sorted; empty for data-free
    # CQL. ``None`` when the service does not report it, which a caller choosing data must refuse.
    retrieve_types: Optional[List[str]] = None

    def kind_of(self, define: str) -> str:
        """The row shape of ``define`` (:data:`SCALAR` | :data:`RELATION`), defaulting to :data:`SCALAR`
        when talking to an older compile service that does not describe kinds."""
        return self.define_kinds.get(define, SCALAR)


# --------------------------------------------------------------------------------------------------
# Credential providers (auth) — standard OAuth, provider-neutral.
# --------------------------------------------------------------------------------------------------

class CredentialProvider(abc.ABC):
    """Yields the HTTP auth headers for a compile request. The compiler touches NO data, so auth is
    light and is entirely the host's concern (BUILD-COMPILE-SERVICE)."""

    @abc.abstractmethod
    def auth_headers(self) -> Dict[str, str]:
        ...


class NoAuth(CredentialProvider):
    """No auth — the OSS default (a localhost host / a network that fronts its own auth)."""

    def auth_headers(self) -> Dict[str, str]:
        return {}


class StaticBearer(CredentialProvider):
    """A fixed OAuth 2.0 Bearer token (RFC 6750) from config/env."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("StaticBearer requires a non-empty token")
        self._token = token

    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


# RFC 8693 token-exchange type URIs.
_GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_TYPE_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"


class OAuth2TokenExchange(CredentialProvider):
    """Generic OAuth 2.0 **Token Exchange** (RFC 8693): exchange an ambient ``subject_token`` at
    ``token_url`` for an ``audience``-scoped access token, then present it as a Bearer header.

    This is the provider-neutral core of the recipe proven for a Databricks App (a serverless notebook
    exchanging its token for an App-audience token). Only the platform-specific INPUTS —
    resolving the audience (the App's OAuth client id) and obtaining the subject token — are supplied by
    a platform plugin; the exchange itself is standard OIDC and lives here. The exchanged token is
    cached for the provider's lifetime.
    """

    def __init__(self, token_url: str, subject_token: str, audience: str,
                 scope: str = "all-apis", subject_token_type: str = _TYPE_JWT,
                 requested_token_type: str = _TYPE_ACCESS_TOKEN,
                 retry_errors: Tuple[str, ...] = (), retry_seconds: float = 0.0):
        if not (token_url and subject_token and audience):
            raise ValueError("OAuth2TokenExchange requires token_url, subject_token and audience")
        self._token_url = token_url
        self._subject_token = subject_token
        self._audience = audience
        self._scope = scope
        self._subject_token_type = subject_token_type
        self._requested_token_type = requested_token_type
        self._retry_errors = frozenset(retry_errors)
        self._retry_seconds = retry_seconds
        self._cached: Optional[str] = None

    def _exchange(self) -> str:
        """One exchange, retried while the server answers with one of ``retry_errors`` and the next wait
        still fits in ``retry_seconds`` (`SVC-EXCHANGE-RETRY`). Off by default: a platform plugin opts in for
        an error its platform reports transiently. Each retry logs one warning; the last failure raises."""
        import time
        deadline = time.monotonic() + self._retry_seconds
        tries, wait = 1, 5.0
        while True:
            resp = self._post()
            if resp.status_code == 200:
                if tries > 1:
                    log.info("OAuth token exchange succeeded on try %d", tries)
                return resp.json()["access_token"]
            reason = self._reason(resp)
            if reason not in self._retry_errors or time.monotonic() + wait > deadline:
                raise CompileError(f"OAuth token exchange failed ({resp.status_code}, {reason}); "
                                   "check the subject token type, requested scope and App audience")
            log.warning("OAuth token exchange failed (%d, %s) on try %d; retrying in %.0f s",
                        resp.status_code, reason, tries, wait)
            time.sleep(wait)
            tries, wait = tries + 1, min(wait * 2, 30.0)

    def _post(self):
        import requests  # lazy: keep the package importable without requests for non-remote use
        data = {
            "grant_type": _GRANT_TOKEN_EXCHANGE,
            "subject_token": self._subject_token,
            "subject_token_type": self._subject_token_type,
            "requested_token_type": self._requested_token_type,
            "scope": self._scope,
            "audience": self._audience,
        }
        # Never redirect a request carrying the subject token.
        return requests.post(self._token_url, data=data, timeout=30, allow_redirects=False)

    @staticmethod
    def _reason(resp) -> str:
        """SVC-DISCOVERY-PLUGIN — the standard OAuth error code only, never credentials or a server body
        that might echo them."""
        try:
            error = resp.json().get("error")
        except (ValueError, AttributeError):
            error = None
        known_errors = {"invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
                        "unsupported_grant_type", "invalid_scope", "invalid_target", "access_denied"}
        return error if isinstance(error, str) and error in known_errors else "unrecognized OAuth error"

    def auth_headers(self) -> Dict[str, str]:
        if self._cached is None:
            self._cached = self._exchange()
        return {"Authorization": f"Bearer {self._cached}"}


# --------------------------------------------------------------------------------------------------
# Compile providers (transport).
# --------------------------------------------------------------------------------------------------

class CompileProvider(abc.ABC):
    """Compiles a request into a Spark SQL SCRIPT (EXEC-SQL-BACKEND) + metadata, and (optionally)
    DESCRIBES a library's neutral graph metadata."""

    @abc.abstractmethod
    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        """Compile to a SQL SCRIPT (EMIT-SQL-EXECUTE) the client runs via ``spark.sql``."""
        ...

    def describe(self, request: CompileRequest) -> LibraryGraph:
        """Return the neutral [[LibraryGraph]] for the library. Optional capability — a provider that only
        compiles may leave this unimplemented."""
        raise NotImplementedError(f"{type(self).__name__} does not support describe()")

    def compile_pipeline(self, request: CompileRequest) -> PipelinePlan:
        """Return the compile-once :class:`PipelinePlan` (`PIPE-SQL-DAG-REUSE`) — one dataset recipe per
        materialized define, cut from a single whole-library lowering. Optional capability."""
        raise NotImplementedError(f"{type(self).__name__} does not support compile_pipeline()")

    def translate(self, cql: str, includes: Sequence[str] = ()) -> str:
        """CQL → ELM JSON (`EMIT-INTERCHANGE`), with ``includes`` (CQL text) as translation sources so the
        root's ``include`` statements resolve. Optional capability."""
        raise NotImplementedError(f"{type(self).__name__} does not support translate()")

    def resource_types(self) -> List[str]:
        """The FHIR resource types the compiler models (`SVC-RESOURCE-SCHEMA`), sorted. Optional
        capability."""
        raise NotImplementedError(f"{type(self).__name__} does not support resource_types()")

    def resource_schemas(self, types: Sequence[str]) -> Dict[str, "ResourceSchema"]:
        """Each of ``types``' ``__resource`` struct, from the compiler's model (`CLIENT-RESOURCE-SCHEMA`). An
        unmodeled type raises, naming it. Optional capability."""
        raise NotImplementedError(f"{type(self).__name__} does not support resource_schemas()")


class _JsonProvider(CompileProvider):
    """SVC-COMPILER-UC — one protocol decoder for HTTP and UC transports; no compiler logic."""

    def _post(self, path: str, request: "CompileRequest | dict") -> dict:
        raise NotImplementedError

    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        body = self._post("/compile-sql", request)
        return SqlCompileResult(
            statements=[SqlStatement(name=s["name"], kind=s["kind"], sql=s["sql"]) for s in body.get("statements", [])],
            target=body["target"],
            defines=list(body.get("defines", [])),
            shapes=[DefineShape(define=s["define"], obj=s["object"], kind=s["kind"],
                                value_column=s.get("valueColumn", VALUE_COLUMN))
                    for s in body.get("shapes", [])])

    def compile_pipeline(self, request: CompileRequest) -> PipelinePlan:
        body = self._post("/compile-pipeline", request)
        if ((request.measurement_period is not None or request.data_source is not None)
                and body.get("batchProtocol") != "cql-batch-v1"):
            raise CompileError("Compiler does not support cql-batch-v1; deploy the bundled compiler before this client")
        return PipelinePlan(datasets=[
            DatasetRecipe(
                name=d["name"],
                target=d["target"],
                reads=list(d.get("reads", [])),
                statements=[SqlStatement(name=s["name"], kind=s["kind"], sql=s["sql"])
                            for s in d.get("statements", [])],
                query=d["query"],
                library=d.get("library", ""),
                library_version=d.get("libraryVersion", ""),
                define=d.get("define", ""),
                sql_file=d.get("sqlFile", ""),
                kind=d.get("kind", SCALAR),
                value_column=d.get("valueColumn", VALUE_COLUMN),
                retrieve_read=_read_coordinates(d.get("retrieveRead")),
                durable=_durable_identity(d.get("durable")))
            for d in body.get("datasets", [])],
            compiler_version=body.get("compilerVersion", ""))

    def compile_pipeline_json(self, body: dict) -> dict:
        """``POST /compile-pipeline`` with a wire body as built, answered as sent: for a caller that parses plans
        with its own types."""
        return self._post("/compile-pipeline", body)

    def translate(self, cql: str, includes: Sequence[str] = ()) -> str:
        body = self._post("/translate", {"cql": cql, "includes": list(includes)})
        if not body.get("ok") or not body.get("elm"):
            diagnostics = body.get("diagnostics") or ["no ELM returned"]
            raise CompileError(f"translate failed: {diagnostics[0]}")
        elm = body["elm"]
        return elm if isinstance(elm, str) else json.dumps(elm)

    def translate_result(self, cql: str, includes: Sequence[str] = ()) -> dict:
        """``/translate`` as answered, ``{ok, elm, diagnostics}``, for a caller that reports diagnostics rather than
        raising on them (`BUILD-RESOLVE-COMPILE`'s ``Translate``). A transport failure still raises."""
        body = self._post("/translate", {"cql": cql, "includes": list(includes)})
        return {"ok": bool(body.get("ok")), "elm": body.get("elm"), "diagnostics": list(body.get("diagnostics") or [])}

    def resource_types(self) -> List[str]:
        body = self._post("/resource-schema", {})
        types = body.get("resourceTypes")
        if not types:
            raise CompileError(f"{self._base}/resource-schema reported no modeled resource types")
        return sorted(types)

    def resource_schemas(self, types: Sequence[str]) -> Dict[str, "ResourceSchema"]:
        wanted = sorted(set(types))
        body = self._post("/resource-schema", {"types": wanted})
        schemas = body.get("schemas") or {}
        unmodeled = [t for t in wanted if not (schemas.get(t) or {}).get("modeled")]
        if unmodeled:
            raise CompileError(f"{self._base}/resource-schema does not model {', '.join(unmodeled)}")
        missing = [t for t in wanted if "schema" not in schemas[t]]
        if missing:
            raise CompileError(f"{self._base}/resource-schema returned no schema for {', '.join(missing)}; deploy "
                               f"a compiler service from the same release as this client")
        return {t: ResourceSchema(fhir_type=t, struct=_struct_type(schemas[t]["schema"]), ddl=schemas[t]["ddl"],
                                  patient_context=bool(schemas[t].get("patientContext")))
                for t in wanted}

    def describe(self, request: CompileRequest) -> LibraryGraph:
        body = self._post("/describe", request)
        return LibraryGraph(
            defines=list(body.get("defines", [])),
            dependencies={k: list(v) for k, v in body.get("dependencies", {}).items()},
            boundaries=list(body.get("boundaries", [])),
            materialized_defines=list(body.get("materializedDefines", [])),
            compiler_version=body.get("compilerVersion", ""),
            terminology_version=body.get("terminologyVersion"),
            encoding=body.get("encoding", "struct"),
            retrieve_demand={k: list(v) for k, v in body.get("retrieveDemand", {}).items()},
            define_kinds={k: str(v) for k, v in body.get("defineKinds", {}).items()},
            value_column=body.get("valueColumn", VALUE_COLUMN),
            retrieve_types=(sorted(body["retrieveTypes"]) if body.get("retrieveTypes") is not None else None))


class RemoteProvider(_JsonProvider):
    """SVC-PROTOCOL — HTTP transport to the local OSS host or deployed Compiler App."""

    def __init__(self, endpoint: str, credentials: Optional[CredentialProvider] = None, timeout: float = 60.0):
        if not endpoint:
            raise ValueError("RemoteProvider requires an endpoint")
        self._base = endpoint.rstrip("/")
        self._credentials = credentials or NoAuth()
        self._timeout = timeout

    def _post(self, path: str, request: "CompileRequest | dict") -> dict:
        import requests  # lazy: keep the package importable without requests for embedded/plugin use
        url = self._base + path
        headers = {"Content-Type": "application/json", **self._credentials.auth_headers()}
        body = request.to_json() if isinstance(request, CompileRequest) else request
        # SVC-DISCOVERY-PLUGIN — an App login redirect is an authentication error, not JSON.
        # Do not follow it or forward credentials/request content to a different endpoint.
        resp = requests.post(url, json=body, headers=headers, timeout=self._timeout, allow_redirects=False)
        if 300 <= resp.status_code < 400:
            raise CompileError(f"compiler {path} returned an authentication/endpoint redirect ({resp.status_code}); "
                               "check the endpoint and its OAuth credentials")
        if resp.status_code != 200:
            raise CompileError(f"{url} returned {resp.status_code}: {resp.text}")
        try:
            return resp.json()
        except ValueError as error:
            raise CompileError(f"compiler {path} returned a non-JSON response; check the endpoint and its OAuth credentials") from error


@dataclass(frozen=True, kw_only=True)
class ResourceSchema:
    """One FHIR type's ``__resource`` struct in the compiler's model (`CLIENT-RESOURCE-SCHEMA`): the struct a
    ``retrieve_<Type>`` returns (`BIND-CONTRACT`), as a PySpark ``StructType`` and as DDL text.
    ``patient_context`` is whether the type is in the Patient compartment, which decides its ``__subject_id``
    (`DATA-SUBJECT-KEY`)."""
    fhir_type: str
    struct: object
    ddl: str
    patient_context: bool


def _struct_type(schema_json: Mapping):
    """A PySpark ``StructType`` from Spark's JSON schema. Needs PySpark, and no session."""
    from pyspark.sql.types import StructType  # lazy: the client imports without PySpark

    return StructType.fromJson(dict(schema_json))


class CompileError(RuntimeError):
    """A loud compile failure (CORE-NO-SILENT-MISLOWER) — never a silent empty plan."""


# --------------------------------------------------------------------------------------------------
# Resolution: config + entry-point plugins.
# --------------------------------------------------------------------------------------------------

# Provider kinds beyond the built-in ``remote``, resolved IN THIS PACKAGE (decision 10). This was an
# entry-point group while the core and the Databricks layer were separate distributions; the indirection
# existed to cross that boundary, and in one distribution it only hides a call from its reader. Imports
# stay LAZY so the platform modules — and the Databricks SDK they import — are never pulled in by the
# neutral ``remote`` path.
def _databricks_app_provider(conf):
    """The ``databricks-app`` kind: resolve the Compiler Service App URL from the workspace App registry
    and authenticate by serverless-to-App OAuth token exchange (`SVC-DISCOVERY-PLUGIN`)."""
    from .provider import make_provider  # lazy: imports the Databricks SDK
    return make_provider(conf)


#: Provider kind -> factory ``f(conf) -> CompileProvider``.
_PROVIDER_KINDS = {"databricks-app": _databricks_app_provider}


def resolve_credentials(conf: Mapping[str, str]) -> CredentialProvider:
    """Build the credential provider from config/env (auth kind ``none`` | ``bearer`` |
    ``oauth2-token-exchange``). A platform plugin supplies its own credential provider via the compile
    provider it registers, so this covers only the built-in standard mechanisms."""
    kind = (config.resolve(conf, config.CONF_AUTH_KIND, config.ENV_AUTH_KIND, "none") or "none").lower()
    if kind == "none":
        return NoAuth()
    if kind == "bearer":
        return StaticBearer(config.resolve(conf, config.CONF_AUTH_TOKEN, config.ENV_AUTH_TOKEN) or "")
    if kind == "oauth2-token-exchange":
        return OAuth2TokenExchange(
            token_url=conf.get(config.CONF_AUTH_TOKEN_URL, ""),
            subject_token=conf.get(config.CONF_AUTH_SUBJECT_TOKEN, ""),
            audience=conf.get(config.CONF_AUTH_AUDIENCE, ""),
            scope=conf.get(config.CONF_AUTH_SCOPE, "all-apis"),
            subject_token_type=conf.get(config.CONF_AUTH_SUBJECT_TOKEN_TYPE, _TYPE_JWT))
    raise ValueError(f"unknown auth kind '{kind}' (built-ins: none, bearer, oauth2-token-exchange; "
                     "platform auth is provided by a registered provider plugin)")


def resolve_provider(conf: Mapping[str, str], endpoint: Optional[str] = None) -> CompileProvider:
    """Pick the compile provider by precedence: config/env ``provider.kind`` (default ``remote``).
    ``remote`` builds a ``RemoteProvider`` from the endpoint + resolved credentials; any other kind is
    resolved from the in-package kind registry (``databricks-app``), whose modules import lazily so the
    neutral ``remote`` path never loads a platform SDK.

    ``endpoint`` is the explicit-arg tier of `BUILD-RESOLVE` (top precedence); the compiler endpoint has
    NO auto-discovery on OSS (no app registry), so it resolves explicit > conf > env > FAIL."""
    kind = (config.resolve(conf, config.CONF_PROVIDER_KIND, config.ENV_PROVIDER, "remote") or "remote").lower()
    if kind == "remote":
        ep = config.resolve_setting(
            "compiler endpoint", explicit=endpoint, conf=conf, conf_key=config.CONF_PROVIDER_ENDPOINT,
            env=config.ENV_ENDPOINT, required=True)
        return RemoteProvider(ep, resolve_credentials(conf))
    factory = _load_plugin(kind)
    if factory is None:
        raise ValueError(f"unknown provider kind '{kind}' — expected 'remote' or one of "
                         f"{sorted(_PROVIDER_KINDS)}")
    return factory(conf)


def _load_plugin(name: str):
    """Return the factory ``f(conf) -> CompileProvider`` for provider kind ``name``, or ``None``."""
    return _PROVIDER_KINDS.get(name)
