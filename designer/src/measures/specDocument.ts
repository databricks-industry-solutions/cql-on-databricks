// SPEC-DOCUMENT / SPEC-CONCEPTS / SPEC-RULES / SPEC-PLACEMENT — the Specification's derived structure.
//
// This is the derivation the Specification renders: population criteria decomposed into Concepts, each
// decomposed into Rules, with everything shared hoisted into COMMON. It is a pure function over the ELM
// the SPA already has (`specModel.ts` + `elmGraph.ts`) — no backend call, no store, no heuristic.
//
// THE GOVERNING CONSTRAINT: every element is a CQL DEFINE — never a function, never an ELM sub-branch.
// That is not stylistic. The BFF collects results and a truthy-subject count for EVERY define
// (`run.py`), so elements-as-defines have real, engine-produced metrics with nothing synthesised and
// nothing attributed by guesswork. Defines also carry author-given names, so anchors need not be
// invented, and they can disclose their own CQL at the point a derived claim is made.
//
// The three determinations this module owns:
//
//  1. CONCEPTS AND RULES. A Concept is a define reached at DEPTH 1 from a population criterion — the
//     coarse idea the population is composed of. A Rule is a define in that Concept's transitive
//     closure, flat however deep. Concept granularity is therefore a property of the CQL's own
//     structure, deliberately: a flatly-written population yields one Concept and no Rules, and the
//     document honestly reports an under-factored measure rather than inventing structure the logic
//     does not have.
//
//  2. FUNCTIONS ARE TRANSPARENT. Never an element, but the walk passes THROUGH them, so `FHIRHelpers`
//     vanishes as the plumbing it is while a clinical define invoked as `Exclusions."Has Excluded Care"`
//     still surfaces. Cutting at functions instead would silently drop shared exclusions from the
//     document.
//
//  3. PLACEMENT. Every define renders exactly ONCE, at the lowest common dominator of everything that
//     reaches it: one Concept → a Rule under it; two Concepts under one population → hoisted to a
//     population-level Rule; two populations that do not reach each other → hoisted to the root, which
//     IS the COMMON section. One rule applied recursively, so COMMON is not a second mechanism.
//
//     The non-reaching qualifier is load-bearing. Populations routinely reach each other — a typical
//     `Denominator` is literally `"Initial Population"` — so unqualified sharing would hoist the whole
//     Initial Population closure into COMMON and the section would become the document.
//
// ORDER IS PRESENTATIONAL. ELM has no evaluation order for `A and not B and not C`, so rules order by
// graph layer then declaration order. Neither is semantic: a renderer must not imply sequence, and
// per-rule counts DO NOT SUM — a subject failing two rules is counted by both.
//
// METRICS HAVE TWO GRAINS (SPEC-METRIC). A define's result type gives the ELEMENT grain and the ELM
// context gives the SUBJECT grain, and a resource-valued define has both: "149,882 patients ·
// 412,003 encounters" says more than either figure alone, and the two answer different questions.
// A Boolean define has only the subject grain — there is no second thing to count.
//
// Both are countable from what a run already returns. A list-valued define lowers to a per-element
// relation (`__subject_id, __ordinal, __val`), so distinct subjects and distinct elements are both
// there. Note that a define returning whole resources yields only their IDs — enough to count
// elements and to size each subject's list, and not enough for anything richer, so no metric may
// promise a distribution over resource attributes.
//
// Result types arrive from the translator (`EnableResultTypes`); until a given deployment's ELM carries
// them, `returns` is null, `metricForm` yields `none`, and `computational` cannot be told apart from
// `qualifying`. That is reported rather than approximated (`coding-standards.md`, "Data provenance") —
// a metric that cannot be drawn honestly is not drawn.

import { elmReferenceData } from "../authoring/elmDefines.ts";
import { closureOf, type LogicGraph } from "../authoring/logicGraph.ts";
import { overlayKey, type NarratedText, type SpecBlock, type SpecSegment,
  type SpecificationOverlay } from "../library-client.ts";
import { POPULATIONS } from "./model.ts";
import type { SpecEntry, SpecModel } from "./specModel.ts";

/** SPEC-POLARITY — derived from the ELM, never narrated. `disqualifying` is what the renderer
 *  emphasises as "does not count". */
export type RulePolarity = "qualifying" | "disqualifying" | "computational";

/** SPEC-METRIC — the metric's FORM is a typed dispatch off the define's ELM result type, never a guess.
 *  `none` means OMITTED: a metric that cannot be drawn honestly is not drawn. */
export type MetricForm = "count" | "cardinality-histogram" | "distribution" | "none";

/** The SUBJECT grain, from the ELM context — what one row of a run is. An episode-of-care measure does
 *  not count patients, and a document that says it does is stating something false. */
export type MetricUnit = "patients" | "episodes" | "encounters" | "records";

/** SPEC-METRIC / APP-VALUE-READABLE — is one returned value a ROW or an aggregable VALUE?
 *
 *  The distinction decides whether a frequency may be DRAWN over the returned values. `true` occurring in
 *  1,204 of 1,288 subjects is a distribution and a share bar states it. An `Encounter` occurring once is
 *  not: resources are near-unique by construction, so a bar over them draws eight equal segments and
 *  invites a reader to compare frequencies that only measure how many distinct records came back. Same
 *  count column, different claim — and the bar is the part that makes the claim.
 *
 *  It is a typed dispatch off the ELM result type, exactly like `MetricForm`, and NOT read off the values
 *  themselves: sniffing each row's shape would let one define's block draw bars on some rows and not
 *  others, which is a worse reading than either answer consistently applied. */
export type MetricValueShape = "row" | "value";

export interface SpecMetric {
  form: MetricForm;
  /** Distinct subjects meeting the define — the figure every element can carry. */
  subjectUnit: MetricUnit;
  /** The ELEMENT grain for a resource- or list-valued define ("Encounter", "Condition"), giving the
   *  second figure: how many of the THING, not how many people. Null when the define yields no
   *  countable element (a Boolean answers about the subject and nothing else). */
  elementGrain: string | null;
  /** The ELM result type both grains were dispatched from — the audit trail. */
  resultType: string | null;
  /** APP-VALUE-READABLE — whether a returned value is a row or an aggregable value. Decides whether
   *  the Preview block may draw a share bar over the returned values. */
  valueShape: MetricValueShape;
  /** The define whose run count is this metric's value. Always the element's own define. */
  countOf: string;
  /** What the value is a share OF: a population's define name, or null for the evaluated-subject total.
   *  Stated so a figure cannot be read against the wrong denominator. */
  base: string | null;
}

