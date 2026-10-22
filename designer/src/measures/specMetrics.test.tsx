// SPEC-ATTRITION / SPEC-METRIC-REF — the base rules, and the arithmetic every figure in the document
// is divided by.
//
// This file exists because both defects the design shipped with were base defects, not rendering ones:
// the Numerator took `Denominator Exclusions` as its base and read "782 → 6,103 · 780%", and the
// reported rate divided by the denominator's own count while the prose beside it said not to. A wrong
// base is the worst available failure here — the figure is precise, plausible, and about the wrong
// denominator — so the rules are pinned rather than the pixels.

import { describe, expect, it } from "vitest";
import { ALL_PATIENTS, funnelFromCounts, removedFrom } from "./model.ts";
import type { SpecMetric } from "./specDocument.ts";
import {
  buildMetricCohorts, buildMetricSet, formatShare, metricKey, metricTitle, resolveMetric,
} from "./specMetrics.ts";

const LIB = "ExampleMeasure";

/** SPEC-POPULATION-CASCADE — the criterion below sits under the Numerator, so it is counted among the
 *  patients entering it. Only the cohort's NAME travels in this map; its size is on the payload. */
const COHORTS = new Map([[metricKey(LIB, "Encounters During Period"),
  { name: "Denominator after exclusions", allOf: [], noneOf: [] }]]);

/** The common proportion shape: a denominator equal to its initial population, and a removal stage.
 *
 *  DECLARED A PROPORTION, because this measure is one (SPEC-SCORING-METHODOLOGY). Every base rule below is a
 *  function of the scoring type: with none declared there is no reporting chain, so a figure gets its
 *  own count and no share rather than a base borrowed from a shape the measure might not be. */
function set() {
  return buildMetricSet({
    library: LIB, version: "1.0.000", provenance: "sample", generated: "2026-08-02T00:00:00Z",
    subjects: 12_728,
    defines: [
      { define: "Initial Population", library: LIB, subjects: 11_154, counted: 11_154, cohortSubjects: 12_728 },
      { define: "Denominator", library: LIB, subjects: 11_154, counted: 11_154, cohortSubjects: 11_154 },
      { define: "Denominator Exclusions", library: LIB, subjects: 790, counted: 782, cohortSubjects: 11_154 },
      { define: "Numerator", library: LIB, subjects: 6_900, counted: 6_103, cohortSubjects: 10_372 },
      { define: "Encounters During Period", library: LIB, subjects: 7_605, counted: 5_210,
        cohortSubjects: 10_372, elements: 31_529 },
      { define: "Has Excluded Care", library: "Exclusions", subjects: 214, counted: 214, cohortSubjects: 12_728 },
    ],
  }, "proportion", COHORTS)!;
}

/** The same measure with NO declared scoring — what most measures are until `Measure.scoring` is synced.
 *  With no scoring there is no cascade, so the server counted every define among every evaluated subject. */
function unscored() {
  const scored = set();
  return buildMetricSet({
    library: LIB, version: "1.0.000", provenance: "sample", generated: "2026-08-02T00:00:00Z",
    subjects: 12_728,
    defines: [...scored.byKey].map(([key, f]) => ({
      define: key.split("::")[1]!, library: key.split("::")[0]!, subjects: f.met, counted: f.met,
      cohortSubjects: 12_728,
    })),
  }, null, new Map())!;
}

/** A metric as `specDocument.ts` derives it: the name to count, and the population it sits under. */
const metric = (countOf: string, base: string | null = null): SpecMetric => ({
  form: "count", subjectUnit: "patients", elementGrain: null, resultType: "Boolean",
  valueShape: "value", countOf, base,
});

