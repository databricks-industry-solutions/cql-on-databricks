// APP-MEASURE-DOC / APP-LIVING-MEASURE / APP-STEP-INTO-PATIENT / ACC-KA-TRACE / SVC-RUN-EVAL
// — the Designer workspace's domain helpers: the top-level module map, the per-tab view type,
// and the pure functions that turn a Live /api/run result (SVC-RUN-EVAL) into the population funnel
// and the Patient Lens, plus the Knowledge-Architecture up-trace (ACC-KA-TRACE) from a deciding
// criterion to its Pathway recommendation. Kept framework-free and unit-testable; the React
// components consume these.

import type { DefineResultEntry, RunResult } from "../wire.ts";
import type { Definition } from "../catalog.ts";
import { NUMERATOR, parseScoring, readoutOf, removesFrom, type Readout } from "./specScoring.ts";


/** DESIGNER-ONE-MODULE — the Designer has ONE module (`DEC-DESIGNER-ONE-MODULE`). Bulk FHIR Sharing,
 *  Mapping, Scheduling and Data Readiness were removed, and the module switcher with them. Settings is
 *  removed (`APP-SETTINGS`, SUPERSEDED). Id stays `measures`. */
export type TopModuleId = "measures";

export interface MwModule {
  id: TopModuleId;
  label: string;
  glyph: string;
  blurb: string;
  /** `active` = the built module; `soon` = placeholder. */
  state: "active" | "soon";
}

export const MW_MODULES: MwModule[] = [
  // The authoring product — a living clinical spec you read static and compute Live (THEME-LIVING-DOC).
  { id: "measures", label: "Designer", glyph: "◈", blurb: "Living clinical specifications — measures, libraries & predictive factors", state: "active" },
];

/** Build the URL path for a top-level module (APP-URL-ROUTING). Shared so that both navigation
 *  handlers and `<a href>` tags produce consistent URLs. */
export function pathForModule(id: TopModuleId): string {
  if (id === "measures") return "/measures";
  return `/${id}`;
}

// APP-LENSES — Designer is ONE surface, not a set of lenses over a shared spine (APP-ONE-SURFACE).
// Validate and Analyze were removed with their modules. The remaining union has ONE reader-facing member.
// `cql` is not a tab (APP-CQL-AUTHORITATIVE) — it is the dependency view, reached by following an include.
// The type stays a union rather than collapsing to a string because the router, the tour's deep links
// and the palette all address a view by name.
export type MeasureView = "define" | "cql";

/** The lens bar's entries. ONE, so the bar does not render (`MeasureWorkspace`): a switcher between one
 *  destination and itself is chrome that costs a reader a glance and gives nothing back. Kept as a list
 *  rather than deleted so re-introducing a second surface is an entry here, not a rebuild. */
export const MEASURE_LENSES: { id: MeasureView; label: string }[] = [
  { id: "define", label: "Define" },
];

/** Secondary/utility views. CQL is NOT a tab (APP-CQL-AUTHORITATIVE — authoritative but secondary):
 *  it's reached per-criterion via "View CQL source" in the Specification, or by exporting the package
 *  from the Open menu (DESIGNER-TOP-BAR). */
export const MEASURE_UTILITY: { id: MeasureView; label: string }[] = [];

// Criterion DOM ids live in specModel.ts (`specCellId`), library-qualified — a name-only id collided
// whenever two libraries, or two overloads, declared the same name (APP-SPEC-MODEL).

/** APP-DEFAULT-EXAMPLE — the bundled example artifact every feature demonstrates on: the Example
 *  Condition measure when present (the reviewed landing example), else the first measure, else the first
 *  definition. Shared by the workspace landing, the Scheduling screen, and the Product Tour so they all
 *  point at the same concrete, offline-capable example. */
export function defaultExampleId(defs: Definition[]): string {
  const example = defs.find((d) => /example/i.test(d.name));
  if (example) return example.id;
  const measure = defs.find((d) => d.cat !== "lib");
  return (measure ?? defs[0])?.id ?? "";
}


/** EVERY eCQM population define, in canonical document order. A run's `counts` are keyed by define
 *  name; these are the names that are populations at all, as against intermediate logic.
 *
 *  This list answers one question: *is this define a population* — which is a fact about the measure
 *  and has nothing to do with scoring. A separate, entirely different question — *which populations
 *  carry the score forward* — depends entirely on the declared scoring type. Separating them means
 *  every scoring type can recognize its populations, including the continuous-variable measure's
 *  `Measure Population`, which now appears in the document section, the funnel stage, the nav row, and
 *  the metric.
 *
 *  The second question is answered by `specScoring.populationChain(kind)`, and callers that mean the
 *  CHAIN must ask for it — a base taken against this superset is a denominator the measure does not have. */
export const POPULATIONS = [
  "Initial Population",
  "Denominator",
  "Denominator Exclusions",
  "Denominator Exceptions",
  "Numerator",
  "Numerator Exclusions",
  // Continuous-variable measures. Present so they are recognized as populations everywhere the six
  // above are; what the terminal does with them is `specScoring.ts`'s and is deliberately refused
  // (APP-FLOW-GRAIN). The spellings are the XSD's `PopulationKind`, singular `-exclusion` there and
  // written out here as the define names CQL declares.
  "Measure Population",
  "Measure Population Exclusions",
  "Measure Observation",
] as const;

/** SPEC-METRIC — what the evaluated total is CALLED, in the one place both surfaces read it from.
 *
 *  The funnel's first bar and the base named beside every root-level define's figure are the same
 *  number, and a reader can only see that if they are the same words: "68 of evaluated subjects" under
 *  a chart headed "All patients 68" asks them to guess whether two figures that match are the same
 *  figure. One spelling, so the question does not arise. */
