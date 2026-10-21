// SPEC-MEASURE-METADATA, APP-ASSISTANT-MARKDOWN — the app's ONE markdown renderer.
//
// WHY THIS EXISTS. `Measure.description`, `.rationale`, `.clinicalRecommendationStatement`, `.usage`,
// `.copyright` and `.disclaimer` are FHIR `markdown`, and CQL on Databricks QUOTES them rather than narrating
// over them. Quoting verbatim means honouring the paragraph breaks and links the author wrote:
// a steward's copyright notice runs to several paragraphs and a rationale cites guidelines by URL. Flattening all of it
// into one run of text is a silent edit of someone else's words.
//
// IT NOW ALSO RENDERS THE ASSISTANT'S ANSWERS (`APP-ASSISTANT-MARKDOWN`), which is why the block
// set grew from paragraphs to lists and tables. One renderer, not two: a product that parsed one
// markdown dialect in the document and a second one in the drawer under it would answer "is this
// bold?" differently in two places on the same screen, and the answer that ships is whichever the
// reader happened to look at second.
//
// STILL DELIBERATELY PARTIAL, and that is the design. Paragraphs, bold, inline code, fenced code,
// links, bulleted and numbered lists, and pipe tables — because those are what the two corpora use.
// Anything else (headings, block quotes, images, nested lists) renders as the literal characters the
// author typed, which is what a reader would see in any other plain renderer, and never as a broken
// tag or a swallowed line. The assistant is TOLD this set, so its half of the contract is stated
// rather than hoped for (`assistant/ask.py`, `RENDERING_INSTRUCTIONS`).
//
// NO LIBRARY. A full markdown engine on a page that shows clinical prose is a large HTML-injection
// surface, and the drawer's text is MODEL OUTPUT — the one input in the app an attacker could reach
// through the data it reads. This returns React elements only, so nothing here can inject markup.

import { Fragment, type ReactNode } from "react";

/** One top-level thing a block of markdown says. Paragraphs are text; a list is its items; a table is
 *  its header row and its body rows. Nothing here carries markup — every string still goes through
 *  `inline()` at render, which is what keeps the no-injection property above true for cells too. */
export type Block =
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "code"; text: string; lang: string };

/** APP-ASSISTANT-REFS — the two places a CALLER may mean more by a construct than markdown does.
 *
 *  The assistant's answers carry the document's own inline references and its artifacts, and neither
 *  is a markdown idea: `cql-databricks:define/…` is a link only in syntax, and a ```cql-databricks-chart fence is not
 *  code. Both arrive here as SLOTS rather than as knowledge, so this module keeps knowing only
 *  markdown — the alternative was a renderer that imports the spec model, which makes the document's
 *  quoting path depend on the assistant's vocabulary.
 *
 *  Each slot may DECLINE by returning null, and then the default rendering happens: an unresolvable
 *  reference falls through to the literal characters, exactly as an unknown link scheme already does.
 *  That is the same degradation the document's own bindings follow — never wrong, only less. */
export interface MarkdownSlots {
  /** A link whose target is not http(s). Return null to leave the author's characters. */
  link?: (text: string, href: string) => ReactNode | null;
  /** A fenced block, by its info string (`cql-databricks-chart`). Return null for an ordinary code block. */
  fence?: (lang: string, body: string) => ReactNode | null;
}

/** Markdown as blocks. `className` lands on every top-level block, so callers keep their own prose
 *  styling and a caller that only ever passes paragraphs is unaffected by the wider block set. */
export function Markdown(
  { text, className, slots }: { text: string; className?: string; slots?: MarkdownSlots },
) {
  return (
    <>
      {blocks(text).map((block, i) => {
        if (block.kind === "p") {
          return (
            <p key={i} className={className}><MarkdownInline text={block.text} slots={slots} /></p>
          );
        }
        if (block.kind === "table") {
          return <MarkdownTable key={i} className={className} head={block.head} rows={block.rows}
            slots={slots} />;
        }
        if (block.kind === "code") {
          const rendered = slots?.fence?.(block.lang, block.text);
          if (rendered) return <Fragment key={i}>{rendered}</Fragment>;
          // A fenced block is VERBATIM — no inline pass over it, no entity decoding. Its whole claim
          // is that these are the exact characters, and a renderer that turned `**` inside a snippet
          // into bold would be editing code while displaying it as code.
          return <pre key={i} className={className}><code>{block.text}</code></pre>;
        }
        const List = block.kind === "ul" ? "ul" : "ol";
        return (
          <List key={i} className={className}>
            {block.items.map((item, j) => (
              <li key={j}><MarkdownInline text={item} slots={slots} /></li>
            ))}
          </List>
        );
      })}
    </>
  );
}

