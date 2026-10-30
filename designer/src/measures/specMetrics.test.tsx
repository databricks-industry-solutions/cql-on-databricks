// SPEC-ATTRITION / SPEC-METRIC-REF / SPEC-POPULATION-CASCADE — the base rules, and the arithmetic every
// figure in the document is divided by.
//
// A wrong base is the worst available failure here — the figure is precise, plausible, and about the
// wrong denominator — so the rules are pinned rather than the pixels.
//
// POPULATIONS ARE COUNTED AS WRITTEN (amended 2026-10-29). A population's figure is its own define's
// subjects, a share of every evaluated patient. Only the score is cascaded, and the CLIENT computes it
// (RUN-CLIENT-MEASURE-SCORE); it arrives on the metrics payload as `score`. A criterion is still counted
// among the patients entering the population it sits under.

import { describe, expect, it } from "vitest";
import { ALL_PATIENTS, funnelFromCounts } from "./model.ts";
import type { SpecDocument, SpecMetric } from "./specDocument.ts";
import {
  buildMetricCohorts, buildMetricSet, formatShare, metricFunnel, metricKey, metricTitle, resolveMetric,
} from "./specMetrics.ts";

const LIB = "ExampleMeasure";

/** SPEC-POPULATION-CASCADE — the criterion below sits under the Numerator, so it is counted among the
 *  patients entering it. Only the cohort's NAME travels in this map; its size is on the payload. */
const COHORTS = new Map([[metricKey(LIB, "Encounters During Period"),
  { name: "Denominator after exclusions", allOf: [], noneOf: [] }]]);

/** A proportion measure as the server now answers it: every population counted among all 12,728
 *  evaluated subjects, the criterion among the patients entering the Numerator, and the client's score. */
function set() {
  return buildMetricSet({
    library: LIB, version: "1.0.000", provenance: "run", generated: "2026-08-02T00:00:00Z",
    subjects: 12_728,
    defines: [
      { define: "Initial Population", library: LIB, subjects: 11_154, counted: 11_154, cohortSubjects: 12_728 },
      { define: "Denominator", library: LIB, subjects: 11_154, counted: 11_154, cohortSubjects: 12_728 },
      { define: "Denominator Exclusions", library: LIB, subjects: 790, counted: 790, cohortSubjects: 12_728 },
      { define: "Numerator", library: LIB, subjects: 6_900, counted: 6_900, cohortSubjects: 12_728 },
      { define: "Encounters During Period", library: LIB, subjects: 7_605, counted: 5_210,
        cohortSubjects: 10_372, elements: 31_529 },
      { define: "Has Excluded Care", library: "Exclusions", subjects: 214, counted: 214, cohortSubjects: 12_728 },
    ],
    score: { scoring: "proportion", kind: "rate", value: 6_103 / 10_372, numerator: 6_103, base: 10_372 },
  }, "proportion", COHORTS)!;
}

/** A metric as `specDocument.ts` derives it: the name to count, and the population it sits under. */
const metric = (countOf: string, base: string | null = null): SpecMetric => ({
  form: "count", subjectUnit: "patients", elementGrain: null, resultType: "Boolean",
  valueShape: "value", countOf, base,
});

describe("the base rules (SPEC-ATTRITION)", () => {
  it("measures every population against the evaluated total, and names it", () => {
    for (const name of ["Initial Population", "Denominator", "Denominator Exclusions", "Numerator"]) {
      const m = resolveMetric(set(), metric(name), LIB)!;
      expect(m.base).toEqual({ name: ALL_PATIENTS, subjects: 12_728 });
      expect(m.removes).toBe(false);
    }
    expect(resolveMetric(set(), metric("Numerator"), LIB)!.share).toBeCloseTo(6_900 / 12_728);
  });

  it("names that base what the funnel's first bar is called, because it IS that bar", () => {
    const m = resolveMetric(set(), metric("Initial Population"), LIB)!;
    const first = funnelFromCounts(set().counts, set().subjects)!.stages[0]!;
    expect(m.base).toEqual({ name: first.name, subjects: first.count });
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
    const m = resolveMetric(set(), metric("Has Excluded Care", null), "Exclusions")!;
    expect(m.base).toEqual({ name: ALL_PATIENTS, subjects: 12_728 });
  });

  it("joins library-qualified, so one name in two libraries is two figures", () => {
    expect(resolveMetric(set(), metric("Has Excluded Care"), "Exclusions")!.subjects).toBe(214);
    expect(resolveMetric(set(), metric("Has Excluded Care"), LIB)).toBeNull();
  });
});