export const ALL_PATIENTS = "All patients";

/** Truthy in the clinical sense — mirrors the backend's population membership test (run.py `_truthy`):
 *  boolean true, a non-zero number, a non-empty string, or a non-empty list/object. */
export function isTruthy(v: unknown): boolean {
  if (v === true) return true;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return v.trim() !== "" && v !== "false";
  if (Array.isArray(v)) return v.length > 0;
  if (v && typeof v === "object") return Object.keys(v).length > 0;
  return false;
}

/** SPEC-ATTRITION — one removal, drawn as a SEGMENT OF ITS BASE'S OWN BAR rather than as a row.
 *
 *  An exclusion is a SUBSET of the population it comes out of, and a stacked segment says that where a
 *  separate row says the opposite — that something happened after the denominator, to some other cohort.
 *  Two earlier shapes said it wrong. A line of red text between two full-width bars gave the removal no
 *  width at all, in a chart whose whole premise is that width IS magnitude, and left the bar above it
 *  undisturbed so the subtraction happened where nothing visible happened. A floating block on its own
 *  row drew it to scale but still read as a fourth population.
 *
 *  Stacking it also recovers the rate's base for free: the SOLID part of the Denominator's bar is what
 *  the removals left, which is exactly what the Numerator is divided by. A derived `Performance
 *  denominator` row would be a bar with no define in the CQL to correspond to it. Stacking means the
 *  segment boundary states the same quantity without needing that extra row. */
export interface FunnelRemoval {
  name: string;
  count: number;
  /** Share of the cohort — the segment's width on the funnel's ONE axis, as for every other width here. */
  pct: number;
  /** Where the segment starts on that axis. Segments tile the tail of their stage's bar, left to right in
   *  declaration order, beginning where the kept remainder ends. */
  offsetPct: number;
}

export interface FunnelStage {
  name: string;
  count: number;
  pct: number; // relative to the funnel base (0–100)
  /** Removals taken OUT of this stage, in declaration order — segments of its bar, never rows of their
   *  own. Absent where the declared scoring says nothing is removed from this population. */
  removals?: FunnelRemoval[];
  /** What `removals` left. For a proportion's Denominator this IS the reported rate's base, which is why
   *  the solid segment's right edge and the Numerator's `rateBase` are one number. */
  remaining?: number;
  /** `remaining` as a share of the cohort — the solid segment's width, and the first removal's offset. */
  remainingPct?: number;
  /** The base `rate` is taken against, carried so the figure can NAME it in visible text. It was a
   *  `title` attribute, which is to say invisible: a Numerator reading `56.7%` beside a bar drawn at its
   *  share of the cohort is two percentages against two bases with nothing on screen separating them. */
  rateBase?: number;
  /** SPEC-REPORTED-RESULT — the stage's share of ITS OWN base, where that differs from `pct` and the
   *  declared scoring gives a well-known formula for it. Set only on the Numerator of a proportion,
   *  where it is the measure's SCORE: numerator over the denominator less exclusions and exceptions.
   *
   *  `pct` stays the share of the cohort and keeps driving the bar's WIDTH, so the funnel still
   *  narrows top to bottom. Only the figure printed beside the name changes — a Numerator labelled
   *  44.8% is its share of everyone evaluated, which is a number the measure does not report and
   *  nobody asked it for. */
  rate?: number;
}

export interface Funnel {
  total: number;
  stages: FunnelStage[];
  /** SPEC-SCORING-METHODOLOGY — what this measure REPORTS, which depends on how it is scored.
   *
   *  Was `score: number | null`, computed as Numerator ÷ Denominator for every measure with both. That
   *  is the proportion formula, and it was applied to ratio and continuous-variable measures too — a
   *  rate stated with a proportion's authority over a shape that does not have one. `null` now means
   *  "not computed"; a `readout` of kind `unsupported` means "this shape has no figure we can state",
   *  and the two are different things a surface has to say differently. */
  readout: Readout;
}

/** Build the population funnel from a run result. `total` is the number of evaluated subjects; each
 *  population define present in `counts` becomes a stage, ordered canonically. */
export function buildFunnel(run: RunResult | null, scoring?: string | null): Funnel | null {
  if (!run || !run.ok || !run.counts) return null;
  // A run's `results` is a SAMPLE of members, not the cohort, so the largest population is the better
  // statement of the total here. That choice belongs to this caller, which knows what its `results` are
  // — `funnelFromCounts` is handed a total and states it (see there).
  const sampled = run.results ? Object.keys(run.results).length : 0;
  const largest = Math.max(sampled, ...POPULATIONS.map((n) => run.counts![n] ?? 0));
  return funnelFromCounts(run.counts, largest, scoring);
}

/** The funnel from a bare counts map — one implementation, whatever produced the counts.
 *
 *  A run reaches it through `buildFunnel`; the measure's metric set reaches it directly
 *  (`specMetrics.ts`). Two funnel builders would let the reported rate in the document's header
 *  disagree with the one in the Analyze lens, which is the same class of defect `APP-SPEC-MODEL`
 *  names for structure. */
