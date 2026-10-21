// APP-SPEC-MODEL / APP-NAV-METRIC — the left pane's ROW RENDERERS, in one place because there is
// one row style and there must go on being one.
//
// WHY THIS FILE EXISTS. The pane carries two arrangements of one specification — the Outline
// (`DefineNav.tsx`) and the Composition (`CompositionNav.tsx`) — and `specComposition.ts` states
// the invariant they share: "the rows ARE the Outline's rows, so a define looks and selects the same in
// both arrangements and the two cannot drift into two row styles for one model." While both arrangements
// were closures in a single component that held for free. Split across two files it would have lasted
// until the first divergent edit, so the renderers moved here and both files call the same ones.
//
// It is a KIT, not a component: `buildNavRowKit` returns the three render functions bound to the graph,
// the selection and the metrics, and a caller holds it in a `useMemo`. That is the shape `CompositionPane`
// already used — it received `renderEntry` / `renderRetrieve` / `renderCollapsible` as props — lifted one
// level so the pane shell owns them rather than one of the two tabs.

import { useEffect, useRef, type ReactNode } from "react";
import { closureOf, dependentsOf, type LogicGraph, type LogicNode } from "../authoring/logicGraph.ts";
import type { DefineStatusValue } from "../wire.ts";
import { ChevronDown, CornerUpLeft } from "../icons.tsx";
import { isDefineSelected, isRetrieveSelected, type Selection } from "./model.ts";
import { retrieveLabel } from "./specComposition.ts";
import type { SpecDocument } from "./specDocument.ts";
import { metricTitle, resolveMetric, type MetricSet, type ResolvedMetric } from "./specMetrics.ts";
import type { SpecEntry, SpecEntryKind } from "./specModel.ts";
import type { SpecRetrieve } from "./specRetrieves.ts";

// The Type facet offers only what the nav can LIST — the spec model's entry kinds. Value sets and
// retrieves are graph leaves that feed the Data-type facet; they were never rows (APP-SPEC-MODEL).
export const TYPE_CHIP: Record<SpecEntryKind, string> = { population: "pop", define: "def", function: "fn" };

// DESIGNER-DEFINE-STATUS — status indicator classes for the per-define pipeline status dot.
const STATUS_CLASS: Record<DefineStatusValue, string> = {
  unknown: "st-unknown", stale: "st-stale", computing: "st-computing", fresh: "st-fresh", error: "st-error",
};
const STATUS_TITLE: Record<DefineStatusValue, string> = {
  unknown: "Not deployed", stale: "Stale — needs recompute", computing: "Computing…", fresh: "Fresh", error: "Error",
};

export function chipClass(t: SpecEntryKind): string {
  return t === "population" ? "pop" : t === "function" ? "fn" : "def";
}

/** APP-MEASURE-FLOW — open a collapsed group at the moment it COMES to hold the selection.
 *
 *  The failure this fixes: the pane tints a selected row and never reveals it, so a selection made in
 *  the document, from a pasted `?crit=` link or by Back landed inside a closed group and the reader saw
 *  nothing move. `ReadGroup` defaults closed, so for a read that was every time.
 *
 *  It fires on the TRANSITION, not on the state, which is what leaves the reader in charge: a derived
 *  `open={open || holdsSelection}` would make a group holding the selection impossible to close, and
 *  closing the thing you just looked at is a normal thing to want. */
export function useRevealOnSelect(holdsSelection: boolean, open: () => void) {
  const was = useRef(holdsSelection);
  useEffect(() => {
    if (holdsSelection && !was.current) open();
    was.current = holdsSelection;
  }, [holdsSelection, open]);
}

/** Options a row renderer accepts. `rooted` is the Flow tab's spine marker (APP-MEASURE-FLOW): the
 *  population the tree below is currently hanging from. It is deliberately NOT the same thing as
 *  `active` — a reader roots on a population and then selects a define inside it, at which point the two
 *  land on different rows and the pane has to show both.
 *
 *  `onActivate` overrides what the row's click DOES, never what it looks like. The Flow spine needs one
 *  click to re-root AND select, which is two URL params written together; everywhere else the default
 *  (select this define) is right. It is an override rather than a second renderer because a spine row
 *  and an Outline row must stay one row style — see this file's header. */
export interface RowOpts { key?: string; repeat?: boolean; rooted?: boolean; onActivate?: () => void }

