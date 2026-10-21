// APP-NO-UI-MOCK — which blocks of a Data tab payload wear the sample mark.
//
// This is the rule that makes a MIXED payload permissible at all. The profile is now part read from the
// evaluation (the value profile, the top values, sex and age band) and part invented (code hits, the
// temporal spread, the cuts no table answers), and the only reason that is acceptable rather than
// dishonest is that the mark lands on exactly the invented half. Get it wrong in one direction and real
// figures carry a false warning; wrong in the other and a fabricated distribution is drawn as a result,
// which is the failure `APP-NO-UI-MOCK` exists to prevent and which no screenshot would reveal.

import { describe, expect, it } from "vitest";
import type { DefineProfile } from "../wire.ts";
import { isSampled } from "./Workbench.tsx";

const base = (over: Partial<DefineProfile>): DefineProfile => ({
  library: "ExampleMeasure", define: "Initial Population",
  provenance: "mixed", generated: "2026-08-05T00:00:00Z", ...over,
});

describe("the per-block sample mark (APP-NO-UI-MOCK)", () => {
  it("marks exactly the blocks the payload names, and no others", () => {
    const p = base({ sampled: ["valueSets", "temporal"] });
    expect(isSampled(p, "valueSets")).toBe(true);
    expect(isSampled(p, "temporal")).toBe(true);
    // The blocks that were READ must come back unmarked — a warning on a real figure teaches the
    // reader to discount the mark everywhere, which costs more than it saves.
    expect(isSampled(p, "values")).toBe(false);
    expect(isSampled(p, "sample")).toBe(false);
  });

  it("marks nothing when the payload names nothing", () => {
    const p = base({ provenance: "run", sampled: [] });
    expect(isSampled(p, "values")).toBe(false);
    expect(isSampled(p, "temporal")).toBe(false);
  });

  it("falls back to marking EVERYTHING on a payload with no block list", () => {
    // A payload predating per-block provenance says only `sample`. Reading that as "nothing is
    // sampled" would silently unmark a wholly fabricated profile, so the fallback errs the safe way.
    const p = base({ provenance: "sample", sampled: undefined });
    expect(isSampled(p, "values")).toBe(true);
    expect(isSampled(p, "temporal")).toBe(true);
  });

  it("leaves a wholly-real payload with no block list unmarked", () => {
    const p = base({ provenance: "run", sampled: undefined });
    expect(isSampled(p, "values")).toBe(false);
  });
});