export function funnelFromCounts(
  counts: Record<string, number>, subjects = 0, scoring?: string | null,
): Funnel | null {
  // EVERY BAR IS A POPULATION THE MEASURE DECLARES, and nothing derived earns one. What SCORING decides
  // is which of those populations are REMOVALS — drawn as segments inside the bar they come out of rather
  // than as bars themselves (`removesFrom`) — plus the Numerator's figure and the READOUT below. With an
  // undeclared scoring nothing is a removal and every population is a plain bar, which is the honest
  // reading: with no formula there is nothing to say about what comes out of what.
  const present = POPULATIONS.filter((n) => n in counts);
  // THE TOTAL IS THE CALLER'S TO STATE, and this function no longer second-guesses it. It used to take
  // `Math.max(subjects, firstPopulation)`, which reads as defensive and is not: when the evaluated total
  // came back as 0 — the Patient binding was being called on a retired signature (`insights/metrics.py`)
  // — the max silently relabelled the Initial Population count as "All patients", so the header reported
  // 67 while the criterion under it reported its base as 0. A wrong total that looks plausible is worse
  // than none: with no cohort there is no funnel, and the absence is the caller's to explain.
  const kind = parseScoring(scoring);
  const total = subjects;
  if (total === 0) return { total: 0, stages: [], readout: null };

  // The reported figure is `specScoring.ts`'s, because it is a function of the DECLARED scoring type
  // and nothing here knows that. What it keeps from the version that lived here: a proportion's base is
  // the denominator LESS what the exclusions and exceptions removed, not the denominator's own count —
  // a narration can say so, and a header reporting `numerator / denominator` beside that sentence is a
  // document contradicting itself.
  const readout = readoutOf(kind, counts);

  const share = (n: number) => Math.round((n / total) * 1000) / 10;

  // WHICH POPULATIONS ARE REMOVALS is the DECLARED SCORING's answer, not a name test — the rule that read
  // "Exclusions" or "Exceptions" in the name called a ratio's Numerator Exclusions a denominator removal.
  // Grouped by the population they come out of, because that is the bar they are drawn INSIDE.
  const removalsByBase = new Map<string, string[]>();
  for (const n of present) {
    const base = removesFrom(kind, n);
    if (base !== null) removalsByBase.set(base, [...(removalsByBase.get(base) ?? []), n]);
  }

  const stages: FunnelStage[] = [];
  stages.push({ name: ALL_PATIENTS, count: total, pct: 100 });
  for (const n of present) {
    // A removal earns no row of its own: it is a subset of its base, and it is drawn as a segment of that
    // base's bar (`FunnelRemoval`).
    if (removesFrom(kind, n) !== null) continue;
    const count = counts[n] ?? 0;
    const stage: FunnelStage = { name: n, count, pct: share(count) };

    const taken = removalsByBase.get(n);
    if (taken) {
      stage.remaining = count - taken.reduce((sum, r) => sum + (counts[r] ?? 0), 0);
      stage.remainingPct = share(stage.remaining);
      // Offsets ACCUMULATE THE ROUNDED shares rather than being computed from raw counts, so the segments
      // tile with no hairline gaps between them at any cohort size.
      let cursor = stage.remainingPct;
      stage.removals = taken.map((r) => {
        const seg = { name: r, count: counts[r] ?? 0, pct: share(counts[r] ?? 0), offsetPct: cursor };
        cursor += seg.pct;
        return seg;
      });
    }
    // SPEC-REPORTED-RESULT — the Numerator's own figure is the measure's SCORE, and for a declared
    // proportion that formula is settled: numerator over the denominator less its removals. It is
    // taken from the readout rather than divided again here, so the number beside the bar and the one
    // in the document's header cannot disagree.
    if (readout?.kind === "rate" && n === NUMERATOR) {
      stage.rate = readout.value;
      stage.rateBase = readout.base;
    }
    stages.push(stage);
  }
  return { total, stages, readout };
}

/** How many subjects the removal criteria took OUT of `base`, for a declared scoring type.
 *
 *  Exported because the reported figure and every inline figure measured against the same base have to
 *  agree (`SPEC-ATTRITION`): the Numerator section's share IS the reported rate, so the two reading one
 *  subtraction is what keeps one number from appearing twice as two.
 *
 *  Scoring-aware: subtracts only what belongs to the scoring type and base. */
export function removedFrom(
  counts: Record<string, number>, base: string, scoring?: string | null,
): number {
  const kind = parseScoring(scoring);
  return POPULATIONS
    .filter((n) => removesFrom(kind, n) === base)
    .reduce((sum, n) => sum + (counts[n] ?? 0), 0);
}

/** A result table name as `PIPE-RESULT-NAMING` writes it: lowercased, non-alphanumerics collapsed to
 *  underscores. Mirrors the backend's `_naming._normalize` — the two have to agree for a population's
 *  table to be recognized as that population at all. */
function normalizeResultName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

/** PIPE-PRE-COMPUTED — convert pre-computed results from `/api/results` into a `RunResult`
 *  shape that the existing `buildFunnel()` and `patientRows()` can consume.
 *
 *  THE POPULATION MATCH HAPPENS HERE, ONCE. The server used to send a pre-aggregated `funnel` whose
 *  camelCase keys were mapped back to define names on arrival; that was a second answer to "what did
 *  this measure score" (`SPEC-ATTRITION`) and it was built from `COUNT(*)`. What arrives now is one
 *  count per define, and the population defines are recognized by matching normalized names against
 *  `POPULATIONS` — the same names `PIPE-RESULT-NAMING` derived the tables from.
 *
 *  Per-member results are not available from this route (future: member pagination), which is why the
 *  Patient Lens still needs a run.
 *
 *  TAKES ONLY `defines`, because the streaming run's `done` frame carries the same rows without the
 *  schema identity around them (`SVC-RUN-STAGE`) and both callers must land on the same counts. The
 *  population match is the part that has to be shared: a caller keying `counts` by the raw define
 *  name produces a well-formed result the funnel cannot read. */
export function preComputedToRunResult(
  results: { defines: Pick<DefineResultEntry, "define" | "subjects">[] },
): RunResult {
  const populations = new Map(POPULATIONS.map((n) => [normalizeResultName(n), n as string]));
  const counts: Record<string, number> = {};
  for (const d of results.defines) {
    const population = populations.get(normalizeResultName(d.define));
    if (population) counts[population] = d.subjects;
  }
  return {
    ok: true,
    defines: results.defines.map((d) => d.define),
    counts: Object.keys(counts).length > 0 ? counts : undefined,
    // Per-member results not yet available from /api/results (PIPE-PRE-COMPUTED future: member pagination).
    results: undefined,
  };
}

