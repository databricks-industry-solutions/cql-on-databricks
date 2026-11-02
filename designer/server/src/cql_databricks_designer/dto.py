"""Request models that more than one PACKAGE needs — and a note on why each is here.

It began as `routes/dto.py`, holding the one model several routers shared. SPLIT-PACKAGES turned
"several routers" into "several packages": `TranslateRequest` is below. A model two packages construct
cannot live in either without making one depend on the other for a dataclass. (`KnowledgeBindingDto` was
here too; it went with the Data Source record, `DATA-SOURCE-UC-TAG`.)

`TranslateRequest` is shared by three endpoints that belong to three DIFFERENT systems:

| endpoint | system | fields it actually reads |
|---|---|---|
| `POST /api/translate` | the compiler proxy | `cql`, `includes`, `include_elm` |
| `POST /api/libraries/resolve` | knowledge (`KNOWLEDGE-RESOLVE-API`) | `cql` |
| `POST /api/sample-run` | Designer (`APP-NO-UI-MOCK`) | `cql` |

**It is not a shared CONTRACT; it is one class reused for convenience.** Two of the three consumers read
a single field, so there is no coupling here to resolve — only a class to stop sharing, by letting each
router declare the body it actually reads.

That is deliberately NOT done in the routerizing change, because narrowing a model changes the generated
OpenAPI schema (three declared fields become one) while changing no request handling at all — Pydantic
defaults to `extra="ignore"`, so a client posting all three fields is unaffected either way. A schema
change buried inside a 1,800-line mechanical move is invisible; it belongs in a commit where the schema
diff IS the commit. `CALC-API` owns the compiler/calculation base-path split and is the natural home,
but this is small enough not to wait for it.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TranslateRequest(BaseModel):
    cql: str = ""
    includes: list[str] | None = None
    #: RUN-ADDRESS-LOCATION — where the includes resolve first; None is the environment's knowledge.
    location: str | None = None
    #: DOC-ENVIRONMENT-PICKER — the environment whose knowledge resolves the includes; None is the default.
    environment: str | None = None
    # APP-SPEC-MODEL: also return the ELM (and source) of every library in the include closure.
    # The Define lens derives its whole model from ELM — membership, signatures, bodies via statement
    # locators, dependency edges — so it needs the includes compiled, not just resolved. Off by default:
    # callers that only want diagnostics (the authoring editor) should not pay for N extra translates.
    include_elm: bool = False


# APP-SAMPLE-METRICS, SPEC-READ-REF, APP-COHORT-ALGEBRA — the insight request bodies of the Designer's
# `/api/libraries/…` routes (`routes/runs.py`).

class DefineRefDto(BaseModel):
    """A define, library-qualified."""

    name: str
    library: str

class MetricCohortDto(BaseModel):
    """SPEC-POPULATION-CASCADE — the subjects a define is counted among: in every `allOf` define and in no
    `noneOf` define. Both empty is every evaluated subject. The client derives it from the declared
    scoring; the server only counts."""

    model_config = {"populate_by_name": True}
    all_of: list[DefineRefDto] = Field(alias="allOf")
    none_of: list[DefineRefDto] = Field(alias="noneOf")

class MetricDefineDto(BaseModel):
    """One define the client wants a figure for, as IT derived the define from the ELM."""

    name: str
    library: str
    #: The ELM result type (`Boolean`, `List<Encounter>`). Decides the element grain, nothing else.
    resultType: str | None = None
    #: SPEC-POPULATION-CASCADE — required: a define with no stated cohort has no counted figure.
    cohort: MetricCohortDto

class ReadDto(BaseModel):
    """SPEC-READ-REF — one READ the client wants a figure for, as IT derived the read from the ELM.

    The client owns what a measure reads (`specRetrieves.ts`, `APP-SPEC-MODEL`); this carries just
    enough of that determination to identify the materialized scan answering it. `key` is the client's
    own dedupe key and comes straight back on the response, so neither side has to know the other's
    identifiers.
    """

    key: str
    #: Unqualified FHIR resource type — `Condition`, `Encounter`.
    type: str
    #: The QICore / US Core profiles this read resolves against, last segment only. The EXACT
    #: discriminator where one resource type is read through more than one profile.
    profiles: list[str] = []

class ReadMetricsRequest(BaseModel):
    """SPEC-READ-REF — the read-metrics request body. Module scope, for the reason `MeasureMetricsRequest`
    records: postponed annotations make a locally-defined body model silently degrade to a query param."""

    model_config = {"populate_by_name": True}
    reads: list[ReadDto] = []
    #: DOC-ENVIRONMENT-PICKER — the environment the figures are read in; None is the default.
    environment: str | None = None
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    #: RUN-ADDRESS-BY-LIBRARY — the key a draft's run returned, to read that draft; ``""`` reads the version.
    draft: str = ""
    result_set: dict | None = Field(default=None, alias="resultSet")

class FlowCriterionDto(BaseModel):
    """One sibling criterion of a gate, library-qualified."""

    name: str
    library: str

class FlowGateDto(BaseModel):
    """APP-COHORT-ALGEBRA — one gate the client wants decomposed.

    THE BODY IS THE QUESTION. `specDocument.ts` already derives each population's Concepts
    (`SPEC-CONCEPTS` / `SPEC-PLACEMENT`) and the client re-reads that determination into these gates;
    the server has no ELM graph and building one would be a second opinion able to disagree with the
    first (`APP-SPEC-MODEL`). Same argument as the metrics request above.
    """

    model_config = {"populate_by_name": True}
    #: The population being entered.
    stage: str
    stage_library: str = Field(alias="stageLibrary")
    #: The cohort entering it. Absent means the stage's own relation bounds it.
    base: str | None = None
    base_library: str | None = Field(default=None, alias="baseLibrary")
    #: `all-of` (an entry conjunction) or `any-of` (the exclusion disjunction). Decides only whether a
    #: subject fails a criterion by missing it or by meeting it.
    polarity: str = "all-of"
    criteria: list[FlowCriterionDto] = []

class CohortFlowRequest(BaseModel):
    """APP-COHORT-ALGEBRA — the flow request body. Module scope for the reason above."""

    model_config = {"populate_by_name": True}
    gates: list[FlowGateDto] = []
    #: DOC-ENVIRONMENT-PICKER — the environment the figures are read in; None is the default.
    environment: str | None = None
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    #: RUN-ADDRESS-BY-LIBRARY — the key a draft's run returned, to read that draft; ``""`` reads the version.
    draft: str = ""
    result_set: dict | None = Field(default=None, alias="resultSet")

class MeasureMetricsRequest(BaseModel):
    """APP-SAMPLE-METRICS — the metric request body.

    MODULE SCOPE, not nested in `create_app`. This file uses postponed annotations, so FastAPI resolves
    a handler's types through the MODULE namespace: a locally-defined body model is unresolvable there
    and silently degrades to a required QUERY parameter, which fails every request with
    `missing: query.req` rather than with anything naming the real problem.

    **THE EVALUATION SCOPE IS PART OF THE QUESTION.** A figure is a fact about one evaluation — one
    environment's data over one measurement period — so asking for "the measure's numbers" without naming it is
    asking something with several different true answers. The sample builder could
    ignore the scope because it invented the figures; a warehouse-backed one cannot even find the tables.
    """

    model_config = {"populate_by_name": True}
    defines: list[MetricDefineDto] = []
    #: DOC-ENVIRONMENT-PICKER — the environment the figures are read in; None is the default.
    environment: str | None = None
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    #: RUN-ADDRESS-BY-LIBRARY — the key a draft's run returned, to read that draft; ``""`` reads the version.
    draft: str = ""
    result_set: dict | None = Field(default=None, alias="resultSet")
    #: RUN-CLIENT-MEASURE-SCORE — the measure's DECLARED scoring (`Measure.scoring`), which the score is
    #: computed under. Absent means none is declared, and no score is returned.
    scoring: str | None = None


class RunRequest(BaseModel):
    """SVC-RUN-STAGE — a run: the CQL, the environment it runs in and its overrides (``periodStart``, ``periodEnd``).
    The Designer's `/api/run` takes this body."""
    model_config = {"populate_by_name": True}
    cql: str = ""
    #: RUN-ADDRESS-LOCATION — where the library's includes resolve first (``store:<catalog>.knowledge`` or
    #: ``folder:<path>``); None is the environment's knowledge.
    location: str | None = None
    defines: list[str] | None = None
    overrides: dict[str, str] | None = None
    #: DOC-ENVIRONMENT-PICKER — the environment the run is in; None is the default.
    environment: str | None = None
    #: RUN-RECOMPUTE-OVERRIDE — run every define of the closure even where its rows are current.
    recompute: bool = False
    #: RUN-CLIENT-REFRESH-RESUME — explicit cutoff and stable logical session for retries.
    computed_since: datetime | None = None
    session_id: str | None = None
