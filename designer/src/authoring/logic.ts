// APP-LOGIC-VIEW — derive the Narrative model from CQL source.
//
// The Narrative view is a single, ordered list of every definition with its CQL on demand
// (APP-LOGIC-VIEW); the CQL view is the whole library read-only. This module is the pure derivation
// the views render — a light SOURCE scan (not a parser; real semantics come from the engine via
// translate, APP-DIAGNOSTICS). Plain-language summaries per define are AI-generated in S3
// (APP-NL/APP-AI-CACHE); here each entry carries its own CQL slice.

export type DefineKind = "define" | "function";

export interface DefineEntry {
  /** The declared name (unquoted). */
  name: string;
  kind: DefineKind;
  /** public / private when stated, else "". */
  access: string;
  /** The define's own CQL slice (from its `define` line to the next top-level define / EOF). */
  cql: string;
}

// A top-level `define` with (in CQL order) an optional `public`/`private` access modifier, an optional
// `fluent` modifier, then an optional `function`, capturing the quoted or bare name. Anchored to line
// start (multiline) so it does not match the word "define" inside an expression or comment.
// `fluent` MUST be consumed here: without it `define fluent function "ToString"(…)` parses as a define
// NAMED "fluent", which then collapses every fluent function in a library into one bogus entry.
const DEFINE_RE = /^\s*define\s+(?:(public|private)\s+)?(?:fluent\s+)?(function\s+)?(?:"([^"]+)"|([A-Za-z_]\w*))/gm;

/** Every top-level definition in source order (APP-LOGIC-VIEW: a single ordered list), each with the
 *  CQL slice from its `define` to the next top-level define (or end of source). */
export function parseDefinitions(cql: string): DefineEntry[] {
  const starts: { idx: number; access: string; kind: DefineKind; name: string }[] = [];
  for (let m = DEFINE_RE.exec(cql); m !== null; m = DEFINE_RE.exec(cql)) {
    starts.push({
      idx: m.index,
      access: m[1] ?? "",
      kind: m[2] ? "function" : "define",
      name: m[3] ?? m[4] ?? "",
    });
  }
  return starts.map((s, i) => {
    const end = i + 1 < starts.length ? starts[i + 1]!.idx : cql.length;
    return { name: s.name, kind: s.kind, access: s.access, cql: cql.slice(s.idx, end).trim() };
  });
}