describe("the score (RUN-CLIENT-MEASURE-SCORE)", () => {
  it("is the client's, with its own cascaded numerator and base", () => {
    // The raw Numerator (6,900) holds patients outside the Denominator; the score counts 6,103 of the
    // 10,372 left after exclusions. The two need not add up, and the funnel names the score's base.
    expect(set().readout).toEqual({ kind: "rate", value: 6_103 / 10_372, numerator: 6_103, base: 10_372 });
    const num = metricFunnel(set())!.stages.find((s) => s.name === "Numerator")!;
    expect(num).toMatchObject({ count: 6_900, rateBase: 10_372 });
    expect(formatShare(num.rate!)).toBe("58.8%");
  });

  it("is absent when the payload carries none", () => {
    const bare = buildMetricSet({
      library: LIB, version: "1", provenance: "run", generated: "", subjects: 10, defines: [],
    }, null, new Map())!;
    expect(bare.readout).toBeNull();
  });

  it("states an empty base as no rate", () => {
    const empty = buildMetricSet({
      library: LIB, version: "1", provenance: "run", generated: "", subjects: 10, defines: [],
      score: { scoring: "proportion", kind: "rate", numerator: 0, base: 0 },
    }, "proportion", new Map())!;
    expect(empty.readout).toBeNull();
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
    expect(title).toContain("6,900 subjects counted (6,900 met)");
    expect(title).toContain(`of ${ALL_PATIENTS} (12,728)`);
  });

  it("formats every share the same way, to one decimal", () => {
    // 59% under 58.8% invites a reader to look for a difference that is not there.
    expect(formatShare(0.588)).toBe("58.8%");
    expect(formatShare(1)).toBe("100.0%");
  });
});

describe("the cohorts a metrics request names (SPEC-POPULATION-CASCADE)", () => {
  it("counts populations as written and criteria among the patients entering their population", () => {
    const entry = (name: string) => ({ name, library: LIB });
    const element = (define: string) => ({ define, library: LIB, rules: [] });
    const scope = (name: string, concepts: string[] = []) => ({
      kind: "population", name, population: entry(name), concepts: concepts.map(element), rules: [],
    });
    const doc = {
      populations: [
        scope("Initial Population", ["Has Diabetes"]),
        // A population can sit under another as a Concept; it is still counted as written.
        scope("Denominator", ["Initial Population"]),
        scope("Denominator Exclusions", ["In Hospice Care"]),
        scope("Numerator", ["No HbA1c In Period"]),
      ],
    } as unknown as SpecDocument;
    const cohorts = buildMetricCohorts(doc, "proportion");
    const everyone = { name: ALL_PATIENTS, allOf: [], noneOf: [] };
    for (const population of ["Initial Population", "Denominator", "Denominator Exclusions", "Numerator"]) {
      expect(cohorts.get(metricKey(LIB, population))).toEqual(everyone);
    }
    expect(cohorts.get(metricKey(LIB, "No HbA1c In Period"))).toEqual({
      name: "Denominator after exclusions",
      allOf: [entry("Initial Population"), entry("Denominator")], noneOf: [entry("Denominator Exclusions")],
    });
    expect(cohorts.get(metricKey(LIB, "In Hospice Care"))!.name).toBe("Denominator");
    expect(cohorts.get(metricKey(LIB, "Has Diabetes"))).toEqual(everyone);
    expect(buildMetricCohorts(doc, null).get(metricKey(LIB, "No HbA1c In Period"))!.allOf).toEqual([]);
  });
});
