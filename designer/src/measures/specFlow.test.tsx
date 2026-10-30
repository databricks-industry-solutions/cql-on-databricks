// APP-COHORT-ALGEBRA — the gates the client asks the warehouse about.
//
// THE FAILURE THIS EXISTS FOR: a second decomposition. `specDocument.ts` already decides what a
// population is made of — its depth-1 Concepts at their lowest common dominator (`SPEC-CONCEPTS` /
// `SPEC-PLACEMENT`) — and the rail states figures ABOUT that decomposition. The moment this module
// works out for itself which defines a population is "really" made of, the rail can attribute a
// departure to a criterion the document does not show, and the two surfaces disagree about the same
// measure. That is the failure `APP-SPEC-MODEL` names, and it would be invisible: both halves would
// look right on their own.
//
// Real ELM, from the same Compiler Service fixture the model tests use.

import { describe, expect, it } from "vitest";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import { buildFlowRequest, partitions, rankedCriteria,
  type FlowGateDto } from "./specFlow.ts";
import { buildSpecDocument } from "./specDocument.ts";
import { buildSpecModel, type SpecLibraryInput } from "./specModel.ts";
import elmFixtures from "./__fixtures__/spec-patterns-elm.json";

const elm = JSON.parse(elmFixtures.root.elm);

/** The synthetic ExampleReadingControl 1.0.0's real ELM — the compiler's own output.
 *
 *  A fixture whose Initial Population HAS Concepts, and for a reason this module cares about: in a
 *  measure whose Initial Population has no Concepts at all, so the base-less
 *  first-stage case — the one the server-side bug lived in — simply does not arise there. A test that
 *  cannot reach the case it is named for passes for the wrong reason. */
function doc() {
  const info = parseElmDefines(elmFixtures.root.elm);
  if (!info) throw new Error("spec-patterns fixture did not parse");
  // No `source`: bodies are recovered by locator slicing and nothing here reads one.
  const root: SpecLibraryInput = { info, elm, source: "" };
  const spec = buildSpecModel({ root, includes: [] });
  return buildSpecDocument(spec, new Map<string, unknown>([[spec.root, elm]]));
}

describe("buildFlowRequest re-reads the document's own decomposition", () => {
  it("asks about exactly the Concepts the Specification renders, in the same libraries", () => {
    const d = doc();
    const gates = buildFlowRequest(d, "proportion");
    for (const gate of gates) {
      const scope = d.populations.find((p) => p.population?.name === gate.stage)!;
      expect(gate.criteria).toEqual(
        scope.concepts.map((c) => ({ name: c.define, library: c.library })));
    }
    // …and it asked about something, or the loop above proved nothing.
    expect(gates.length).toBeGreaterThan(0);
  });

  it("qualifies every criterion by library", () => {
    // Two libraries in one closure routinely declare the same define name, so a bare name is not an
    // address (`SPEC-ANCHOR`). The server resolves (library, define) against the compiler's own record.
    for (const gate of buildFlowRequest(doc(), "proportion")) {
      for (const c of gate.criteria) expect(c.library).toBeTruthy();
      expect(gate.stageLibrary).toBeTruthy();
    }
  });

  it("skips a population with no Concepts rather than asking an unanswerable question", () => {
    // One unfactored boolean has no siblings to attribute a departure to. The server would omit it;
    // not asking is the same answer without the round trip, and the pane says so where the rail was.
    const d = doc();
    const flat = d.populations.filter((p) => p.population && p.concepts.length === 0);
    const asked = new Set(buildFlowRequest(d, "proportion").map((g) => g.stage));
    for (const scope of flat) expect(asked.has(scope.population!.name)).toBe(false);
  });
});

