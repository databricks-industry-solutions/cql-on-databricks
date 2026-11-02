// APP-SPEC-MODEL / SPEC-ATTRITION — the document's figures, resolved in ONE place.
//
// Every surface that renders a number reads this module: the nav row, the criterion heading, a figure
// cited inside prose (`SPEC-METRIC-REF`) and the reported result. That is the whole design constraint —
// a nav row saying 8,204 beside a section saying 8,207 is the two-surfaces-disagreeing failure
// `APP-SPEC-MODEL` exists to prevent, and the only way to be sure of it is for there to be one
// resolver rather than four call sites each doing their own arithmetic.
//
// WHAT THIS OWNS: joining a define to its figure, resolving that figure's BASE, and dividing the two.
// WHAT IT DOES NOT: the unit ("patients"), the form and the enclosing population — all three are
// `SPEC-METRIC` determinations already made from the ELM in `specDocument.ts`, and re-deciding them
// here would be a second opinion able to disagree with the first.
//
// THE BASE IS NEVER GUESSED. `SPEC-METRIC` requires a percentage to state what it is a percentage OF,
// because a bare "149,882 patients" invites comparison against the wrong denominator. Two rules, and
// nothing else: a population's base is the stage above it in the reporting chain, and every other
// define's base is the population it sits under (`SpecMetric.base`, derived from placement). A define
// with neither — the Initial Population at the head of the chain, and every COMMON define reached from
// more than one population — is measured against the evaluated total, which is stated rather than
// inferred and is named `ALL_PATIENTS`: the same words the funnel's first bar carries, because it is
// the same number.

import { ALL_PATIENTS, POPULATIONS, type Funnel, funnelFromCounts } from "./model.ts";
import { cascadeOf, parseScoring, readoutFromScore, type MeasureScoreDto, type Readout } from "./specScoring.ts";
import type { SpecDocument, SpecMetric } from "./specDocument.ts";

/** The wire payload: what the run (or the sample generator) produced. Mirrors `insights/model.py`. */
export interface MeasureMetricsDto {
  library: string;
  version: string;
  provenance: "sample" | "run";
  generated: string;
  /** Every subject the measure was evaluated against, before any criterion. */
  subjects: number;
  caveat?: string;
  /** RUN-CLIENT-MEASURE-SCORE — the client's score, or null when no scoring was declared. */
  score?: MeasureScoreDto | null;
  /** SPEC-POPULATION-CASCADE — `subjects` is MET, across every evaluated subject; `counted` is those in
   *  the cohort the request named, and `cohortSubjects` that cohort's size. */
  defines?: {
    define: string; library: string; subjects: number; counted: number; cohortSubjects: number;
    elements?: number;
  }[];
}

/** One define's figures. `elements` is null wherever there is no element grain — a Boolean says
 *  something about the subject and nothing else, so an element count would be an invented dimension.
 *
 *  SPEC-POPULATION-CASCADE — `subjects` is the COUNTED figure, the one every surface shows; `met` is the
 *  define true across every evaluated subject, which only the define section shows beside it. */
export interface DefineFigure {
  subjects: number;
  met: number;
  /** The cohort `subjects` was counted among, by the name the request gave it. */
  cohort: { name: string; subjects: number };
  elements: number | null;
}

/** SPEC-POPULATION-CASCADE — the cohort one define is counted among, as the metrics request states it:
 *  in every `allOf` define and in no `noneOf` define, both library-qualified. */
export interface MetricCohort {
  name: string;
  allOf: { name: string; library: string }[];
  noneOf: { name: string; library: string }[];
}

const EVERYONE: MetricCohort = { name: ALL_PATIENTS, allOf: [], noneOf: [] };

/** The population names a metric's base rule recognizes (`baseOf`). */
const POPULATION_NAMES: ReadonlySet<string> = new Set(POPULATIONS);

/** SPEC-POPULATION-CASCADE — every define's cohort, keyed like the metric set (`metricKey`).
 *
 *  A population is counted AS WRITTEN, among every evaluated subject: only the
 *  score is cascaded, and the client computes it (`RUN-CLIENT-MEASURE-SCORE`). A criterion is counted among
 *  the patients ENTERING the population it sits under, which is the same cascade — so `No HbA1c In
 *  Period` under the Numerator is counted among the Denominator less its Exclusions, and its share
 *  reads as what that step did to the patients who reached it. Placement decides which population a
 *  criterion sits under (`SPEC-PLACEMENT`); a COMMON define, any define no population reaches, and every
 *  define of a measure with no declared scoring are counted among every evaluated subject. */
