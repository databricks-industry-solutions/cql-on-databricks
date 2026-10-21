// APP-SPEC-MODEL — ONE model of what a specification contains, rendered two ways.
//
// The Define lens shows the same specification through two surfaces: the left nav (DefineNav) and the
// Specification page (MeasureNarrative). What the specification CONTAINS — which libraries, which
// entries, how many variants each has, and which are eliminated — is decided HERE, once, and both
// surfaces render the result. Neither surface may re-derive membership; if the nav shows a row the page
// shows a section, and vice versa. (Before this module they each computed their own list from different
// inputs and disagreed: the nav collapsed FHIRHelpers' 251 `ToString` overloads to one row and hid
// eliminated defines, while the page rendered all 297 declarations and honoured no visibility at all.)
//
// EVERYTHING HERE COMES FROM THE COMPILER'S ELM (APP-CQL-AUTHORITATIVE). There is no regex and no
// source scan: the BFF compiles the whole include closure (`/api/translate` with `includeElm`), so every
// library — root and includes alike — arrives as a typed catalog. Membership, kind, overload signatures
// and return types are read off the ELM; each declaration's CQL text is recovered by slicing the source
// at the statement's `locator` span; dependency edges come from the ELM expression tree (`elmGraph.ts`).
//
// The two determinations this model owns:
//
//  1. OVERLOAD COLLAPSING. CQL permits many declarations of one name differing only in operand types —
//     FHIRHelpers declares `ToString` once per FHIR type. They are ONE concept, so they are ONE entry
//     carrying its `variants`. The nav renders the entry as a row; the page renders it as a section with
//     a variants table. A specification never repeats a name.
//
//  2. ELIMINATION. A define not reachable from any population is eliminated — it is part of the library
//     but plays no part in the measure (PROC-DATA-PROVENANCE). Reachability walks the compiler's
//     `describe` dependency map when a Data Source has produced one; otherwise it walks the ELM-derived
//     graph, whose edges are equally exact and equally cross-library.
//
// LIBRARY OWNERSHIP is by DECLARING SOURCE: each entry belongs to the library whose ELM declares it. The
// describe graph contributes EDGES only, never membership — merging its cross-library define list into
// the root library is what previously listed an include's defines twice in the nav.

import { buildElmGraph, type ElmLibrarySource } from "../authoring/elmGraph.ts";
import type { ElmDefine, ElmLibraryInfo } from "../authoring/elmDefines.ts";
import type { LogicGraph } from "../authoring/logicGraph.ts";
import type { DescribeResult } from "../wire.ts";
import { POPULATIONS } from "./model.ts";

export type SpecEntryKind = "population" | "define" | "function";

/** One declaration of an entry. An entry with more than one variant is an OVERLOAD GROUP. */
export interface SpecVariant {
  /** The declared parameter list ("value FHIR.AccountStatus"); empty for expression defines. */
  params: string;
  /** The declared return type, when the translator annotated one. */
  returns: string | null;
  /** This declaration's CQL body, sliced from source at the ELM statement's locator. */
  body: string;
}

/** One named concept in the specification — the unit both surfaces render. */
export interface SpecEntry {
  /** Unique within the model: library + name. Safe as a React key. */
  key: string;
  name: string;
  kind: SpecEntryKind;
  /** The library that DECLARES it. */
  library: string;
  /** Every declaration of this name, in declaration order. Length > 1 = an overload group. */
  variants: SpecVariant[];
  /** Not reachable from any population — present in the library, absent from the measure. */
  eliminated: boolean;
  /** The Specification page's anchor for this entry, unique across libraries. */
  domId: string;
}

export interface SpecLibrary {
  name: string;
  /** True for the library being read; false for its includes. */
  isRoot: boolean;
  /** False when an include failed to compile — its entries are unknown, not empty. */
  resolved: boolean;
  /** Non-population entries, in declaration order. */
  entries: SpecEntry[];
  domId: string;
}

