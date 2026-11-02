import type { ReferenceSet } from "./measures/specRetrieves.ts";

// SVC-PROTOCOL — the CQL on Databricks app backend's /api wire types the SPA consumes.
//
// The backend (Python/FastAPI, SVC-APP-TOPOLOGY) owns the contract; the SPA consumes these hand-mirrored
// types. Kept small and frontend-only.

/** One library from the root's include closure, compiled (APP-SPEC-MODEL). Present only when the
 *  request set `includeElm` — the Define lens needs its includes compiled, not merely resolved. */
export interface TranslatedInclude {
  name: string;
  version: string;
  /** The library's CQL, so statement locators in its ELM can be resolved to source text. */
  source: string;
  elm: string | null;
  ok: boolean;
  diagnostics: string[];
}

/** `POST /api/translate` result: ELM JSON (null on error), diagnostics, and an ok flag.
 *  Fail-loud (CORE-NO-SILENT-MISLOWER): `ok:false` + non-empty diagnostics, never a silent pass. */
export interface TranslateResult {
  elm: string | null;
  diagnostics: string[];
  ok: boolean;
  /** The compiled include closure, when `includeElm` was requested. */
  includes?: TranslatedInclude[];
  /** SPEC-EXPLAIN-INPUTS — one resolved reference set per library in the closure, root first, derived
   *  from the ELM in this very response.
   *
   *  IT RIDES WITH THE ELM RATHER THAN HAVING A ROUTE, and that is the point: a `GET .../reference-set`
   *  would have to compile the closure a SECOND time, and the client would then hold a model built from
   *  one compilation and reads resolved from another — the two-determinations failure this exists to
   *  end (`APP-SPEC-MODEL`). Present only with `includeElm`. */
  referenceSets?: ReferenceSet[];
}

/** RUN-CLIENT-REQUEST-REOPEN — ordinary request context, not a compiled plan or credential.
 *  Hand-mirrors selection_reading.selection_address; send draft CQL only in POST bodies. */
export interface ResultSetAddress {
  destination: [string, string];
  publisher: string;
  session_id: string;
  selection_id: string;
  request_hash: string;
  purpose: "private" | "published";
  population: string;
  targets: string[];
  computed_since: string | null;
  calculation?: {
    /** CLIENT-ENVIRONMENT — the environment the calculation ran in. */
    environment: string;
    runtime_catalog: string;
    runtime_schema: string;
    library?: string;
    version?: string;
    cql?: string;
    period: [string, string];
    targets: string[];
  };
}

/** `POST /api/run` result (SVC-RUN-EVAL): per-member define values + population counts. */
export interface RunResult {
  resultSet?: ResultSetAddress;
  ok: boolean;
  context?: string;
  defines?: string[];
  results?: Record<string, Record<string, unknown>>;
  counts?: Record<string, number>;
  error?: string;
  diagnostics?: string[];
  /** RUN-CLIENT-RESULT-SETS — presentation/cache label; resultSet carries the request for reopening. */
  draft?: string;
  /** APP-UPDATE-MODE — when the shown results were computed: the latest successful computation among
   *  the selection's outputs (ISO 8601). */
  computedAt?: string;
}

/** RUN-CLIENT-REQUEST-REOPEN — one output's reuse decision, as the run and reopen routes report it. */
export interface RunOutcome {
  definition: string;
  decision: "computed" | "reused";
  reason: string;
  completedAt: string;
}

/** APP-COMPUTE-TARGET — `GET /api/warehouses`: the SQL warehouses the signed-in user can see. */
export interface WarehouseRecord {
  id: string;
  name: string | null;
  state: string | null;
}
export interface WarehouseList {
  /** The deployment's `WAREHOUSE_ID`, used when no warehouse is chosen. */
  default: string;
  warehouses: WarehouseRecord[];
}

/** DOC-ENVIRONMENT-PICKER — an environment the picker offers (`GET /api/environments`). Hand-mirrors
 *  `routes/environments.py`'s record. An environment is a Unity Catalog catalog tagged
 *  `cql_databricks_environment = <name>` (`CLIENT-ENVIRONMENT`); read-only here, declared by tagging the catalog.
 *  `name` is the tag's value and the environment's identifier on every request. */
export interface EnvironmentRecord {
  name: string;
  catalog: string;
  /** The layout version the catalog says it follows. */
  version: string;
  /** True on the one environment a run with nothing picked uses. */
  default: boolean;
  /** One sentence about the environment: the catalog's comment. Absent when it has none. */
  description?: string;
}

/** RUN-PROGRESS-TASKS — one definition of the run's closure, as `/api/run-stream` reports it.
 *
 *  The client's `materialize` reports the closure once before it runs (`on_plan`) and each definition as
 *  it is reused or computed (`on_step`), so this list IS the unit of work the run processes — which is
 *  what makes counting it honest progress rather than a guess. `taskKey` is the definition's key. */
export interface RunTaskStatus {
  taskKey: string;
  /** Set by the route's `TaskProgress`; it never sends `skipped`. */
  status: "pending" | "running" | "completed" | "failed" | "skipped";
}