/** SPEC-ANCHOR — a link to another element, ALWAYS library-qualified: two libraries may declare one
 *  name, so an unqualified reference resolves to the wrong define. */
export interface SpecRef {
  library: string;
  define: string;
  /** The in-document anchor, when the referenced define is an element of this Specification. */
  domId: string | null;
}

/** SPEC-NARRATIVE-TAGS — one prose field, carrying its own provenance.
 *
 *  Provenance is PER FIELD, not per element, and that is load-bearing. A define routinely carries
 *  `@description` in its CQL (the author's assertion, `authored`) while its `guidance` exists only as
 *  an interpretation in the Specification overlay (`narrated`). One provenance on the element would
 *  have to lie about one of them. */
export interface SpecProse {
  /** The PLAIN-TEXT projection — a nav summary, a catalogue row, a title. A cited figure reads as "—"
   *  here, because a one-line summary has no metric set to resolve one against. */
  text: string;
  /** SPEC-METRIC-REF — the inline content, where some of it is a CITED FIGURE rather than prose. Must
   *  be carried through: the renderer resolves figures from these, and dropping them silently degraded
   *  every cited quantity in the document to the em-dash the plain projection uses for an absence. */
  segments?: SpecSegment[];
  /** SPEC-NARRATIVE-DEVICES — Include/Exclude blocks and code tables, after the lead prose. */
  blocks?: SpecBlock[];
  /** The lines of the define's CQL this prose is about — the citation. */
  sourceSpan?: string;
  provenance: "authored" | "narrated";
  /** A separate axis UNDER `narrated` — how the prose was produced, not what it is. */
  method?: "ai-drafted" | "human-written";
  /** The CQL annotation tag this prose belongs in — what Accept would write back to. */
  sourceTag?: string;
}

/** SPEC-NARRATIVE-TAGS — the narration triple (+ title) for one element.
 *
 *  A reader asks the same three questions of a population, a Concept and a Rule, so all three take
 *  the same shape. `text` alone is the failure this triple exists to fix: a criterion narrated only
 *  by WHAT it is restates its own define name and tells a reviewer nothing they can act on.
 *
 *  TWO SOURCES, ONE PRECEDENCE (designer/docs/narrative-tags.md). A tag in the CQL wins — it was
 *  published with the artifact and travels to anyone else's CQL tooling. The Specification overlay
 *  supplies prose only where the CQL is silent. That is a stated precedence rule between two
 *  provenances of the same field, and which one is showing is always visible on the page. */
export interface SpecNarration {
  /** `@title` — the clinician-facing name, when the define's own name is not it. */
  title: SpecProse | null;
  /** `@description` — WHAT this element is. */
  text: SpecProse | null;
  /** `@comment` — the author's own elaboration on the description, in their own words.
   *
   *  NOT one of the three questions, and that is why it is a field of its own rather than folded into
   *  `text`. `@description` is a one-or-two-sentence answer a consumer prints in a list; `@comment` is
   *  where the same author says the rest — a helper's interval function describes itself in one line and
   *  then spends a paragraph on which choice types it covers and why. Concatenating them would make the
   *  one-line projection (`SpecProse.text` in a nav row) a paragraph. This document keeps both separate.
   *
   *  ALWAYS `authored`: it is a tag in the CQL, so it is the author's assertion whatever the Explain
   *  toggle is doing (`SPEC-EXPLAIN-VIEW`). The overlay has no counterpart — the XSD carries the triple
   *  and nothing else — so this field is populated from tags alone. */
  comment: SpecProse | null;
  /** `@rationale` — WHY it matters clinically. */
  rationale: SpecProse | null;
  /** `@guidance` — HOW TO READ IT: the boundary, the tie-break, the misreading people make. */
  guidance: SpecProse | null;
}

/** True when an element has nothing narrated at all — it then renders under its define name:
 *  degraded, never wrong, and never filled in with a guess. */
export function isNarrated(n: SpecNarration | null): n is SpecNarration {
  return n != null && (n.title != null || n.text != null || n.comment != null
    || n.rationale != null || n.guidance != null);
}

/** APP-NARRATIVE-PROVENANCE — a maximal run of adjacent prose sharing one provenance. */
export interface ProseRun<T> {
  provenance: SpecProse["provenance"];
  method?: SpecProse["method"];
  items: T[];
}

/** APP-NARRATIVE-PROVENANCE — group ordered prose into runs of one provenance, so the page can
 *  disclose a RUN rather than mark every field in it.
 *
 *  WHY THIS IS GROUPED AT ALL. Provenance stayed per FIELD (`SpecProse.provenance`, and that does not
 *  change here — `SPEC-NARRATIVE-TAGS` requires it), but the MARK was per field too, and no shipped
 *  Specification instance contains a single `authored` field: everything is narrated, so everything
 *  was chipped, three or four times per define across forty defines. The chip's own rationale — only
 *  the exceptional case is marked, so the mark stays a signal — was defeated by the corpus. A run is
 *  the same claim stated once over the fields it actually covers.
 *
 *  ADJACENCY IS THE WHOLE RULE. A field only joins the run above it, so an `authored` field breaks the
 *  run and renders outside it; the reader gets a boundary they can see instead of a token they have to
 *  read. `method` splits a run too — an AI draft and a person's sentence are not interchangeable to a
 *  reviewer, and one header cannot honestly speak for both.
 *
 *  Grouping is a RENDERING decision over unchanged data. Nothing is re-derived, nothing is dropped:
 *  concatenating the runs' items reproduces the input in order. */
export function proseRuns<T>(items: T[], prose: (item: T) => SpecProse): ProseRun<T>[] {
  const runs: ProseRun<T>[] = [];
  for (const item of items) {
    const p = prose(item);
    const open = runs[runs.length - 1];
    if (open && open.provenance === p.provenance && open.method === p.method) open.items.push(item);
    else runs.push({ provenance: p.provenance, method: p.method, items: [item] });
  }
  return runs;
}

