// SPEC-DOCUMENT / SPEC-CONCEPTS / SPEC-RULES / SPEC-PLACEMENT / SPEC-POLARITY — the Specification's
// derived structure. These tests pin the derivation the document depends on: what becomes a Concept,
// what becomes a Rule, where a shared define lands, and which rules disqualify.
//
// THE FIXTURE. `specModel.test.tsx` uses real Compiler-Service ELM, and so would this — but the cases
// that matter here (a negated reference, a define shared across two non-reaching populations, a clinical
// define reached through a function) are not in that fixture and the service is not reachable offline to
// produce them. So the ELM is built here, node-for-node against the shapes the real fixture actually
// contains: `{type,locator,name}` ExpressionRefs, `{type,operand}` Exists/Not, `{type,dataType}`
// Retrieves, and statements carrying `locator`/`name`/`context`/`accessLevel`. The builder emits the CQL
// source alongside it with matching locators, so the code each element discloses is really sliced out of
// source rather than asserted into place.
//
// The main fixture carries NO result types, matching the ELM a deployment without `EnableResultTypes`
// emits — so the structural tests describe the document as it derives with the least information
// available. A second fixture at the bottom adds them, pinning the two-grain metric that arrives with
// them (SPEC-METRIC).

import { describe, expect, it } from "vitest";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import type { NarratedText, SpecificationOverlay } from "../library-client.ts";
import { buildSpecModel, specCellId, type SpecLibraryInput } from "./specModel.ts";
import {
  buildSpecDocument, initials, proseRuns, sectionBadges, withoutNarrated,
  type ProseRun, type SpecDocument, type SpecProse, type SpecRule, type SpecScope,
} from "./specDocument.ts";

// ── ELM fixture builder ──────────────────────────────────────────────────────────────────────────

const ref = (name: string) => ({ type: "ExpressionRef", name, annotation: [] });
const not = (operand: unknown) => ({ type: "Not", annotation: [], operand });
const exists = (operand: unknown) => ({ type: "Exists", annotation: [], signature: [], operand });
const retrieve = (fhirType: string) => ({
  type: "Retrieve", dataType: `{http://hl7.org/fhir}${fhirType}`, annotation: [],
  include: [], codeFilter: [], dateFilter: [], otherFilter: [],
});
const and = (...operand: unknown[]) => ({ type: "And", annotation: [], signature: [], operand });
const literal = (value: string) => ({
  type: "Literal", valueType: "{urn:hl7-org:elm-types:r1}Boolean", value, annotation: [],
});

/** The result-type shapes the translator emits with `EnableResultTypes`. */
const named = (name: string) => ({ type: "NamedTypeSpecifier", name, annotation: [] });
const listOf = (elementType: unknown) => ({ type: "ListTypeSpecifier", elementType, annotation: [] });
const fhir = (t: string) => named(`{http://hl7.org/fhir}${t}`);
const system = (t: string) => named(`{urn:hl7-org:elm-types:r1}${t}`);
const choiceOf = (...choice: unknown[]) => ({ type: "ChoiceTypeSpecifier", choice, annotation: [] });

interface Def {
  name: string;
  /** The CQL body, as one line — what the element discloses as its code. */
  body: string;
  expression: unknown;
  kind?: "define" | "function";
  params?: string;
  context?: string;
  /** Present only in the result-typed fixture. */
  resultTypeSpecifier?: unknown;
  /** CQL annotation tags, as the translator emits them with `EnableAnnotations`. */
  tags?: Record<string, string>;
}

/** The `annotation` array the translator emits: the `a:s` source narrative it always includes once
 *  `EnableAnnotations` is on, plus the `t` tag pairs when the define carries a comment block. The
 *  narrative entry is present in every fixture so the reader is exercised against the real payload,
 *  not a tags-only idealisation of it. */
function annotationFor(d: Def): unknown[] {
  const narrative = { type: "Annotation", s: { r: "1", s: [{ value: [`define "${d.name}":`] }] } };
  if (!d.tags) return [narrative];
  const t = Object.entries(d.tags).map(([name, value]) => ({ name, value }));
  return [{ type: "Annotation", s: narrative.s, t }];
}

/** Emit a library as BOTH its CQL source and its ELM, with locators that line up — so the model
 *  recovers each body by slicing the source exactly as it does for compiler output. */
function library(name: string, defs: Def[]): SpecLibraryInput {
  const lines = [`library ${name} version '1.0.0'`, "", "using FHIR version '4.0.1'", ""];
  const statements: Record<string, unknown>[] = [];
  for (const d of defs) {
    const header = d.kind === "function"
      ? `define function "${d.name}"(${d.params ?? ""}):`
      : `define "${d.name}":`;
    const start = lines.length + 1;
    lines.push(header, `  ${d.body}`, "");
    const end = start + 1;
    statements.push({
      locator: `${start}:1-${end}:${(`  ${d.body}`).length}`,
      name: d.name,
      context: d.context ?? "Patient",
      accessLevel: "Public",
      annotation: annotationFor(d),
      ...(d.kind === "function" ? { type: "FunctionDef", operand: [] } : {}),
      ...(d.resultTypeSpecifier ? { resultTypeSpecifier: d.resultTypeSpecifier } : {}),
      expression: d.expression,
    });
  }
  const elm = {
    library: {
      identifier: { id: name, version: "1.0.0" },
      usings: { def: [{ localIdentifier: "FHIR", uri: "http://hl7.org/fhir", version: "4.0.1" }] },
      statements: { def: statements },
    },
  };
  const json = JSON.stringify(elm);
  const info = parseElmDefines(json);
  if (!info) throw new Error(`fixture ${name} did not parse`);
  return { info, elm: JSON.parse(json), source: lines.join("\n") };
}

