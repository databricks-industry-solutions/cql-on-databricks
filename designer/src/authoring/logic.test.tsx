// PHASE-S2 / APP-LOGIC-VIEW (PROC-TDD) — Narrative derivation from CQL source.
import { describe, it, expect } from "vitest";
import { libraryHeader, parseDefinitions } from "./logic.ts";

const SRC = `library ExampleMeasure version '1.2.0'
using FHIR version '4.0.1'

valueset "Example Condition": 'http://example.org/vs/example-condition'

context Patient

define "Has Example Condition":
  exists ([Condition: "Example Condition"])

define public "Denominator":
  "Has Example Condition"

define function "ToString"(value String):
  value
`;

describe("libraryHeader", () => {
  it("reads the library name, version, and model", () => {
    expect(libraryHeader(SRC)).toEqual({ name: "ExampleMeasure", version: "1.2.0", model: "FHIR 4.0.1" });
  });
  it("is empty for a headerless snippet", () => {
    expect(libraryHeader("define \"X\": 1").name).toBe("");
  });
});

describe("parseDefinitions (APP-LOGIC-VIEW)", () => {
  it("lists every top-level definition in source order with kind + access", () => {
    const defs = parseDefinitions(SRC);
    expect(defs.map((d) => d.name)).toEqual(["Has Example Condition", "Denominator", "ToString"]);
    expect(defs.map((d) => d.kind)).toEqual(["define", "define", "function"]);
    expect(defs[1]!.access).toBe("public");
  });

  it("captures each define's own CQL slice", () => {
    const defs = parseDefinitions(SRC);
    expect(defs[0]!.cql).toContain('define "Has Example Condition"');
    expect(defs[0]!.cql).toContain("exists ([Condition");
    expect(defs[0]!.cql).not.toContain("Denominator"); // slice stops at the next define
  });

  it("does not match the word 'define' inside an expression", () => {
    const defs = parseDefinitions(`define "A": 1\n// define is a keyword\ndefine "B": 2`);
    expect(defs.map((d) => d.name)).toEqual(["A", "B"]);
  });
});