/** SPEC-EXPLAIN-VIEW — the overlay with every `narrated` field removed.
 *
 *  The Explain view is a PROVENANCE FILTER, not a second document. Off, the page shows only what can
 *  be stated without interpretation: the ELM's structure, metrics and code, plus prose its author
 *  published (a CQL `@` tag, or a field of the FHIR `Measure` resource — both `authored`). On, the
 *  interpreted layer is added back. Nothing is re-derived and no section moves, because placement was
 *  never the overlay's to decide (`SPEC-PLACEMENT`).
 *
 *  Filtering by PROVENANCE rather than by source file is what makes it honest. A steward's own description
 *  is not an interpretation just because it arrives through the same payload as our narration, and
 *  hiding it would misrepresent the deterministic view as poorer than it is.
 *
 *  A field-level filter, deliberately: an element narrated in some fields and authored in others keeps
 *  exactly the authored ones, which is the same per-field rule the provenance chips already follow. */
export function withoutNarrated(overlay: SpecificationOverlay | null): SpecificationOverlay | null {
  if (!overlay) return null;
  const keep = (t?: NarratedText): NarratedText | undefined =>
    t && t.provenance !== "narrated" ? t : undefined;
  const elements: SpecificationOverlay["elements"] = {};
  for (const [key, n] of Object.entries(overlay.elements)) {
    const kept = {
      title: keep(n.title), description: keep(n.description),
      rationale: keep(n.rationale), guidance: keep(n.guidance),
    };
    // An element left with nothing is dropped, so the page reaches its honest "not narrated" state
    // rather than rendering an empty narration block.
    if (Object.values(kept).some(Boolean)) elements[key] = kept;
  }
  const libraryOverviews: SpecificationOverlay["libraryOverviews"] = {};
  for (const [name, t] of Object.entries(overlay.libraryOverviews)) {
    const k = keep(t);
    if (k) libraryOverviews[name] = k;
  }
  const intent = overlay.overview.intent && {
    statement: keep(overlay.overview.intent.statement),
    clinicalRationale: keep(overlay.overview.intent.clinicalRationale),
    recommendation: keep(overlay.overview.intent.recommendation),
  };
  const scope = overlay.overview.scope && {
    inScope: keep(overlay.overview.scope.inScope),
    nonGoals: (overlay.overview.scope.nonGoals ?? []).filter((n) => n.provenance !== "narrated"),
  };
  return {
    ...overlay,
    // The precedence statement is the product's own prose about the document, not narration of the
    // measure, and it is the one line that stays true in both views.
    overview: {
      description: keep(overlay.overview.description),
      intent: intent && Object.values(intent).some(Boolean) ? intent : undefined,
      scope: scope && (scope.inScope || scope.nonGoals.length > 0) ? scope : undefined,
      guidance: keep(overlay.overview.guidance),
      howToRead: keep(overlay.overview.howToRead),
    },
    elements,
    libraryOverviews,
  };
}

/** One derived element — a Concept or a Rule. Both are defines, so both carry the same evidence. */
interface SpecElementBase {
  /** Unique within the document: library + define. Safe as a React key. */
  key: string;
  define: string;
  library: string;
  /** SPEC-ANCHOR — keyed off the library-qualified define name, so it survives a recompile. */
  domId: string;
  /** PRESENTATIONAL short label ("C1", "QBR-3"). It renumbers when an element is inserted, so nothing
   *  may key off it — anchors and citations use `domId` / `define`. */
  alias: string;
  metric: SpecMetric;
  /** The other elements this one references — the Sources chips. */
  sources: SpecRef[];
  /** Prose the CQL carries about itself. Null when the define is un-annotated: the element then renders
   *  under its define name — degraded, never wrong, and never filled in with a guess. */
  narration: SpecNarration | null;
  /** The catalogue entry, carrying the CQL this element derives from (SPEC-ANCHOR). */
  entry: SpecEntry;
}

export interface SpecRule extends SpecElementBase {
  polarity: RulePolarity;
}

export interface SpecConcept extends SpecElementBase {
  rules: SpecRule[];
}

/** A population criterion, or COMMON — the two render in the identical card format because placement
 *  makes COMMON the degenerate case of the same rule. */
export interface SpecScope {
  kind: "population" | "common";
  /** The population's entry; null for COMMON, which is not a define. */
  population: SpecEntry | null;
  /** Display name — the population's define name, or "Common". */
  name: string;
  domId: string;
  /** SPEC-NARRATIVE-TAGS — the population's own what/why/how-to-read. A population criterion is a
   *  define like any other, so it takes the same triple as a Concept or a Rule; narrating it with
   *  only a one-line "what" was the gap that made the document unreviewable. Null for COMMON, which
   *  is not a define. */
  narration: SpecNarration | null;
  metric: SpecMetric | null;
  concepts: SpecConcept[];
  /** Rules hoisted to this scope: reached from two or more of its Concepts, so they belong to neither. */
  rules: SpecRule[];
}

/** SPEC-DERIVED-APPENDIX — the document's reference tables, DERIVED from the ELM declarations.
 *
 *  These were hand-written in the narration instance, which meant an OID could go stale with nothing
 *  to catch it — prose a reader can see is wrong, a transcribed identifier they cannot. Every row here
 *  is a declaration of a library in the closure, library-qualified because two libraries may bind the
 *  same value-set name to different OIDs.
 *
 *  Code counts and expansion dates are NOT here. They belong to the terminology tables
 *  (CONTENT-VSAC-AUTHORITY), which the document has no read path to yet, so the columns are omitted
 *  rather than filled from a transcription. */
export interface SpecAppendix {
  valuesets: { name: string; oid: string | null; library: string }[];
  codes: { name: string; code: string | null; display: string | null; system: string | null; library: string }[];
  parameters: { name: string; type: string | null; library: string }[];
}

/** SPEC-DEFINITIONS — what a Definitions card renders for one entry: the SAME derivation the
 *  criteria sections use, so a define reads identically wherever it is opened. Keyed by the entry's
 *  library-qualified key, because two libraries may declare one name. */
export interface SpecDefineView {
  narration: SpecNarration | null;
  metric: SpecMetric;
}

export interface SpecDocument {
  /** Population criteria in eCQM order. */
  populations: SpecScope[];
  /** Null when nothing is shared across non-reaching populations. */
  common: SpecScope | null;
  /** The derived reference tables. */
  appendix: SpecAppendix;
  /** Every entry in the model, by `library::define` — what the Definitions cards render. */
  byDefine: Record<string, SpecDefineView>;
}

/** Populations whose members are being EXCLUDED — everything they reach is disqualifying by position,
 *  regardless of the negation parity inside them. */
