// SPEC-SCORING-METHODOLOGY — the scoring explainer is DERIVED, so what it refuses to say matters as
// much as what it says. A formula is the most authoritative-looking thing on a page; one rendered for
// the wrong scoring type would be believed.

import { describe, expect, it } from "vitest";
import { parseScoring, populationChain, readoutOf, readoutText, removesFrom, scoringLabel, scoringMethodology } from "./specScoring.ts";

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
// THE FAILURE THESE EXIST FOR: every display path in the app hard-assumed a proportion. `POP_ORDER`
// was the proportion chain and every measure's figures were measured against it; `funnelFromCounts`
// divided Numerator by Denominator whatever the measure was; the document header printed the literal
// string "proportion" over all of them. A ratio rendered with a proportion's formula reads
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
    // The bug this replaces, precisely: the old rule matched the substring "Exclusions" and subtracted
    // it from the denominator, which is a subtraction no ratio measure performs.
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

describe("readoutOf — the terminal varies with scoring, the spine does not (APP-FLOW-GRAIN)", () => {
  const counts = {
    "Initial Population": 100, "Denominator": 80, "Denominator Exclusions": 5, "Numerator": 40,
  };

  it("a proportion reports a rate over the denominator AFTER its removals", () => {
    expect(readoutOf("proportion", counts)).toEqual({
      kind: "rate", value: 40 / 75, numerator: 40, base: 75,
    });
  });

  it("a ratio reports both counts and never a percentage", () => {
    // Its numerator is not a subset of its denominator, so a percentage of one by the other is not a
    // quantity that means anything.
    expect(readoutOf("ratio", counts)).toEqual({ kind: "ratio", numerator: 40, denominator: 80 });
    expect(readoutText(readoutOf("ratio", counts))!.caption).toMatch(/not a subset/);
  });

  it("a cohort reports one count", () => {
    expect(readoutOf("cohort", counts))
      .toEqual({ kind: "count", population: "Initial Population", subjects: 100 });
  });

  it("a continuous variable REFUSES, and says why", () => {
    // It reports a distribution of its Measure Observation. The observation is a CQL function, so the
    // pipeline materializes no per-subject relation for it and there is nothing to aggregate — a probe
    // (APP-PROBE). Refusing with the reason is the honest answer; printing the Measure Population's
    // own count under a "performance rate" label would answer a different question with equal
    // confidence, which is the failure this whole module exists to stop.
    const r = readoutOf("continuous-variable", counts);
    expect(r).toMatchObject({ kind: "unsupported", scoring: "continuous-variable" });
    expect(readoutText(r)!.figure).toBe("—");
    expect(readoutText(r)!.caption).toMatch(/probe/);
  });

  it("reports nothing at all for an undeclared scoring", () => {
    expect(readoutOf(null, counts)).toBeNull();
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