// APP-EXEC-MODE / DATA-SOURCE-MODE — the compute pill's Data Source group. ExecMode covers the two OFFLINE
// / read states that need no backend binding: `static` (None — read the spec, no data + no compute) and
// `deck` (Synthetic — compute a bundled synthetic funnel, offline; the glass-box works with no
// backend). The real backend targets — customer Live sources and Conformance decks — come from the Data Source
// REGISTRY (DATA-SOURCE-REGISTRY, `GET /api/data-sources`); they are listed alongside these and run via
// `data_source_id` (DATA-SOURCE-PROFILE), the backend resolving the bind origin. The former `sample`/`live`
// ExecModes are RETIRED from the menu — a Live source is now a registered Data Source (sampling lives in its
// UDTFs, not a client cap); the union keeps those members only for the Inspector's run-state labels.
export type ExecMode = "static" | "deck" | "sample" | "live";

/** Compute character of a mode: `none` = no run, `synthetic` = offline deck, `live` = real backend. */
export type ExecKind = "none" | "synthetic" | "live";

export interface ExecModeInfo { id: ExecMode; label: string; kind: ExecKind; blurb: string; }

export const EXEC_MODES: ExecModeInfo[] = [
  { id: "static", label: "None", kind: "none", blurb: "No computed data" },
];

export function execModeInfo(id: ExecMode): ExecModeInfo {
  return EXEC_MODES.find((m) => m.id === id) ?? EXEC_MODES[0]!;
}

// APP-COMPUTE-TARGET — the runtime the spec runs on. The literal survives in older stored selections only.
// AMENDED 2026-10-10: the pill's Compute section picks a SQL warehouse (`warehouseId` in the stored state).
export type ComputeTarget = "serverless";

// APP-UPDATE-MODE — whether a measure computes on its own. AMENDED 2026-10-10: one checkable
// "Automatic" item, unchecked by default (Manual). Checked, it takes the wash and tick of a selected row;
// unchecked it is a plain row with no dot, and the status line beside the pill says why nothing is shown. `paused` is Manual: nothing computes until Run is pressed. `live` is Automatic:
// a calculation change (CQL, data source, period, warehouse) that finds no results runs Compute missing.
// Opening a measure always shows its existing results, read-only, in either mode
// (RUN-CLIENT-REQUEST-REOPEN).
//
// The 2026-08-08 checkbox read as on while unchecked (the row took a dot either way), so a reader waited on
// a run that was never going to start and saw only "Idle".
export type UpdateMode = "live" | "paused";

/** APP-UPDATE-MODE — the one checkable item. Unchecked is Manual (the default): press Refresh to compute. */
export const AUTOMATIC = { label: "Automatic", blurb: "Update when the library changes" };

/** APP-UPDATE-MODE — stored-state migration. Only an explicitly stored `live` stays Automatic; a stored
 *  `paused`, a missing value, or anything else is Manual. */
export function storedUpdateMode(value: unknown): UpdateMode {
  return value === "live" ? "live" : "paused";
}

/** APP-UPDATE-MODE — why the page shows what it shows. Never a bare "Idle". */
export type RunStatus =
  | { kind: "no-source" }
  | { kind: "checking" }
  | { kind: "not-computed" }
  | { kind: "changed" }
  | { kind: "shown"; computedAt?: string }
  | { kind: "running"; done?: number; total?: number }
  | { kind: "failed" };

/** APP-UPDATE-MODE — the status line's words for each reason. */
export function runStatusText(status: RunStatus): string {
  switch (status.kind) {
    case "no-source": return "No data source";
    case "checking": return "Looking for your results…";
    case "not-computed": return "Not computed. Press Refresh";
    case "changed": return "Calculation changed since last run";
    case "shown": return "Last Data Refresh";
    case "running": return status.total ? `Running ${status.done ?? 0}/${status.total}` : "Running…";
    case "failed": return "Failed. Retry";
  }
}

/** APP-UPDATE-MODE — the status's second line: when the shown results were computed ("Oct 10, 2:56 PM").
 *  Only the shown status has one. A missing time is stated, never left blank (`SPEC-METRIC`). */
export function runStatusWhen(status: RunStatus): string | undefined {
  if (status.kind !== "shown") return undefined;
  return status.computedAt ? formatWhen(status.computedAt) : "Time not recorded";
}

function formatWhen(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t)
    ? new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : iso;
}

// PIPE-MEASUREMENT-PERIOD / DESIGNER-TOP-BAR — the measurement period is an EXECUTION PARAMETER, and it
// is dialled in the toolbar (`ExecParamBar`) beside the compute pill rather than inside it: the pill
// answers "against what, and how often", the parameters answer "dialled to what", and folding the second
// question into the first control put the answer behind a caret.
export const PERIOD_YEAR_COUNT = 5;

/** The selectable measurement periods, most recent first: the current calendar year and the four before
 *  it. Future periods are not offered — a measure cannot be computed over data that does not exist. */
export function periodYears(now: Date = new Date()): number[] {
  const current = now.getFullYear();
  return Array.from({ length: PERIOD_YEAR_COUNT }, (_, i) => current - i);
}

