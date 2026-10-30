// PHASE-S2/S5 (PROC-TDD) / APP-LOGIC-VIEW, APP-DEP-COUNTS — dependency-graph derivation.
import { describe, it, expect } from "vitest";
import { buildLogicGraph, closureOf } from "./logicGraph.ts";

const SRC = `library M version '1.0.0'
using FHIR version '4.0.1'
valueset "Pap Test": 'http://x/pap'
context Patient
define function "ToInterval"(p FHIR.Period): p
define "Has Pap": exists ([Observation: "Pap Test"])
define "Age OK": AgeInYears() >= 21
define "Initial Population": "Age OK"
define "Numerator": "Initial Population" and "Has Pap"`;

describe("buildLogicGraph", () => {
  const g = buildLogicGraph(SRC);
  const byLabel = (l: string) => g.nodes.find((n) => n.label === l)!;

  it("creates nodes for defines, functions, value sets and retrieves", () => {
    expect(g.nodes.find((n) => n.id === "def:Numerator")?.type).toBe("population");
    expect(g.nodes.find((n) => n.id === "def:Has Pap")?.type).toBe("define");
    expect(g.nodes.find((n) => n.id === "fn:ToInterval")?.type).toBe("function");
    expect(g.nodes.find((n) => n.id === "vs:Pap Test")?.type).toBe("valueset");
    expect(g.nodes.find((n) => n.id === "rt:Observation")?.type).toBe("retrieve");
  });

  it("tags eCQM populations by name", () => {
    expect(byLabel("Initial Population").type).toBe("population");
    expect(byLabel("Numerator").type).toBe("population");
    expect(byLabel("Has Pap").type).toBe("define");
  });

  it("resolves downstream deps (uses): a quoted ref to a value set vs a sibling define, plus retrieves", () => {
    expect(byLabel("Has Pap").deps.sort()).toEqual(["rt:Observation", "vs:Pap Test"]);
    expect(byLabel("Numerator").deps.sort()).toEqual(["def:Has Pap", "def:Initial Population"]);
    expect(byLabel("Age OK").deps).toEqual([]); // AgeInYears() is not a declared function → not a dep
  });

  it("computes dependents (used by — the upstream direction, APP-DEP-COUNTS)", () => {
    expect(byLabel("Numerator").dependents).toEqual([]);
    expect(byLabel("Has Pap").dependents).toEqual(["def:Numerator"]);
    expect(byLabel("Initial Population").dependents).toEqual(["def:Numerator"]);
    expect(byLabel("Pap Test").dependents).toEqual(["def:Has Pap"]);
  });

  it("computes the transitive closure of what a node uses", () => {
    const cl = closureOf(g, "def:Numerator");
    expect([...cl].sort()).toEqual(
      ["def:Age OK", "def:Has Pap", "def:Initial Population", "rt:Observation", "vs:Pap Test"]);
  });

  it("layers topologically (leaves 0, populations highest)", () => {
    expect(byLabel("Pap Test").layer).toBe(0);
    expect(byLabel("Has Pap").layer).toBe(1);
    expect(byLabel("Numerator").layer).toBe(2);
  });
});
