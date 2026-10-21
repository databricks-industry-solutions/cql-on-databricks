// APP-ASSISTANT-REFS — an answer points at the document in the document's own vocabulary.
//
// What these pin is the property that makes the feature safe to hand a model: a reference is a
// CITATION, not a claim. The address is parsed strictly, the phrase is always the reader's own words,
// a figure carries no number of its own, and anything malformed degrades to what the model typed
// rather than to some other define's data.

import { describe, expect, it } from "vitest";
import { ARTIFACT_FENCE, referenceSegment } from "./assistantRefs.ts";
import { parseChart } from "./AnswerChart.tsx";

describe("reference addresses (APP-ASSISTANT-REFS)", () => {
  it("binds a phrase to a define without touching the phrase", () => {
    // THE PHRASE STAYS PROSE. Substituting the declared name would put `Example Condition Diagnosis` in
    // title case in the middle of a clinical sentence.
    expect(referenceSegment("patients with an example condition",
      "cql-databricks:define/ExampleMeasure/Example%20Condition%20Diagnosis")).toEqual({
      kind: "define",
      text: "patients with an example condition",
      library: "ExampleMeasure",
      define: "Example Condition Diagnosis",
      elementKind: "rule",
    });
  });

  it("carries a figure's ADDRESS and never a figure", () => {
    // SPEC-METRIC-REF / SVC-ANSWER-PROVENANCE — the client resolves this against the same metric set
    // the screen renders, so the assistant cannot state a wrong number; it can only point at one.
    const ref = referenceSegment("", "cql-databricks:figure/ExampleMeasure/Denominator?as=share&grain=element");
    expect(ref).toEqual({ kind: "metric", library: "ExampleMeasure", define: "Denominator",
      as: "share", grain: "element" });
    // The claim stated as structure: the segment has an ADDRESS and no field a number could live in.
    expect(Object.keys(ref!).sort()).toEqual(["as", "define", "grain", "kind", "library"]);
  });

  it("defaults a figure to the subject count, which is what a sentence usually means", () => {
    expect(referenceSegment("", "cql-databricks:figure/ExampleMeasure/Denominator"))
      .toMatchObject({ as: "count", grain: "subject" });
  });

  it("addresses a read by PROFILE, with type only as the fallback", () => {
    // Profile first is not a preference: "problem list" and "encounter diagnosis" are two profiles of
    // ONE resource type, so a type-addressed reference would give both phrases the same read.
    expect(referenceSegment("the problem list",
      "cql-databricks:read/qicore-condition-problems-health-concerns"))
      .toEqual({ kind: "read", text: "the problem list",
        profile: "qicore-condition-problems-health-concerns" });
    expect(referenceSegment("an encounter", "cql-databricks:read/type/Encounter"))
      .toEqual({ kind: "read", text: "an encounter", type: "Encounter" });
  });

  it("binds a value set by library and name, leaving which KIND it is to be derived", () => {
    expect(referenceSegment("example condition", "cql-databricks:valueset/ExampleMeasure/Example%20Condition%20Diagnoses"))
      .toEqual({ kind: "terminology", text: "example condition", library: "ExampleMeasure",
        name: "Example Condition Diagnoses" });
  });

  it("declines anything that is not ours, so an ordinary link stays an ordinary link", () => {
    expect(referenceSegment("a guideline", "https://example.org/g")).toBeNull();
    expect(referenceSegment("x", "mailto:someone@example.org")).toBeNull();
  });

  it("declines a half-built address rather than resolving to the wrong thing", () => {
    // The failure mode this prevents: a missing library silently resolving to whichever define shares
    // the name. Plain words are the correct degradation; another measure's figure is not.
    expect(referenceSegment("x", "cql-databricks:define/ExampleMeasure")).toBeNull();
    expect(referenceSegment("x", "cql-databricks:figure//Denominator")).toBeNull();
    expect(referenceSegment("x", "cql-databricks:read/")).toBeNull();
    expect(referenceSegment("x", "cql-databricks:nonsense/a/b")).toBeNull();
  });

  it("survives a badly escaped name instead of losing the answer", () => {
    // A model that escapes a bracket wrongly must cost the sentence its reference, not the turn.
    expect(referenceSegment("x", "cql-databricks:define/ExampleMeasure/Bad%ZZName"))
      .toMatchObject({ define: "Bad%ZZName" });
  });

  it("names both artifact fences and nothing else", () => {
    expect(ARTIFACT_FENCE["cql-databricks-funnel"]).toBe("funnel");
    expect(ARTIFACT_FENCE["cql-databricks-chart"]).toBe("chart");
    expect(ARTIFACT_FENCE["sql"]).toBeUndefined();
  });
});

describe("chart blocks (APP-ASSISTANT-REFS)", () => {
  it("reads a bar chart the model wrote", () => {
    expect(parseChart('{"type":"bar","title":"By band","rows":[["18-44",812],["45-64",2104]]}'))
      .toEqual({ type: "bar", title: "By band", x: undefined, y: undefined,
        rows: [["18-44", 812], ["45-64", 2104]] });
  });

  it("drops a row whose value is not a number rather than plotting it as zero", () => {
    // SPEC-METRIC — absent is not zero. A bar of length zero asserts a count of none, which is a
    // different and usually false claim.
    const spec = parseChart('{"type":"bar","rows":[["a",1],["b",null],["c","2"],["d",4]]}');
    expect(spec!.rows).toEqual([["a", 1], ["d", 4]]);
  });

  it("declines a malformed block, so the reader sees the model's own JSON", () => {
    // A chart drawn from half-understood rows is a wrong picture of real data, which is worse than no
    // picture — and falling back to a code block is what makes the failure diagnosable.
    expect(parseChart("not json")).toBeNull();
    expect(parseChart('{"type":"pie","rows":[["a",1]]}')).toBeNull();
    expect(parseChart('{"type":"bar"}')).toBeNull();
    expect(parseChart('{"type":"bar","rows":[]}')).toBeNull();
  });
});