/** One paragraph's inline markup, with no paragraph of its own — for callers that own the block
 *  element because they hang something else off it (an attribution, a provenance chip). */
export function MarkdownInline({ text, slots }: { text: string; slots?: MarkdownSlots }) {
  return <>{inline(text, slots)}</>;
}

/** Split on blank lines, collapsing the newlines WITHIN a paragraph to spaces. Blank line = paragraph
 *  break, per markdown; a single newline is a space, also per markdown — the wrapping in a source file
 *  is a property of the file, not of the sentence. */
export function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((p) => decodeEntities(p.split("\n").map((l) => l.trim()).join(" ").trim()))
    .filter(Boolean);
}

// A list item is ONE LINE. A wrapped continuation line ends the list and starts a paragraph, which is
// the visible failure rather than the silent one — and the assistant is told to write it that way.
// Leading whitespace is allowed and then IGNORED: a model that indents a sub-bullet gets a flat list
// rather than a line of literal hyphens, because losing one level of nesting is a smaller lie than
// printing markup at a clinician.
const BULLET = /^\s*[-*]\s+(.+?)\s*$/;
const NUMBERED = /^\s*\d+[.)]\s+(.+?)\s*$/;

/** A fenced code block's rail, capturing the info string after it (```sql, ```cql-databricks-chart).
 *
 *  The info string is CARRIED, not applied: this renderer does no syntax highlighting — the app's one
 *  highlighter is Monaco's CQL grammar, and inventing a second here for whatever language a model
 *  names would be two colourings of one kind of text. What reads it is the `fence` slot, for which the
 *  info string is not a language at all but the name of an artifact (`APP-ASSISTANT-REFS`). */