export interface SpecModel {
  /** The root library's name. */
  root: string;
  /** The measure's population criteria, in eCQM order (root library only). */
  populations: SpecEntry[];
  /** Root library first, then each include in declaration order. */
  libraries: SpecLibrary[];
  /** Every entry name in the model — the cross-reference vocabulary for "Uses". */
  names: Set<string>;
  /** Names reachable from a population. Empty when there is nothing to trace from (a plain library
   *  has no populations), in which case nothing is eliminated. */
  liveNames: Set<string>;
  /** The ELM-derived dependency graph over the same libraries — the nav's relational chrome. */
  graph: LogicGraph;
}

/** A compiled library as the workspace hands it to the model. */
export interface SpecLibraryInput {
  info: ElmLibraryInfo;
  /** The parsed ELM document, for expression-tree edges. */
  elm: unknown;
  /** The library's CQL, for locator slicing. */
  source: string;
}

/** Every run of non-alphanumerics folded to a single dash, ends trimmed.
 *
 *  CASE IS PRESERVED: CQL is case-sensitive and libraries really do declare names differing only in
 *  case (a helper library may declare both `ToInterval` and `toInterval`). Lowercasing collapses them onto one
 *  DOM id — the very collision this id scheme exists to prevent. HTML5 ids are case-sensitive. */
function slug(s: string): string {
  const out: string[] = [];
  let pendingDash = false;
  for (const ch of s) {
    const alnum = (ch >= "a" && ch <= "z") || (ch >= "A" && ch <= "Z") || (ch >= "0" && ch <= "9");
    if (alnum) {
      if (pendingDash && out.length > 0) out.push("-");
      pendingDash = false;
      out.push(ch);
    } else {
      pendingDash = true;
    }
  }
  return out.join("");
}

/** The Specification page anchor for an entry — library-qualified, so two libraries declaring the same
 *  name get two anchors instead of colliding on one. */
export const specCellId = (library: string, name: string) => `mw-crit-${slug(library)}-${slug(name)}`;

/** The Specification page anchor for a library heading. */
export const specLibId = (library: string) => `mw-lib-${slug(library)}`;

const POP_SET = new Set<string>(POPULATIONS);

/** Slice the source text covered by an ELM locator ("6:1-7:7": 1-based lines and columns, end
 *  inclusive). This is how a statement's CQL is recovered — the ELM carries the expression TREE, not
 *  the text, but it does carry exactly where the text was. */
function sliceLocator(source: string, locator: string | null): string {
  if (!locator) return "";
  const dash = locator.indexOf("-");
  if (dash < 0) return "";
  const start = locator.slice(0, dash).split(":");
  const end = locator.slice(dash + 1).split(":");
  const sl = Number(start[0]), sc = Number(start[1]), el = Number(end[0]), ec = Number(end[1]);
  if (!Number.isFinite(sl) || !Number.isFinite(sc) || !Number.isFinite(el) || !Number.isFinite(ec)) return "";
  const lines = source.split("\n");
  if (sl < 1 || el > lines.length) return "";
  const picked = lines.slice(sl - 1, el);
  if (picked.length === 0) return "";
  // Trim the last line to the end column, then the first to the start column (order matters when the
  // span is a single line).
  picked[picked.length - 1] = picked[picked.length - 1]!.slice(0, ec);
  picked[0] = picked[0]!.slice(sc - 1);
  return picked.join("\n");
}

/** The body of a declaration: its source slice with the `define …:` header removed.
 *
 *  The header ends at the first `:` that is not inside quotes or brackets — the declaration's own colon.
 *  Scanned character by character rather than matched, so a quoted name containing a colon, or a
 *  parameter list with a qualified type, cannot mislead it. */
function stripHeader(declaration: string): string {
  let inQuote = false, inTick = false, depth = 0;
  for (let i = 0; i < declaration.length; i++) {
    const ch = declaration[i];
    if (ch === '"' && !inTick) inQuote = !inQuote;
    else if (ch === "'" && !inQuote) inTick = !inTick;
    else if (!inQuote && !inTick) {
      if (ch === "(" || ch === "[" || ch === "{") depth++;
      else if (ch === ")" || ch === "]" || ch === "}") depth--;
      else if (ch === ":" && depth === 0) return dedent(declaration.slice(i + 1));
    }
  }
  return dedent(declaration);
}

