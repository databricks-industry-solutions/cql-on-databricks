// APP-GRAMMAR — CQL syntax highlighting for STATIC code blocks.
//
// The Define lens discloses the CQL behind every element (`SPEC-LOGIC-PANES`). Unhighlighted, a
// `Query` with a `without … such that` relationship reads as an undifferentiated wall of mono, and
// the reader cannot see where the logic turns — which is the one thing the disclosure is for.
//
// ONE VOCABULARY, NOT A SECOND ONE. The keyword set is imported from `packages/cql-grammar`, the same
// Monarch grammar the Monaco editor registers (`cqlLanguage.ts`), which is itself modelled on the
// cqframework VS Code extension's grammar. So a keyword is a keyword in the editor and in the
// document, and adding one is a single edit. What is NOT shared is the tokenizer: Monarch's state
// machine belongs to Monaco, and pulling the editor into a read-only page to colour twelve lines
// would be an absurd dependency.
//
// A LEXER, NOT A PARSER (`APP-DIAGNOSTICS`). It colours tokens; it does not know whether the CQL
// is valid, and it must never imply that it does. Real diagnostics come from the compiler.
//
// The classes mirror Monarch's own token names so the two stay legible side by side: comment,
// string, identifier.quoted, number, keyword, operator.

import { type ReactNode } from "react";
import { cqlMonarchLanguage } from "../../packages/cql-grammar/src/cql.monarch.ts";

/** The grammar's keyword list, lowercased for the case-insensitive match CQL requires
 *  (`ignoreCase: true` in the Monarch definition — `Exists` and `exists` are one keyword). */
const KEYWORDS = new Set(
  ((cqlMonarchLanguage["keywords"] as string[] | undefined) ?? []).map((k) => k.toLowerCase()),
);

/** One pass, ordered by precedence. A comment wins over everything inside it, a string wins over a
 *  keyword inside it, and a quoted identifier is never a keyword however it is spelled — which is why
 *  `define "Not Done"` colours the name as a name. */
const TOKEN = new RegExp([
  "(?<comment>\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?(?:\\*\\/|$))",
  "(?<qid>\"(?:[^\"\\\\]|\\\\.)*\"|`(?:[^`\\\\]|\\\\.)*`)",
  "(?<str>'(?:[^'\\\\]|\\\\.)*')",
  "(?<date>@\\d{4}(?:-\\d{2}(?:-\\d{2}(?:T[\\d:.]*)?)?)?)",
  "(?<num>\\d+(?:\\.\\d+)?)",
  "(?<word>[A-Za-z_][\\w]*)",
  "(?<op>[<>=!~+\\-*/^&]+)",
].join("|"), "g");

const CLASS: Record<string, string> = {
  comment: "cql-com", qid: "cql-qid", str: "cql-str", date: "cql-num", num: "cql-num",
  op: "cql-op",
};

/** Turn a quoted name into something clickable, or null to leave it as plain syntax. The caller owns
 *  what a name RESOLVES to — this module knows lexing, not the document. */
export type CqlLinker = (name: string, raw: string) => ReactNode | null;

/** Tokenize CQL into React nodes. Plain text stays unwrapped — a span per character of whitespace
 *  would triple the DOM of every code block for nothing. */
