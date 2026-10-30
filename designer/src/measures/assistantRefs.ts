// APP-ASSISTANT-REFS — the assistant's answers point at the document, in the document's own
// vocabulary.
//
// WHAT THIS IS. The model writes ordinary markdown links whose target is a `cql-databricks:` address —
// `[example condition](cql-databricks:define/ExampleMeasure/Example%20Condition%20Diagnosis)` —
// and this turns one into the SAME `SpecSegment` the Specification instance carries. The drawer then
// renders it through the document's own `Segments`, so the phrase gets the same link ink, the same
// lens popup and the same verbs a phrase in the specification gets. Nothing here renders anything.
//
// WHY A LINK AND NOT A NEW WIRE FIELD. A link is already in the set the model is told it may write
// (`APP-ASSISTANT-MARKDOWN`), already parsed, and already degrades correctly: a target this module
// does not recognise, or one that resolves to nothing, leaves the words the model wrote. So the
// feature costs no new answer shape, no change to the SSE contract, and — the property that matters
// most — nothing to strip when an answer is quoted somewhere with no renderer.
//
// WHY THE MODEL CAN NAME THESE AT ALL. `get_specification` returns the composed overlay — the very
// payload the SPA renders — so the libraries, define names, value-set names and read profiles the
// model writes are read out of the document it is answering about, not invented. That is what makes
// this a citation rather than a guess.
//
// IT IS DELIBERATELY ABSENT FROM MCP. The syntax is taught only in `RENDERING_INSTRUCTIONS`, the
// drawer's own system message (`assistant/ask.py`). An external client — Genie One — has no renderer
// for a `cql-databricks:` target and would print the raw address at its reader, so it is never told the
// vocabulary exists. `ANSWERING_INSTRUCTIONS`, which both clients receive, says nothing about it.

import type { SpecDefineRef, SpecMetricRef, SpecReadRef, SpecSegment,
  SpecTerminologyRef } from "../library-client.ts";

/** The scheme every reference target carries. Bare, not `cql-databricks://`: these are opaque addresses in
 *  our own space rather than locations on a host, and a host-shaped url invites a reader to paste it
 *  into a browser. */
const SCHEME = "cql-databricks:";

/** A block-level artifact a fenced block names. Not a segment — a funnel is not a token in a
 *  sentence, and rendering one inside a paragraph would nest a figure inside prose. */
export type ArtifactKind = "funnel" | "chart";

/** The fence info strings that name an artifact. */
export const ARTIFACT_FENCE: Record<string, ArtifactKind> = {
  "cql-databricks-funnel": "funnel",
  "cql-databricks-chart": "chart",
};

/** One reference target, parsed into the segment the document renders — or null when it is not one of
 *  ours, or is malformed.
 *
 *  NULL IS A REAL ANSWER AND THE COMMON ONE. A target naming a kind we do not publish, or missing the
 *  parts its kind requires, degrades to the characters the model typed rather than throwing: an answer
 *  is not worth losing over a mistyped citation, and a half-built segment would resolve to some OTHER
 *  define, which is the one outcome worse than plain words.
 */
export function referenceSegment(text: string, href: string): SpecSegment | null {
  if (!href.startsWith(SCHEME)) return null;
  const [path, query] = href.slice(SCHEME.length).split("?");
  const parts = (path ?? "").split("/").map(decodeSegment).filter((p) => p.length > 0);
  const [kind, ...rest] = parts;
  const args = new URLSearchParams(query ?? "");

  if (kind === "define") return defineRef(text, rest);
  if (kind === "figure") return figureRef(rest, args);
  if (kind === "read") return readRef(text, rest, args);
  if (kind === "valueset") return terminologyRef(text, rest);
  return null;
}

/** `cql-databricks:define/<library>/<define>` — a phrase that names another element of the measure. */
function defineRef(text: string, rest: string[]): SpecDefineRef | null {
  const [library, define] = rest;
  if (!library || !define) return null;
  return {
    kind: "define",
    // THE PHRASE STAYS PROSE. The model's own words are what render; the declared name is only the
    // address. Substituting the latter would put `Example Condition Diagnosis` in title case in
    // the middle of a clinical sentence.
    text,
    library,
    define,
    // INERT HERE, and that is why it is not asked for. `elementKind` is a narrator's assertion for the
    // type chip and the DOCUMENT's own structure wins wherever they disagree (`SpecDefineRef`), so an
    // answer has nothing to add — and a model guessing "concept" versus "rule" would be inventing a
    // classification the compiler already established (`SPEC-EXPLAIN-INPUTS`).
    elementKind: "rule",
  };
}

/** `cql-databricks:figure/<library>/<define>?as=count|share&grain=subject|element` — a CITED figure.
 *
 *  The reference carries no number, which is the entire point (`SPEC-METRIC-REF`): the client resolves
 *  it against the same metric set the document and the nav render, so the assistant CANNOT state a
 *  wrong figure — it can only point at one — and the figure it points at is the engine's, not its own
 *  derivation (`SVC-ANSWER-PROVENANCE`). A model that wants to assert a number it computed has to
 *  write the number, where the derived-figure notice already covers it. */
function figureRef(rest: string[], args: URLSearchParams): SpecMetricRef | null {
  const [library, define] = rest;
  if (!library || !define) return null;
  const as = args.get("as");
  const grain = args.get("grain");
  return {
    kind: "metric",
    library,
    define,
    grain: grain === "element" ? "element" : "subject",
    as: as === "share" ? "share" : "count",
  };
}

/** `cql-databricks:read/<profile>` or `cql-databricks:read/type/<FhirType>` — a phrase that names a read.
 *
 *  PROFILE FIRST. "Problem list" and "encounter diagnosis" are two profiles of one resource type, so a
 *  type-addressed reference is the fallback for an unprofiled read and never the way to name a
 *  profiled one — addressing by type would give both phrases the same read and the same number. */
function readRef(text: string, rest: string[], args: URLSearchParams): SpecReadRef | null {
  const type = args.get("type") ?? (rest[0] === "type" ? rest[1] : undefined);
  const profile = rest[0] === "type" ? undefined : rest[0];
  if (!profile && !type) return null;
  return { kind: "read", text, ...(profile ? { profile } : {}), ...(type ? { type } : {}) };
}

/** `cql-databricks:valueset/<library>/<name>` — a phrase that names a value set or a direct reference code.
 *
 *  ONE KIND FOR BOTH, resolved out of the Appendix by `(library, name)`: which of the two a name is,
 *  is a fact the ELM states, so it is derived at resolution rather than declared in the address. */
function terminologyRef(text: string, rest: string[]): SpecTerminologyRef | null {
  const [library, name] = rest;
  if (!library || !name) return null;
  return { kind: "terminology", text, library, name };
}

/** Percent-decoding that cannot throw. A define name carries spaces and the occasional bracket, and a
 *  model that escapes one of them wrongly must cost the sentence its reference, not the answer. */
function decodeSegment(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}
