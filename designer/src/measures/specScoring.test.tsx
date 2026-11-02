// SPEC-SCORING-METHODOLOGY — the scoring explainer is DERIVED, so what it refuses to say matters as
// much as what it says. A formula is the most authoritative-looking thing on a page; one rendered for
// the wrong scoring type would be believed.

import { describe, expect, it } from "vitest";
import { cascadeOf, parseScoring, populationChain, readoutFromScore, readoutText, removesFrom, scoringLabel, scoringMethodology } from "./specScoring.ts";

const PROPORTION = ["Initial Population", "Denominator", "Denominator Exclusions", "Numerator"];

describe("scoringMethodology", () => {
  it("writes the formula with the terms the measure actually declares", () => {
    expect(scoringMethodology("proportion", PROPORTION)!.formula)
      .toBe("Performance rate = Numerator ÷ (Denominator − Denominator Exclusions)");
  });

  it("names an exception term only for a measure that has one", () => {
    // A formula carrying a clause about a population the measure does not declare states a rule that
    // cannot apply to it.
    expect(scoringMethodology("proportion", ["Denominator", "Numerator"])!.formula)
      .toBe("Performance rate = Numerator ÷ Denominator");
    expect(scoringMethodology("proportion", [...PROPORTION, "Denominator Exceptions"])!.formula)
      .toContain("− Denominator Exclusions − Denominator Exceptions");
  });

  it("states the exception rule only where exceptions exist, because it is the uncheckable part", () => {
    // "Removed only where the patient is NOT in the numerator" is nowhere in the CQL, so a reader
    // cannot recover it — which is exactly why the document has to say it, and why it must not appear
    // for a measure where it means nothing.
    expect(scoringMethodology("proportion", PROPORTION)!.caveat).toBeNull();
    expect(scoringMethodology("proportion", [...PROPORTION, "Denominator Exceptions"])!.caveat)
      .toContain("only ever helps");
  });

  it("says nothing at all unless the scoring type is DECLARED as a proportion", () => {
    // Inferring it from population names cannot separate a proportion from a ratio, and the failure is
    // silent (`coding-standards.md`: raise the gap, do not approximate).
    expect(scoringMethodology(null, PROPORTION)).toBeNull();
    expect(scoringMethodology(undefined, PROPORTION)).toBeNull();
    expect(scoringMethodology("ratio", PROPORTION)).toBeNull();
    expect(scoringMethodology("continuous-variable", PROPORTION)).toBeNull();
  });

  it("says nothing where the declared type and the declared populations contradict", () => {
    // The instance says proportion, the logic declares a ratio's populations. That is a defect to see,
    // not a shape to render a formula around.
    expect(scoringMethodology("proportion", [...PROPORTION, "Numerator Exclusions"])).toBeNull();
    expect(scoringMethodology("proportion", ["Initial Population", "Measure Population"])).toBeNull();
  });

  it("says nothing for a library or a cohort measure", () => {
    expect(scoringMethodology("proportion", [])).toBeNull();
    expect(scoringMethodology("proportion", ["Initial Population"])).toBeNull();
  });

  it("orders the roles the way the formula reads them", () => {
    expect(scoringMethodology("proportion", PROPORTION)!.roles.map((r) => r.name)).toEqual([
      "Initial Population", "Denominator", "Denominator Exclusions", "Numerator",
    ]);
  });

  it("says of the Initial Population that it is NOT the rate's base", () => {
    // The confusion this block exists to prevent: the outer boundary is the most prominent figure on
    // the page and it is not what the rate divides by.
    const initial = scoringMethodology("proportion", PROPORTION)!.roles[0]!;
    expect(initial.effect).toContain("not the rate's base");
  });
});

// ── the scoring model ────────────────────────────────────────────────────────────────────────────
// THE FAILURE THESE EXIST FOR: every display path in the app hard-assumes a proportion. `POP_ORDER`
// is the proportion chain and every measure's figures are measured against it; `funnelFromCounts`
// divides Numerator by Denominator whatever the measure is; the document header prints the literal
// string "proportion" over all of them.
// authoritative and is wrong, which is the whole reason this module refuses rather than guesses.

describe("parseScoring", () => {
  it("accepts the four FHIR scoring codes and nothing else", () => {
    for (const k of ["proportion", "ratio", "continuous-variable", "cohort"] as const) {
      expect(parseScoring(k)).toBe(k);
    }
    // Absent and unrecognized are ONE answer, because neither is a shape this app can state.
    for (const v of [null, undefined, "", "Proportion", "continuous variable", "rate"]) {
      expect(parseScoring(v)).toBeNull();
    }
  });
});

describe("populationChain", () => {
  it("gives each scoring type the chain it actually reports through", () => {
    expect(populationChain("proportion")).toContain("Denominator Exceptions");
    // A ratio's numerator has its own exclusions and no exceptions.
    expect(populationChain("ratio")).toContain("Numerator Exclusions");
    expect(populationChain("ratio")).not.toContain("Denominator Exceptions");
    // A continuous variable narrows to a Measure Population, and has no numerator at all.
    expect(populationChain("continuous-variable")).toEqual(
      ["Initial Population", "Measure Population", "Measure Population Exclusions"]);
    expect(populationChain("continuous-variable")).not.toContain("Numerator");
    // A cohort narrows nothing — it reports one count.
    expect(populationChain("cohort")).toEqual(["Initial Population"]);
  });

  it("has NO chain for an undeclared scoring", () => {
    // Not an empty chain — no chain. A caller must be able to tell "this measure reports nothing" from
    // "we do not know what this measure reports", because only the second is a reason to say so.
    expect(populationChain(null)).toBeNull();
  });
});

