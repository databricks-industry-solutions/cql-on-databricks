// APP-MEASURE-DOC / APP-STEP-INTO-PATIENT / ACC-KA-TRACE (PROC-TDD) — the Designer
// workspace's pure domain helpers: the Live-run → funnel + Patient Lens transforms, and the KA
// up-trace (deciding criterion → Pathway recommendation).
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import type { RunResult } from "../wire.ts";
import {
  ALL_PATIENTS, buildFunnel, funnelFromCounts, patientRows, decidingCriterion, pathwayFor, isTruthy,
  MW_MODULES, parseNavTab, loadPanes, savePanes,
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

  it("reports no score: a run carries none", () => {
    expect(buildFunnel(RUN)!.readout).toBeNull();
    expect(buildFunnel(RUN)!.stages.every((s) => s.rate == null)).toBe(true);
  });
});

describe("funnelFromCounts (SPEC-POPULATION-CASCADE, RUN-CLIENT-MEASURE-SCORE)", () => {
  const counts = { "Initial Population": 100, "Denominator": 80, "Denominator Exclusions": 5, "Numerator": 40 };
  const rate = { kind: "rate" as const, value: 38 / 75, numerator: 38, base: 75 };

  it("draws every population as a plain bar, counted as written", () => {
    // Raw Denominator Exclusions can hold patients who were never in the Denominator, so drawing them
    // inside the Denominator's bar would subtract two unrelated sets. Every population is its own bar.
    const f = funnelFromCounts(counts, 128, rate)!;
    expect(f.stages.map((s) => s.name)).toEqual([
      ALL_PATIENTS, "Initial Population", "Denominator", "Denominator Exclusions", "Numerator",
    ]);
    expect(f.stages.every((s) => !("removals" in s) && !("remaining" in s))).toBe(true);
    expect(f.stages.find((s) => s.name === "Denominator Exclusions")).toMatchObject({ count: 5, pct: 3.9 });
  });

  it("carries the client's score on the Numerator and never divides again", () => {
    // SPEC-REPORTED-RESULT — the score's numerator and base are the client's cascaded counts (38 of 75),
    // not the raw Numerator over anything shown here.
    const f = funnelFromCounts(counts, 128, rate)!;
    const num = f.stages.find((s) => s.name === "Numerator")!;
    expect(num).toMatchObject({ count: 40, rate: 38 / 75, rateBase: 75 });
    expect(f.readout).toBe(rate);
    expect(f.stages.filter((s) => s.rate != null).map((s) => s.name)).toEqual(["Numerator"]);
  });

  it("carries no rate without a score", () => {
    const f = funnelFromCounts(counts, 128, null)!;
    expect(f.readout).toBeNull();
    expect(f.stages.every((s) => s.rate == null)).toBe(true);
  });

  it("states the total it was given and never substitutes the first population for it", () => {
    // The shipped defect. `Math.max(subjects, firstPopulation)` looked defensive and was not: when the
    // evaluated total came back 0 (the Patient binding was called on a retired signature), the max
    // relabelled the Initial Population count as "All patients" — so the header reported 67 while the
    // criterion beside it reported its base as 0, and nothing on the page could explain the gap.
    const plain = { "Initial Population": 100, "Denominator": 80, "Numerator": 40 };
    expect(funnelFromCounts(plain, 128)!.stages[0])
      .toMatchObject({ name: ALL_PATIENTS, count: 128 });
    // With no cohort there is no funnel — an absence the caller explains, not one this papers over.
    expect(funnelFromCounts(plain, 0)).toEqual({ total: 0, stages: [], readout: null });
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

// ── APP-PANE-STATE ───────────────────────────────────────────────────────
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
    // `flow` is in this list on purpose: it names a tab that is no longer offered, and a link to it
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