export function highlightCql(source: string, link?: CqlLinker, cited?: [number, number] | null): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  // SPEC-NARRATIVE-DEVICES — the cited region is marked by CHARACTER OFFSET, computed once from the
  // line range. Splitting the source into lines and lexing each would be simpler and wrong: a block
  // comment spans lines, and a per-line lexer would reopen it on every one.
  const inCited = (offset: number) => cited != null && offset >= cited[0] && offset < cited[1];
  const mark = (node: ReactNode, offset: number) =>
    inCited(offset) ? <span key={"c" + key++} className="cql-cited">{node}</span> : node;
  const plain = (text: string, offset: number) => {
    if (cited == null) { out.push(text); return; }
    // A run of plain text can straddle the boundary, so it is cut at it rather than marked whole.
    const cuts = [offset, Math.max(offset, Math.min(cited[0], offset + text.length)),
                  Math.max(offset, Math.min(cited[1], offset + text.length)), offset + text.length];
    for (let i = 0; i < 3; i++) {
      const slice = text.slice(cuts[i]! - offset, cuts[i + 1]! - offset);
      if (slice) out.push(mark(slice, cuts[i]!));
    }
  };
  for (const m of source.matchAll(TOKEN)) {
    const at = m.index ?? 0;
    if (at > last) plain(source.slice(last, at), last);
    const groups = m.groups ?? {};
    const kind = Object.keys(groups).find((g) => groups[g] !== undefined) ?? "";
    // A bare word is a keyword or an identifier; identifiers get no class, because colouring every
    // name would leave nothing for the keywords to stand out against.
    // A quoted identifier is the one token that NAMES something the document also renders — a define,
    // a value set — so it is the one the reader should be able to follow. Resolution is the caller's:
    // an unresolved name stays plain syntax rather than becoming a link to nowhere.
    // Both spellings of a reference are offered to the linker: `"Encounters During Period"` and the
    // bare `encounterFor` of a fluent function call. A bare word is only offered when it is NOT a
    // keyword, so `exists` can never resolve to something.
    const referenceName = kind === "qid" ? m[0].slice(1, -1)
      : (kind === "word" && !KEYWORDS.has(m[0].toLowerCase()) ? m[0] : null);
    if (referenceName !== null && link) {
      const resolved = link(referenceName, m[0]);
      if (resolved) {
        out.push(mark(<span key={key++} className={kind === "qid" ? "cql-qid" : undefined}>{resolved}</span>, at));
        last = at + m[0].length;
        continue;
      }
    }
    const cls = kind === "word"
      ? (KEYWORDS.has(m[0].toLowerCase()) ? "cql-kw" : null)
      : (CLASS[kind] ?? null);
    out.push(mark(cls ? <span key={key++} className={cls}>{m[0]}</span> : m[0], at));
    last = at + m[0].length;
  }
  if (last < source.length) plain(source.slice(last), last);
  return out;
}

/** A CQL source block. `aria-label` names it as code for a screen reader, which cannot infer that
 *  from the colours. */
export function CqlCode({ source, className, link, highlight }: {
  source: string; className?: string; link?: CqlLinker;
  /** SPEC-NARRATIVE-DEVICES — a 1-based inclusive line range ("2-5") marking the lines a narrated
   *  claim is about. Per LINE because that is the grain a reader can see; marking half a line would
   *  be precision without meaning. */
  highlight?: string | null;
}) {
  return (
    <pre className={className ? `mw-cql ${className}` : "mw-cql"} aria-label="CQL source">
      <code>{highlightCql(source, link, citedOffsets(source, highlight))}</code>
    </pre>
  );
}

/** Turn a 1-based inclusive LINE range into the character range it covers. Per line because that is
 *  the grain a reader can see; marking half a line would be precision without meaning. */
export function citedOffsets(source: string, span?: string | null): [number, number] | null {
  const range = lineRange(span);
  if (!range) return null;
  const lines = source.split("\n");
  if (range[0] > lines.length) return null;
  let start = 0;
  for (let i = 0; i < range[0] - 1; i++) start += lines[i]!.length + 1;
  let end = start;
  for (let i = range[0] - 1; i < Math.min(range[1], lines.length); i++) end += lines[i]!.length + 1;
  return [start, Math.min(end, source.length)];
}

/** `"4"` or `"2-5"` as an inclusive pair, or null. A malformed span highlights nothing rather than
 *  guessing at what the author meant. */
function lineRange(span?: string | null): [number, number] | null {
  if (!span) return null;
  const parts = span.split("-").map(function (n) { return Number.parseInt(n, 10); });
  if (parts.some(function (n) { return !Number.isFinite(n) || n < 1; })) return null;
  const from = parts[0]!;
  const to = parts.length > 1 ? parts[1]! : from;
  return to >= from ? [from, to] : null;
}
