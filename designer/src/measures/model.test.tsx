// APP-MEASURE-DOC / APP-STEP-INTO-PATIENT / ACC-KA-TRACE (PROC-TDD) — the Designer
// workspace's pure domain helpers: the Live-run → funnel + Patient Lens transforms, and the KA
// up-trace (deciding criterion → Pathway recommendation).
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import type { RunResult } from "../wire.ts";
import {
  ALL_PATIENTS, buildFunnel, funnelFromCounts, patientRows, decidingCriterion, pathwayFor, isTruthy,
  MW_MODULES, parseNavTab, loadPanes, savePanes, removedFrom,
} from "./model.ts";

const RUN: RunResult = {
  ok: true,
  counts: { "Initial Population": 100, "Denominator": 80, "Denominator Exclusions": 5, "Numerator": 40 },
  results: {
    p1: { "Initial Population": true, "Denominator": true, "Numerator": true },
    p2: { "Initial Population": true, "Denominator": true, "Numerator": false },
    p3: { "Initial Population": true, "Denominator": true, "Denominator Exclusions": true, "Numerator": false },
    p4: { "Initial Population": true, "Denominator": false, "Numerator": false },
    p5: { "Initial Population": false },
  },
};

describe("buildFunnel (SVC-RUN-EVAL)", () => {
  it("returns null for an absent or failed run", () => {
    expect(buildFunnel(null)).toBeNull();
    expect(buildFunnel({ ok: false })).toBeNull();
  });

  it("bases the funnel on the largest population count, not the subject sample", () => {
    // A run's `results` is a SAMPLE of members (5 here), not the cohort, so this caller states the
    // largest population as the total. The choice lives here because only this caller knows that.
    const f = buildFunnel(RUN)!;
    expect(f.total).toBe(100);
    expect(f.stages[0]).toMatchObject({ name: ALL_PATIENTS, count: 100, pct: 100 });
    const denom = f.stages.find((s) => s.name === "Denominator")!;
    expect(denom.pct).toBe(80);
  });

  it("attaches removals to the population they come OUT of, per the DECLARED scoring", () => {
    // A population is a removal when the scoring says it removes from something — not when its name
    // contains "Exclusions", which called a ratio's Numerator Exclusions a denominator removal. And a
    // removal is a SUBSET of its base, so it is a segment of that bar and never a row of its own.
    const f = buildFunnel(RUN, "proportion")!;
    expect(f.stages.map((s) => s.name)).toEqual([
      ALL_PATIENTS, "Initial Population", "Denominator", "Numerator",
    ]);
    const den = f.stages.find((s) => s.name === "Denominator")!;
    expect(den.removals!.map((r) => r.name)).toEqual(["Denominator Exclusions"]);
    expect(f.stages.find((s) => s.name === "Numerator")!.removals).toBeUndefined();
    // Undeclared: nothing removes from anything, so every population is a plain bar — including the
    // exclusions, which with no formula this cannot claim came out of the denominator.
    const bare = buildFunnel(RUN)!;
    expect(bare.stages.map((s) => s.name)).toContain("Denominator Exclusions");
    expect(bare.stages.every((s) => s.removals === undefined)).toBe(true);
  });

  it("makes the solid segment's edge the rate's own base", () => {
    // The quantity the Numerator is divided by is visible as the boundary between the solid head and
    // the hatched tail, so it needs no separate row.
    const f = buildFunnel(RUN, "proportion")!;
    const den = f.stages.find((s) => s.name === "Denominator")!;
    expect(den.remaining).toBe(75);                     // denominator 80 LESS its 5 exclusions
    expect(den.remaining).toBe((f.readout as { base: number }).base);
    expect(den.remaining).toBe(f.stages.find((s) => s.name === "Numerator")!.rateBase);
  });

  it("tiles consecutive removals across the tail of their base's bar", () => {
    // The segments run left to right in declaration order, starting where the kept remainder ends, and
    // offsets accumulate the ROUNDED shares so no hairline gap opens between them.
    const f = buildFunnel({
      ok: true,
      counts: {
        "Initial Population": 100, "Denominator": 80, "Denominator Exclusions": 6,
        "Denominator Exceptions": 3, "Numerator": 40,
      },
    }, "proportion")!;
    const den = f.stages.find((s) => s.name === "Denominator")!;
    expect(den).toMatchObject({ pct: 80, remaining: 71, remainingPct: 71 });
    expect(den.removals).toEqual([
      { name: "Denominator Exclusions", count: 6, pct: 6, offsetPct: 71 },   // spans 71 → 77
      { name: "Denominator Exceptions", count: 3, pct: 3, offsetPct: 77 },   // spans 77 → 80
    ]);
    expect(f.stages.find((s) => s.name === "Numerator")!.rateBase).toBe(71);
  });

  it("leaves a population with no removals undivided", () => {
    const f = buildFunnel({
      ok: true, counts: { "Initial Population": 100, "Denominator": 80, "Numerator": 40 },
    }, "proportion")!;
    const den = f.stages.find((s) => s.name === "Denominator")!;
    expect(den.removals).toBeUndefined();
    expect(den.remaining).toBeUndefined();
  });

  it("scores against the denominator AFTER exclusions, not the denominator's own count", () => {
    // SPEC-REPORTED-RESULT — an eCQM proportion measure's performance rate is the numerator over the
    // denominator LESS its exclusions and exceptions. A narration can state this in as many
    // words ("the Denominator's own count is not the rate's base"), and the document now prints the
    // rate at its head beside that sentence, so `40 / 80` was a header contradicting its own prose.
    // SPEC-SCORING-METHODOLOGY — and it only reports a rate at all because the measure is DECLARED a
    // proportion. Undeclared, the readout is null and the header states nothing rather than a figure
    // whose formula it cannot vouch for.
    const f = buildFunnel(RUN, "proportion")!;
    expect(f.readout).toMatchObject({ kind: "rate", base: 75, numerator: 40 });
    expect((f.readout as { value: number }).value).toBeCloseTo(40 / 75);
    expect(buildFunnel(RUN)!.readout).toBeNull();
  });

  it("labels the Numerator with the measure's SCORE, not its share of the cohort", () => {
    // SPEC-REPORTED-RESULT — a proportion's score is a settled formula, so the funnel states it rather
    // than the Numerator's share of everyone evaluated. The shipped bar read `Numerator 44.8%` against
    // a cohort of 400 while the measure scored 85.2%: a figure the measure does not report, printed
    // where its rate belongs.
    const f = buildFunnel(RUN, "proportion")!;
    const num = f.stages.find((s) => s.name === "Numerator")!;
    expect(num.rate).toBeCloseTo(40 / 75);              // denominator 80 LESS its 5 exclusions
    expect(num.pct).toBe(40);                           // share of the cohort — still drives the width
    expect(num.rate).toBe((f.readout as { value: number }).value);
    // And the base is CARRIED, so the view can print "53.3% of 75" instead of hiding the base in a
    // `title` attribute where a reader comparing it to the bar's own width will never find it.
    expect(num.rateBase).toBe(75);
    // Only the Numerator carries one: no other stage has a base the declared scoring settles.
    expect(f.stages.filter((s) => s.rate != null).map((s) => s.name)).toEqual(["Numerator"]);
  });

  it("carries no rate when the scoring is undeclared", () => {
    // Undeclared scoring has no formula to bake in, so the funnel states the share it can defend
    // rather than one it cannot (SPEC-SCORING-METHODOLOGY).
    const f = buildFunnel(RUN)!;
    expect(f.stages.every((s) => s.rate == null)).toBe(true);
  });

  it("states the total it was given and never substitutes the first population for it", () => {
    // The shipped defect. `Math.max(subjects, firstPopulation)` looked defensive and was not: when the
    // evaluated total came back 0 (the Patient binding was called on a retired signature), the max
    // relabelled the Initial Population count as "All patients" — so the header reported 67 while the
    // criterion beside it reported its base as 0, and nothing on the page could explain the gap.
    const counts = { "Initial Population": 100, "Denominator": 80, "Numerator": 40 };
    expect(funnelFromCounts(counts, 128)!.stages[0])
      .toMatchObject({ name: ALL_PATIENTS, count: 128 });
    // With no cohort there is no funnel — an absence the caller explains, not one this papers over.
    expect(funnelFromCounts(counts, 0)).toEqual({ total: 0, stages: [], readout: null });
  });

  it("scores against the plain denominator when a measure declares no removals", () => {
    const f = buildFunnel({
      ok: true, counts: { "Initial Population": 100, "Denominator": 80, "Numerator": 40 },
    }, "proportion")!;
    expect((f.readout as { value: number }).value).toBeCloseTo(0.5);
  });

  it("refuses a rate for a scoring type that does not have one", () => {
    // THE FAILURE THIS EXISTS FOR: every measure got `Numerator / Denominator` regardless of what it
    // was, so a ratio measure's header read as a percentage of a denominator its numerator is not a
    // subset of. A ratio reports both counts; a cohort reports one; a continuous variable reports a
    // distribution we cannot compute and says so.
    const counts = { "Initial Population": 100, "Denominator": 80, "Numerator": 40 };
    expect(buildFunnel({ ok: true, counts }, "ratio")!.readout)
      .toEqual({ kind: "ratio", numerator: 40, denominator: 80 });
    expect(buildFunnel({ ok: true, counts }, "cohort")!.readout)
      .toMatchObject({ kind: "count", population: "Initial Population", subjects: 100 });
    expect(buildFunnel({ ok: true, counts }, "continuous-variable")!.readout)
      .toMatchObject({ kind: "unsupported", scoring: "continuous-variable" });
  });

  it("never takes a ratio's Numerator Exclusions off the denominator", () => {
    // The name-shaped rule this replaces subtracted anything containing "Exclusions" from the
    // denominator, which for a ratio is a subtraction no measure performs.
    const counts = {
      "Initial Population": 100, "Denominator": 80, "Numerator": 40, "Numerator Exclusions": 10,
    };
    expect(removedFrom(counts, "Denominator", "ratio")).toBe(0);
    expect(removedFrom(counts, "Numerator", "ratio")).toBe(10);
    expect(removedFrom(counts, "Denominator", "proportion")).toBe(0);
  });
});