const EXCLUSION_POPULATIONS = new Set<string>([
  "Denominator Exclusions", "Denominator Exceptions", "Numerator Exclusions",
]);

const defId = (name: string) => `def:${name}`;
const fnId = (name: string) => `fn:${name}`;

/** Build the Specification's derived structure (SPEC-DOCUMENT). See the module header for the three
 *  determinations; every STRUCTURAL input is compiler-derived.
 *
 *  `overlay` contributes PROSE ONLY (APP-NARRATIVE-OVERLAY). It cannot add, remove, reorder or reparent
 *  an element — placement is the ELM's determination and stays that way — so a wrong overlay produces
 *  wrong words, never a wrong document. */
export function buildSpecDocument(
  model: SpecModel, elmByLibrary: Map<string, unknown>, overlay?: SpecificationOverlay | null,
): SpecDocument {
  const entries = entryIndex(model);
  const kinds = kindIndex(model);
  const negated = negatedReferences(elmByLibrary, kinds);
  const contexts = contextIndex(elmByLibrary);
  const narration = narrationIndex(elmByLibrary, entries, overlay ?? null);

  // Every population present in the model, in eCQM order.
  const populations = POPULATIONS
    .map((name) => model.populations.find((p) => p.name === name))
    .filter((p): p is SpecEntry => Boolean(p));

  // What each population reaches, and which OTHER populations it reaches — the qualifier PLACEMENT
  // turns on below. Closure is over the ELM graph, which resolves cross-library refs exactly.
  const popNames = new Set(populations.map((p) => p.name));
  const reachOf = new Map<string, Set<string>>();
  const popsReached = new Map<string, string[]>();
  for (const p of populations) {
    const reach = definesReachedFrom(model.graph, defId(p.name), kinds);
    reachOf.set(p.name, reach);
    popsReached.set(p.name, [...reach].filter((n) => popNames.has(n) && n !== p.name));
  }

  // Depth-1 defines per population — its Concepts, before placement decides where each one lands.
  const conceptsOf = new Map<string, string[]>();
  const everyConcept = new Set<string>();
  for (const p of populations) {
    const direct = directDefines(model.graph, defId(p.name), kinds).filter((n) => !popNames.has(n));
    conceptsOf.set(p.name, direct);
    for (const c of direct) everyConcept.add(c);
  }

  // PLACEMENT (SPEC-PLACEMENT). A define's scope is the single minimal population that reaches it, or
  // COMMON when two populations reach it and neither reaches the other.
  const scopeOf = new Map<string, string | null>(); // define -> population name, or null for COMMON
  for (const define of allReachedDefines(reachOf)) {
    const reaching = populations.filter((p) => reachOf.get(p.name)!.has(define)).map((p) => p.name);
    const minimal = reaching.filter((p) => !reaching.some((q) => q !== p && popsReached.get(p)!.includes(q)));
    scopeOf.set(define, minimal.length === 1 ? minimal[0]! : null);
  }

  // Concept aliases number across the whole document (C1, C2, …), so a citation is unambiguous.
  let conceptRank = 0;
  const build = (scopeName: string | null): { concepts: SpecConcept[]; rules: SpecRule[] } => {
    // The Concepts landing in this scope, in the order their population reaches them.
    const conceptNames = orderDefines(
      [...everyConcept].filter((c) => (scopeOf.get(c) ?? null) === scopeName),
      model.graph,
    );

    // Every define in this scope that is not itself a Concept is a Rule; its owner is the Concept whose
    // closure contains it, or the scope itself when two Concepts share it.
    const ruleNames = orderDefines(
      [...scopeOf.entries()]
        .filter(([d, s]) => s === scopeName && !everyConcept.has(d) && !popNames.has(d))
        .map(([d]) => d),
      model.graph,
    );

    const closureByConcept = new Map<string, Set<string>>();
    for (const c of conceptNames) closureByConcept.set(c, definesReachedFrom(model.graph, defId(c), kinds));

    const concepts: SpecConcept[] = [];
    const hoisted: SpecRule[] = [];
    const ruleOwner = new Map<string, string | null>();
    for (const r of ruleNames) {
      const owners = conceptNames.filter((c) => closureByConcept.get(c)!.has(r));
      ruleOwner.set(r, owners.length === 1 ? owners[0]! : null);
    }

    for (const name of conceptNames) {
      const entry = entries.get(name);
      if (!entry) continue;
      conceptRank += 1;
      const owned = ruleNames.filter((r) => ruleOwner.get(r) === name);
      const alias = `C${conceptRank}`;
      const built = elementBase(entry, alias, model, entries, contexts, narration, scopeName, kinds);
      const rules = owned.map((r, i) => makeRule(r, alias, i + 1, scopeName));
      // A Concept's Sources exclude its own Rules: the rules ARE the decomposition, so listing them
      // again below restates the card in chip form. Sources are what the Concept reaches BESIDES the
      // ideas it is already made of.
      const ownRules = new Set(rules.map((r) => r.define));
      concepts.push({ ...built, sources: built.sources.filter((s) => !ownRules.has(s.define)), rules });
    }
    // Shared between Concepts (or reached with no Concept in between) — this scope's own rules.
    // Scope-level rules stem from the POPULATION name, which is always a short eCQM standard name
    // ("Initial Population" → IP, "Denominator Exclusions" → DE), so initials read well there.
    const scopeAlias = initials(scopeName ?? "Common");
    hoisted.push(
      ...ruleNames.filter((r) => ruleOwner.get(r) === null)
        .map((r, i) => makeRule(r, scopeName ?? "Common", i + 1, scopeName, scopeAlias)),
    );
    return { concepts, rules: hoisted };

    function makeRule(
      name: string, ownerName: string, index: number, scope: string | null, aliasStem?: string,
    ): SpecRule {
      const entry = entries.get(name)!;
      // Rules under a Concept stem from the CONCEPT'S ALIAS (C10-1), not from initials of its name.
      // Real define names are long and clinical — "Has Encounter During Period and Condition Active At
      // Its Start" initialises to "HEDP", which is noise. The concept alias
      // is always short, always readable, and makes the parent obvious at a glance.
      const stem = aliasStem ?? ownerName;
      return {
        ...elementBase(entry, `${stem}-${index}`, model, entries, contexts, narration, scope, kinds),
        polarity: polarityOf(name, entry, scope, negated, reachOf),
      };
    }
  };

  const populationScopes: SpecScope[] = populations.map((p) => {
    const built = build(p.name);
    return {
      kind: "population" as const,
      population: p,
      name: p.name,
      domId: p.domId,
      narration: narration.get(p.name) ?? null,
      metric: metricFor(p, contexts, null),
      concepts: built.concepts,
      rules: built.rules,
    };
  });

  const commonBuilt = build(null);
  const common: SpecScope | null =
    commonBuilt.concepts.length === 0 && commonBuilt.rules.length === 0
      ? null
      : {
          kind: "common", population: null, name: "Common", domId: COMMON_DOM_ID,
          narration: null, metric: null,
          concepts: commonBuilt.concepts, rules: commonBuilt.rules,
        };

  // SPEC-DEFINITIONS — every entry, not only the ones a population reaches. A define nobody reaches
  // still has a dependency index and still deserves a card; that it is unreachable is reported by
  // the elimination rule, not by withholding its derivation.
  const byDefine: Record<string, SpecDefineView> = {};
  for (const entry of [...model.populations, ...model.libraries.flatMap((l) => l.entries)]) {
    byDefine[entry.key] = {
        narration: narration.get(entry.name) ?? null,
      metric: metricFor(entry, contexts, null),
    };
  }

  return {
    populations: populationScopes, common, appendix: buildAppendix(model, elmByLibrary), byDefine,
  };
}