const FENCE = /^\s*```(.*)$/;

/** A pipe table's second line: the `---|---` rule that separates the header from the body. It is what
 *  DISTINGUISHES a table from a sentence containing a pipe, which is why a table is only recognised
 *  when this follows the header row. */
function isDelimiter(line: string): boolean {
  return line.includes("|") && line.includes("-") && /^[\s|:-]+$/.test(line);
}

function isRow(line: string): boolean {
  return line.includes("|");
}

/** A row's cells. The outer pipes are optional, per GFM, so they are stripped before splitting. */
function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

/** The block structure of a markdown document. Exported because it is the testable half — what got
 *  recognised as a list or a table is the claim, and rendering it is mechanical. */
export function blocks(text: string): Block[] {
  const lines = text.split("\n");
  const out: Block[] = [];
  let prose: string[] = [];

  const flushProse = () => {
    for (const para of paragraphs(prose.join("\n"))) out.push({ kind: "p", text: para });
    prose = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;

    // A fence is checked FIRST and consumes everything to its close, because inside it nothing else
    // is markdown: a `- ` line in a CQL snippet is a minus sign, not a bullet. An unclosed fence runs
    // to the end of the text rather than falling back to prose — the author opened a block, and
    // guessing they did not is how a code sample arrives as a paragraph with backticks in it.
    const fence = FENCE.exec(line);
    if (fence) {
      flushProse();
      const body: string[] = [];
      for (i++; i < lines.length && !FENCE.test(lines[i]!); i++) body.push(lines[i]!);
      out.push({ kind: "code", text: body.join("\n"), lang: fence[1]!.trim() });
      continue;
    }

    const pattern = BULLET.test(line) ? BULLET : NUMBERED.test(line) ? NUMBERED : null;

    if (pattern) {
      flushProse();
      const items: string[] = [];
      for (; i < lines.length; i++) {
        const item = pattern.exec(lines[i]!);
        if (item) { items.push(item[1]!); continue; }
        // A blank line BETWEEN items is still one list — markdown's "loose" list, and the shape a
        // model writes when it puts air around its bullets.
        if (lines[i]!.trim() === "" && pattern.test(lines[i + 1] ?? "")) continue;
        break;
      }
      i--;
      out.push(pattern === BULLET ? { kind: "ul", items } : { kind: "ol", items });
      continue;
    }

    if (isRow(line) && isDelimiter(lines[i + 1] ?? "")) {
      flushProse();
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && isRow(lines[i]!); i++) rows.push(cells(lines[i]!));
      i--;
      out.push({ kind: "table", head, rows });
      continue;
    }

    prose.push(line);
  }

  flushProse();
  return out;
}

/** A table, rendered as a table — the shape the drawer was placed at the foot of the reading column
 *  to be able to show (`APP-ASSISTANT`). A row's cells are rendered as the row has them: a short
 *  row leaves trailing cells empty and a long one keeps its extras, because padding or truncating to
 *  the header is an edit of a figure nobody asked for. */
function MarkdownTable(
  { head, rows, className, slots }: {
    head: string[]; rows: string[][]; className?: string; slots?: MarkdownSlots;
  },
) {
  return (
    <table className={className}>
      <thead>
        <tr>{head.map((cell, i) => <th key={i}><MarkdownInline text={cell} slots={slots} /></th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => <td key={j}><MarkdownInline text={cell} slots={slots} /></td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const INLINE = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g;

/** The named and numeric HTML entities published eCQM prose actually contains.
 *
 *  `Measure.guidance` and `.description` are FHIR `markdown`, but the eCQM tooling writes them
 *  HTML-escaped: numerator guidance arrives as `&lt;100 mmHg`, which React — correctly — renders
 *  as the six literal characters. Decoding is not optional politeness, it is the difference between
 *  "below 100" and gibberish in the middle of a clinical threshold.
 *
 *  Decoded to TEXT, never to markup: the result is still rendered as a React string, so a decoded
 *  `&lt;script&gt;` is displayed, not executed. */
const ENTITIES: Record<string, string> = {
  lt: "<", gt: ">", amp: "&", quot: '"', apos: "'", nbsp: "\u00a0",
  ndash: "\u2013", mdash: "\u2014", rsquo: "\u2019", lsquo: "\u2018",
  ldquo: "\u201c", rdquo: "\u201d", hellip: "\u2026", deg: "\u00b0",
  ge: "\u2265", le: "\u2264",
};

/** Decode HTML entities to the characters the author meant. Ampersand LAST is not needed here — each
 *  match is replaced once, so `&amp;lt;` decodes to `&lt;` and stops, which is the correct answer. */
export function decodeEntities(text: string): string {
  return text.replace(/&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);/g, (whole, name: string) => {
    if (name.startsWith("#x") || name.startsWith("#X")) {
      const code = parseInt(name.slice(2), 16);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    if (name.startsWith("#")) {
      const code = parseInt(name.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[name] ?? whole;
  });
}

/** One paragraph's inline markup. Links open in a new tab: the document is a reading surface and a
 *  citation must not navigate the reader out of the measure they are reviewing. */
function inline(raw: string, slots?: MarkdownSlots): ReactNode[] {
  const text = decodeEntities(raw);
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    if (m[1] !== undefined && m[2] !== undefined) {
      // Only http(s) is linked. A `javascript:` or `data:` url in an href is the one way markup-free
      // rendering could still execute something, and no legitimate citation needs another scheme.
      const href = /^https?:\/\//i.test(m[2]) ? m[2] : null;
      // A non-http target is offered to the caller's slot BEFORE it degrades: that is where
      // `cql-databricks:define/…` becomes a reference (`APP-ASSISTANT-REFS`). A slot that declines, or a
      // caller that has none, leaves the author's characters exactly as before.
      const slotted = href ? null : slots?.link?.(m[1], m[2]);
      out.push(href
        ? <a key={at} href={href} target="_blank" rel="noreferrer noopener">{m[1]}</a>
        : slotted ? <Fragment key={at}>{slotted}</Fragment> : m[0]);
    } else if (m[3] !== undefined) {
      out.push(<b key={at}>{m[3]}</b>);
    } else if (m[4] !== undefined) {
      // Narration quotes CQL inline — `Interval[20, 90]`, `encounterFor()`, a define name. Rendering the
      // backticks literally puts punctuation in the middle of a clinical sentence and loses the one
      // signal that says "these characters are code, read them exactly".
      out.push(<code key={at}>{m[4]}</code>);
    }
    last = at + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