/** A measure shaped to exercise every determination at once: two non-reaching populations sharing a
 *  define, a population that merely re-states another, an exclusion population, a negated reference,
 *  and a clinical define reachable only THROUGH a function. */
function measure(): SpecLibraryInput {
  return library("Doc", [
    { name: "Initial Population", context: "Patient",
      body: 'exists "Example Condition Diagnosis" and exists "Encounters During Period"',
      expression: and(exists(ref("Example Condition Diagnosis")), exists(ref("Encounters During Period"))) },
    { name: "Denominator", body: '"Initial Population"', expression: ref("Initial Population") },
    { name: "Denominator Exclusions", body: '"Excluded Care"', expression: ref("Excluded Care") },
    { name: "Numerator", body: '"Controlled Reading"', expression: ref("Controlled Reading") },

    { name: "Example Condition Diagnosis", body: 'exists "Example Condition Codes"',
      tags: { title: "Example Condition Diagnosis", description: "Who counts as having primary example condition.",
        comment: "Secondary condition is out — the value set is the boundary." },
      expression: exists(ref("Example Condition Codes")) },
    { name: "Example Condition Codes", body: '[Condition: "Example Condition Diagnoses"]',
      expression: retrieve("Condition") },
    { name: "Encounters During Period", body: "[Encounter]", expression: retrieve("Encounter") },
    { name: "Excluded Care", body: 'exists "Excluded Care Encounters"',
      expression: exists(ref("Excluded Care Encounters")) },
    { name: "Excluded Care Encounters", body: '[Encounter: "Excluded Care"]', expression: retrieve("Encounter") },

    { name: "Controlled Reading", body: '"Qualifying Reading" and exists "Encounters During Period"',
      expression: and(ref("Qualifying Reading"), exists(ref("Encounters During Period"))) },
    { name: "Qualifying Reading",
      body: 'not "Reading During Inpatient" and "ToUnit"("mm[Hg]") is not null',
      expression: and(not(ref("Reading During Inpatient")), ref("ToUnit")) },
    { name: "Reading During Inpatient", body: "[Encounter]",
      tags: { description: "A reading does not count if taken during an inpatient encounter.",
              rationale: "Acute-setting readings are not representative of ambulatory control." },
      expression: retrieve("Encounter") },

    // A function: never an element itself, but the define it calls must still surface.
    { name: "ToUnit", kind: "function", params: "unit System.String", body: '"Unit Label"',
      expression: ref("Unit Label") },
    { name: "Unit Label", body: "true", expression: literal("true") },
  ]);
}

function document(): SpecDocument {
  const model = buildSpecModel({ root: measure(), includes: [] });
  return buildSpecDocument(model, new Map([["Doc", measure().elm]]));
}

const scope = (doc: SpecDocument, name: string): SpecScope =>
  doc.populations.find((p) => p.name === name)!;
const conceptNames = (s: SpecScope) => s.concepts.map((c) => c.define);
const ruleNames = (s: SpecScope) => s.concepts.flatMap((c) => c.rules.map((r) => r.define));