describe("removesFrom", () => {
  it("takes a ratio's Numerator Exclusions off the NUMERATOR, never the denominator", () => {
    // The failure this guards against: a rule that matches the substring "Exclusions" subtracts it from
    // the denominator, which is a subtraction no ratio measure performs.
    expect(removesFrom("ratio", "Numerator Exclusions")).toBe("Numerator");
    expect(removesFrom("proportion", "Numerator Exclusions")).toBeNull();
    expect(removesFrom("proportion", "Denominator Exclusions")).toBe("Denominator");
    expect(removesFrom("continuous-variable", "Denominator Exclusions")).toBeNull();
    expect(removesFrom("continuous-variable", "Measure Population Exclusions")).toBe("Measure Population");
  });

  it("removes nothing with no declared scoring", () => {
    expect(removesFrom(null, "Denominator Exclusions")).toBeNull();
  });
});

describe("readoutFromScore — the client's score, never computed here (RUN-CLIENT-MEASURE-SCORE)", () => {
  it("a proportion reports the client's rate with its own counted numerator and base", () => {
    expect(readoutFromScore({ scoring: "proportion", kind: "rate", value: 38 / 75, numerator: 38, base: 75 }))
      .toEqual({ kind: "rate", value: 38 / 75, numerator: 38, base: 75 });
  });

  it("a ratio reports both counts and never a percentage", () => {
    const r = readoutFromScore({ scoring: "ratio", kind: "ratio", value: 0.5, numerator: 40, base: 80 });
    expect(r).toEqual({ kind: "ratio", numerator: 40, denominator: 80 });
    expect(readoutText(r)!.caption).toMatch(/not a subset/);
  });

  it("a cohort reports one count", () => {
    expect(readoutFromScore({ scoring: "cohort", kind: "count", population: "Initial Population", numerator: 100 }))
      .toEqual({ kind: "count", population: "Initial Population", subjects: 100 });
  });

  it("a continuous variable REFUSES, and says why", () => {
    // Printing the Measure Population's own count under a "performance rate" label would answer a
    // different question with equal confidence, which is the failure this module exists to stop.
    const r = readoutFromScore({ scoring: "continuous-variable", kind: "unsupported", why: "not scored here" });
    expect(r).toMatchObject({ kind: "unsupported", scoring: "continuous-variable" });
    expect(readoutText(r)!.figure).toBe("—");
    expect(readoutText(r)!.caption).toBe("not scored here");
  });

  it("reports nothing without a score, or with an empty base", () => {
    expect(readoutFromScore(null)).toBeNull();
    expect(readoutFromScore({ scoring: "proportion", kind: "rate", numerator: 0, base: 0 })).toBeNull();
    expect(readoutText(null)).toBeNull();
  });
});

describe("scoringLabel — what the document header prints", () => {
  it("prints the declared type, and NOTHING where none is declared", () => {
    // The slot held the literal "proportion" for every measure: a false statement in a clinical
    // document, printed with the same authority as the FHIR version beside it.
    expect(scoringLabel("proportion")).toBe("proportion");
    expect(scoringLabel("continuous-variable")).toBe("continuous variable");
    expect(scoringLabel(null)).toBeNull();
    expect(scoringLabel("nonsense")).toBeNull();
  });
});

describe("cascadeOf (SPEC-POPULATION-CASCADE)", () => {
  const declared = new Set([...PROPORTION, "Denominator Exceptions"]);

  it("counts the Numerator inside the Denominator, less its exclusions", () => {
    // HL7 CQF Measures: each population is a subset of the one before it. The CQL does not say so.
    expect(cascadeOf("proportion", "Numerator", declared)).toEqual({
      allOf: ["Initial Population", "Denominator"], noneOf: ["Denominator Exclusions"],
      label: "Denominator after exclusions",
    });
  });

  it("removes an exception only outside the Numerator", () => {
    expect(cascadeOf("proportion", "Denominator Exceptions", declared)).toEqual({
      allOf: ["Initial Population", "Denominator"], noneOf: ["Denominator Exclusions", "Numerator"],
      label: "Denominator after exclusions, outside the Numerator",
    });
  });

  it("counts the exclusions inside the Denominator they remove from", () => {
    expect(cascadeOf("proportion", "Denominator Exclusions", declared)!.allOf)
      .toEqual(["Initial Population", "Denominator"]);
  });

  it("names only populations the measure declares", () => {
    expect(cascadeOf("proportion", "Numerator", new Set(["Initial Population", "Denominator", "Numerator"])))
      .toEqual({ allOf: ["Initial Population", "Denominator"], noneOf: [], label: "Denominator" });
  });

  it("counts the Initial Population among everyone, which the caller names", () => {
    expect(cascadeOf("proportion", "Initial Population", declared))
      .toEqual({ allOf: [], noneOf: [], label: null });
  });

  it("nests a ratio's Numerator in the Initial Population, not the Denominator", () => {
    const ratio = new Set(["Initial Population", "Denominator", "Numerator", "Numerator Exclusions"]);
    expect(cascadeOf("ratio", "Numerator", ratio)!.allOf).toEqual(["Initial Population"]);
    expect(cascadeOf("ratio", "Numerator Exclusions", ratio)!.allOf)
      .toEqual(["Initial Population", "Numerator"]);
  });

  it("has no cascade without a declared scoring, or for a define that is not a population", () => {
    expect(cascadeOf(null, "Numerator", declared)).toBeNull();
    expect(cascadeOf("proportion", "Has Diabetes", declared)).toBeNull();
  });
});