describe("patientRows classification (APP-STEP-INTO-PATIENT)", () => {
  const rows = patientRows(RUN);
  const by = (id: string) => rows.find((r) => r.id === id)!;

  it("classifies each member's landing from their per-define values", () => {
    expect(by("p1").landing).toBe("numerator");
    expect(by("p2").landing).toBe("denominator");
    expect(by("p3").landing).toBe("excluded");
    expect(by("p4").landing).toBe("population");
    expect(by("p5").landing).toBe("out");
  });

  it("records the population defines a member reached, in funnel order", () => {
    expect(by("p1").reached).toEqual(["Initial Population", "Denominator", "Numerator"]);
    expect(by("p5").reached).toEqual([]);
  });
});

describe("decidingCriterion — the criterion that placed a member (ACC-KA-TRACE)", () => {
  const rows = patientRows(RUN);
  const by = (id: string) => rows.find((r) => r.id === id)!;

  it("points at the met target, the firing exclusion, or the failed gate", () => {
    expect(decidingCriterion(by("p1"))).toEqual({ define: "Numerator", met: true });
    expect(decidingCriterion(by("p2"))).toEqual({ define: "Numerator", met: false });
    expect(decidingCriterion(by("p3"))).toEqual({ define: "Denominator Exclusions", met: true });
    expect(decidingCriterion(by("p4"))).toEqual({ define: "Denominator", met: false });
    expect(decidingCriterion(by("p5"))).toEqual({ define: "Initial Population", met: false });
  });
});