/** SPEC-DERIVED-APPENDIX — the reference tables, read off the ELM of every library in the closure.
 *
 *  Library order follows the model (root first, then includes in declaration order) and declaration
 *  order within each, so the tables read the way the CQL is written. A name declared by two libraries
 *  yields two rows: they are two bindings, and collapsing them would hide a real disagreement about
 *  which OID a name means. */
function buildAppendix(model: SpecModel, elmByLibrary: Map<string, unknown>): SpecAppendix {
  const appendix: SpecAppendix = { valuesets: [], codes: [], parameters: [] };
  for (const lib of model.libraries) {
    const elm = elmByLibrary.get(lib.name);
    if (!elm) continue;
    const { valueSets, codes, parameters } = elmReferenceData(elm);
    for (const v of valueSets) appendix.valuesets.push({ name: v.name, oid: v.id, library: lib.name });
    for (const c of codes) {
      appendix.codes.push({ name: c.name, code: c.code, display: c.display, system: c.system, library: lib.name });
    }
    for (const p of parameters) appendix.parameters.push({ name: p.name, type: p.type, library: lib.name });
  }
  return appendix;
}

/** The shared shape of a Concept and a Rule — both are defines, so both derive it the same way. */
function elementBase(
  entry: SpecEntry, alias: string, model: SpecModel, entries: Map<string, SpecEntry>,
  contexts: Map<string, string | null>, narration: Map<string, SpecNarration>, scope: string | null,
  kinds: Map<string, "define" | "function">,
): Omit<SpecConcept, "rules"> {
  return {
    key: entry.key,
    define: entry.name,
    library: entry.library,
    domId: entry.domId,
    alias,
    metric: metricFor(entry, contexts, scope),
    sources: sourcesOf(entry.name, model, entries, kinds),
    narration: narration.get(entry.name) ?? null,
    entry,
  };
}

/** SPEC-METRIC — both grains from the ELM result type, the subject grain from the ELM context, base
 *  always stated. */
function metricFor(
  entry: SpecEntry, contexts: Map<string, string | null>, scope: string | null,
): SpecMetric {
  const resultType = entry.variants[0]?.returns ?? null;
  return {
    form: metricForm(resultType),
    subjectUnit: unitOf(contexts.get(entry.name) ?? null),
    elementGrain: elementGrainOf(resultType),
    resultType,
    valueShape: valueShapeOf(resultType),
    countOf: entry.name,
    base: scope,
  };
}

/** Boolean → a count; a list → how many the subject has; a number → its distribution; anything else
 *  (an interval, a datetime) has no honest figure, so it has none. */
function metricForm(resultType: string | null): MetricForm {
  if (!resultType) return "none";
  if (resultType === "Boolean") return "count";
  if (resultType.startsWith("List<")) return "cardinality-histogram";
  if (resultType === "Integer" || resultType === "Decimal" || resultType === "Long") return "distribution";
  if (resultType === "Quantity" || resultType === "FHIR.Quantity") return "distribution";
  if (resultType.startsWith("Interval<") || resultType === "DateTime" || resultType === "Date") return "none";
  // A selected resource or tuple is present-or-absent per subject, which IS a count.
  return "count";
}

/** The thing a resource-valued define counts, so the metric can read "412,003 encounters" beside its
 *  subject figure. `List<FHIR.Encounter>` and a bare `FHIR.Encounter` both have the Encounter grain;
 *  a Boolean has none, because the only thing it says anything about is the subject.
 *
 *  A GRAIN IS A NOUN A READER CAN SAY, NOT A TYPE EXPRESSION. The figure renders it lower-cased and
 *  pluralised ("412,003 encounters"), so anything that is not a single named type comes out as the
 *  declaration itself printed into a heading — `Choice<FHIR.Condition, FHIR.Condition>` did, lower-cased
 *  and with an `s` on the closing angle bracket. A choice has no one element name by construction, so it
 *  has NO grain, the same answer a Boolean and a Tuple already give. */
function elementGrainOf(resultType: string | null): string | null {
  if (!resultType) return null;
  const inner = resultType.startsWith("List<") && resultType.endsWith(">")
    ? resultType.slice(5, -1)
    : resultType;
  if (inner === "Boolean" || inner.startsWith("Interval<") || inner.startsWith("Choice<")
    || inner === "Tuple") return null;
  return inner.startsWith("FHIR.") ? inner.slice(5) : inner;
}

/** CQL's own scalars, plus the FHIR COMPLEX datatypes that are values rather than records. FHIR primitives
 *  (`FHIR.dateTime`, `FHIR.string`) are lowerCamelCase by spec convention and are caught by that instead of
 *  being enumerated here — the list would be forty entries long and would still miss the next one. */
const VALUE_TYPES = new Set([
  "Any", "Boolean", "Integer", "Long", "Decimal", "String", "Date", "DateTime", "Time",
  "Quantity", "Ratio", "Range", "Code", "Concept", "Coding", "CodeableConcept", "Period",
]);

