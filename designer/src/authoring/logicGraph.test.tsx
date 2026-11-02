// APP-LOGIC-VIEW, APP-DEP-COUNTS (PROC-TDD) — the walks the views share: what a node uses, what uses it,
// and layers. The graph is built from ELM (elmGraph.ts); here it is written out so each walk is tested alone.
import { describe, it, expect } from "vitest";
import { closureOf, computeLayers, dependentsOf, type LogicGraph, type LogicNode, type LogicNodeType } from "./logicGraph.ts";

const TYPE_OF: Record<string, LogicNodeType> = { def: "define", fn: "function", vs: "valueset", rt: "retrieve" };

/** A graph from "what each node uses". Dependents are the inverse, and layers come from the real computation. */
function graphOf(uses: Record<string, string[]>): LogicGraph {
  const byId: Record<string, LogicNode> = {};
  for (const id of Object.keys(uses)) {
    const type = TYPE_OF[id.split(":")[0]!]!;
    byId[id] = { id, label: id.slice(id.indexOf(":") + 1), type, deps: uses[id]!, dependents: [], cql: null, hint: "", layer: 0 };
  }
  for (const n of Object.values(byId)) for (const d of n.deps) byId[d]!.dependents.push(n.id);
  computeLayers(new Map(Object.entries(byId)));
  return { nodes: Object.values(byId), byId };
}

const g = graphOf({
  "vs:Pap Test": [],
  "rt:Observation": [],
  "def:Age OK": [],
  "def:Has Pap": ["rt:Observation", "vs:Pap Test"],
  "def:Initial Population": ["def:Age OK"],
  "def:Numerator": ["def:Has Pap", "def:Initial Population"],
});

describe("closureOf (APP-LOGIC-VIEW)", () => {
  it("computes the transitive closure of what a node uses", () => {
    const cl = closureOf(g, "def:Numerator");
    expect([...cl].sort()).toEqual(
      ["def:Age OK", "def:Has Pap", "def:Initial Population", "rt:Observation", "vs:Pap Test"]);
  });
});

describe("dependentsOf (APP-DEP-COUNTS)", () => {
  it("computes the transitive set of what uses a node", () => {
    expect([...dependentsOf(g, "vs:Pap Test")].sort()).toEqual(["def:Has Pap", "def:Numerator"]);
    expect([...dependentsOf(g, "def:Age OK")].sort()).toEqual(["def:Initial Population", "def:Numerator"]);
    expect(dependentsOf(g, "def:Numerator").size).toBe(0);
  });
});

describe("computeLayers (APP-LOGIC-VIEW)", () => {
  it("layers topologically (leaves 0, populations highest)", () => {
    const layer = (id: string) => g.byId[id]!.layer;
    expect(layer("vs:Pap Test")).toBe(0);
    expect(layer("def:Has Pap")).toBe(1);
    expect(layer("def:Numerator")).toBe(2);
  });
});