describe("buildSpecDocument (SPEC-DOCUMENT)", () => {
  it("makes a Concept of each define the population reaches directly", () => {
    // SPEC-CONCEPTS — depth 1 from the population, and nothing deeper.
    expect(conceptNames(scope(document(), "Initial Population"))).toEqual(["Example Condition Diagnosis"]);
    expect(conceptNames(scope(document(), "Numerator"))).toEqual(["Controlled Reading"]);
  });

  it("makes a Rule of every define below a Concept, however deep", () => {
    // SPEC-RULES — "Qualifying Reading" is depth 2 from Numerator and "Reading During Inpatient"
    // depth 3; both are rules of the one Concept, flat.
    const numerator = scope(document(), "Numerator");
    expect(ruleNames(numerator)).toEqual(
      expect.arrayContaining(["Qualifying Reading", "Reading During Inpatient"]),
    );
  });

  it("sees THROUGH functions — the define a helper calls still surfaces, the helper never does", () => {
    // The one case an opaque cut would break: `Unit Label` is reachable only via `ToUnit`.
    const doc = document();
    const everyElement = doc.populations.flatMap((p) => [
      ...p.concepts.flatMap((c) => [c.define, ...c.rules.map((r) => r.define)]),
      ...p.rules.map((r) => r.define),
    ]);
    expect(everyElement).toContain("Unit Label");
    expect(everyElement).not.toContain("ToUnit");
  });

  it("hoists a define shared by two non-reaching populations into COMMON", () => {
    // SPEC-PLACEMENT — "Encounters During Period" is reached from Initial Population and from Numerator,
    // and neither reaches the other.
    const doc = document();
    expect(doc.common).not.toBeNull();
    expect(conceptNames(doc.common!)).toContain("Encounters During Period");
    expect(conceptNames(scope(doc, "Initial Population"))).not.toContain("Encounters During Period");
  });

  it("does NOT hoist when one population merely re-states another", () => {
    // The load-bearing qualifier: Denominator reaches Initial Population, so IP's closure stays in IP
    // instead of the whole measure collapsing into COMMON. The reaches edge is not a field on the
    // document — it is an input to PLACEMENT and nothing else — so what asserts it is where the
    // defines LANDED.
    const doc = document();
    expect(conceptNames(scope(doc, "Denominator"))).toEqual([]);
    expect(conceptNames(scope(doc, "Initial Population"))).toContain("Example Condition Diagnosis");
    expect(conceptNames(doc.common!)).not.toContain("Example Condition Diagnosis");
  });

  it("renders every define exactly once across the whole document", () => {
    const doc = document();
    const placed = [...doc.populations, ...(doc.common ? [doc.common] : [])].flatMap((s) => [
      ...s.concepts.flatMap((c) => [c.define, ...c.rules.map((r) => r.define)]),
      ...s.rules.map((r) => r.define),
    ]);
    expect(placed).toHaveLength(new Set(placed).size);
  });

  it("marks a negated reference disqualifying", () => {
    // SPEC-POLARITY — read at the reference site: `not "Reading During Inpatient"`.
    const doc = document();
    const rule = doc.populations.flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Reading During Inpatient")!;
    expect(rule.polarity).toBe("disqualifying");
  });

  it("marks a rule under an exclusion population disqualifying by POSITION, not by negation", () => {
    // "Excluded Care Encounters" is reached positively — no `Not` anywhere above it — but the
    // population it serves removes members, so it does not qualify anyone.
    const exclusions = scope(document(), "Denominator Exclusions");
    expect(conceptNames(exclusions)).toEqual(["Excluded Care"]);
    const rule = exclusions.concepts[0]!.rules.find((r) => r.define === "Excluded Care Encounters")!;
    expect(rule.polarity).toBe("disqualifying");
  });

  it("leaves an un-negated rule qualifying", () => {
    const rule = document().populations.flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Example Condition Codes")!;
    expect(rule.polarity).toBe("qualifying");
  });

  it("gives every element the CQL it derives from (SPEC-ANCHOR)", () => {
    const concept = scope(document(), "Initial Population").concepts[0]!;
    expect(concept.entry.variants[0]!.body).toBe('exists "Example Condition Codes"');
  });

  it("anchors on the library-qualified define, never on the display alias", () => {
    // SPEC-ANCHOR — the alias renumbers when an element is inserted; the anchor must not.
    const concept = scope(document(), "Numerator").concepts[0]!;
    expect(concept.domId).toBe(concept.entry.domId);
    expect(concept.domId).toContain("Doc");
    expect(concept.domId).not.toContain(concept.alias);
  });

  it("excludes a Concept's own Rules from its Sources", () => {
    // The rules are the decomposition — repeating them as chips below restates the card in miniature.
    const concept = scope(document(), "Denominator Exclusions").concepts[0]!;
    const ruleDefines = concept.rules.map((r) => r.define);
    expect(ruleDefines.length).toBeGreaterThan(0);
    for (const s of concept.sources) expect(ruleDefines).not.toContain(s.define);
  });

  it("never lists a function as a Source", () => {
    // Functions are transparent everywhere, chips included: listing them turned a clinical concept
    // into a row of conversion helpers.
    const all = document().populations.flatMap((p) => [...p.concepts, ...p.concepts.flatMap((c) => c.rules)]);
    for (const e of all) for (const s of e.sources) expect(s.define).not.toBe("ToUnit");
  });

  it("links sources library-qualified so a duplicated name cannot resolve wrongly", () => {
    const concept = scope(document(), "Numerator").concepts[0]!;
    // "Qualifying Reading" is one of this concept's own Rules, so it is not repeated as a chip;
    // "Encounters During Period" lives in COMMON, so the link out to it is exactly what Sources is for.
    expect(concept.sources.map((s) => s.define)).toEqual(["Encounters During Period"]);
    for (const s of concept.sources) expect(s.library).toBe("Doc");
  });

  it("takes the subject grain from the ELM context, not from prose", () => {
    // An episode measure must never report "patients".
    expect(scope(document(), "Numerator").metric!.subjectUnit).toBe("patients");
  });

  it("reports no metric while the ELM carries no result types", () => {
    // Result types arrive from the translator; until they do, both grains are underivable and the
    // metric says so rather than guessing a shape. This test changes when they land.
    const concept = scope(document(), "Numerator").concepts[0]!;
    expect(concept.metric.resultType).toBeNull();
    expect(concept.metric.form).toBe("none");
    expect(concept.metric.elementGrain).toBeNull();
  });

  it("states the base a count is a share of", () => {
    const concept = scope(document(), "Numerator").concepts[0]!;
    expect(concept.metric.base).toBe("Numerator");
    expect(concept.metric.countOf).toBe("Controlled Reading");
    // COMMON has no enclosing population, so its base is the evaluated-subject total.
    expect(document().common!.concepts[0]!.metric.base).toBeNull();
  });
});