/** APP-VALUE-READABLE — a row, or a value? Unwraps `List<…>`, because a list of Encounters and one
 *  Encounter are the same KIND of thing returned once or many times.
 *
 *  IT DEFAULTS TO `row`, and the asymmetry is the point: an unrecognized type read as a value draws a bar
 *  that asserts a frequency nobody measured, while one read as a row merely declines to draw it. When only
 *  one of two errors states something false, the default belongs on the other side. With NO result type at
 *  all — the translator run without `EnableResultTypes` — there is nothing to dispatch on, so no bar. */
export function valueShapeOf(resultType: string | null): MetricValueShape {
  if (!resultType) return "row";
  let inner = resultType.trim();
  if (inner.startsWith("List<") && inner.endsWith(">")) inner = inner.slice(5, -1).trim();
  // An interval IS a value — one span, however it is written.
  if (inner.startsWith("Interval<")) return "value";
  // A tuple is an anonymous record, which is a row with no type name.
  if (inner === "Tuple" || inner.startsWith("Tuple{")) return "row";
  const bare = inner.startsWith("FHIR.") ? inner.slice(5) : inner;
  if (VALUE_TYPES.has(bare)) return "value";
  // A FHIR primitive is lowerCamelCase; a FHIR resource is UpperCamelCase.
  return bare.length > 0 && bare[0] === bare[0]!.toLowerCase() ? "value" : "row";
}

function unitOf(context: string | null): MetricUnit {
  if (context === "Encounter") return "encounters";
  if (context === "Episode of Care") return "episodes";
  if (context === "Patient") return "patients";
  return "records";
}

/** SPEC-POLARITY — a rule disqualifies when its reference sits under an odd number of ELM `Not` nodes,
 *  or when the population that reaches it is an exclusion.
 *
 *  A define that computes a value rather than filtering is `computational` — neither qualifying nor
 *  disqualifying, and dressing it as either would misread the logic. Telling the two apart needs the
 *  declared result type, which the ELM does not carry today (see the module header): until it does, no
 *  element is classified `computational`, because the information to do so does not exist. */
function polarityOf(
  name: string, entry: SpecEntry, scope: string | null,
  negated: Set<string>, reachOf: Map<string, Set<string>>,
): RulePolarity {
  const resultType = entry.variants[0]?.returns ?? null;
  const computes = resultType !== null && resultType !== "Boolean" && !resultType.startsWith("List<");
  if (computes) return "computational";
  if (negated.has(name)) return "disqualifying";
  for (const pop of EXCLUSION_POPULATIONS) {
    if (reachOf.get(pop)?.has(name)) return "disqualifying";
  }
  if (scope !== null && EXCLUSION_POPULATIONS.has(scope)) return "disqualifying";
  return "qualifying";
}

/** The ELEMENTS an element directly references, as library-qualified links (SPEC-ANCHOR).
 *
 *  Functions are excluded here for the same reason they are excluded everywhere else: they are
 *  transparent plumbing, not ideas. Listing them turned a shared exclusion define into a chip row of
 *  conversion helpers and nothing a clinician could use. The walk still passes THROUGH a function to
 *  the defines beyond it, so nothing real is lost. */
function sourcesOf(
  name: string, model: SpecModel, entries: Map<string, SpecEntry>,
  kinds: Map<string, "define" | "function">,
): SpecRef[] {
  const node = model.graph.byId[defId(name)] ?? model.graph.byId[fnId(name)];
  if (!node) return [];
  const out: SpecRef[] = [];
  const seen = new Set<string>();
  const visit = (nodeId: string, depth: number) => {
    if (depth > 8) return; // a helper chain this deep is plumbing, not provenance
    for (const depId of model.graph.byId[nodeId]?.deps ?? []) {
      const dep = model.graph.byId[depId];
      if (!dep || dep.type === "valueset" || dep.type === "retrieve") continue;
      if (kinds.get(dep.label) === "function") { visit(depId, depth + 1); continue; }
      if (seen.has(dep.label)) continue;
      seen.add(dep.label);
      const entry = entries.get(dep.label);
      if (entry) out.push({ library: entry.library, define: entry.name, domId: entry.domId });
    }
  };
  visit(node.id, 0);
  return out;
}

// ── ELM walks ────────────────────────────────────────────────────────────────────────────────────

/** Defines referenced under an odd number of `Not` nodes, anywhere in the compiled set.
 *
 *  This is the one thing the dependency graph cannot answer: it records THAT one define references
 *  another, not whether the reference was negated. Read at the reference site, which is where the ELM
 *  actually holds it. */
function negatedReferences(
  elmByLibrary: Map<string, unknown>, kinds: Map<string, "define" | "function">,
): Set<string> {
  const negated = new Set<string>();
  const walk = (node: unknown, depth: number): void => {
    if (Array.isArray(node)) {
      for (const child of node) walk(child, depth);
      return;
    }
    if (typeof node !== "object" || node === null) return;
    const obj = node as Record<string, unknown>;
    const type = obj["type"];
    const next = type === "Not" ? depth + 1 : depth;
    if ((type === "ExpressionRef" || type === "FunctionRef") && next % 2 === 1) {
      const ref = obj["name"];
      if (typeof ref === "string" && kinds.has(ref)) negated.add(ref);
    }
    for (const key of Object.keys(obj)) {
      // Type specifiers describe TYPES, not referenced expressions — descending invents references.
      if (key === "signature" || key === "resultTypeSpecifier" || key === "operandTypeSpecifier") continue;
      walk(obj[key], next);
    }
  };
  for (const elm of elmByLibrary.values()) {
    const lib = (elm as Record<string, unknown> | null)?.["library"] as Record<string, unknown> | undefined;
    for (const stmt of ((lib?.["statements"] as Record<string, unknown[]> | undefined)?.["def"] ?? [])) {
      walk((stmt as Record<string, unknown>)["expression"], 0);
    }
  }
  return negated;
}

// ── Graph walks (functions transparent) ──────────────────────────────────────────────────────────

/** The DEFINES a node reaches directly, seeing through functions. A function is never an element, but
 *  the defines it calls are — so `Exclusions."Has Excluded Care"` reached via a helper still surfaces. */