// APP-EXEC-PARAMS — an execution PARAMETER: a named value the measure is COMPILED AND RUN WITH, as
// opposed to a property of the document (which the specification states) or of the runtime (which the
// Data Source and Compute target state). The measurement period is one of these, and today it is the
// only one the app has a source for — but a measure package declares its own parameters, so the toolbar
// bar holds a LIST and renders whatever is in it. Adding the next parameter is appending to that list;
// it costs no change to the control.
//
// A parameter is an ORDERED DOMAIN plus a cursor into it, which is why it is stepped rather than picked
// from a list: `options` is in display order and the ‹ › arrows move one step along it, so the control
// says which way is "more" and refuses to leave the domain (no arrow at the ends). Each parameter
// carries its OWN setter — the array is the whole contract between the workspace and the pill, with no
// id-dispatch in between.
export interface ExecParamOption {
  id: string;
  label: string;
}

export interface ExecParam {
  id: string;
  label: string;
  /** SPEC-PARAMETER-REF — the CQL parameter this control DIALS, where it dials one. Declared here rather
   *  than matched by convention at the call site: the join between the Designer's own execution control and the
   *  logic's declared parameter is a fact about this control, and a document citing a parameter's value
   *  needs it to be exact. Absent for a control that is not a CQL parameter. */
  cqlName?: string;
  /** The domain in display order: `‹` steps toward the head, `›` toward the tail. */
  options: ExecParamOption[];
  /** The selected option's id. */
  value: string;
  onChange: (value: string) => void;
}

export const PERIOD_PARAM_ID = "measurementPeriod";

/** PIPE-MEASUREMENT-PERIOD — the measurement period as an execution parameter. Years run oldest → newest
 *  so that `›` moves FORWARD in time, the direction the arrow points. */
export function periodParam(year: number, onChange: (year: number) => void): ExecParam {
  return {
    id: PERIOD_PARAM_ID,
    label: "Measurement period",
    cqlName: "Measurement Period",
    options: [...periodYears()].reverse().map((y) => ({ id: String(y), label: `CY ${y}` })),
    value: String(year),
    onChange: (v) => onChange(Number(v)),
  };
}

/** The label of a parameter's current value — what the stepper prints between its arrows. */
export function paramValueLabel(p: ExecParam): string {
  return p.options.find((o) => o.id === p.value)?.label ?? p.value;
}

/** SPEC-PARAMETER-REF — the values a narration may cite, keyed by CQL PARAMETER NAME.
 *
 *  The raw `value` rather than the display label, and the difference is the whole point of having both:
 *  the stepper prints `CY 2026` because a toolbar control needs to say what kind of thing it is, and a
 *  sentence wants `the 2026 measurement period` because the words either side already say. */
export function citableParams(params: ExecParam[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of params) if (p.cqlName) out[p.cqlName] = p.value;
  return out;
}

// DESIGNER-TOP-BAR — appearance is a per-USER preference, so it has ONE owner (the app frame) and ONE
// store, shared across screens and surviving a reload. This is the only way multiple screens can have
// consistent theme state.
export type ThemeChoice = "system" | "light" | "dark";

const THEME_KEY = "cql-databricks:theme";

export function loadTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch { return "system"; } // private mode
}

export function saveTheme(t: ThemeChoice) {
  try {
    if (t === "system") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, t);
  } catch { /* quota / private mode */ }
}

/** The `data-mw-theme` attribute value for a choice — ABSENT for `system`, which is what lets the
 *  `prefers-color-scheme` media query decide (measures.css keys dark off `.mw:not([data-mw-theme="light"])`). */
export function themeAttr(t: ThemeChoice): "light" | "dark" | undefined {
  return t === "system" ? undefined : t;
}

// ── the left pane's arrangement, and both panes' width (SPEC-NAV-COMPOSITION) ─────────────────────

/** The two arrangements of one specification. **Outline** is the document's table of contents, in the
 *  order it is published; **Composition** is the same defines as a tree, one branch per population,
 *  bottoming out at the reads (`CompositionNav.tsx`). Seeing every population's decomposition at once
 *  is how a reader finds where a define is actually used.
 *
 *  It is a PARSED UNION rather than a `useState` string because it lives in the URL as `?nav=`: the
 *  arrangement a colleague is looking at travels with the link. `?nav=flow` in an old link parses to
 *  `outline` rather than erroring — a stale address lands the reader somewhere real. */
export type NavTab = "outline" | "composition";

export function parseNavTab(v: string | null | undefined): NavTab {
  return v === "composition" ? "composition" : "outline";
}

/** APP-PANE-STATE — a side pane's width. `normal` is the responsive default and deliberately writes
 *  no CSS rule of its own, which is what lets it fall back to the viewport media queries. */
export type PaneSize = "collapsed" | "normal" | "expanded";
export interface PaneState { nav: PaneSize; workbench: PaneSize }

const PANES_KEY = "cql-databricks:panes";
const PANE_SIZES = new Set<PaneSize>(["collapsed", "normal", "expanded"]);
const DEFAULT_PANES: PaneState = { nav: "normal", workbench: "normal" };

/** Pane geometry is a per-user PREFERENCE, not an address — which is why it is in `localStorage` and
 *  `?crit=` is in the URL. Pasting a link has to land a colleague on the same row; it has no business
 *  resizing their panes. */
export function loadPanes(): PaneState {
  try {
    const raw = JSON.parse(localStorage.getItem(PANES_KEY) || "{}") as Partial<PaneState>;
    return {
      nav: PANE_SIZES.has(raw.nav as PaneSize) ? raw.nav as PaneSize : DEFAULT_PANES.nav,
      workbench: PANE_SIZES.has(raw.workbench as PaneSize) ? raw.workbench as PaneSize : DEFAULT_PANES.workbench,
    };
  } catch { return DEFAULT_PANES; } // private mode
}

export function savePanes(p: PaneState) {
  try { localStorage.setItem(PANES_KEY, JSON.stringify(p)); } catch { /* quota / private mode */ }
}

