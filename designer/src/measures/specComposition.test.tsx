// SPEC-NAV-COMPOSITION — the population-rooted composition tree, against the real ELM of the synthetic
// ExampleReadingControl 1.0.0 (`__fixtures__/spec-patterns/ExampleReadingControl.cql`). The whole value of this view is that its shape is the measure's actual shape, so
// the fixture is the compiler's own output rather than a hand-drawn tree that would agree with a guess.
//
// The cases that matter are the two a tree of this shape most easily gets wrong:
// a define reached from two branches must be expanded ONCE, and a function must be walked THROUGH.

import { describe, expect, it } from "vitest";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import { buildComposition, pruneComposition, subjectName, type CompositionNode } from "./specComposition.ts";
import { buildSpecModel, filterSpecModel, type SpecLibraryInput, type SpecModel } from "./specModel.ts";
import { buildRetrieves } from "./specRetrieves.ts";
import elmFixtures from "./__fixtures__/spec-patterns-elm.json";
import refsets from "./__fixtures__/spec-patterns-refset.json";
import type { ReferenceSet } from "./specRetrieves.ts";

const elm = JSON.parse(elmFixtures.root.elm);
const refset = refsets.root;

/** SPEC-DATA-REQUIREMENTS — the reads, at the grain the document states them, inverted per define
 *  exactly as the nav inverts them. */
function readsByDefine(spec: SpecModel): Map<string, ReturnType<typeof buildRetrieves>> {
  const out = new Map<string, ReturnType<typeof buildRetrieves>>();
  for (const r of buildRetrieves(spec, [refset as unknown as ReferenceSet], new Map())) {
    for (const site of r.usedBy) out.set(site.define, [...(out.get(site.define) ?? []), r]);
  }
  return out;
}

function model(): SpecModel {
  const info = parseElmDefines(elmFixtures.root.elm);
  if (!info) throw new Error("spec-patterns fixture did not parse");
  // No `source`: bodies are recovered by locator slicing and nothing here reads a body. The graph and
  // the entry list — all this derivation stands on — come from the ELM.
  const input: SpecLibraryInput = { info, elm, source: "" };
  return filterSpecModel(buildSpecModel({ root: input, includes: [] }), false);
}

/** Every subject name in a subtree, in render order, with depth as leading dots. */
function outline(nodes: CompositionNode[], depth = 0): string[] {
  return nodes.flatMap((n) => [
    `${".".repeat(depth)}${subjectName(n.subject)}${n.repeat ? " (repeat)" : ""}`,
    ...outline(n.children, depth + 1),
  ]);
}

/** Every name in a subtree, flattened. */
const names = (nodes: CompositionNode[]): string[] =>
  nodes.flatMap((n) => [subjectName(n.subject), ...names(n.children)]);

/** Only the rows that are declarations (populations, defines, functions). */
const entryNames = (nodes: CompositionNode[]): string[] =>
  nodes.flatMap((n) => [
    ...(n.subject.kind === "entry" ? [n.subject.entry.name] : []), ...entryNames(n.children),
  ]);

