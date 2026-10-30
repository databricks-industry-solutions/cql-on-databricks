// APP-SPEC-MODEL — the ONE model both Define-lens surfaces render. These tests pin the guarantee
// the nav and the Specification page depend on: one entry per NAME (overloads collapse), library
// ownership by declaring library, bodies recovered from ELM locators, and elimination decided once.
//
// The fixtures are REAL ELM, produced by the Compiler Service from the CQL stored alongside it
// (`__fixtures__/spec-model-elm.json`) — the model consumes compiler output, so the tests do too.

import { describe, expect, it } from "vitest";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import type { DescribeResult } from "../wire.ts";
import {
  buildSpecModel, eliminatedCount, filterSpecModel, sortSpecModel, specCellId, specEntryCount,
  type SpecLibraryInput, type SpecModel,
} from "./specModel.ts";
import { POPULATIONS } from "./model.ts";
import fixtures from "./__fixtures__/spec-model-elm.json";

function libraryInput(key: "demo" | "helpers"): SpecLibraryInput {
  const f = fixtures[key];
  const info = parseElmDefines(f.elm);
  if (!info) throw new Error(`fixture ${key} did not parse`);
  return { info, elm: JSON.parse(f.elm), source: f.source };
}

const demo = () => libraryInput("demo");
const helpers = () => libraryInput("helpers");

const build = (over: Partial<Parameters<typeof buildSpecModel>[0]> = {}): SpecModel =>
  buildSpecModel({ root: demo(), includes: [helpers()], ...over });