// ── Product Tour (APP-TOUR) ────────────────────────────────────────────────────────────────────
// A deep-link into the workspace opened on the default example (APP-DEFAULT-EXAMPLE) at a given
// lens/mode. The Tour's "Show me" links carry one of these (or a `module` for aspirational surfaces).
export interface ExampleTarget { lens: MeasureView; mode: ExecMode; }
export interface DeepLink { openId?: string; lens?: MeasureView; mode?: ExecMode; }

export interface TourFeature {
  id: string;
  eyebrow: string;
  title: string;
  dek: string;
  chips?: string[];
  /** What the pillar is FOR, as a short list under the dek — a title and one or two sentences each.
   *  For a pillar whose substance is its uses rather than a screen (the clinical decision layer). */
  applications?: { title: string; body: string }[];
  /** "Show me" opens the default example at this lens/mode… */
  example?: ExampleTarget;
  /** …or switches to another module (aspirational surfaces)… */
  module?: TopModuleId;
  /** …or opens an object in the Databricks WORKSPACE this deployment belongs to — the published
   *  example notebooks (`SVC-EXAMPLE-NOTEBOOKS`), which are a product artifact rather than a screen.
   *  The workspace is a different host from the App, so the URL is composed from the host the BFF
   *  reports (`workspaceObjectUrl`); the path alone is stated whether or not that host resolved. */
  workspacePath?: string;
}

/** SVC-EXAMPLE-NOTEBOOKS / SVC-EXAMPLE-INDEX — where every deploy publishes the runnable notebooks, and
 *  the index page that is the folder's front door. Mirrored by `scripts/deploy/publish-examples.sh` (which
 *  writes them); this is the second statement of the same address, and it is the one the product links to. */
export const EXAMPLES_PATH = "/Shared/CqlDatabricks/examples/00_start_here";

/** The browser URL of a workspace object — `https://<host>/#workspace<path>`. The host comes from the
 *  BFF (`GET /api/workspace-host`) because a Databricks App is served from `*.databricksapps.com`,
 *  a different origin from the workspace it was deployed into: the page cannot derive it. */
export function workspaceObjectUrl(host: string, path: string): string {
  return `${host.replace(/\/+$/, "")}/#workspace${path}`;
}

// The tour is GENERATED from this registry (APP-TOUR: kept updated) — add a feature here and it
// appears in the tour, deep-linking into the real product rather than a hand-maintained snapshot.
//
// ONE SECTION PER PILLAR. The page sells CQL_DATABRICKS, not the Designer: each section is a
// theme of the product rather than a feature of a screen (`THEME-COMMODITIZE`, `THEME-LIVING-DOC`,
// `THEME-GLASSBOX`, `SEC-GOVERNANCE` + `SEC-OBO`, `THEME-KNOWLEDGE-ARCH`, `APP-ANALYTICS`, the clinical decision layer). Mapping
// (Data Readiness) and Sharing moved with their modules (DESIGNER-ONE-MODULE). Each topic from the
// per-pane cards — narrative, figures, inspector, nav — is now a sentence or a chip inside the pillar
// it belongs to: eleven cards about one module could not say what the product IS, but a cohesive theme can.
export const TOUR_FEATURES: TourFeature[] = [
  {
    id: "analytics", eyebrow: "Into the analytical world", title: "Your analytics stack is now your measurement stack.",
    dek: "Every CQL engine ends at the report; analytics is where the CQL world stops. Results from CQL on Databricks are ordinary governed tables, so everything downstream is native — dashboards, alerts, pipelines, a plain-English question answered from CQL logic by someone who will never open CQL on Databricks. The measure stops being a quarterly report and becomes a metric anyone can ask.",
    chips: ["AI/BI dashboards", "cql() in a notebook", "Genie One", "Alerts & pipelines", "OpenSharing"],
    workspacePath: EXAMPLES_PATH,
  },
  {
    id: "living-doc", eyebrow: "Computable knowledge, as intended", title: "Translation drift ends here.",
    dek: "A specification hand-ported to another language on another platform starts diverging the day the port is made, while the clinician who wrote the intent can't read the port to verify it. CQL on Databricks executes natively; it renders, computes and explains it, the way HL7 meant it to be used.",
    chips: ["No hand-ported logic", "Prose bound to the logic", "Attrition per criterion", "Ask a define what it returned"],
    example: { lens: "define", mode: "static" },
  },
  {
    id: "trust", eyebrow: "Faithful representation and execution", title: "Numbers you recognize, verify, and trust.",
    dek: "Click a population count and descend to one member — which criterion placed them, said in plain language. Run against golden results and get a receipt rather than a green tick: per-population tallies, the mismatches, the match rate. Every result carries what produced it, at an address you can reproduce quarters later. This is where number disputes end.",
    chips: ["Trace individual patients", "Conformance validation", "Population distributions"],
    example: { lens: "define", mode: "deck" },
  },
  {
    id: "governance", eyebrow: "Governance you already run", title: "Discoverable, traceable, and governed to the row.",
    dek: "Clinical measurement runs on the most sensitive data an organization holds, and everything CQL on Databricks produces is an ordinary Unity Catalog asset — so it inherits the governance you already operate rather than asking for one of its own. Measures, results and the data behind them are discoverable and tagged, which is the difference between people finding what exists and asking around for it. Lineage runs end to end: a reported figure traces back through every result table to the source relations that fed it, so an audit is a question you answer rather than a reconstruction. And access stays where it belongs — roles and attributes, governed tags, row filters and column masks over your most sensitive assets, enforced by the catalog itself. The compute runs in your own workspace as the signed-in user: no extract, no copy, nothing leaving your perimeter.",
    chips: ["Discovery & tagging", "End-to-end lineage", "RBAC & ABAC policies", "Unity Catalog"],
  },
  {
    id: "ontology", eyebrow: "Clinical Ontology", title: "One ontology behind every use of it.",
    dek: "FHIR standardized how clinical data is shared. CQL on Databricks standardizes how clinical knowledge is shared, on a platform most organizations already operate. It adopts HL7's own architecture — narrative guideline, structured knowledge, executable logic — so a clinical definition belongs to the ontology rather than to whatever happens to consume it. The define that decides a measure's numerator is the same factor a predictive model uses and the same criterion a care team works from: measurement, prediction and care management speaking one vocabulary. Define a controlled reading once and everything downstream means the same thing by it.",
    chips: ["Common data model", "Composable knowledge", "Clinical knowledge sharing"],
  },
  {
    id: "decision-layer", eyebrow: "A clinical decision layer", title: "Deterministic clinical logic under every agent and analysis.",
    dek: "Agents and models are probabilistic; the clinical definitions they act on shouldn't be. CQL on Databricks gives them a deterministic decision layer: the same trusted definition, evaluated the same way every time, so an agent's answer, a model's feature and a quality report all rest on logic a clinician can read and check. It runs continuously across your clinical workflows, not once a quarter at the end of them.",
    applications: [
      { title: "Predictive analytics on agreed definitions.", body: "Build models on the clinical definitions your organization has already agreed on. Each CQL define becomes a predictor with a known, citable meaning, so a model's inputs can be audited as easily as its outputs." },
      { title: "Care gaps, found while they can still be closed.", body: "Find each patient's open gap from the same definition the measure reports on, with the evidence behind it, and act on it one patient at a time. Quality is reported continuously, so a gap shows up while there's still time to close it, not when it's counted at year end." },
      { title: "High-risk members, defined precisely.", body: "Build care-management cohorts from exact clinical criteria: multiple chronic conditions, a recent inpatient stay, a gap in medication. Models rank the cohort by risk; CQL states why each member is in it." },
      { title: "Payment integrity on transparent rules.", body: "Check services against clinical rules anyone can read: missing prerequisites, duplicates, care that doesn't match the record. Models flag the anomaly; CQL states which rule it broke." },
    ],
  },
];