function directDefines(graph: LogicGraph, id: string, kinds: Map<string, "define" | "function">): string[] {
  const out: string[] = [];
  const seen = new Set<string>([id]);
  const visit = (nodeId: string): void => {
    const node = graph.byId[nodeId];
    if (!node) return;
    for (const depId of node.deps) {
      if (seen.has(depId)) continue;
      seen.add(depId);
      const dep = graph.byId[depId];
      if (!dep || dep.type === "valueset" || dep.type === "retrieve") continue;
      if (kinds.get(dep.label) === "function") visit(depId); // transparent
      else out.push(dep.label);
    }
  };
  visit(id);
  return out;
}

/** Every define in a node's transitive closure, functions excluded but walked through. */
function definesReachedFrom(
  graph: LogicGraph, id: string, kinds: Map<string, "define" | "function">,
): Set<string> {
  const out = new Set<string>();
  for (const depId of closureOf(graph, id)) {
    const dep = graph.byId[depId];
    if (!dep || dep.type === "valueset" || dep.type === "retrieve") continue;
    if (kinds.get(dep.label) === "function") continue;
    out.add(dep.label);
  }
  return out;
}

function allReachedDefines(reachOf: Map<string, Set<string>>): Set<string> {
  const out = new Set<string>();
  for (const reach of reachOf.values()) for (const n of reach) out.add(n);
  return out;
}

/** Presentational order: graph layer (deepest dependency first), then name. ELM has no evaluation
 *  order, so this is a stable reading order and nothing more. */
function orderDefines(names: string[], graph: LogicGraph): string[] {
  return names.slice().sort((a, b) => {
    const la = graph.byId[defId(a)]?.layer ?? 0;
    const lb = graph.byId[defId(b)]?.layer ?? 0;
    return lb - la || a.localeCompare(b);
  });
}

// ── Indexes ──────────────────────────────────────────────────────────────────────────────────────

function entryIndex(model: SpecModel): Map<string, SpecEntry> {
  const out = new Map<string, SpecEntry>();
  for (const p of model.populations) out.set(p.name, p);
  for (const lib of model.libraries) for (const e of lib.entries) if (!out.has(e.name)) out.set(e.name, e);
  return out;
}

function kindIndex(model: SpecModel): Map<string, "define" | "function"> {
  const out = new Map<string, "define" | "function">();
  for (const lib of model.libraries) {
    for (const e of lib.entries) out.set(e.name, e.kind === "function" ? "function" : "define");
  }
  for (const p of model.populations) out.set(p.name, "define");
  return out;
}

/** SPEC-NARRATIVE-TAGS — each define's prose, from its CQL tags first and the overlay second.
 *
 *  THE PRIORITY PATH IS THE CQL. Tags in the comment above a define (`@description`, `@rationale`,
 *  `@guidance`, …) are emitted into `annotation[].t[]` when `EnableAnnotations` is on, and they travel
 *  with the artifact to anyone else's CQL tooling. That makes them authoritative in a way a file we
 *  own can never be, so they win FIELD BY FIELD: a define with `@description` and no `@guidance` shows
 *  its authored description beside a narrated guidance line, each labelled for what it is.
 *
 *  Tags the translator does not recognise are still emitted — the CQL spec allows any identifier — so
 *  unknown tag names are ignored rather than guessed at. `annotation` also carries the `a:s` source
 *  narrative, which is not prose about the define and is skipped. */
function narrationIndex(
  elmByLibrary: Map<string, unknown>, entries: Map<string, SpecEntry>,
  overlay: SpecificationOverlay | null,
): Map<string, SpecNarration> {
  const authored = new Map<string, SpecNarration>();
  for (const elm of elmByLibrary.values()) {
    const lib = (elm as Record<string, unknown> | null)?.["library"] as Record<string, unknown> | undefined;
    for (const stmt of ((lib?.["statements"] as Record<string, unknown[]> | undefined)?.["def"] ?? [])) {
      const s = stmt as Record<string, unknown>;
      const name = s["name"];
      if (typeof name !== "string" || authored.has(name)) continue;
      const narration = narrationFromTags(tagsOf(s["annotation"]));
      if (narration) authored.set(name, narration);
    }
  }
  if (!overlay) return authored;

  // Join the overlay by the LIBRARY-QUALIFIED key. The declaring library comes from the entry index,
  // so `Exclusions."Has Excluded Care"` reads Exclusions' narration and not the root library's.
  const out = new Map<string, SpecNarration>(authored);
  for (const [name, entry] of entries) {
    const narrated = overlay.elements[overlayKey(entry.library, name)];
    if (!narrated) continue;
    const own = authored.get(name);
    out.set(name, {
      title: own?.title ?? prose(narrated.title),
      text: own?.text ?? prose(narrated.description),
      // No overlay counterpart, by design: `@comment` is free-form elaboration and the XSD carries the
      // triple. There is nothing to fall back TO, so the tag's own value passes straight through.
      comment: own?.comment ?? null,
      rationale: own?.rationale ?? prose(narrated.rationale),
      guidance: own?.guidance ?? prose(narrated.guidance),
    });
  }
  return out;
}

/** One overlay field as prose, or null. `provenance` is the server's — never assumed here, because
 *  an overlay may legitimately carry `authored` text (a measure we own, tags already accepted). */
function prose(field: NarratedText | undefined): SpecProse | null {
  // Prose may be device blocks with no lead sentence — a bare Include/Exclude list is a
  // legitimate answer to "what is this".
  if (!field || (!field.text && !field.blocks?.length)) return null;
  return {
    text: field.text,
    segments: field.segments,
    blocks: field.blocks,
    sourceSpan: field.sourceSpan,
    provenance: field.provenance === "authored" ? "authored" : "narrated",
    method: field.method,
    sourceTag: field.sourceTag,
  };
}

/** The `{name, value}` tag pairs on an ELM element, flattened across its annotations. */
function tagsOf(annotation: unknown): Map<string, string> {
  const out = new Map<string, string>();
  const entries = Array.isArray(annotation) ? annotation : annotation == null ? [] : [annotation];
  for (const entry of entries) {
    if (typeof entry !== "object" || entry === null) continue;
    const t = (entry as Record<string, unknown>)["t"];
    if (!Array.isArray(t)) continue;
    for (const tag of t) {
      if (typeof tag !== "object" || tag === null) continue;
      const { name, value } = tag as Record<string, unknown>;
      if (typeof name === "string" && typeof value === "string" && !out.has(name)) {
        out.set(name, value.trim());
      }
    }
  }
  return out;
}