/** The `results` payload the `done` frame carries — the same rows `GET /api/results` serves, minus the
 *  per-define `status` the run does not know at that point. `PreComputedResults` is the fuller shape. */
export interface RunStageResults {
  schema: string;
  defines: Omit<DefineResultEntry, "status">[];
}

/** SVC-RUN-STAGE — a progress event from the streaming `/api/run-stream` SSE endpoint.
 *  The frontend shows the current stage instead of a generic "computing…" indicator.
 *
 *  **THE STAGES ARE THE ONES THE RUN ROUTE ACTUALLY SENDS**, and that is the whole point of this comment. The run
 *  route sends `compiling`, `profile`, `executing` and a `done` frame carrying `results` + `status`. Typing any other
 *  vocabulary here would let both bugs stand: a final result read from a key nothing sends (so every successful run
 *  reports "stream ended unexpectedly"), and progress bands spent on phases that never arrive (`RUN-PROGRESS-TASKS`).
 *  A stage this union does not name is a backend that has moved on, and it must fail to compile rather than silently
 *  score 0. */
export interface RunStage {
  resultSet?: ResultSetAddress;
  stage: "connecting" | "compiling" | "profile" | "executing" | "done" | "error";
  message?: string;
  /** Overall Job state (stage=executing, stage=done): `running` | `completed` | `failed` | `timeout`. */
  status?: string;
  /** RUN-PROGRESS-TASKS — every definition of the run and its state (stage=executing). */
  tasks?: RunTaskStatus[];
  /** The result rows written by this run (stage=done, when the run completed). */
  results?: RunStageResults;
  /** Per-step timings for the whole orchestration journey (stage=profile). */
  steps?: { name: string; elapsed_ms: number }[];
  /** Total orchestration wall-clock in ms (stage=profile). */
  total_ms?: number;
  /** Error message (stage=error). */
  error?: string;
  /** The run's identity (stage=done). `draft` labels its complete selection (RUN-CLIENT-RESULT-SETS). */
  identity?: { library: string; version: string; environment: string; draft: string };
  /** RUN-CLIENT-REQUEST-REOPEN — per-output reuse decisions (stage=done). */
  outcomes?: RunOutcome[];
}

/** DESIGNER-SDP-NAV — the merged LibraryGraph from the engine's `/describe` endpoint.
 *  Hand-mirrors the engine's `LibraryGraph` (cql-databricks providers.py). The frontend merges this
 *  with the local CQL source scan (node classification + leaf nodes) to build the hybrid graph.
 *  Nothing fetches one for a Data Source: there is no `data_sources` table to read a library manifest from. */
export interface DescribeResult {
  defines: string[];
  dependencies: Record<string, string[]>;
  boundaries: string[];
  materializedDefines: string[];
  compilerVersion: string | null;
  /** Per-FHIR-type demanded fields from the compilation (EMIT-DESCRIBE-ENCODING). */
  retrieveDemand?: Record<string, string[]>;
  /** Warnings from the BFF (e.g. libraries that failed to translate). */
  warnings?: string[];
}

/** DESIGNER-DEFINE-STATUS — how a define's result stands, where a reader is shown it.
 *
 *  **Nothing feeds this live, and that is a consequence rather than an oversight.** It comes
 *  from the SDP pipeline's event log, which says which of a pipeline's defines are computing; a
 *  barrier task has no event log and IS one define, so the per-define feed and the per-task feed
 *  collapse into one — `RunTaskStatus` above (`RUN-PROGRESS-TASKS`). Reconnecting the nav's dots
 *  means mapping a task key back to a define, which nothing does yet. */
export type DefineStatusValue = "unknown" | "stale" | "computing" | "fresh" | "error";

/** PIPE-PRE-COMPUTED — one define's counts from `/api/results`, at the two grains `SPEC-METRIC` names.
 *
 *  `subjects` is how many distinct subjects MET the define — not how many rows its relation holds, which
 *  is what a row count reports (57,565,315,157 for one measured exclusion whose true subject
 *  count is 146). `elements` is the per-element grain and is ABSENT wherever there is none to report. */
export interface DefineResultEntry {
  name: string;
  define: string;
  subjects: number;
  elements?: number | null;
  status: string;
}

/** PIPE-PRE-COMPUTED — pre-computed results for a (library, version, environment).
 *  Returned by `GET /api/results`.
 *
 *  NO FUNNEL ON THE WIRE (`SPEC-ATTRITION`). It is derived from these counts by `funnelFromCounts()`,
 *  the one funnel builder. A second, `COUNT(*)`-based funnel on the wire would put two answers to
 *  "what did this measure score" on one page. */
export interface PreComputedResults {
  schema: string;
  libraryName: string;
  version: string;
  versionKind: "published" | "draft" | "private";
  /** RUN-CLIENT-RESULT-SETS — opaque handle pinning this complete selection. */
  draft?: string;
  defines: DefineResultEntry[];
}