describe("buildSpecModel (APP-SPEC-MODEL)", () => {
  it("collapses overloads of one name into a single entry carrying its variants", () => {
    const lib = build().libraries.find((l) => l.name === "Helpers")!;
    expect(lib.entries.map((e) => e.name)).toEqual(["ToString", "ToQuantity"]);

    const toString = lib.entries.find((e) => e.name === "ToString")!;
    expect(toString.variants).toHaveLength(2);
    // Typed operands come from the ELM — this is what distinguishes one overload from another.
    expect(toString.variants.map((v) => v.params)).toEqual(["value FHIR.string", "value FHIR.uri"]);
  });

  it("reads a `define fluent function` under its real name", () => {
    // A source scan mistakes `define fluent function "ToQuantity"` for a define NAMED "fluent";
    // the ELM simply carries the name.
    const lib = build().libraries.find((l) => l.name === "Helpers")!;
    const quantity = lib.entries.find((e) => e.name === "ToQuantity")!;
    expect(quantity.kind).toBe("function");
    expect(quantity.variants[0]!.params).toBe("quantity FHIR.Quantity");
    expect(lib.entries.map((e) => e.name)).not.toContain("fluent");
  });

  it("recovers each declaration's own CQL body from its ELM locator", () => {
    const lib = build().libraries.find((l) => l.name === "Helpers")!;
    const toString = lib.entries.find((e) => e.name === "ToString")!;
    // Both overloads read the same here, but each is sliced from its OWN span — the old name-keyed
    // body map gave every overload the LAST one's body.
    expect(toString.variants.map((v) => v.body)).toEqual(["value.value", "value.value"]);

    const root = build().libraries.find((l) => l.isRoot)!;
    expect(root.entries.find((e) => e.name === "Has Screening")!.variants[0]!.body)
      .toBe('exists [Observation: "Screening"]');
  });

  it("recovers bodies regardless of ELM statement order", () => {
    // The translator emits statements in its own order (in this fixture "Has Screening" precedes
    // "Numerator", the reverse of the source), so bodies must not be matched positionally.
    const numerator = build().populations.find((p) => p.name === "Numerator")!;
    expect(numerator.variants[0]!.body).toBe('"Has Screening"');
  });

  it("assigns each entry to the library that DECLARES it, never listing one twice", () => {
    const model = build({
      // A describe response spanning both libraries must not fold Helpers' defines into the root.
      describeGraph: {
        defines: ["Initial Population", "Denominator", "Numerator", "Has Screening", "ToString"],
        dependencies: { Denominator: ["Initial Population"], Numerator: ["Has Screening"] },
        boundaries: [], materializedDefines: [], compilerVersion: null,
      } satisfies DescribeResult,
    });
    const root = model.libraries.find((l) => l.isRoot)!;
    expect(root.entries.map((e) => e.name)).not.toContain("ToString");
    const everywhere = model.libraries.flatMap((l) => l.entries.map((e) => `${l.name}::${e.name}`));
    expect(new Set(everywhere).size).toBe(everywhere.length);
  });

  it("gives every entry a unique DOM anchor, including across libraries", () => {
    const ids = build().libraries.flatMap((l) => l.entries.map((e) => e.domId));
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain(specCellId("Helpers", "ToString"));
  });

  it("keeps names that differ only in case on separate anchors", () => {
    // CQL is case-sensitive and real content relies on it — a helper library may declare both `ToInterval`
    // and `toInterval`. A lowercasing id scheme collapses them onto one anchor.
    expect(specCellId("Helpers", "ToInterval")).not.toBe(specCellId("Helpers", "toInterval"));
  });

  it("marks defines unreachable from any population as eliminated, using the compiler's edges", () => {
    const model = build({
      includes: [],
      describeGraph: {
        defines: ["Initial Population", "Denominator", "Numerator", "Has Screening", "Orphan Helper"],
        dependencies: { Denominator: ["Initial Population"], Numerator: ["Has Screening"] },
        boundaries: [], materializedDefines: [], compilerVersion: null,
      } satisfies DescribeResult,
    });
    const root = model.libraries.find((l) => l.isRoot)!;
    expect(root.entries.find((e) => e.name === "Has Screening")!.eliminated).toBe(false);
    expect(root.entries.find((e) => e.name === "Orphan Helper")!.eliminated).toBe(true);
    expect(eliminatedCount(model)).toBe(1);
  });

  it("traces reachability from the ELM expression tree when there is no describe response", () => {
    const model = build();
    const root = model.libraries.find((l) => l.isRoot)!;
    // "Numerator" → "Has Screening" is an ExpressionRef in the ELM, so it is reachable.
    expect(root.entries.find((e) => e.name === "Has Screening")!.eliminated).toBe(false);
    expect(root.entries.find((e) => e.name === "Orphan Helper")!.eliminated).toBe(true);
    expect(model.libraries.find((l) => l.name === "Helpers")!.entries.every((e) => e.eliminated)).toBe(true);
  });

  it("derives value sets and reads from the ELM, not from the text", () => {
    const { graph } = build();
    const deps = graph.byId["def:Has Screening"]!.deps.map((id) => graph.byId[id]!);
    expect(deps.map((d) => d.type).sort()).toEqual(["retrieve", "valueset"]);
    expect(deps.find((d) => d.type === "retrieve")!.label).toBe("Observation");
    expect(deps.find((d) => d.type === "valueset")!.label).toBe("Screening");
  });

  it("counts dependents in both directions for the nav's chrome", () => {
    const { graph } = build();
    expect(graph.byId["def:Has Screening"]!.dependents).toContain("def:Numerator");
    expect(graph.byId["def:Initial Population"]!.dependents).toContain("def:Denominator");
  });

  it("eliminates nothing when there is no population to trace from (a plain library)", () => {
    const model = buildSpecModel({ root: helpers(), includes: [] });
    expect(model.liveNames.size).toBe(0);
    expect(eliminatedCount(model)).toBe(0);
  });

  it("orders populations in eCQM order, not declaration order", () => {
    // APP-FLOW-SPINE leans on this and cannot check it for itself: the spine renders
    // `SpecModel.populations` as-is, and its whole licence to draw them as a vertical sequence is that
    // the order is the MEASURE's — its scoring semantics, via `POPULATIONS` — rather than anything we
    // derived from the graph. In declaration order the spine would quietly assert a sequence the
    // measure does not have, which is the failure that retired the Logic lens.
    const names = build().populations.map((p) => p.name);
    expect(names).toEqual(["Initial Population", "Denominator", "Numerator"]);
    expect(names).toEqual(POPULATIONS.filter((n) => names.includes(n)));
  });

  it("lists an include that failed to compile rather than dropping it", () => {
    const missing = build({ includes: [], unresolved: [{ name: "Missing" }] })
      .libraries.find((l) => l.name === "Missing")!;
    expect(missing.resolved).toBe(false);
    expect(missing.entries).toEqual([]);
  });
});