describe("metrics once the translator annotates result types (SPEC-METRIC)", () => {
  /** The same measure shape, compiled WITH `EnableResultTypes`. */
  function typed(): SpecLibraryInput {
    return library("Typed", [
      { name: "Initial Population", body: 'exists "Encounters During Period"',
        resultTypeSpecifier: system("Boolean"), expression: exists(ref("Encounters During Period")) },
      { name: "Numerator", body: '"Controlled"',
        resultTypeSpecifier: system("Boolean"), expression: ref("Controlled") },
      { name: "Encounters During Period", body: "[Encounter]",
        resultTypeSpecifier: listOf(fhir("Encounter")), expression: retrieve("Encounter") },
      // Depth 1 from Numerator, so a Concept; the two below it are Rules and carry polarity.
      { name: "Controlled", body: '"Encounter Count" > 2 and exists "Recent Encounter"',
        resultTypeSpecifier: system("Boolean"),
        expression: and(ref("Encounter Count"), exists(ref("Recent Encounter")),
          exists(ref("Chosen Condition"))) },
      { name: "Encounter Count", body: 'Count("Recent Encounter")',
        resultTypeSpecifier: system("Integer"), expression: ref("Recent Encounter") },
      { name: "Recent Encounter", body: "[Encounter]",
        resultTypeSpecifier: listOf(fhir("Encounter")), expression: retrieve("Encounter") },
      // A define the translator types as a CHOICE — the shape FHIRHelpers-style selections produce.
      { name: "Chosen Condition", body: '"Recent Encounter"',
        resultTypeSpecifier: choiceOf(fhir("Condition"), fhir("Condition")),
        expression: ref("Recent Encounter") },
    ]);
  }
  const typedDoc = () =>
    buildSpecDocument(buildSpecModel({ root: typed(), includes: [] }), new Map([["Typed", typed().elm]]));

  /** Every Rule in the document, wherever placement put it. */
  const rules = (): SpecRule[] => {
    const doc = typedDoc();
    const scopes = [...doc.populations, ...(doc.common ? [doc.common] : [])];
    return scopes.flatMap((s) => [...s.concepts.flatMap((c) => c.rules), ...s.rules]);
  };
  const find = (name: string): SpecRule => rules().find((r) => r.define === name)!;

  it("gives a resource-valued define BOTH grains", () => {
    // "412,003 encounters" and "149,882 patients" answer different questions, so the metric carries
    // the element grain alongside the subject grain rather than choosing one.
    const encounters = find("Recent Encounter");
    expect(encounters.metric.elementGrain).toBe("Encounter");
    expect(encounters.metric.subjectUnit).toBe("patients");
    expect(encounters.metric.form).toBe("cardinality-histogram");
  });

  it("gives a Boolean define the subject grain only", () => {
    // There is no second thing to count: the define says something about the subject and nothing else.
    const numerator = typedDoc().populations.find((p) => p.name === "Numerator")!;
    expect(numerator.metric!.form).toBe("count");
    expect(numerator.metric!.elementGrain).toBeNull();
  });

  it("gives a CHOICE-typed define no element grain (SPEC-METRIC)", () => {
    // The grain is rendered lower-cased and pluralised beside the figure, so a type EXPRESSION landing
    // here printed `choice<fhir.condition, fhir.condition>s` into the criterion's heading. A choice has
    // no one element name to say, which is the same answer a Boolean gives for the same reason.
    expect(find("Chosen Condition").metric.resultType).toBe("Choice<FHIR.Condition, FHIR.Condition>");
    expect(find("Chosen Condition").metric.elementGrain).toBeNull();
  });

  it("renders a numeric define as a distribution, not a count", () => {
    const count = find("Encounter Count");
    expect(count.metric.form).toBe("distribution");
    expect(count.metric.resultType).toBe("Integer");
  });

  it("carries the row/value shape alongside the grains (APP-VALUE-READABLE)", () => {
    // The Preview block dispatches off this to decide whether a share bar may be drawn at all, so it
    // rides on the metric beside the grains rather than being re-derived where it is rendered.
    expect(find("Recent Encounter").metric.valueShape).toBe("row");
    expect(find("Encounter Count").metric.valueShape).toBe("value");
    const numerator = typedDoc().populations.find((p) => p.name === "Numerator")!;
    expect(numerator.metric!.valueShape).toBe("value");
  });

  it("classifies a value-computing define as computational, not qualifying", () => {
    // SPEC-POLARITY — the distinction result types unlock: a count neither qualifies nor disqualifies.
    expect(find("Encounter Count").polarity).toBe("computational");
    // A list of resources still filters, so it keeps its qualifying/disqualifying reading.
    expect(find("Recent Encounter").polarity).toBe("qualifying");
  });
});

describe("rule aliases (SPEC-ANCHOR)", () => {
  it("stems a SCOPE's own rules from the population name", () => {
    // Population names are short eCQM standards, so initials read well there.
    expect(initials("Initial Population")).toBe("IP");
    expect(initials("Denominator Exclusions")).toBe("DE");
  });

  it("stems a Concept's rules from the CONCEPT ALIAS, not from its name", () => {
    // Real define names are long clinical phrases — "Has Encounter During Period and
    // Condition Active At Its Start" initialises to "HEDP", which is noise. The alias is
    // always short and makes the parent obvious.
    const concept = scope(document(), "Numerator").concepts[0]!;
    expect(concept.alias).toMatch(/^C\d+$/);
    expect(concept.rules.map((r) => r.alias))
      .toEqual(concept.rules.map((_, i) => `${concept.alias}-${i + 1}`));
  });
});

