// APP-SAMPLE-METRICS / SPEC-ATTRITION — the document's figures, resolved in ONE place.
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

import { ALL_PATIENTS, removedFrom, type Funnel, funnelFromCounts } from "./model.ts";
import { parseScoring, populationChain, removesFrom } from "./specScoring.ts";
import type { SpecMetric } from "./specDocument.ts";

/** The wire payload: what the run (or the sample generator) produced. Mirrors `insights/model.py`. */
export interface MeasureMetricsDto {
  library: string;
  version: string;
  provenance: "sample" | "run";
  generated: string;
  /** Every subject the measure was evaluated against, before any criterion. */
  subjects: number;
  caveat?: string;
  defines?: { define: string; library: string; subjects: number; elements?: number }[];
}

/** One define's two grains. `elements` is null wherever there is no element grain — a Boolean says
 *  something about the subject and nothing else, so an element count would be an invented dimension. */
export interface DefineFigure {
  subjects: number;
  elements: number | null;
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
}

/** A figure ready to render: the value, what it is a share OF, and the share itself. */
export interface ResolvedMetric {
  subjects: number;
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
  dto: MeasureMetricsDto | null, scoring?: string | null,
): MetricSet | null {
  if (!dto) return null;
  const byKey = new Map<string, DefineFigure>();
  const counts: Record<string, number> = {};
  for (const d of dto.defines ?? []) {
    byKey.set(metricKey(d.library, d.define), { subjects: d.subjects, elements: d.elements ?? null });
    // A bare-name index alongside the qualified one. Collisions are real (`FHIRHelpers.ToString`) and
    // resolve to the first declaration; every surface that has a library uses the qualified key, and
    // this one exists for the funnel, whose stages are population names the root library declares.
    if (!(d.define in counts)) counts[d.define] = d.subjects;
  }
  return {
    provenance: dto.provenance,
    caveat: dto.caveat ?? null,
    subjects: dto.subjects,
    byKey,
    counts,
    scoring: scoring ?? null,
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

  const base = baseOf(set, metric, library);
  return {
    subjects: own.subjects,
    elements: own.elements,
    base,
    // Guarded against a zero base rather than rendering Infinity or NaN into a clinical document.
    share: base && base.subjects > 0 ? own.subjects / base.subjects : null,
    // SPEC-SCORING-METHODOLOGY — a stage REMOVES when the declared scoring says it removes from
    // something. The name-shaped test that preceded this called a ratio's `Numerator Exclusions` a
    // denominator removal, which is a subtraction no ratio measure performs.
    removes: removesFrom(parseScoring(set.scoring), metric.countOf) !== null,
  };
}

/** The two base rules, and no third.
 *
 *  THE CHAIN IS THE DECLARED SCORING'S (`specScoring.populationChain`), not the full population list.
 *  It used to be every population name in one fixed order, which is the proportion chain — so a ratio's
 *  figures were measured against a proportion's stages, and a continuous variable's against stages it
 *  does not have. With no declared scoring there is NO chain and no population base: the figure renders
 *  with its own count and no share, rather than against a denominator borrowed from a shape this
 *  measure might not be. */
function baseOf(
  set: MetricSet, metric: SpecMetric, library: string,
): { name: string; subjects: number } | null {
  const kind = parseScoring(set.scoring);
  const chain = populationChain(kind);
  const stage = chain ? chain.indexOf(metric.countOf) : -1;
  if (chain && stage >= 0) {
    // A removal stage is measured against what it removes FROM — not against the stage above it in the
    // list, which for `Numerator Exclusions` would be the Numerator and would read as though the
    // exclusions were a subset of the people who passed.
    const removesFromName = removesFrom(kind, metric.countOf);
    if (removesFromName) {
      const count = set.counts[removesFromName];
      return count != null ? { name: removesFromName, subjects: count } : null;
    }
    // Otherwise the nearest KEPT stage above it, skipping the removals. Nearest rather than
    // immediately-preceding because a measure declaring no `Denominator Exceptions` must not leave the
    // Numerator without a base — and SKIPPING REMOVALS because a removal is not a stage the funnel
    // passes through: taking `Denominator Exclusions` as the Numerator's base read "782 → 6,103 · 780%",
    // a figure that is not wrong so much as meaningless.
    for (let i = stage - 1; i >= 0; i--) {
      const name = chain[i]!;
      if (removesFrom(kind, name) !== null) continue;
      const count = set.counts[name];
      if (count == null) continue;
      // A stage drawn from the denominator is drawn from what the removals LEFT. This is the reported
      // rate's own base (`funnelFromCounts`), so the Numerator's inline share and the rate in the
      // document's header are one number rather than two that invite comparison.
      const removed = removedFrom(set.counts, name, set.scoring);
      return removed > 0
        ? { name: `${name} after exclusions`, subjects: count - removed }
        : { name, subjects: count };
    }
    // The first stage in the chain — Initial Population — narrows the whole cohort, so its base is the
    // funnel's own first bar, by that name (`ALL_PATIENTS`).
    return { name: ALL_PATIENTS, subjects: set.subjects };
  }
  // Every other define: the population it sits under, as placement determined (`SPEC-PLACEMENT`).
  if (metric.base) {
    const count = set.byKey.get(metricKey(library, metric.base))?.subjects
      ?? set.counts[metric.base];
    if (count != null) return { name: metric.base, subjects: count };
  }
  // COMMON — reached from more than one population, so its share is of the whole evaluation
  // (`SPEC-METRIC`). Named, because an unnamed denominator is the thing the base rule exists to stop.
  return { name: ALL_PATIENTS, subjects: set.subjects };
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
  const grains = [`${m.subjects.toLocaleString()} subjects`];
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
  // used to fall back to the largest population when the total was 0, which drew an "All patients" bar
  // identical to the Initial Population it sits above — a first stage that appeared to exclude nobody,
  // and a base the criterion beside it reported as 0. It no longer substitutes; the total is stated.
  return funnelFromCounts(set.counts, set.subjects, set.scoring);
}