describe("filterSpecModel (APP-SPEC-MODEL)", () => {
  it("hides eliminated entries once, so both surfaces render the same set", () => {
    const model = build();
    const namesOf = (m: SpecModel) => m.libraries.flatMap((l) => l.entries.map((e) => e.name));

    expect(namesOf(filterSpecModel(model, false))).not.toContain("Orphan Helper");
    expect(namesOf(filterSpecModel(model, false))).not.toContain("ToString");
    expect(namesOf(filterSpecModel(model, true))).toContain("Orphan Helper");
    expect(namesOf(filterSpecModel(model, true))).toContain("ToString");

    // ToString stays ONE entry whether hidden or shown — collapsing is independent of visibility.
    const shown = filterSpecModel(model, true).libraries.find((l) => l.name === "Helpers")!;
    expect(shown.entries.filter((e) => e.name === "ToString")).toHaveLength(1);
  });

  it("keeps the header count equal to what is rendered", () => {
    const model = build();
    const hidden = filterSpecModel(model, false);
    expect(specEntryCount(hidden)).toBe(
      hidden.populations.length + hidden.libraries.reduce((n, l) => n + l.entries.length, 0),
    );
    expect(specEntryCount(hidden)).toBeLessThan(specEntryCount(model));
  });

  it("leaves populations untouched — they are never eliminated", () => {
    const model = build();
    expect(filterSpecModel(model, false).populations).toEqual(model.populations);
  });

  it("drops a library with nothing visible from both surfaces, but keeps the root and unresolved ones", () => {
    // Everything in Helpers is eliminated here, so an empty heading in the page and a "· 0" group in
    // the nav would be pure noise — the library goes entirely.
    const hidden = filterSpecModel(build({ unresolved: [{ name: "Missing" }] }), false);
    expect(hidden.libraries.map((l) => l.name)).not.toContain("Helpers");
    expect(hidden.libraries.find((l) => l.isRoot)).toBeDefined();
    expect(hidden.libraries.map((l) => l.name)).toContain("Missing");
  });
});

// SPEC-SHARED-ORDER — order is a property of the specification, so it is decided HERE and both surfaces
// render the result. The bug this closes: the nav sorted its own copy (opening on `topo`) while the page
// rendered declaration order, and `declared` was not even one of the nav's options — so the two could
// not be made to agree by any setting.
describe("sortSpecModel (SPEC-SHARED-ORDER)", () => {
  const rootNames = (m: SpecModel) => m.libraries.find((l) => l.isRoot)!.entries.map((e) => e.name);

  it("leaves the model untouched on `declared` — the ELM's order IS declaration order", () => {
    const model = build();
    expect(sortSpecModel(model, "declared")).toBe(model);
    expect(rootNames(model)).toEqual(["Has Screening", "Orphan Helper"]);
  });

  it("orders A–Z by name", () => {
    expect(rootNames(sortSpecModel(build(), "az"))).toEqual(["Has Screening", "Orphan Helper"]);
    const helperLib = sortSpecModel(build(), "az").libraries.find((l) => l.name === "Helpers")!;
    expect(helperLib.entries.map((e) => e.name)).toEqual(["ToQuantity", "ToString"]);
  });

  it("orders topologically, deepest layer first, with name as the tie-break", () => {
    // Asserted as the PROPERTY rather than as a name list: layers come from the graph, so a fixture that
    // grows must not need this test rewritten to keep meaning something.
    const sorted = sortSpecModel(build(), "topo");
    for (const lib of sorted.libraries) {
      const layers = lib.entries.map((e) =>
        sorted.graph.byId[e.kind === "function" ? `fn:${e.name}` : `def:${e.name}`]?.layer ?? 0);
      expect(layers).toEqual([...layers].sort((a, b) => b - a));
    }
    // A total order matters: equal rows reshuffling between renders would move sections under the reader.
    expect(rootNames(sortSpecModel(sorted, "topo"))).toEqual(rootNames(sorted));
  });

  it("never sorts populations — they stay in eCQM reporting order", () => {
    // The carve-out that used to live in the nav. Topological sort puts Initial Population LAST, which
    // is how the nav and the page came to disagree about where the measure starts.
    const model = build();
    for (const sort of ["topo", "az", "usedby", "uses"] as const) {
      expect(sortSpecModel(model, sort).populations.map((p) => p.name))
        .toEqual(model.populations.map((p) => p.name));
    }
  });

  it("does not mutate the model it is given", () => {
    // Both surfaces hold the same object; sorting in place would reorder one surface's props behind
    // React's back and re-render only the other.
    const model = build();
    const before = rootNames(model);
    sortSpecModel(model, "az");
    sortSpecModel(model, "usedby");
    expect(rootNames(model)).toEqual(before);
  });
});
