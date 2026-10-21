// APP-STYLE-GUIDE / APP-DESIGN-SYSTEM — build `designer/docs/design-system.html` from the spec plus the
// hand-kept showcase template.
//
// WHY A TEMPLATE AND NOT A PLAIN MARKDOWN RENDER. The rendered page is not a rendering of the `.md`, and
// treating it as one is what would destroy it. Most of the page is a LIVE SHOWCASE — real buttons, pills,
// swatches and lens bars drawn with the system's own tokens, so the page is a specimen of the thing it
// documents. None of that is derivable from prose.
//
// The split is by who can own each half: the showcase stays hand-authored in `design-system.tmpl.html`,
// the specification is generated from `design-system.md` on every build and injected at `<!-- SPEC -->`.
// Neither half can drift from its source, because neither half has a second copy.
//
// There is no PDF. Generating one needs a headless browser, a large dependency for a file whose only
// advantage over this page is offline sharing.

import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "marked";

const DOC = resolve(dirname(fileURLToPath(import.meta.url)), "../docs");
const SPEC = resolve(DOC, "design-system.md");
const TEMPLATE = resolve(DOC, "design-system.tmpl.html");
const OUT = resolve(DOC, "design-system.html");

/** The one place the template hands the spec its slot. Absent, the build fails loud rather than writing a
 *  page that silently lost half its content (`CORE-NO-SILENT-MISLOWER`). */
const SLOT = "<!-- SPEC -->";

/** Tables are the bulk of this document and several are wider than the reading column. The template gives
 *  `.tablewrap` the horizontal scrollport, so a generated table gets the same treatment a hand-written one
 *  does — otherwise the page body scrolls sideways instead of the table. */
function wrapTables(html) {
  return html.replace(/<table>/g, '<div class="tablewrap"><table>')
             .replace(/<\/table>/g, "</table></div>");
}

/** The `.md`'s own H1 is dropped: the section it lands in already carries a title, and two titles in a row
 *  read as a rendering mistake rather than as structure. */
function dropLeadingH1(md) {
  return md.replace(/^#\s+.*\r?\n/, "");
}

const [md, template] = await Promise.all([
  readFile(SPEC, "utf8"),
  readFile(TEMPLATE, "utf8"),
]);

if (!template.includes(SLOT)) {
  throw new Error(`${TEMPLATE} has no ${SLOT} slot — the spec would be dropped silently.`);
}

const body = wrapTables(marked.parse(dropLeadingH1(md), { gfm: true, mangle: false, headerIds: false }));
const generated = [
  "<!-- GENERATED FROM design-system.md BY scripts/build-design-doc.mjs — DO NOT EDIT HERE -->",
  '<div class="spec-md">',
  body.trim(),
  "</div>",
].join("\n");

await writeFile(OUT, template.replace(SLOT, generated), "utf8");

const lines = md.split("\n").length;
console.log(`design-system.html ← design-system.md (${lines} lines) + design-system.tmpl.html`);