export function buildMetricCohorts(
  doc: SpecDocument | null, scoring: string | null | undefined,
): Map<string, MetricCohort> {
  const out = new Map<string, MetricCohort>();
  if (!doc) return out;
  const kind = parseScoring(scoring);
  const refs = new Map<string, { name: string; library: string }>();
  for (const scope of doc.populations) {
    if (scope.population) refs.set(scope.name, { name: scope.population.name, library: scope.population.library });
  }
  const declared = new Set(refs.keys());
  const ref = (name: string) => refs.get(name)!;
  const cohortOfScope = (name: string): MetricCohort => {
    const cascade = cascadeOf(kind, name, declared);
    return cascade && cascade.label
      ? { name: cascade.label, allOf: cascade.allOf.map(ref), noneOf: cascade.noneOf.map(ref) }
      : EVERYONE;
  };
  for (const scope of doc.populations) {
    if (!scope.population) continue;
    const cohort = cohortOfScope(scope.name);
    for (const concept of scope.concepts) {
      out.set(metricKey(concept.library, concept.define), cohort);
      for (const rule of concept.rules) out.set(metricKey(rule.library, rule.define), cohort);
    }
    for (const rule of scope.rules) out.set(metricKey(rule.library, rule.define), cohort);
  }
  // Populations LAST: one population can sit under another as a Concept (a Denominator written as
  // `"Initial Population"`), and it is counted as written, not in the cohort it sits under.
  for (const scope of doc.populations) {
    const entry = scope.population;
    if (entry) out.set(metricKey(entry.library, entry.name), EVERYONE);
  }
  return out;
}

/** The cohort for one define: its placement's, or every evaluated subject where nothing places it. */
export function cohortOf(cohorts: Map<string, MetricCohort>, library: string, define: string): MetricCohort {
  return cohorts.get(metricKey(library, define)) ?? EVERYONE;
}

/** The resolved metric layer, keyed the way the document joins: `"<library>::<define>"`. */
export interface MetricSet {
  provenance: "sample" | "run";
  caveat: string | null;
  subjects: number;
  byKey: Map<string, DefineFigure>;
  /** Subject counts by BARE define name, for the funnel and for a reference that knows no library. */
  counts: Record<string, number>;
  /** SPEC-SCORING-METHODOLOGY — the measure's DECLARED scoring, which decides the reporting chain every
   *  base below is taken against, and what the funnel reports at the end of it. Carried on the set
   *  rather than passed to each call so one document cannot resolve two figures under two scorings.
   *  Null where the measure declares none, and then no figure gets a population base at all. */
  scoring: string | null;
  /** RUN-CLIENT-MEASURE-SCORE — what the measure reports, from the client's score. */
  readout: Readout;
}

/** A figure ready to render: the value, what it is a share OF, and the share itself. */
export interface ResolvedMetric {
  /** SPEC-POPULATION-CASCADE — the COUNTED figure. */
  subjects: number;
  /** The define true across every evaluated subject, before the cascade. */
  met: number;
  elements: number | null;
  /** The base's own count, and the name to state it by. Null when nothing sensible bounds this figure. */
  base: { name: string; subjects: number } | null;
  /** `subjects / base.subjects`, or null with no base. Never above 1 — the chain guarantees it. */
  share: number | null;
  /** True for a criterion that REMOVES from its base rather than narrowing it forward. */
  removes: boolean;
}

export function metricKey(library: string, define: string): string {
  return `${library}::${define}`;
}

/** Build the metric layer from the wire payload.
 *
 *  `scoring` comes from the Specification overlay rather than the metrics payload: it is a property of
 *  the MEASURE, not of an evaluation of it, and the endpoint that counts subjects has no business
 *  having an opinion about how they are scored. */
export function buildMetricSet(
  dto: MeasureMetricsDto | null, scoring: string | null | undefined, cohorts: Map<string, MetricCohort>,
): MetricSet | null {
  if (!dto) return null;
  const byKey = new Map<string, DefineFigure>();
  const counts: Record<string, number> = {};
  for (const d of dto.defines ?? []) {
    byKey.set(metricKey(d.library, d.define), {
      subjects: d.counted, met: d.subjects, elements: d.elements ?? null,
      cohort: { name: cohortOf(cohorts, d.library, d.define).name, subjects: d.cohortSubjects },
    });
    // A bare-name index alongside the qualified one. Collisions are real (`FHIRHelpers.ToString`) and
    // resolve to the first declaration; every surface that has a library uses the qualified key, and
    // this one exists for the funnel, whose stages are population names the root library declares.
    if (!(d.define in counts)) counts[d.define] = d.counted;
  }
  return {
    provenance: dto.provenance,
    caveat: dto.caveat ?? null,
    subjects: dto.subjects,
    byKey,
    counts,
    scoring: scoring ?? null,
    readout: readoutFromScore(dto.score),
  };
}