/** Drop leading blank lines, trailing whitespace, and the common indent. */
function dedent(text: string): string {
  const lines = text.split("\n");
  while (lines.length > 0 && lines[0]!.trim() === "") lines.shift();
  while (lines.length > 0 && lines[lines.length - 1]!.trim() === "") lines.pop();
  if (lines.length === 0) return "";
  // The first line carries the declaration's own leading space, not the body's indent — measure the
  // common indent over the continuation lines only, then left-trim the first.
  const indents = lines.slice(1).filter((l) => l.trim() !== "").map((l) => l.length - l.trimStart().length);
  const min = indents.length ? Math.min(...indents) : 0;
  return lines.map((l, i) => (i === 0 ? l.trim() : l.slice(min).trimEnd())).join("\n");
}

/** Group values by key, preserving first-appearance order of the keys. */
function groupByName<T>(items: T[], name: (t: T) => string): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const k = name(item);
    const bucket = out.get(k);
    if (bucket) bucket.push(item);
    else out.set(k, [item]);
  }
  return out;
}

function variantOf(d: ElmDefine, source: string): SpecVariant {
  return { params: d.params, returns: d.returns, body: stripHeader(sliceLocator(source, d.locator)) };
}

/** Build the specification model (APP-SPEC-MODEL). The single derivation both Define-lens surfaces
 *  render — see the module header for the ownership and collapsing rules. */
export function buildSpecModel(input: {
  /** The root library, compiled. */
  root: SpecLibraryInput;
  /** Its include closure, compiled, in declaration order. */
  includes: SpecLibraryInput[];
  /** Include declarations that failed to compile — listed, but with unknown contents. */
  unresolved?: { name: string }[];
  /** The compiler's cross-library dependency map, when a Data Source supplies one. */
  describeGraph?: DescribeResult | null;
}): SpecModel {
  const { root, includes, unresolved = [], describeGraph } = input;
  const rootName = root.info.name;

  const buildEntries = (lib: SpecLibraryInput, isRoot: boolean): { entries: SpecEntry[]; populations: Map<string, SpecEntry> } => {
    const entries: SpecEntry[] = [];
    const populations = new Map<string, SpecEntry>();
    for (const [name, defs] of groupByName(lib.info.defines, (d) => d.name)) {
      // Populations belong to the MEASURE — an include that happens to declare one of those names is
      // declaring an ordinary define, not a population criterion.
      const isPop = isRoot && POP_SET.has(name);
      const entry: SpecEntry = {
        key: `${lib.info.name}::${name}`,
        name,
        kind: isPop ? "population" : defs[0]!.kind === "function" ? "function" : "define",
        library: lib.info.name,
        variants: defs.map((d) => variantOf(d, lib.source)),
        eliminated: false, // resolved below, once reachability is known
        domId: specCellId(lib.info.name, name),
      };
      if (isPop) populations.set(name, entry);
      else entries.push(entry);
    }
    return { entries, populations };
  };

  const rootBuilt = buildEntries(root, true);
  const libraries: SpecLibrary[] = [
    { name: rootName, isRoot: true, resolved: true, entries: rootBuilt.entries, domId: specLibId(rootName) },
    ...includes.map((inc) => ({
      name: inc.info.name,
      isRoot: false,
      resolved: true,
      entries: buildEntries(inc, false).entries,
      domId: specLibId(inc.info.name),
    })),
    ...unresolved.map((u) => ({
      name: u.name, isRoot: false, resolved: false, entries: [] as SpecEntry[], domId: specLibId(u.name),
    })),
  ];

  // The ELM-derived graph over every compiled library — exact edges, cross-library included.
  const elmLibraries: ElmLibrarySource[] = [root, ...includes].map((l) => ({ info: l.info, elm: l.elm }));
  const graph = buildElmGraph(elmLibraries, POP_SET);

  const liveNames = reachableNames(describeGraph, graph);
  const populations = POPULATIONS.map((n) => rootBuilt.populations.get(n)).filter((e): e is SpecEntry => Boolean(e));
  if (liveNames.size > 0) {
    for (const lib of libraries) for (const e of lib.entries) e.eliminated = !liveNames.has(e.name);
  }

  const names = new Set<string>();
  for (const p of populations) names.add(p.name);
  for (const lib of libraries) for (const e of lib.entries) names.add(e.name);

  return { root: rootName, populations, libraries, names, liveNames, graph };
}