/** Library identity returned from the orchestrator run path. */
export interface LibraryIdentity {
  library: string;
  version: string;
  environment: string;
}

// ── APP-DATA-PROFILE — one define's data profile (`insights/model.py`) ─────────────────────────
//
// Hand-mirrors the Python dataclasses. `Returns` is deliberately absent: result types come from the
// translator's own ELM and are parsed client-side (`elmDefines.ts`), so a second server-side copy
// could disagree with the first (APP-SPEC-MODEL — one model per determination).

/** Where a profile's figures came from. `sample` is scaffolding and is rendered behind a visible mark;
 *  `run` is a real evaluation; `mixed` is a payload whose blocks differ — see `DefineProfile.sampled`.
 *  On the wire because a distribution is indistinguishable from a plausible invention once it is drawn
 *  as a bar (APP-NO-UI-MOCK). */
export type ProfileProvenance = "sample" | "run" | "mixed";

/** The profile's block names. `DefineProfile.sampled` names the fabricated ones on a given payload. */
export type ProfileBlock = "sample" | "values" | "valueSets" | "temporal" | "strata";

/** One bucket of a distribution. `share` is of its own stratum's total, never of the measure. */
export interface ProfileBin {
  label: string;
  count: number;
  share: number;
}

/** APP-PROFILE-STRATA — one stratification of a define's output.
 *
 *  `grain` is load-bearing: a cut is either over the SUBJECTS the define is true of (age, sex, payer)
 *  or over the ELEMENTS it returned (encounters by month, by class). One patient with nine encounters
 *  contributes nine to an element cut and one to a subject cut, so a chart that does not say which it
 *  is cannot be read. `unit` names what one count IS and is rendered, never inferred. */
export interface ProfileStratum {
  id: string;
  label: string;
  grain: "subject" | "element";
  dimension: string;
  unit: string;
  bins: ProfileBin[];
  note?: string;
  /** APP-NO-UI-MOCK — this CUT is fabricated. Per stratum because the strata block is the one that
   *  is internally mixed: sex and age band are read from the Patient binding, the element-grain cuts
   *  have no relation to read. */
  sampled?: boolean;
}

/** A top-N returned value — the sample shown beside the result type. */
export interface ProfileSample {
  value: string;
  count: number;
  share: number;
}

export interface ProfileCodeHit {
  code: string;
  display: string;
  system: string;
  count: number;
}

/** APP-LIVING-CHARTS — code-hit frequency for one value set the define reads. `declared` is absent
 *  until the terminology read path exists: "0 codes declared" and "we have not looked" are opposite
 *  claims, so the field is omitted rather than zeroed. */
export interface ProfileValueSet {
  name: string;
  oid?: string;
  declared?: number;
  fired: number;
  top: ProfileCodeHit[];
}

/** `inPeriod` separates the measurement period from its look-back — a define whose events all land
 *  outside the period is the anchoring error the chart exists to expose. */
/** SPEC-TERMINOLOGY-EXTRACT — one coded value found at one curated path on the returned records. */
export interface TerminologyCode {
  system: string | null;
  code: string | null;
  display: string | null;
  subjects: number;
  records: number;
}

/** One curated coded path, with its codes ranked by how many subjects carry them.
 *
 *  `codes` MAY BE EMPTY, and that is a claim rather than a gap: the server curates which paths are worth
 *  reading per (data model, resource type), so a path it named and found nothing at is reporting "no body
 *  site recorded on any of these" — which the pane states rather than hides (`SPEC-METRIC`). */
export interface TerminologySection {
  path: string;
  label: string;
  resourceType: string;
  codes: TerminologyCode[];
}

export interface ProfileTemporalBin {
  label: string;
  count: number;
  inPeriod: boolean;
}

/** SPEC-METRIC — what came back, at both grains. `elements` is absent for a define with no element
 *  grain: a Boolean says something about the subject and nothing else. */
export interface ProfileValues {
  subjects: number;
  elements?: number;
  nullRate: number;
  cardinality?: ProfileBin[];
}

export interface DefineProfile {
  library: string;
  define: string;
  provenance: ProfileProvenance;
  generated: string;
  /** Why the payload is what it is — rendered next to the provenance mark. */
  caveat?: string;
  /** APP-NO-UI-MOCK — the blocks on THIS payload that are fabricated. Everything not named here is
   *  read from the evaluation. Per-block because a payload with a real value profile and an invented
   *  temporal spread cannot be described by one word: `run` would strip the mark off the invented bars,
   *  `sample` would put a false warning on real figures. */
  sampled?: ProfileBlock[];
  sample?: ProfileSample[];
  values?: ProfileValues;
  valueSets?: ProfileValueSet[];
  /** SPEC-TERMINOLOGY-EXTRACT — DERIVED, never sampled: a `GROUP BY` over the define's own result
   *  relation. Absent for a define that returns no resources (a boolean define has no record to read). */
  terminology?: TerminologySection[];
  temporal?: ProfileTemporalBin[];
  strata?: ProfileStratum[];
}