describe("the entering cohort", () => {
  it("enters the first stage from nothing, so the server reads the evaluated cohort", () => {
    // There is no population above the Initial Population for those subjects to have departed from.
    const first = buildFlowRequest(doc(), "proportion").find((g) => g.stage === "Initial Population");
    expect(first).toBeDefined();
    expect(first!.base).toBeUndefined();
  });

  it("enters a removal population from what it REMOVES FROM", () => {
    // `Denominator Exclusions` narrows the people already in the Denominator, not the whole cohort.
    const gates = buildFlowRequest(doc(), "proportion");
    const excl = gates.find((g) => g.stage === "Denominator Exclusions")!;
    expect(excl.base).toBe("Denominator");
  });

  it("skips removals when finding the stage above, as the metric base rule does", () => {
    // Taking `Denominator Exclusions` as the Numerator's base reads as though the exclusions were the
    // people who passed — the "782 → 6,103 · 780%" figure that is not wrong so much as meaningless.
    const num = buildFlowRequest(doc(), "proportion").find((g) => g.stage === "Numerator")!;
    expect(num.base).toBe("Denominator");
  });
});

describe("polarity", () => {
  it("makes an exclusion population the DISJUNCTION and everything else the conjunction", () => {
    // The one thing that differs between the two shapes, and the narrowest claim we can support: the
    // ELM operator that would settle it directly is not derived anywhere (APP-FLOW-SPINE, deferred),
    // so this reads the population's ROLE, which the declared scoring already tells us.
    const gates = buildFlowRequest(doc(), "proportion");
    expect(gates.map((g) => [g.stage, g.polarity])).toEqual([
      ["Initial Population", "all-of"],
      ["Denominator Exclusions", "any-of"],
      ["Numerator", "all-of"],
    ]);
  });
});

describe("asking nothing is a real answer", () => {
  it("asks for no gates at all with no declared scoring", () => {
    // With no scoring there is no chain, so nothing knows which population removes from which — and a
    // guessed base would attribute departures to the wrong cohort.
    expect(buildFlowRequest(doc(), null)).toEqual([]);
    expect(buildFlowRequest(doc(), "nonsense")).toEqual([]);
  });

  it("asks for no gates with no document", () => {
    expect(buildFlowRequest(null, "proportion")).toEqual([]);
  });
});

describe("reading the answer back (APP-DECISIVE-COUNT)", () => {
  const gate: FlowGateDto = {
    stage: "Initial Population", library: "Demo", polarity: "all-of",
    entered: 149_882, reached: 42_318, passed: 42_318, multiple: 7_440,
    criteria: [
      { library: "Demo", define: "In Age Range", decisive: 9_830, anyCause: 21_096 },
      { library: "Demo", define: "Has Example Condition", decisive: 38_204, anyCause: 58_332 },
      { library: "Enc", define: "Encounters During Period", decisive: 52_090, anyCause: 71_415 },
    ],
  };

  it("ranks by the decisive count, because the rail's question is what decides most", () => {
    // "One criterion decides 71% of your failures" is a RANKING, and a rail in declaration order
    // buries it.
    expect(rankedCriteria(gate).map((c) => c.define))
      .toEqual(["Encounters During Period", "Has Example Condition", "In Age Range"]);
    // Non-mutating — the gate is shared with whatever else renders it.
    expect(gate.criteria[0]!.define).toBe("In Age Range");
  });

  it("verifies the partition rather than assuming it", () => {
    // Guaranteed by the SQL's construction, and still checked: a renderer that sums the parts into a
    // total should not print a figure that has quietly stopped adding up.
    expect(partitions(gate)).toBe(true);
    expect(partitions({ ...gate, multiple: 0 })).toBe(false);
  });

  it("does not let the any-cause figures pass for a partition", () => {
    // They sum to 150,843 against a cohort of 149,882 — they overlap by construction, which is why the
    // decisive count is the one the rail leads with.
    const anyCause = gate.criteria.reduce((s, c) => s + c.anyCause, 0);
    expect(anyCause).toBeGreaterThan(gate.entered);
  });
});