// The Synthetic funnel is BFF-served (APP-NO-UI-MOCK): the SPA fetches it via
// `run-client.ts` `sampleRun` (`POST /api/sample-run`) rather than fabricating data here — the generator
// lives in the backend (`cql_databricks_designer/sample_deck.py`). Deterministic + offline (no live-data connection).

export type Landing = "numerator" | "denominator" | "excluded" | "population" | "out";

export interface PatientRow {
  id: string;
  /** Display label — subject ids are opaque, so we show a shortened form. */
  label: string;
  landing: Landing;
  values: Record<string, unknown>;
  /** Population defines (in `POPULATIONS`) this subject is truthy for — drives the funnel pins. */
  reached: string[];
}

const LANDING_LABEL: Record<Landing, string> = {
  numerator: "numerator", denominator: "denominator", excluded: "excluded", population: "in population", out: "not in population",
};
export function landingLabel(l: Landing): string { return LANDING_LABEL[l]; }

// Abbreviated landing tokens for the Patient Lens list chip — the compact type-chip vocabulary the nav
// and Define view use (pop/def/vs/…), so a patient row reads as [landing chip] + name, parallel to a
// nav row (APP-STEP-INTO-PATIENT, APP-STYLE-GUIDE). The full prose stays in the stepped-in header.
const LANDING_CHIP: Record<Landing, string> = {
  numerator: "num", denominator: "den", excluded: "exc", population: "ip", out: "out",
};
export function landingChip(l: Landing): string { return LANDING_CHIP[l]; }

/** Where one subject LANDED. Proportion-shaped, deliberately and not yet generalized.
 *
 *  SPEC-SCORING-METHODOLOGY made scoring a first-class input everywhere a FIGURE is stated, and stopped
 *  short here. The reason is that this serves the Patient Lens, which is fed only by `run.results` — and
 *  `preComputedToRunResult` returns `results: undefined`, so the only path that reaches it is a live
 *  `/api/run`. Threading scoring through would make `Landing` nullable and ripple into `landingLabel`,
 *  `landingChip`, the funnel pins and `PATHWAY_BY_DEFINE`, to change the behaviour of a surface that
 *  currently has no data. There is also no designed vocabulary for where a subject "lands" in a
 *  continuous-variable measure, and inventing one to fill a type is the speculative abstraction
 *  `coding-standards.md` forbids.
 *
 *  So: this is correct for a proportion and states nothing useful for anything else. Generalize it when
 *  the per-member path lands (`APP-STEP-INTO-PATIENT`), together with the landing vocabulary. */
function classify(vals: Record<string, unknown>): Landing {
  const t = (n: string) => isTruthy(vals[n]);
  if (t("Numerator")) return "numerator";
  if (t("Denominator Exclusions") || t("Numerator Exclusions")) return "excluded";
  if (t("Denominator")) return "denominator";
  if (t("Initial Population")) return "population";
  return "out";
}

/** Short, stable display label for an opaque subject id. */
function shortLabel(id: string): string {
  if (id === "*") return "Unfiltered";
  const tail = id.length > 10 ? id.slice(-6) : id;
  return `Patient ${tail}`;
}

/** Turn a run's per-member `results` into Patient Lens rows. */
export function patientRows(run: RunResult | null): PatientRow[] {
  if (!run || !run.ok || !run.results) return [];
  return Object.entries(run.results).map(([id, values]) => ({
    id,
    label: shortLabel(id),
    landing: classify(values),
    values,
    reached: POPULATIONS.filter((n) => isTruthy(values[n])),
  }));
}