describe("pathwayFor — climb to the guideline recommendation (ACC-KA-TRACE up)", () => {
  it("returns the authored recommendation for a linked define", () => {
    const rec = pathwayFor("Numerator")!;
    expect(rec.rec).toBe("3.2");
    expect(rec.guideline).toBeTruthy();
    expect(rec.statement.length).toBeGreaterThan(0);
  });

  it("returns null when no Pathway link is authored", () => {
    expect(pathwayFor("Has Example Condition")).toBeNull();
  });
});

// The offline Test-deck synthetic run moved to the BFF (APP-NO-UI-MOCK): see the backend test
// `cql_databricks_designer/tests/test_sample_deck.py`. The SPA fetches it via `run-client.ts` `sampleRun`.

describe("MW_MODULES registry", () => {
  it("has unique module ids", () => {
    const ids = MW_MODULES.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("isTruthy mirrors the backend population membership test", () => {
  it("treats non-zero numbers, non-empty strings/collections as members", () => {
    expect(isTruthy(true)).toBe(true);
    expect(isTruthy(1)).toBe(true);
    expect(isTruthy(0)).toBe(false);
    expect(isTruthy("false")).toBe(false);
    expect(isTruthy([])).toBe(false);
    expect(isTruthy([1])).toBe(true);
  });
});

// ── APP-MEASURE-FLOW / APP-PANE-STATE ───────────────────────────────────────────────────────
// The left pane's arrangement is a URL param and its width is a stored preference, so both are parsed
// rather than trusted. THE FAILURE THESE EXIST FOR: a `?nav=` from a future version, a hand-edited
// link, or a localStorage value written by an older build must not put the pane in a state it has no
// rendering for — it falls back, silently and predictably.
//
// They are pinned here, in the pure model, deliberately: there is no `MeasureWorkspace.test.tsx` and
// standing one up needs five fetches and a stream mocked. Putting the parse and the persistence in
// `model.ts` is what makes the URL and storage contracts testable without it.

describe("the nav tab is parsed, never trusted (SPEC-NAV-COMPOSITION)", () => {
  it("round-trips the two arrangements", () => {
    expect(parseNavTab("outline")).toBe("outline");
    expect(parseNavTab("composition")).toBe("composition");
  });

  it("falls back to the Outline for anything else", () => {
    // Absent is the common case — `?nav=` is omitted for the default, so the URL stays short.
    // `flow` is in this list on purpose: the Flow tab is retired, and a link somebody sent last week
    // still has to land its reader somewhere real rather than on a blank pane.
    for (const v of [null, undefined, "", "flow", "COMPOSITION", "../etc"]) {
      expect(parseNavTab(v)).toBe("outline");
    }
  });
});

describe("pane sizes persist as a preference (APP-PANE-STATE)", () => {
  // This jsdom setup ships `localStorage` as a bare `{}` with no Storage API on it, so every read in
  // `model.ts` throws and is swallowed by its own try/catch. That is the right PRODUCTION behaviour
  // (private mode does the same) and it makes the parse untestable, so the store is stubbed here. The
  // stub is a real Map, not a mock: what these pin is the parsing, and a mock returning canned values
  // would pin nothing.
  let store: Map<string, string>;
  beforeEach(() => {
    store = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v); },
      removeItem: (k: string) => { store.delete(k); },
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("defaults both panes to normal, which is the responsive width", () => {
    expect(loadPanes()).toEqual({ nav: "normal", workbench: "normal" });
  });

  it("round-trips a saved pair", () => {
    savePanes({ nav: "expanded", workbench: "collapsed" });
    expect(loadPanes()).toEqual({ nav: "expanded", workbench: "collapsed" });
  });

  it("rejects a value it has no rendering for, per pane", () => {
    store.set("cql-databricks:panes", JSON.stringify({ nav: "enormous", workbench: "collapsed" }));
    expect(loadPanes()).toEqual({ nav: "normal", workbench: "collapsed" });
  });

  it("survives unparseable storage rather than throwing into the render", () => {
    store.set("cql-databricks:panes", "{not json");
    expect(loadPanes()).toEqual({ nav: "normal", workbench: "normal" });
  });

  it("survives a store that is not there at all — private mode, and this very environment", () => {
    vi.stubGlobal("localStorage", {});
    expect(loadPanes()).toEqual({ nav: "normal", workbench: "normal" });
    expect(() => savePanes({ nav: "expanded", workbench: "normal" })).not.toThrow();
  });
});