describe("the base rules (SPEC-ATTRITION)", () => {
  it("measures the first population against the evaluated total, and names it", () => {
    const m = resolveMetric(set(), metric("Initial Population"), LIB)!;
    expect(m.base).toEqual({ name: ALL_PATIENTS, subjects: 12_728 });
    expect(m.share).toBeCloseTo(11_154 / 12_728);
  });

  it("names that base what the funnel's first bar is called, because it IS that bar", () => {
    // The shipped defect this pins: the Initial Population's base read "0" while the chart above it
    // read 67, and nothing on the page said the two were meant to be the same number. They are one
    // figure — same value, same words — so a reader can see it rather than infer it.
    const m = resolveMetric(set(), metric("Initial Population"), LIB)!;
    const first = funnelFromCounts(set().counts, set().subjects)!.stages[0]!;
    expect(m.base).toEqual({ name: first.name, subjects: first.count });
  });

  it("measures a removal stage against the denominator it removes FROM", () => {
    const m = resolveMetric(set(), metric("Denominator Exclusions"), LIB)!;
    expect(m.removes).toBe(true);
    expect(m.base).toEqual({ name: "Denominator", subjects: 11_154 });
    expect(m.share).toBeCloseTo(782 / 11_154);
  });

  it("never takes a REMOVAL stage as a forward stage's base", () => {
    // The shipped defect. Walking back from `Numerator` found `Denominator Exclusions` first, so the
    // heading read "782 → 6,103 · 780%" — a figure that is not wrong so much as meaningless.
    const m = resolveMetric(set(), metric("Numerator"), LIB)!;
    expect(m.base!.name).not.toContain("Exclusions");
    expect(m.share!).toBeLessThanOrEqual(1);
  });

  it("draws the numerator from what the exclusions LEFT, matching the reported rate", () => {
    // One number, two places. The Numerator section's share and the rate in the document's header are
    // the same quantity against the same base, so they must be computed the same way.
    const m = resolveMetric(set(), metric("Numerator"), LIB)!;
    expect(m.base).toEqual({ name: "Denominator after exclusions", subjects: 11_154 - 782 });
    expect(formatShare(m.share!))
      .toBe(formatShare((funnelFromCounts(set().counts, set().subjects, "proportion")!
        .readout as { value: number }).value));
  });

  it("falls back to the EVALUATED TOTAL when the measure declares no scoring", () => {
    // The regression this guards is the one the old code shipped in reverse: every measure got the
    // PROPORTION chain, so a ratio's figures were measured against stages it does not have and a
    // continuous variable's against a denominator it has none of.
    //
    // The fallback is the evaluated cohort, and it is chosen because it is TRUE regardless of scoring:
    // "6,103 of 12,728 evaluated subjects" is a statement any measure can make. What it must not do is
    // reach into the proportion chain — the Numerator's base here is not `Denominator after exclusions`,
    // because whether this measure even HAS a denominator in that sense is exactly what is undeclared.
    const m = resolveMetric(unscored(), metric("Numerator"), LIB)!;
    expect(m.subjects).toBe(unscored().byKey.get(metricKey(LIB, "Numerator"))!.subjects);
    expect(m.base).toEqual({ name: ALL_PATIENTS, subjects: 12_728 });
    expect(m.removes).toBe(false);
  });

  it("does not call a removal a removal without a scoring that says so", () => {
    const m = resolveMetric(unscored(), metric("Denominator Exclusions"), LIB)!;
    expect(m.removes).toBe(false);
    expect(m.base).toEqual({ name: ALL_PATIENTS, subjects: 12_728 });
  });

  it("measures a criterion against the cohort it was counted among (SPEC-POPULATION-CASCADE)", () => {
    // The criterion sits under the Numerator, so it was counted among the patients ENTERING the
    // Numerator — not against the Numerator, of which it is not a subset.
    const m = resolveMetric(set(), metric("Encounters During Period", "Numerator"), LIB)!;
    expect(m.base).toEqual({ name: "Denominator after exclusions", subjects: 10_372 });
    expect(m.subjects).toBe(5_210);
    expect(m.met).toBe(7_605);
    expect(m.share).toBeCloseTo(5_210 / 10_372);
  });

  it("measures a define reached from several populations against the whole evaluation", () => {
    // COMMON has no single enclosing population, so its share is of everything the measure evaluated —
    // stated, because an unnamed denominator is what the base rule exists to prevent (SPEC-METRIC).
    const m = resolveMetric(set(), metric("Has Excluded Care", null), "Exclusions")!;
    expect(m.base).toEqual({ name: ALL_PATIENTS, subjects: 12_728 });
  });

  it("joins library-qualified, so one name in two libraries is two figures", () => {
    expect(resolveMetric(set(), metric("Has Excluded Care"), "Exclusions")!.subjects).toBe(214);
    expect(resolveMetric(set(), metric("Has Excluded Care"), LIB)).toBeNull();
  });
});

describe("absent figures (SPEC-SAMPLE-METRIC)", () => {
  it("is null with no metric set, so the caller renders nothing rather than a zero", () => {
    expect(resolveMetric(null, metric("Numerator"), LIB)).toBeNull();
  });

  it("is null for a define the set carries no figure for", () => {
    // "No run has produced this" and "the answer is none" are opposite claims.
    expect(resolveMetric(set(), metric("Nothing Named This"), LIB)).toBeNull();
  });

  it("carries the element grain only where there is one", () => {
    expect(resolveMetric(set(), metric("Encounters During Period"), LIB)!.elements).toBe(31_529);
    expect(resolveMetric(set(), metric("Numerator"), LIB)!.elements).toBeNull();
  });
});

