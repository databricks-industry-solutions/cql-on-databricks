// SPEC-NO-UI-PROSE — prose in the reading column has exactly TWO legitimate origins, and a component
// is neither of them.
//
// The rule: a sentence a clinician reads in the Specification comes from the measure's own CQL
// annotation tags (`designer/docs/narrative-tags.md` — `@description`, `@intent`, `@guidance`, …) or from a
// Specification instance under `cql_databricks_designer/specifications/`. Both are reviewed, attributable,
// filterable by the Explain toggle, and correctable without a deploy. A paragraph written into
// `MeasureNarrative.tsx` is none of those, and it renders in the same voice, the same column and the
// same type as the author's own words — so the reader cannot tell it apart, and the provenance
// machinery (`APP-NARRATIVE-PROVENANCE`) reports nothing about it because it does not know it exists.
//
// WHY A TEST AND NOT A CONVENTION. Both violations this was written for read as perfectly reasonable
// code and were defended in their own comments at the time: the reaches sentence ("derived from the
// ELM, so it is a fact") and the How-to-read fallback ("product copy about the page, not narration of a
// measure"). Neither argument is about the reader's position, which is the thing that actually decides
// it — and the fallback had gone stale exactly where it claimed authority, still describing an "Explain
// chip" the document replaced with a heading badge. A grep is the only thing that catches the next one.
//
// WHAT THIS DOES NOT FORBID. Labels, headings, empty states, tooltips, control text, and the
// disclaimer that states whose words a paragraph is — none of them are prose ABOUT THE MEASURE. The
// discriminator this test uses is length: a label is a few words, a sentence about the logic runs on.
// Domain vocabulary that is generic rather than measure-specific ("a value set is an external list")
// is a separate open question, tracked in the requirement rather than pinned here.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

/** The files that render the reading column. The nav, the panes and the chrome are not in scope: a
 *  pane states figures and a control states its own name, and neither is read as the measure's voice. */
const READING_SURFACES = [
  "src/measures/MeasureNarrative.tsx",
];

/** THE ONE EXEMPTION, and it is the exception that proves the rule.
 *
 *  `APP-NARRATIVE-PROVENANCE` REQUIRES this paragraph to be the product's own words: it is the claim that
 *  the prose above it is NOT the measure author's, and a claim about whose words a document holds
 *  cannot itself be sourced from that document — a Specification instance asserting its own prose is
 *  ours would be the assertion vouching for itself. It also does not sit in the reading column: it is
 *  the body of the provenance popover, opened deliberately, over a heading badge.
 *
 *  Matched on a distinctive fragment rather than a line number, so moving the paragraph does not
 *  silently widen the exemption and rewriting it re-opens the question. */
const EXEMPT = [
  "Not the measure author",   // the provenance disclaimer (`ExplainBadge`)
];

/** JSX text nodes, with comments and code removed — what a reader would actually see. */
function jsxText(source: string): { line: number; text: string }[] {
  const out: { line: number; text: string }[] = [];
  let inBlockComment = false;
  source.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (inBlockComment) {
      if (line.includes("*/")) inBlockComment = false;
      return;
    }
    if (line.startsWith("/*") || line.startsWith("{/*")) {
      if (!line.includes("*/")) inBlockComment = true;
      return;
    }
    if (line.startsWith("//") || line.startsWith("*")) return;
    // Drop JSX tags, expressions and string literals — a `title=` or an aria-label is chrome.
    const text = line
      .replace(/<[^>]*>/g, " ")
      .replace(/\{[^}]*\}/g, " ")
      .replace(/["'`][^"'`]*["'`]/g, " ")
      .trim();
    if (text) out.push({ line: i + 1, text });
  });
  return out;
}

describe("SPEC-NO-UI-PROSE — the reading column carries no prose written in a component", () => {
  it.each(READING_SURFACES)("%s states no sentence about the measure", (file) => {
    const lines = jsxText(readFileSync(file, "utf8"));
    // The disclaimer runs to two lines; exempting only the one carrying the fragment would report its
    // continuation as a fresh violation. Exempt from the match to the end of the paragraph.
    let exempting = false;
    const found = lines.filter(({ text }) => {
      if (EXEMPT.some((f) => text.includes(f))) { exempting = true; return false; }
      if (exempting) { if (/[.]\s*$/.test(text)) exempting = false; return false; }
      return true;
    }).filter(({ text }) => {
      // A run of eight or more plain words with a sentence's punctuation in it. Eight is deliberately
      // generous: "Codes and value sets the logic compares against" is a section label at seven, and
      // the paragraphs this rule exists to stop ran to forty.
      const words = text.split(/\s+/).filter((w) => /^[A-Za-z][A-Za-z'’-]*$/.test(w));
      return words.length >= 8 && /[.;:]/.test(text);
    });
    expect(found.map((f) => `${file}:${f.line}  ${f.text}`)).toEqual([]);
  });
});