export interface NavRowKit {
  renderEntry: (e: SpecEntry, opts?: RowOpts) => ReactNode;
  renderRetrieve: (r: SpecRetrieve, opts: { key: string; repeat: boolean }) => ReactNode;
  renderCollapsible: (
    e: SpecEntry, open: boolean, onToggle: () => void, key: string, opts?: RowOpts,
  ) => ReactNode;
  /** The transitive closures the selection tints — exposed because the pane's reveal effect needs to
   *  know whether a collapsed group contains the selection (APP-DEP-COUNTS). */
  downSet: Set<string>;
  upSet: Set<string>;
}

export function buildNavRowKit({ graph, selected, onSelect, metrics, doc, defineStatuses }: {
  graph: LogicGraph;
  selected: Selection | null;
  onSelect: (sel: Selection) => void;
  /** APP-NAV-METRIC — null with no data source, and the rows then carry no number at all rather than
   *  one "not computed" per row. */
  metrics: MetricSet | null;
  doc: SpecDocument;
  defineStatuses?: Record<string, DefineStatusValue>;
}): NavRowKit {
  // APP-NAV-FILTER / APP-DEP-COUNTS — selecting a criterion subtly highlights its up/down-stream
  // transitive closure: `downSet` = what it USES (closureOf ↓), `upSet` = what USES it (dependentsOf ↑).
  // A READ has no closure: it is a leaf, so selecting one tints nothing. Only a define does.
  let downSet = new Set<string>();
  let upSet = new Set<string>();
  if (selected?.kind === "define") {
    const node = graph.nodes.find((n) => n.label === selected.name && n.type !== "valueset" && n.type !== "retrieve");
    if (node) { downSet = closureOf(graph, node.id); upSet = dependentsOf(graph, node.id); }
  }

  /** The graph node backing an entry — the source of its dep counts and closure highlighting. */
  const nodeOf = (e: SpecEntry): LogicNode | undefined =>
    graph.byId[e.kind === "function" ? `fn:${e.name}` : `def:${e.name}`];

  /** APP-NAV-METRIC — the figure for an entry, resolved once for whichever tab is rendering it. */
  const figureOf = (e: SpecEntry): ResolvedMetric | null =>
    resolveMetric(metrics, doc.byDefine[e.key]?.metric ?? null, e.library);

  // Render one ENTRY as a nav row (APP-SPEC-MODEL: one row per entry, however many declarations it
  // has — the variant count rides on the row so the nav and the page agree at a glance).
  //
  // APP-NAV-METRIC — the row carries the SAME figure as the section it scrolls to, resolved through
  // the same `resolveMetric` the page calls. Two surfaces computing one number independently is how a
  // nav row comes to say 8,204 beside a heading saying 8,207.
  //
  // The share is a DATA BAR BEHIND THE FIGURE, right-aligned and growing leftwards from it — the table
  // idiom, where the bar and the number it scales are one cell. It was a hairline on the row's bottom
  // edge first and that was wrong in a way worth recording: a full-width rule under a label reads as an
  // UNDERLINE, so every row looked like a link and the longest bars looked like the selected row. Behind
  // the number, the mark cannot be read as anything but a magnitude. It still costs no layout — the bar
  // is absolute within the figure's own box, so the gutter chip stays under its heading's first letter
  // (`APP-NAV-DEPTH`).
  //
  // `APP-DEP-COUNTS` lands in the INSPECTOR, not in the row. Two numbers per row bought a reader very
  // little: "3↓ 1↑" says how MANY things a define touches without saying WHICH, which is the only form
  // of that question anyone acts on. The pane answers the shape of it better than a pair of integers can
  // — selecting a row tints its whole transitive closure in place (`downSet` / `upSet` below, still built
  // and still used). The row needs width; in a 260px pane the metric figure, status dot, variant count
  // and a 40-character define name are already competing for it. The counts sit once, beside the selected
  // define, where there is room to name the direction in words rather than in an arrow glyph.
  const entryContent = (e: SpecEntry, opts?: RowOpts) => {
    const st = defineStatuses?.[e.name] as DefineStatusValue | undefined;
    const figure = figureOf(e);
    return (
      <>
        <span className={"k " + chipClass(e.kind)}>{TYPE_CHIP[e.kind]}</span>
        <span className="lab">{e.name}</span>
        {e.variants.length > 1 && <span className="mw-node-variants" title={`${e.variants.length} declarations of this name`}>×{e.variants.length}</span>}
        {opts?.repeat && (
          <span className="rep" title="Reached earlier above — shown here as a reference, not repeated">
            <CornerUpLeft size={11} aria-hidden="true" />
          </span>
        )}
        {st && <span className={`mw-define-st ${STATUS_CLASS[st]}`} title={STATUS_TITLE[st]} aria-label={STATUS_TITLE[st]} />}
        {figure && (
          <span className={"mw-node-met tnum" + (figure.removes ? " removes" : "")}
            title={metricTitle(figure, e.name)}>
            {figure.share != null && (
              <i style={{ width: `${Math.min(figure.share * 100, 100)}%` }} aria-hidden="true" />
            )}
            <b>{figure.subjects.toLocaleString()}</b>
          </span>
        )}
      </>
    );
  };

  /** The row's own classes — shared by the plain button form and the collapsible form. */
  const entryClass = (e: SpecEntry, opts?: RowOpts) => {
    const n = nodeOf(e);
    const rel = isDefineSelected(selected, e.name) ? " active"
      : n && downSet.has(n.id) ? " rel-down" : n && upSet.has(n.id) ? " rel-up" : "";
    return "mw-node" + rel + (e.eliminated ? " eliminated" : "")
      + (opts?.repeat ? " repeat" : "") + (opts?.rooted ? " rooted" : "");
  };

  const renderEntry = (e: SpecEntry, opts?: RowOpts) => (
    <button key={opts?.key ?? e.key} className={entryClass(e, opts)}
      onClick={opts?.onActivate ?? (() => onSelect({ kind: "define", name: e.name }))} title={e.name}>
      {entryContent(e, opts)}
    </button>
  );

  // SPEC-NAV-COMPOSITION — a COLLAPSIBLE row: the twisty expands, the rest of the row selects. A
  // population is both a section of the tree (it collapses) and a define (it selects), and one target
  // cannot mean both — the version that did meant collapsing a population also moved the document, and
  // the version that switched on selection state made what a click does depend on what is already
  // selected. Two targets, each one click, each meaning fixed by WHERE it lands: the tree-view
  // convention. The row is a `div` so the two buttons are siblings rather than nested (which is invalid
  // and unclickable); `.mw-node`'s hover, active wash and selection bar are class-based, so they hold.
  const renderCollapsible = (
    e: SpecEntry, open: boolean, onToggle: () => void, key: string, opts?: RowOpts,
  ) => (
    <div key={key} className={entryClass(e, opts) + " mw-node-open"}>
      <button className="mw-twisty" aria-expanded={open} onClick={onToggle}
        aria-label={`${open ? "Collapse" : "Expand"} ${e.name}`}>
        <ChevronDown size={12} className={"mw-collapse-chevron" + (open ? "" : " collapsed")} aria-hidden="true" />
      </button>
      <button className="sel" onClick={() => onSelect({ kind: "define", name: e.name })} title={e.name}>
        {entryContent(e)}
      </button>
    </div>
  );

  // SPEC-NAV-COMPOSITION — a READ row: where the tree bottoms out, and the only row in it a data
  // source can be checked against. It is not a `SpecEntry` and does not pretend to be one — no metric,
  // no dependency counts, no section of its own — so it carries the `rd` chip, its resource and the
  // filter that decides what counts as a hit (SPEC-DATA-REQUIREMENTS). Clicking scrolls to that
  // resource's group in Data requirements, which states the profile and the `mustSupport` paths.
  const renderRetrieve = (r: SpecRetrieve, opts: { key: string; repeat: boolean }) => (
    <button key={opts.key}
      className={"mw-node" + (isRetrieveSelected(selected, r.key) ? " active" : "")
        + (opts.repeat ? " repeat" : "")}
      title={retrieveLabel(r)} onClick={() => onSelect({ kind: "retrieve", key: r.key })}>
      <span className="k rd">rd</span>
      <span className="lab">{r.type}</span>
      <span className="mw-node-filter">{r.valueSet ?? r.code ?? "every record"}</span>
      {opts.repeat && (
        <span className="rep" title="Reached earlier above — shown here as a reference">
          <CornerUpLeft size={11} aria-hidden="true" />
        </span>
      )}
    </button>
  );

  return { renderEntry, renderRetrieve, renderCollapsible, downSet, upSet };
}