describe("stating the base (SPEC-METRIC)", () => {
  it("names the base and its count wherever the figure has no room to", () => {
    const title = metricTitle(resolveMetric(set(), metric("Numerator"), LIB)!, "Numerator");
    expect(title).toContain("6,103 subjects counted (6,900 met)");
    expect(title).toContain("of Denominator after exclusions (10,372)");
  });

  it("says REMOVED FROM for a removal stage rather than OF", () => {
    const title = metricTitle(resolveMetric(set(), metric("Denominator Exclusions"), LIB)!,
      "Denominator Exclusions");
    expect(title).toContain("removed from Denominator");
  });

  it("formats every share the same way, to one decimal", () => {
    // 59% under 58.8% invites a reader to look for a difference that is not there.
    expect(formatShare(0.588)).toBe("58.8%");
    expect(formatShare(1)).toBe("100.0%");
  });
});

describe("removedFrom (SPEC-SCORING-METHODOLOGY)", () => {
  it("sums the exclusion and exception stages, and nothing else", () => {
    expect(removedFrom({
      "Initial Population": 100, "Denominator": 100, "Denominator Exclusions": 5,
      "Denominator Exceptions": 2, "Numerator": 40,
    }, "Denominator", "proportion")).toBe(7);
  });

  it("is zero for a measure that declares no removals", () => {
    expect(removedFrom({ "Denominator": 80, "Numerator": 40 }, "Denominator", "proportion")).toBe(0);
  });

  it("removes nothing at all with no declared scoring", () => {
    // Which population removes from which is a property of the SCORING, so with none declared there is
    // no answer — and a base guessed here would be a denominator the measure may not have.
    expect(removedFrom({ "Denominator": 100, "Denominator Exclusions": 5 }, "Denominator", null)).toBe(0);
  });
});

describe("the population cascade (SPEC-POPULATION-CASCADE)", () => {
  // The defect: a Numerator written as "no HbA1c in the period" is true for every patient without
  // diabetes too. Counted across everyone it read 3,350 against a Denominator of 570 — a rate of 588%.
  // Synthetic figures: 4,000 patients, 590 with diabetes, 20 in hospice.
  function diabetes() {
    return buildMetricSet({
      library: LIB, version: "1.0.0", provenance: "run", generated: "2026-10-22T00:00:00Z",
      subjects: 4_000,
      defines: [
        { define: "Initial Population", library: LIB, subjects: 590, counted: 590, cohortSubjects: 4_000 },
        { define: "Denominator", library: LIB, subjects: 590, counted: 590, cohortSubjects: 590 },
        { define: "Denominator Exclusions", library: LIB, subjects: 60, counted: 20, cohortSubjects: 590 },
        { define: "Numerator", library: LIB, subjects: 3_350, counted: 150, cohortSubjects: 570 },
      ],
    }, "proportion", new Map())!;
  }

  it("reports the rate from counted figures, so it cannot pass 100%", () => {
    const readout = funnelFromCounts(diabetes().counts, 4_000, "proportion")!.readout;
    expect(readout).toEqual({ kind: "rate", value: 150 / 570, numerator: 150, base: 570 });
  });

  it("gives the Numerator's own figure the same number and share as the rate", () => {
    const m = resolveMetric(diabetes(), metric("Numerator"), LIB)!;
    expect(m.subjects).toBe(150);
    expect(m.met).toBe(3_350);
    expect(m.base).toEqual({ name: "Denominator after exclusions", subjects: 570 });
    expect(formatShare(m.share!)).toBe("26.3%");
  });

  it("builds every define's cohort from its placement and the declared scoring", () => {
    const entry = (name: string) => ({ name, library: LIB });
    const element = (define: string) => ({ define, library: LIB, rules: [] });
    const scope = (name: string, concepts: string[] = []) => ({
      kind: "population", name, population: entry(name), concepts: concepts.map(element), rules: [],
    });
    const doc = {
      populations: [
        scope("Initial Population", ["Has Diabetes"]),
        // A population can sit under another as a Concept; it keeps its OWN cascade.
        scope("Denominator", ["Initial Population"]),
        scope("Denominator Exclusions", ["In Hospice Care"]),
        scope("Numerator", ["No HbA1c In Period"]),
      ],
    } as unknown as Parameters<typeof buildMetricCohorts>[0];
    const cohorts = buildMetricCohorts(doc, "proportion");
    const ref = entry;
    expect(cohorts.get(metricKey(LIB, "No HbA1c In Period"))).toEqual({
      name: "Denominator after exclusions",
      allOf: [ref("Initial Population"), ref("Denominator")], noneOf: [ref("Denominator Exclusions")],
    });
    expect(cohorts.get(metricKey(LIB, "In Hospice Care"))!.name).toBe("Denominator");
    expect(cohorts.get(metricKey(LIB, "Has Diabetes"))).toEqual({ name: ALL_PATIENTS, allOf: [], noneOf: [] });
    expect(cohorts.get(metricKey(LIB, "Initial Population"))!.allOf).toEqual([]);
    expect(buildMetricCohorts(doc, null).get(metricKey(LIB, "No HbA1c In Period"))!.allOf).toEqual([]);
  });
});