describe("buildComposition (SPEC-NAV-COMPOSITION)", () => {
  it("roots the tree at the populations, in eCQM reporting order", () => {
    // The breakdown's AXIS. Rooting here is what fixes the emptiness that retired the per-define
    // version: a population always has dependencies, so the view is never empty.
    const roots = buildComposition(model(), readsByDefine(model()));
    expect(roots.map((r) => r.population.name)).toEqual([
      "Initial Population", "Denominator", "Denominator Exclusions", "Numerator",
    ]);
  });

  it("expands a define reached from two branches ONCE, and marks the second a repeat", () => {
    // `Qualifying Reading` is reached through both the reading-A and the reading-B
    // selection chains. Expanding both would multiply the shared reading chain across every branch that
    // uses it and make the tree longer than the CQL it indexes.
    const numerator = buildComposition(model(), readsByDefine(model())).find((r) => r.population.name === "Numerator")!;
    const rows = outline(numerator.children);
    const qualifying = rows.filter((r) => r.includes("Qualifying Reading"));
    expect(qualifying.length).toBeGreaterThan(1);
    expect(qualifying.filter((r) => !r.endsWith("(repeat)"))).toHaveLength(1);
    // A repeat is a leaf reference — it never carries the subtree again.
    const repeats = collect(numerator.children).filter((n) => n.repeat);
    expect(repeats.length).toBeGreaterThan(0);
    for (const r of repeats) expect(r.children).toEqual([]);
  });

  it("shows the READS a define issues, and never the functions it calls", () => {
    // A read is the LEAF: the tree bottoms out at the data the logic goes and gets, which is the only
    // row in it a data source can be checked against. A call is machinery — a helper between a
    // criterion and the read that decides it is plumbing in the middle of a clinical argument.
    const roots = buildComposition(model(), readsByDefine(model()));
    const all = roots.flatMap((r) => names(r.children));
    expect(all).toContain("Condition · Example Condition Diagnoses");   // a read, not a declaration
    expect(all).not.toContain("visitsFor");
  });

  it("carries a helper's OWN READS up to the caller", () => {
    // Transparency is a walk-through, not a cut, and reads are the part that is easy to lose:
    // `buildRetrieves` attributes a retrieve to the statement that issues it, so the encounter reads
    // inside `visitsFor` belong to `visitsFor` — a row that is not drawn. Cutting the helper
    // without carrying its reads would delete data requirements from the tree that the Data
    // requirements section still lists.
    const spec = model();
    const roots = buildComposition(spec, readsByDefine(spec));
    const qualifying = collect(roots.flatMap((r) => r.children))
      .find((n) => subjectName(n.subject) === "Qualifying Reading" && !n.repeat)!;
    const under = qualifying.children.map((c) => subjectName(c.subject));
    expect(under).not.toContain("visitsFor");
    expect(under.some((u) => u.startsWith("Encounter · "))).toBe(true);
  });

  it("gives a read no subtree — it is where the tree bottoms out", () => {
    const roots = buildComposition(model(), readsByDefine(model()));
    for (const node of collect(roots.flatMap((r) => r.children))) {
      if (node.subject.kind === "retrieve") expect(node.children).toEqual([]);
    }
  });

  it("orders a node's declarations before its reads", () => {
    // Terminology is a different KIND of thing from logic; interleaved, a node's children read as one
    // list of unlike things.
    for (const node of collect(buildComposition(model(), readsByDefine(model())).flatMap((r) => r.children))) {
      const kinds = node.children.map((c) => c.subject.kind);
      expect(kinds).toEqual([...kinds].sort((a, b) => (a === b ? 0 : a === "entry" ? -1 : 1)));
    }
  });

  it("reaches the same defines a population's closure does", () => {
    // The tree is a RENDERING of reachability, not a second opinion about it. `Numerator` reaches the
    // whole reading chain; nothing in the tree is outside the model.
    const spec = model();
    const numerator = buildComposition(spec, readsByDefine(spec)).find((r) => r.population.name === "Numerator")!;
    const reached = new Set(names(numerator.children));
    expect(reached).toContain("Has Reading A Below Threshold");
    expect(reached).toContain("Qualifying Reading");
    expect(numerator.reached).toBe(reached.size);
    const known = new Set(spec.libraries.flatMap((l) => l.entries.map((e) => e.name)));
    for (const n of entryNames(numerator.children)) expect(known.has(n)).toBe(true);
  });

  it("orders siblings by the catalogue's order, so Sort moves the tree too (SPEC-SHARED-ORDER)", () => {
    const spec = model();
    const rank = new Map<string, number>();
    let i = 0;
    for (const p of spec.populations) rank.set(p.name, i++);
    for (const lib of spec.libraries) for (const e of lib.entries) if (!rank.has(e.name)) rank.set(e.name, i++);
    for (const root of buildComposition(spec, readsByDefine(spec))) {
      for (const node of collect(root.children)) {
        const kids = node.children
          .filter((c) => c.subject.kind === "entry")
          .map((c) => rank.get(subjectName(c.subject)) ?? 0);
        expect(kids).toEqual([...kids].sort((a, b) => a - b));
      }
    }
  });
});

describe("pruneComposition (SPEC-NAV-COMPOSITION)", () => {
  it("lifts a dropped node's children rather than losing them", () => {
    const roots = buildComposition(model(), readsByDefine(model()));
    const numerator = roots.find((r) => r.population.name === "Numerator")!;
    const drop = subjectName(numerator.children[0]!.subject);
    const pruned = pruneComposition([numerator],
      (s) => subjectName(s) !== drop)[0]!;
    expect(names(pruned.children)).not.toContain(drop);
    // Everything the dropped node reached is still in the tree, one level shallower — except its value
    // sets, which mean nothing detached from the define that binds them.
    for (const child of numerator.children[0]!.children) {
      if (child.subject.kind === "entry") {
        expect(names(pruned.children)).toContain(child.subject.entry.name);
      }
    }
  });

  it("never drops a population root — the roots are the view's axis, not its content", () => {
    // A facet that could delete the roots would empty the view rather than narrow it.
    const roots = buildComposition(model(), readsByDefine(model()));
    const pruned = pruneComposition(roots, () => false);
    expect(pruned.map((r) => r.population.name)).toEqual(roots.map((r) => r.population.name));
    for (const r of pruned) expect(r.children).toEqual([]);
  });
});

/** Every node in a subtree, flattened. */
function collect(nodes: CompositionNode[]): CompositionNode[] {
  return nodes.flatMap((n) => [n, ...collect(n.children)]);
}