describe("narration from CQL annotation tags (APP-NARRATIVE-TAGS)", () => {
  it("reads a Concept's title and description off its tags", () => {
    const concept = scope(document(), "Initial Population").concepts[0]!;
    expect(concept.narration).not.toBeNull();
    expect(concept.narration!.title!.text).toBe("Example Condition Diagnosis");
    expect(concept.narration!.text!.text).toBe("Who counts as having primary example condition.");
  });

  it("reads a Rule's rationale — the MEANING line the card expands to", () => {
    const rule = document().populations.flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Reading During Inpatient")!;
    expect(rule.narration!.rationale!.text)
      .toBe("Acute-setting readings are not representative of ambulatory control.");
    expect(rule.narration!.title).toBeNull();
  });

  it("marks tag-sourced prose `authored`, not `narrated`, FIELD BY FIELD", () => {
    // O2 — a tag is a human's assertion published with the artifact. Merging it with AI-drafted
    // overlay prose would erase exactly the distinction a reader needs. Provenance sits on each
    // FIELD because a define routinely has an authored description and a narrated guidance line.
    const concept = scope(document(), "Initial Population").concepts[0]!;
    expect(concept.narration!.title!.provenance).toBe("authored");
    expect(concept.narration!.text!.provenance).toBe("authored");
    expect(concept.narration!.text!.sourceTag).toBe("description");
  });

  it("reads @comment as a field of its own — the author's elaboration, kept out of the lede", () => {
    // The tag was on the wire and dropped on the floor. It is NOT concatenated into `text`: the
    // description's plain projection is what a nav row and a catalogue line print, and folding a
    // paragraph into it would make every one-line summary a paragraph. `authored` like every other tag.
    const concept = scope(document(), "Initial Population").concepts[0]!;
    expect(concept.narration!.comment!.text)
      .toBe("Secondary condition is out — the value set is the boundary.");
    expect(concept.narration!.comment!.provenance).toBe("authored");
    expect(concept.narration!.comment!.sourceTag).toBe("comment");
    expect(concept.narration!.text!.text).toBe("Who counts as having primary example condition.");
  });

  it("leaves an un-annotated define with no narration rather than inventing any", () => {
    const rule = document().populations.flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Example Condition Codes")!;
    expect(rule.narration).toBeNull();
  });

  it("ignores the a:s source narrative that rides alongside the tags", () => {
    // Every fixture define carries the narrative entry; only the tagged ones produce narration.
    const doc = document();
    const withNarration = doc.populations
      .flatMap((p) => [...p.concepts, ...p.concepts.flatMap((c) => c.rules)])
      .filter((e) => e.narration !== null).map((e) => e.define);
    expect(withNarration.sort()).toEqual(["Example Condition Diagnosis", "Reading During Inpatient"]);
  });
});

describe("section health badges (REND-BADGES)", () => {
  const base = { defines: ["A", "B"], population: "Numerator", live: true, scorekey: null };

  it("reports not-run before a run", () => {
    // A section that has not been evaluated must not read as passing.
    const b = sectionBadges({ ...base, live: false, counts: null });
    expect(b.validation.state).toBe("not-run");
  });

  it("passes when every define in the section matched somebody", () => {
    const b = sectionBadges({ ...base, counts: { A: 120, B: 4 } });
    expect(b.validation.state).toBe("pass");
    expect(b.validation.count).toBeUndefined();
  });

  it("does NOT flag a criterion that matched nobody (temporary, 2026-08-11)", () => {
    // An exclusion with no members is a perfectly good outcome, so the badge fired on the normal case —
    // one lone `1` beside Denominator Exclusions and nothing else on the page, which reads as a defect
    // rather than as a count. The count is already stated on the criterion itself; an empty criterion
    // that is genuinely worth reporting belongs in the readiness read, with its cause beside it.
    const b = sectionBadges({ ...base, counts: { A: 120, B: 0 } });
    expect(b.validation.state).toBe("pass");
  });

  it("flags a Scorekey mismatch on the population the section serves", () => {
    // The compliance question — does the engine reproduce the golden result — is the strongest
    // signal a section can carry, so it surfaces even when every count is healthy.
    const b = sectionBadges({
      ...base, counts: { A: 120, B: 4 }, scorekey: { Numerator: { mismatches: 3 } },
    });
    expect(b.validation.state).toBe("findings");
    expect(b.validation.detail).toContain("3 Scorekey mismatches");
  });

  it("ignores a Scorekey verdict for a population this section does not serve", () => {
    const b = sectionBadges({
      ...base, counts: { A: 1, B: 1 }, scorekey: { "Denominator Exclusions": { mismatches: 9 } },
    });
    expect(b.validation.state).toBe("pass");
  });

  it("carries VALIDATION alone — no badge for a question nothing can answer yet", () => {
    // Only VALIDATION carries a badge. A badge that always states nothing teaches readers to ignore
    // the badge beside it. This test should change when `describe` → RetrieveHints lands and there is a
    // real figure to carry.
    const b = sectionBadges({ ...base, counts: { A: 1, B: 1 } });
    expect(Object.keys(b)).toEqual(["validation"]);
  });
});