/** The element-level tags (designer/docs/narrative-tags.md), each `authored` because a tag in the CQL
 *  is the author's own assertion.
 *
 *  `@comment` JOINS THE FOUR. It is not a fifth question — it is the author finishing the answer to
 *  the first one — but it is prose they published with the artifact, and this document carries it.
 *  Shared helper libraries show why: an interval helper documented with a one-line `@description`
 *  and a paragraph of `@comment` saying which timing choice types it covers. The page prints both; the
 *  paragraph. The tag vocabulary is open (`designer/docs/narrative-tags.md`), so this list is what we
 *  RENDER, never what an author may write; the unlisted ones are still carried on the wire, unread. */
function narrationFromTags(tags: Map<string, string>): SpecNarration | null {
  const authored = (tag: string): SpecProse | null => {
    const text = tags.get(tag);
    return text ? { text, provenance: "authored", sourceTag: tag } : null;
  };
  const narration: SpecNarration = {
    title: authored("title"), text: authored("description"), comment: authored("comment"),
    rationale: authored("rationale"), guidance: authored("guidance"),
  };
  return isNarrated(narration) ? narration : null;
}

/** Each define's ELM context — the metric's unit comes from here, so an episode measure never reports
 *  "patients". Read off the compiled statements, which is where the translator records it. */
function contextIndex(elmByLibrary: Map<string, unknown>): Map<string, string | null> {
  const out = new Map<string, string | null>();
  for (const elm of elmByLibrary.values()) {
    const lib = (elm as Record<string, unknown> | null)?.["library"] as Record<string, unknown> | undefined;
    for (const stmt of ((lib?.["statements"] as Record<string, unknown[]> | undefined)?.["def"] ?? [])) {
      const s = stmt as Record<string, unknown>;
      const name = s["name"];
      if (typeof name === "string" && !out.has(name)) {
        out.set(name, typeof s["context"] === "string" ? (s["context"] as string) : null);
      }
    }
  }
  return out;
}

/** "Initial Population" → "IP". The stem a SCOPE's own rules carry — presentational only, and used
 *  for population names, which are short eCQM standards rather than long clinical phrases. */
export function initials(name: string): string {
  const words = name.split(/[^A-Za-z0-9]+/).filter(Boolean);
  const letters = words.map((w) => w[0]!.toUpperCase()).join("");
  return letters.slice(0, 4) || "R";
}

/** SPEC-ANCHOR — the COMMON section's anchor. Populations use their own entry's anchor. */
export const COMMON_DOM_ID = "mw-spec-common";

// ── REND-BADGES — section health clues ───────────────────────────────────────────────────────────
//
// Two per section, each scoped to that section's own defines. These are NOT part of the derivation: a
// badge is run-scoped (Validation) or environment-scoped (Mapping), so it joins at render the same way a
// metric does. The derivation stays a pure function of the ELM; badges are what the other lenses say
// about the slice of it this section covers. Both now portal to VALIDATE — correctness and feasibility
// are read in one lens since Map retired (APP-MAP-LENS), which is why they still say which they are.

export type BadgeState = "pass" | "findings" | "not-run";

export interface SpecBadge {
  state: BadgeState;
  /** Present only when the state is `findings` and the count is real. */
  count?: number;
  /** The badge portals to Validate: the Map lens is retired and its coverage read moved there
   *  (APP-MAP-LENS). The field stays because a badge naming its destination is what keeps it a
   *  portal rather than a decoration. */
  lens: "validate";
  /** What the badge is reporting, for its tooltip — never a bare state with no explanation. */
  detail: string;
}

/** ONE BADGE: VALIDATION only. A badge that states nothing on every section of every document
 *  teaches readers to ignore the badge beside it. MAPPING will return when `describe` → RetrieveHints
 *  lands with a coverage figure to carry; the reasoning is preserved in `APP-MAP-LENS`. */
export interface SpecBadges {
  validation: SpecBadge;
}

/** The per-population Scorekey verdict a Conformance run stamps (REG-CONFORMANCE-DECK). */
export interface ScorekeyPopulation {
  mismatches: number;
}

/** REND-BADGES — what Validate and Map say about one section.
 *
 *  VALIDATION reports only what is actually attributable to these defines. Compile diagnostics are
 *  deliberately NOT included: when a library fails to compile there is no ELM, so there is no document
 *  to badge — attributing them to a section would be answering a question that cannot arise. */
export function sectionBadges(input: {
  /** Every define this section covers — the Concept and its Rules, or the population and its own. */
  defines: string[];
  /** The population the section serves, for the Scorekey lookup. Null for COMMON. */
  population: string | null;
  live: boolean;
  counts: Record<string, number> | null;
  scorekey: Record<string, ScorekeyPopulation> | null;
}): SpecBadges {
  if (!input.live || !input.counts) {
    return {
      validation: { state: "not-run", lens: "validate", detail: "Run the measure to check this section." },
    };
  }

  const findings: string[] = [];
  // The Scorekey verdict for the population this section serves — does the engine reproduce the
  // golden result? That is the compliance question, and it is the strongest signal a section can carry.
  const verdict = input.population ? input.scorekey?.[input.population] : undefined;
  if (verdict && verdict.mismatches > 0) {
    findings.push(`${verdict.mismatches} Scorekey mismatch${verdict.mismatches === 1 ? "" : "es"}`);
  }
  // AN EMPTY CRITERION DOES NOT RAISE A FINDING. A signal that fires on the normal case is not a finding,
  // it is a fact about the population. The count is already stated on the criterion itself.
  // The Scorekey verdict stays: "the engine did not reproduce the golden result" is a finding.
  //
  // WHAT RESTORES IT is not this line but a better home: an empty criterion belongs in the readiness
  // read, where it can be stated with its cause, next to the terminology that produced no rows.

  return {
    validation: findings.length > 0
      ? { state: "findings", count: findings.length, lens: "validate", detail: findings.join(" · ") }
      : { state: "pass", lens: "validate", detail: "No findings for this section." },
  };
}

/** Every define a section covers, for badge scoping. */
export function scopeDefines(scope: SpecScope): string[] {
  const out = scope.population ? [scope.population.name] : [];
  for (const c of scope.concepts) { out.push(c.define); for (const r of c.rules) out.push(r.define); }
  for (const r of scope.rules) out.push(r.define);
  return out;
}

/** Every define a Concept covers — itself and its Rules. */
export function conceptDefines(concept: SpecConcept): string[] {
  return [concept.define, ...concept.rules.map((r) => r.define)];
}