/** SPEC-ATTRITION — one define's figure with its base resolved, or null when there is no figure.
 *
 *  Null means NOT COMPUTED, and the caller renders nothing rather than a zero: "no run has produced
 *  this" and "the answer is none" are opposite claims, and a document that confuses them is worse than
 *  one that stays quiet. */
export function resolveMetric(
  set: MetricSet | null, metric: SpecMetric | null, library: string,
): ResolvedMetric | null {
  if (!set || !metric) return null;
  const own = set.byKey.get(metricKey(library, metric.countOf));
  if (!own) return null;

  const base = baseOf(set, metric, own);
  return {
    subjects: own.subjects,
    met: own.met,
    elements: own.elements,
    base,
    // Guarded against a zero base rather than rendering Infinity or NaN into a clinical document.
    share: base && base.subjects > 0 ? own.subjects / base.subjects : null,
    // A population is counted as written, so nothing it shows is "removed from" a stage above it: the
    // removal happens inside the score (`RUN-CLIENT-MEASURE-SCORE`), not in the figure.
    removes: false,
  };
}

/** The two base rules, and no third.
 *
 *  A POPULATION is counted as written, so its base is every evaluated subject (`ALL_PATIENTS`), whatever
 *  the scoring (`SPEC-POPULATION-CASCADE`). Its share of the stage above it would mix
 *  two definitions' subjects: a raw Numerator can hold patients the Denominator never had.
 *
 *  SPEC-ATTRITION — every other define's base is the cohort it was COUNTED among: the patients entering
 *  the population it sits under. */
function baseOf(
  set: MetricSet, metric: SpecMetric, own: DefineFigure,
): { name: string; subjects: number } | null {
  if (POPULATION_NAMES.has(metric.countOf)) return { name: ALL_PATIENTS, subjects: set.subjects };
  return own.cohort;
}

/** One share, one format, everywhere: `58.8%`.
 *
 *  ONE DECIMAL, ALWAYS, and it is not a style preference. The Numerator's inline share and the reported
 *  rate in the document's header ARE the same figure against the same base, so rounding them differently
 *  printed "59%" two inches under "58.8%" and invited a reader to look for the difference. */
export function formatShare(share: number): string {
  return `${(share * 100).toFixed(1)}%`;
}

/** SPEC-METRIC — the base, spelled out, wherever a figure is shown without room to name it.
 *
 *  A percentage that does not say what it is a percentage OF can be read against the wrong denominator,
 *  and the nav row has 186px. The bar and the number live on the row; the sentence naming their base
 *  lives here, which is the smallest place it can live and still always be present. */
export function metricTitle(m: ResolvedMetric, define: string): string {
  const grains = [`${m.subjects.toLocaleString()} subjects counted (${m.met.toLocaleString()} met)`];
  if (m.elements != null) grains.push(`${m.elements.toLocaleString()} elements`);
  if (!m.base) return `${define} — ${grains.join(" · ")}`;
  const verb = m.removes ? "removed from" : "of";
  const pct = m.share != null ? `${formatShare(m.share)} ` : "";
  return `${define} — ${grains.join(" · ")}; ${pct}${verb} ${m.base.name}`
    + ` (${m.base.subjects.toLocaleString()})`;
}

/** SPEC-REPORTED-RESULT — the funnel the document's header reports.
 *
 *  Derived from the same counts every other figure reads, through the same `funnelFromCounts` a run
 *  goes through, so the reported rate and the criterion figures cannot disagree. */
export function metricFunnel(set: MetricSet | null): Funnel | null {
  if (!set) return null;
  // The EVALUATED TOTAL is passed, and it is the only thing the first bar may be. `funnelFromCounts`
  // does not fall back to the largest population when the total is 0. That fallback would draw an "All
  // patients" bar identical to the Initial Population it sits above: a first stage that appeared to
  // exclude nobody, and a base the criterion beside it reported as 0. The total is stated, never substituted.
  return funnelFromCounts(set.counts, set.subjects, set.readout);
}