describe("Specification overlay join (APP-NARRATIVE-OVERLAY)", () => {
  // The overlay narrates defines the CQL says nothing about, and the CQL wins wherever both speak.
  // Its keys are LIBRARY-QUALIFIED, which is what keeps `Exclusions."Has Excluded Care"` from picking
  // up the root library's prose.
  const overlay = (elements: Record<string, unknown>, overview: Record<string, unknown> = {}) => ({
    library: "Doc", version: "1", overview, elements, libraryOverviews: {},
    appendix: { valuesets: [], codes: [], parameters: [] },
  }) as unknown as SpecificationOverlay;

  const withOverlay = (o: SpecificationOverlay | null): SpecDocument =>
    buildSpecDocument(buildSpecModel({ root: measure(), includes: [] }),
      new Map([["Doc", measure().elm]]), o);

  const narrated = (text: string, sourceTag: string) =>
    ({ text, provenance: "narrated", method: "ai-drafted", sourceTag });

  it("narrates a define the CQL is silent about, and marks it narrated", () => {
    const doc = withOverlay(overlay({
      "Doc::Example Condition Codes": {
        description: narrated("Codes that count as primary example condition.", "description"),
        guidance: narrated("Secondary condition is a different value set.", "guidance"),
      },
    }));
    const rule = doc.populations.flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Example Condition Codes")!;
    expect(rule.narration!.text!.text).toBe("Codes that count as primary example condition.");
    expect(rule.narration!.text!.provenance).toBe("narrated");
    expect(rule.narration!.guidance!.sourceTag).toBe("guidance");
  });

  it("carries a CITED FIGURE through as structure, not as its plain-text projection", () => {
    // SPEC-METRIC-REF — the renderer resolves figures from `segments`. Dropping them here degraded every
    // cited quantity in the document to the em-dash the plain projection uses for an absence, and it did
    // so silently: the sentence still read, with a dash where the number belonged.
    const doc = withOverlay(overlay({
      "Doc::Example Condition Codes": {
        guidance: {
          ...narrated("Of those, — qualify.", "guidance"),
          segments: [
            { kind: "text", text: "Of those, " },
            { kind: "metric", library: "Doc", define: "Denominator", grain: "subject", as: "count" },
            { kind: "text", text: " qualify." },
          ],
        },
      },
    }));
    const rule = doc.populations.flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Example Condition Codes")!;
    expect(rule.narration!.guidance!.segments!.map((s) => s.kind))
      .toEqual(["text", "metric", "text"]);
  });

  it("lets a CQL tag beat the overlay FIELD BY FIELD, not element by element", () => {
    // The precedence rule of designer/docs/narrative-tags.md A define with an authored description
    // and no authored guidance must show BOTH — each labelled with its own provenance — rather than
    // the overlay being suppressed wholesale or overwriting what the author wrote.
    const doc = withOverlay(overlay({
      "Doc::Example Condition Diagnosis": {
        description: narrated("An overlay description that must NOT win.", "description"),
        guidance: narrated("Anchored to the period end, not the encounter.", "guidance"),
      },
    }));
    const concept = scope(doc, "Initial Population").concepts[0]!;
    expect(concept.narration!.text!.text).toBe("Who counts as having primary example condition.");
    expect(concept.narration!.text!.provenance).toBe("authored");
    expect(concept.narration!.guidance!.text).toBe("Anchored to the period end, not the encounter.");
    expect(concept.narration!.guidance!.provenance).toBe("narrated");
  });

  it("gives a population criterion the same triple as any other element", () => {
    // The complaint this work answers: a criterion narrated only by WHAT it is says nothing a
    // reviewer can act on, so a population takes `rationale` and `guidance` like a Concept does.
    const doc = withOverlay(overlay({
      "Doc::Initial Population": {
        description: narrated("Patients with identified example condition.", "description"),
        rationale: narrated("Control takes time; recent diagnoses are not yet answerable.", "rationale"),
        guidance: narrated("Age is computed at the START of the period.", "guidance"),
      },
    }));
    const ip = scope(doc, "Initial Population");
    expect(ip.narration!.rationale!.text).toContain("Control takes time");
    expect(ip.narration!.guidance!.text).toContain("START of the period");
  });

  it("ignores overlay prose keyed to a different library", () => {
    // SPEC-ANCHOR — two libraries may declare one name, so an unqualified join attaches the wrong
    // prose to the wrong define. A key for `Other::` must not reach `Doc::`.
    const doc = withOverlay(overlay({
      "Other::Example Condition Diagnosis": { guidance: narrated("Wrong library.", "guidance") },
    }));
    expect(scope(doc, "Initial Population").concepts[0]!.narration!.guidance).toBeNull();
  });

  it("changes no structure — the overlay carries prose and nothing else", () => {
    // A wrong overlay must produce wrong WORDS, never a wrong document: placement stays the ELM's.
    const bare = withOverlay(null);
    const rich = withOverlay(overlay({
      "Doc::Not A Define At All": { description: narrated("Invented.", "description") },
      "Doc::Example Condition Diagnosis": { description: narrated("Overlaid.", "description") },
    }));
    expect(rich.populations.map((p) => p.name)).toEqual(bare.populations.map((p) => p.name));
    for (const [i, p] of rich.populations.entries()) {
      expect(conceptNames(p)).toEqual(conceptNames(bare.populations[i]!));
      expect(ruleNames(p)).toEqual(ruleNames(bare.populations[i]!));
    }
  });

  it("leaves a define nobody narrated with no narration at all", () => {
    expect(withOverlay(overlay({})).populations
      .flatMap((p) => p.concepts.flatMap((c) => c.rules))
      .find((r) => r.define === "Example Condition Codes")!.narration).toBeNull();
  });
});