/** Names reachable from at least one population.
 *
 *  Both inputs are compiler-derived: `describe` is the engine's own merged dependency map (available
 *  once a Data Source has compiled the manifest), and the ELM graph is the expression trees of the
 *  libraries in hand. Same walk, whichever map is present. */
function reachableNames(describeGraph: DescribeResult | null | undefined, graph: LogicGraph): Set<string> {
  const live = new Set<string>();

  const deps = new Map<string, string[]>();
  const populations: string[] = [];
  if (describeGraph && Object.keys(describeGraph.dependencies).length > 0) {
    for (const [name, ds] of Object.entries(describeGraph.dependencies)) deps.set(name, ds);
    for (const name of POPULATIONS) {
      if (name in describeGraph.dependencies || describeGraph.defines.includes(name)) populations.push(name);
    }
  } else {
    for (const n of graph.nodes) {
      if (n.type === "valueset" || n.type === "retrieve") continue;
      if (n.type === "population") populations.push(n.label);
      deps.set(n.label, n.deps
        .map((id) => graph.byId[id])
        .filter((d) => d && d.type !== "valueset" && d.type !== "retrieve")
        .map((d) => d!.label));
    }
  }

  const walk = (name: string) => {
    if (live.has(name)) return;
    live.add(name);
    for (const dep of deps.get(name) ?? []) walk(dep);
  };
  for (const name of populations) walk(name);
  return live;
}

/** Apply the ELIMINATION visibility rule to a model, yielding the model both surfaces render.
 *
 *  Visibility is a property of the SPECIFICATION, not of one pane: hiding eliminated defines changes
 *  what the document IS, so nav and page are handed the same filtered model. Nav-only concerns — text
 *  search and the type/population/data facets — are navigation aids and stay in the nav. ORDER IS NOT
 *  ONE OF THEM: see `sortSpecModel` (SPEC-SHARED-ORDER). */
export function filterSpecModel(model: SpecModel, showEliminated: boolean): SpecModel {
  const libraries = model.libraries
    .map((lib) => (showEliminated ? lib : { ...lib, entries: lib.entries.filter((e) => !e.eliminated) }))
    // A library contributing nothing visible is dropped from BOTH surfaces rather than left as an
    // empty heading in the page and a "· 0" group in the nav. The root library always stays (it IS
    // the document), and an unresolved include stays so the reader learns why it is missing.
    .filter((lib) => lib.isRoot || !lib.resolved || lib.entries.length > 0);
  const names = new Set<string>();
  for (const p of model.populations) names.add(p.name);
  for (const lib of libraries) for (const e of lib.entries) names.add(e.name);
  return { ...model, libraries, names };
}

/** SPEC-SHARED-ORDER — the orders a reader can put the define catalogue in. `declared` is the CQL's own
 *  statement order, which is what the ELM hands us and therefore the model's natural order. */
export type SpecSortKey = "declared" | "topo" | "az" | "usedby" | "uses";