/** Plain-language descriptions for well-known eCQM population defines (Summary view shows NO CQL). */
// DEFINE_BLURB is GONE (SPEC-NARRATIVE-TAGS). It was a hardcoded map of generic sentences —
// "Initial Population: the eligible cohort before any exclusions" — that read like narration while
// saying nothing about how a criterion is actually defined, and it was indistinguishable on the page
// from prose the measure's author wrote. Narration now comes from the CQL's own annotation tags or
// from the Specification overlay, each labelled with its provenance; a define nobody has narrated
// shows nothing at all, which is honest (`coding-standards.md`, "Data provenance").

// ── Knowledge-Architecture up-trace (ACC-KA-TRACE) ───────────────────────────────────────────────
// The KA reference makes the clinical lineage NAVIGABLE: from an
// executable define (L3/L4) climb UP to the Pathway recommendation (L1) it realizes — "this numerator
// implements recommendation 3.2." That up-hop is the glass-box (THEME-GLASSBOX) at the knowledge
// level, and it is the Patient-Lens crown jewel's other direction (APP-STEP-INTO-PATIENT climbs
// down into the Case; this climbs up to the guideline).
//
// The Pathway layer (CPGRecommendation → FHIR PlanDefinition) is not yet real in the model — that is
// W2 (ACC-KA-LAYERS). Here the recommendation is PLUGGABLE, ATTRIBUTED, authored content
// (APP-PLUGGABLE-CLINICAL): the guideline's own statement, never a Databricks clinical assertion
// (APP-CLINICAL-BOUNDARY). It is keyed by population define so the lens can show, for the criterion
// that placed a member, which recommendation that criterion realizes.

export interface PathwayRec {
  /** The recommendation id within the source guideline, e.g. "3.2". */
  rec: string;
  /** The guideline's own normative statement (L1 narrative) — authored, attributed content. */
  statement: string;
  /** The source guideline / steward the recommendation is attributed to. */
  guideline: string;
}

/** Pathway recommendation each population define realizes (authored content; see note above). */
export const PATHWAY_BY_DEFINE: Record<string, PathwayRec> = {
  "Initial Population": {
    rec: "1.1",
    statement: "Identify the eligible population for the measured intervention by age, enrollment and qualifying condition.",
    guideline: "Example guideline",
  },
  "Denominator": {
    rec: "2.1",
    statement: "All eligible patients with the qualifying condition should be assessed against the care target during the period.",
    guideline: "Example guideline",
  },
  "Denominator Exclusions": {
    rec: "2.4",
    statement: "Exclude patients whose care goals make the intervention inappropriate from the measured population.",
    guideline: "Example guideline",
  },
  "Numerator": {
    rec: "3.2",
    statement: "Patients meeting the clinical target receive the recommended intervention within the measurement period.",
    guideline: "Example guideline",
  },
};

/** The recommendation an executable define realizes, or null when no Pathway link is authored yet. */
export function pathwayFor(define: string): PathwayRec | null {
  return PATHWAY_BY_DEFINE[define] ?? null;
}

/** The criterion that PLACED a member — the deciding hop for the up-trace. From a member's landing,
 *  the define that explains where they sit: the target they met, the exclusion that removed them, or
 *  the gate they failed. `met` is whether that criterion is satisfied for them. */
export interface Deciding { define: string; met: boolean }

export function decidingCriterion(p: PatientRow): Deciding {
  const t = (n: string) => isTruthy(p.values[n]);
  switch (p.landing) {
    case "numerator": return { define: "Numerator", met: true };
    case "excluded":
      return { define: t("Denominator Exclusions") ? "Denominator Exclusions" : "Numerator Exclusions", met: true };
    case "denominator": return { define: "Numerator", met: false };
    case "population": return { define: "Denominator", met: false };
    default: return { define: "Initial Population", met: false };
  }
}

// ── APP-LOGIC-VIEW / SPEC-NAV-COMPOSITION — what "selected" means ─────────────────────────────
//
// Selection is the thread that ties the nav, the document and the workbench together, and it used to be
// a define NAME — a bare string, because a define was the only thing there was to select. A READ is now
// selectable too (`SPEC-DATA-REQUIREMENTS`), and a read has no name: its identity is its resource and
// its filter, which is what `SpecRetrieve.key` already is. So selection is a UNION.
//
// It still travels as ONE TOKEN, because two of its carriers are string-shaped and cannot hold a union:
// the URL (`?crit=`, where selection lives so that pasting an address lands a colleague on the same row)
// and the DOM (`data-crit-name`, how a lens finds the element to scroll to). A define's token is its
// bare name, UNCHANGED — every link that already exists keeps working — and a read's is namespaced
// `read:<key>`, so a define named like a key cannot collide with one.

/** What the reader has selected: a declaration, or one of the reads it issues. */
export type Selection =
  | { kind: "define"; name: string }
  | { kind: "retrieve"; key: string };

const READ_PREFIX = "read:";

/** The URL / DOM form of a selection. */
export function selectionToken(sel: Selection): string {
  return sel.kind === "define" ? sel.name : `${READ_PREFIX}${sel.key}`;
}

/** Parse the URL / DOM form back. An unprefixed token is a define, which is what keeps existing
 *  `?crit=` links working. */
export function parseSelection(token: string | null | undefined): Selection | null {
  if (!token) return null;
  return token.startsWith(READ_PREFIX)
    ? { kind: "retrieve", key: token.slice(READ_PREFIX.length) }
    : { kind: "define", name: token };
}

/** True when `sel` is the selection of this define — the comparison every row and section makes. */
export function isDefineSelected(sel: Selection | null, name: string): boolean {
  return sel?.kind === "define" && sel.name === name;
}

/** True when `sel` is the selection of this read. */
export function isRetrieveSelected(sel: Selection | null, key: string): boolean {
  return sel?.kind === "retrieve" && sel.key === key;
}