// ── SPEC-DERIVED-APPENDIX — the reference tables, read off the ELM declarations ──────────────────
//
// These used to be hand-written rows in the narration instance, which is how an OID could go stale
// with nothing to catch it. The Appendix is now derived from the same compiled libraries the document
// above is built from, so it cannot say something the document does not.

describe("Appendix (SPEC-DERIVED-APPENDIX)", () => {
  /** A library carrying the three declaration kinds an Appendix lists. */
  function withDeclarations(name: string, oid: string): SpecLibraryInput {
    const base = library(name, [
      { name: "Initial Population", body: '[Condition: "Example Condition Diagnoses"]',
        expression: retrieve("Condition") },
    ]);
    const elm = base.elm as { library: Record<string, unknown> };
    elm.library["valueSets"] = { def: [{ name: "Example Condition Diagnoses", id: oid }] };
    elm.library["codeSystems"] = { def: [{ name: "LOINC", id: "http://loinc.org" }] };
    elm.library["codes"] = {
      def: [{ name: "Example Reading A", id: "X0001", display: "Example Reading A",
              codeSystem: { name: "LOINC" } }],
    };
    elm.library["parameters"] = {
      def: [{ name: "Measurement Period", parameterTypeSpecifier: named("{urn:hl7-org:elm-types:r1}Any") }],
    };
    return base;
  }

  const appendixDoc = (root: SpecLibraryInput, includes: SpecLibraryInput[] = []) =>
    buildSpecDocument(
      buildSpecModel({ root, includes }),
      new Map([root, ...includes].map((l) => [l.info.name, l.elm])),
    );

  it("lists each declaration with the identifier a reader can look up", () => {
    const { appendix } = appendixDoc(withDeclarations("Doc", "urn:oid:2.25.1"));
    expect(appendix.valuesets).toEqual([
      { name: "Example Condition Diagnoses", oid: "urn:oid:2.25.1", library: "Doc" },
    ]);
    expect(appendix.parameters[0]!.name).toBe("Measurement Period");
  });

  it("resolves a code's system through the library's own declarations", () => {
    // The ELM records `codeSystem: { name }` — a reference. A reference nobody resolved is not
    // something a reader can look up.
    const { appendix } = appendixDoc(withDeclarations("Doc", "urn:oid:1"));
    expect(appendix.codes[0]).toMatchObject({ code: "X0001", system: "LOINC", library: "Doc" });
  });

  it("keeps two libraries' bindings of one name apart", () => {
    // Collapsing them would hide a real disagreement about which OID a name means, which is exactly
    // the kind of thing an appendix exists to expose.
    const { appendix } = appendixDoc(
      withDeclarations("Doc", "urn:oid:1"), [withDeclarations("Shared", "urn:oid:2")]);
    expect(appendix.valuesets.map((v) => [v.library, v.oid])).toEqual([
      ["Doc", "urn:oid:1"], ["Shared", "urn:oid:2"],
    ]);
  });

  it("omits what it cannot obtain rather than transcribing it", () => {
    // Code counts and expansion dates belong to the terminology tables (CONTENT-VSAC-AUTHORITY),
    // which the document has no read path to. There is no field here to fill in from a copy.
    const { appendix } = appendixDoc(withDeclarations("Doc", "urn:oid:1"));
    expect(Object.keys(appendix.valuesets[0]!).sort()).toEqual(["library", "name", "oid"]);
  });
});

// ── SPEC-EXPLAIN-VIEW — the Explain toggle as a provenance filter ────────────────────────────────
//
// Off, the document must carry only what can be stated without interpretation. The claim worth
// pinning is that "without interpretation" means BY PROVENANCE, not by which file the prose arrived
// in: the measure author's own words reach the page through the same payload as our narration, and
// hiding them would misrepresent the deterministic view as poorer than it is.