/** SPEC-SHARED-ORDER — order the define catalogue, for BOTH surfaces at once.
 *
 *  ORDER IS A PROPERTY OF THE SPECIFICATION, exactly as elimination is. Sorting was a nav-only aid, on
 *  the reasoning that the page should keep a stable published reading order — but the nav opened on
 *  `topo` while the page rendered declaration order, so the two disagreed by default, and no setting
 *  could make them agree because declaration order was not one of the nav's options. A nav whose third
 *  row is the eleventh section is not navigation. The stable-reading-order argument survives as the
 *  DEFAULT (`declared`, the CQL's own order): a reader who picks another order moves both surfaces.
 *
 *  POPULATIONS ARE NEVER SORTED. They are stated in eCQM reporting order — Initial Population,
 *  Denominator, exclusions, Numerator — which is the order a reader reasons in; topological sort put
 *  Initial Population last, so nav and page disagreed about where the measure starts. That carve-out
 *  was already in the nav and moves here with the rest.
 *
 *  The graph supplies the relational keys (`layer`, `deps`, `dependents`) and nothing else — membership
 *  is the model's (APP-SPEC-MODEL). Name is the tie-breaker throughout, so an order is total and a
 *  re-render cannot reshuffle equal rows. */
export function sortSpecModel(model: SpecModel, sort: SpecSortKey): SpecModel {
  if (sort === "declared") return model;
  const nodeOf = (e: SpecEntry) => model.graph.byId[e.kind === "function" ? `fn:${e.name}` : `def:${e.name}`];
  const compare = (a: SpecEntry, b: SpecEntry): number => {
    const na = nodeOf(a), nb = nodeOf(b);
    return sort === "az" ? a.name.localeCompare(b.name)
      : sort === "usedby" ? (nb?.dependents.length ?? 0) - (na?.dependents.length ?? 0) || a.name.localeCompare(b.name)
      : sort === "uses" ? (nb?.deps.length ?? 0) - (na?.deps.length ?? 0) || a.name.localeCompare(b.name)
      : (nb?.layer ?? 0) - (na?.layer ?? 0) || a.name.localeCompare(b.name); // topological
  };
  return {
    ...model,
    libraries: model.libraries.map((lib) => ({ ...lib, entries: lib.entries.slice().sort(compare) })),
  };
}

/** Total entries in a model (populations + every library's entries) — the header count, so the nav's
 *  "N defines" always matches what is on screen. */
export function specEntryCount(model: SpecModel): number {
  return model.populations.length + model.libraries.reduce((n, lib) => n + lib.entries.length, 0);
}

/** How many entries were eliminated — what the "Show eliminated defines" toggle would reveal. */
export function eliminatedCount(model: SpecModel): number {
  return model.libraries.reduce((n, lib) => n + lib.entries.filter((e) => e.eliminated).length, 0);
}

/** APP-SPEC-MODEL — the ONE resolution of a bare name to the entry it names.
 *
 *  Selection and every inline reference travel by NAME (`APP-LOGIC-VIEW`) while entries are
 *  library-qualified, so something has to decide which declaration a bare name means. That decision now
 *  lives here once, because it had been made twice: the inspector resolved a selected name to scope its
 *  pane and the Specification resolved the same name to build its reference lenses, and the two agreeing
 *  was a coincidence maintained by hand. With the lens rendering the inspector's own body
 *  (`SPEC-REF-LENS`), a disagreement would put two different defines' figures under one name in one
 *  gesture — the exact drift this requirement exists to prevent.
 *
 *  POPULATIONS FIRST, then libraries in declaration order, first declaration wins. The order is not
 *  arbitrary: the model holds populations in their OWN list rather than among the library entries, so a
 *  lookup that walked only `libraries` resolved every ordinary define and silently missed all four
 *  population criteria — the inspector read "Whole document" while the nav row beside it was plainly
 *  selected. */
export function entriesByName(model: SpecModel): Map<string, SpecEntry> {
  const out = new Map<string, SpecEntry>();
  for (const p of model.populations) out.set(p.name, p);
  for (const lib of model.libraries) for (const e of lib.entries) if (!out.has(e.name)) out.set(e.name, e);
  return out;
}

/** One name, resolved by the rule above. The map is rebuilt per call, which is deliberate: the callers
 *  that resolve ONE name do it once per selection change, and a module-level cache keyed on a model object
 *  would be a second place the model's identity is tracked. A caller resolving hundreds of names per
 *  render (the Specification's inline references) memoises `entriesByName` itself. */
export function entryForName(model: SpecModel, name: string): SpecEntry | null {
  return entriesByName(model).get(name) ?? null;
}