describe("withoutNarrated (SPEC-EXPLAIN-VIEW)", () => {
  const authored = (text: string): NarratedText =>
    ({ text, provenance: "authored", author: "Example Steward" });
  const drafted = (text: string): NarratedText =>
    ({ text, provenance: "narrated", method: "ai-drafted" });

  const full = (): SpecificationOverlay => ({
    library: "Doc", version: "1.0.0", precedence: "The CQL is authoritative.",
    overview: {
      description: authored("Percentage of patients 18-85…"),
      intent: { statement: drafted("Replace diagnosed with controlled."),
                clinicalRationale: authored("Example reading prevalence is common.") },
      scope: { inScope: drafted("The last qualifying reading."), nonGoals: [drafted("Not treatment.")] },
      guidance: authored("Only readings taken by an eligible clinician count."),
      howToRead: drafted("Read down."),
    },
    elements: {
      "Doc::Initial Population": { description: drafted("Patients aged 20 to 90…"),
                                   guidance: drafted("Age is computed at…") },
      "Doc::Mixed": { description: authored("From the CQL tag."), rationale: drafted("Our reading.") },
    },
    libraryOverviews: { Exclusions: drafted("Shared exclusions.") },
    copyright: authored("This measure is not a clinical guideline."),
  });

  it("keeps the author's own words and drops only the interpretation", () => {
    const off = withoutNarrated(full())!;
    expect(off.overview.description?.text).toBe("Percentage of patients 18-85…");
    expect(off.overview.guidance?.text).toBe("Only readings taken by an eligible clinician count.");
    expect(off.overview.intent?.clinicalRationale?.text).toBe("Example reading prevalence is common.");
    expect(off.overview.intent?.statement).toBeUndefined();
    expect(off.overview.howToRead).toBeUndefined();
    expect(off.overview.scope).toBeUndefined();  // every field of it was narrated
    expect(off.copyright?.text).toBe("This measure is not a clinical guideline.");
  });

  it("filters per FIELD, not per element", () => {
    // A define routinely states its `@description` in the CQL and has its rationale only as our
    // reading of the code. Dropping the element wholesale would hide the author's own sentence.
    const off = withoutNarrated(full())!;
    expect(off.elements["Doc::Mixed"]!.description?.text).toBe("From the CQL tag.");
    expect(off.elements["Doc::Mixed"]!.rationale).toBeUndefined();
  });

  it("drops an element left with nothing so the page reaches its honest empty state", () => {
    const off = withoutNarrated(full())!;
    expect(off.elements["Doc::Initial Population"]).toBeUndefined();
    expect(off.libraryOverviews["Exclusions"]).toBeUndefined();
  });

  it("changes no structure — the same document, with less prose", () => {
    // The toggle is a view over one document, not a switch between two. Placement is the ELM's
    // (SPEC-PLACEMENT) and the overlay never had a say in it, on or off.
    const model = buildSpecModel({ root: measure(), includes: [] });
    const elm = new Map([["Doc", measure().elm]]);
    const on = buildSpecDocument(model, elm, full());
    const off = buildSpecDocument(model, elm, withoutNarrated(full()));
    expect(off.populations.map((p) => p.name)).toEqual(on.populations.map((p) => p.name));
    for (const [i, p] of off.populations.entries()) {
      expect(conceptNames(p)).toEqual(conceptNames(on.populations[i]!));
      expect(ruleNames(p)).toEqual(ruleNames(on.populations[i]!));
    }
    expect(off.appendix).toEqual(on.appendix);
  });
});

describe("proseRuns (APP-NARRATIVE-PROVENANCE)", () => {
  const p = (provenance: SpecProse["provenance"], method?: SpecProse["method"]): SpecProse =>
    ({ text: "…", provenance, method });
  const shape = (runs: ProseRun<SpecProse>[]) =>
    runs.map((r) => `${r.provenance}${r.method ? `/${r.method}` : ""}:${r.items.length}`);

  it("states one Narrative once over the fields that share it", () => {
    // The whole point of the well: a criterion whose description, rationale and guidance are all our
    // reading gets ONE mark, where the per-field chip made three.
    const runs = proseRuns([p("narrated", "ai-drafted"), p("narrated", "ai-drafted"), p("narrated", "ai-drafted")],
      (x) => x);
    expect(shape(runs)).toEqual(["narrated/ai-drafted:3"]);
  });

  it("breaks the run where the author's own words start", () => {
    // The boundary a reader must be able to see. An authored field is not swept into a block claiming
    // to be our reading — it leaves the well, which is what makes the well mean anything.
    const runs = proseRuns([p("narrated", "ai-drafted"), p("authored"), p("narrated", "ai-drafted")], (x) => x);
    expect(shape(runs)).toEqual(["narrated/ai-drafted:1", "authored:1", "narrated/ai-drafted:1"]);
  });

  it("does not let one header speak for two methods", () => {
    // An AI draft and a sentence a person wrote are not interchangeable to a reviewer, and the header
    // states the method — so a run that mixed them would be a header asserting something false.
    const runs = proseRuns([p("narrated", "ai-drafted"), p("narrated", "human-written")], (x) => x);
    expect(shape(runs)).toEqual(["narrated/ai-drafted:1", "narrated/human-written:1"]);
  });

  it("groups without reordering or dropping anything", () => {
    // Grouping is a rendering decision over unchanged data (SPEC-NARRATIVE-TAGS keeps provenance per
    // field). Flattening the runs has to give back exactly the input, or the document lost prose.
    const fields = [p("authored"), p("authored"), p("narrated", "ai-drafted"), p("authored")];
    expect(proseRuns(fields, (x) => x).flatMap((r) => r.items)).toEqual(fields);
    expect(proseRuns([], (x: SpecProse) => x)).toEqual([]);
  });
});

describe("Definitions cards (SPEC-DEFINE-SECTIONS)", () => {
  it("gives every entry the same derivation the criteria sections use", () => {
    // A define is a clinical concept wherever it is opened, so its card renders the same index,
    // narration and metric — there is no second, poorer treatment for the catalogue.
    const model = buildSpecModel({ root: measure(), includes: [] });
    const doc = buildSpecDocument(model, new Map([["Doc", measure().elm]]));
    for (const entry of [...model.populations, ...model.libraries.flatMap((l) => l.entries)]) {
      expect(doc.byDefine[entry.key], entry.key).toBeDefined();
    }
    expect(doc.byDefine["Doc::Example Condition Diagnosis"]!.narration).toBeDefined();
  });

  it("indexes an unreachable define too", () => {
    // Eliminated is a fact reported by the elimination rule, not a reason to withhold a derivation.
    const model = buildSpecModel({ root: measure(), includes: [] });
    const doc = buildSpecDocument(model, new Map([["Doc", measure().elm]]));
    expect(doc.byDefine["Doc::Unit Label"]).toBeDefined();
  });
});

