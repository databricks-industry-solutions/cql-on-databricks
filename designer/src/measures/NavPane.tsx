// SPEC-NAV-COMPOSITION — the workspace's LEFT PANE: the measure's structure, and the two arrangements
// of it.
//
// There is one lens now (`APP-ONE-SURFACE`), so the pane is trivially lens-invariant — but the
// property it was given deliberately is worth keeping stated: the measure's structure is the same object
// whatever you are asserting about it, and the pane's job is to hold your place in the measure.
//
// Lives in its own file rather than inside `MeasureWorkspace` because it is now three components and a
// hook's worth of behaviour, and because the pane's contract — tab strip on the first paint, filter
// above the shared scroller, one row kit for both arrangements — is worth being able to mount and test
// without standing up the whole workspace.

import React, { useEffect, useMemo, useRef } from "react";
import type { Definition } from "../catalog.ts";
import type { DefineStatusValue } from "../wire.ts";
import { CompositionNav } from "./CompositionNav.tsx";
import { DefineNav, type AppendixSections, type OverviewSections } from "./DefineNav.tsx";
import { NavFilterBar, useNavFilters } from "./NavFilterBar.tsx";
import { buildNavRowKit } from "./navRows.tsx";
import { ListTree, Waypoints, PanelWidth } from "../icons.tsx";
import type { NavTab, PaneSize, Selection } from "./model.ts";
import { buildComposition, pruneComposition, subjectName } from "./specComposition.ts";
import type { SpecDocument } from "./specDocument.ts";
import type { MetricSet } from "./specMetrics.ts";
import type { SpecModel, SpecSortKey } from "./specModel.ts";
import type { SpecRetrieve } from "./specRetrieves.ts";

export const NAV_TAB_DEFS: { id: NavTab; label: string; icon: typeof ListTree }[] = [
  { id: "outline", label: "Outline", icon: ListTree },
  { id: "composition", label: "Composition", icon: Waypoints },
];

export function NavPane({ dep, definition, source, selected, onSelectDefine, onAnchor, defineStatuses, specModel, visibleSpec, specDoc, retrieves, overviewSections, appendixSections, metrics, showEliminated, onShowEliminatedChange, defineSort, onDefineSortChange, navTab, onNavTab, size, onSize }: {
  dep: { name: string } | null; definition: Definition; source: string;
  selected: Selection | null;
  onSelectDefine: (sel: Selection) => void;
  onAnchor: (domId: string) => void;
  defineStatuses?: Record<string, DefineStatusValue>;
  /** APP-SPEC-MODEL — the unfiltered model (for the eliminated count) and the visible one the
   *  Specification page is rendering right now. */
  specModel: SpecModel | null;
  visibleSpec: SpecModel | null;
  /** SPEC-DOCUMENT — the derived decomposition, so the nav lists exactly what the page renders. */
  specDoc: SpecDocument | null;
  retrieves: SpecRetrieve[];
  overviewSections: OverviewSections;
  appendixSections: AppendixSections | undefined;
  /** APP-NAV-METRIC — the figures, from the same set the page reads. */
  metrics: MetricSet | null;
  showEliminated: boolean;
  onShowEliminatedChange: (v: boolean) => void;
  /** SPEC-SHARED-ORDER — the catalogue's order. The nav offers the choice; the model carries it, so the
   *  page reorders with it. */
  defineSort: SpecSortKey;
  onDefineSortChange: (v: SpecSortKey) => void;
  navTab: NavTab;
  onNavTab: (t: NavTab) => void;
  size: PaneSize;
  onSize: (s: PaneSize) => void;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  // A dependency is a MODE, not a lens: the pane is showing another library's source, so its own
  // arrangement of this measure does not apply.
  if (dep) {
    return (
      <nav className="mw-outline" aria-label="Navigator">
        <div className="mw-opanels">
          <div>
            <div className="mw-out-head"><span className="t">Dependency</span></div>
            <div className="mw-node active"><span className="k">lib</span><span className="lab">{dep.name}</span></div>
          </div>
        </div>
      </nav>
    );
  }

  if (size === "collapsed") {
    return (
      <nav className="mw-outline" aria-label="Navigator">
        <PaneRail side="nav" tabs={NAV_TAB_DEFS} active={navTab}
          onPick={(t) => { onNavTab(t as NavTab); onSize("normal"); }} onExpand={() => onSize("normal")} />
      </nav>
    );
  }

  // APP-STABLE-LAYOUT — the tab strip is on the FIRST paint, before the compile lands. The pane used
  // to render nothing until the model arrived, so the first paint was a blank slab beside a collapsed
  // reading column and the whole page reassembled itself a second later.
  //
  // The body mounts only once the model is real, which is also what keeps `NavPaneBody`'s hooks honest:
  // it takes non-null `visibleSpec` / `specDoc` and needs no empty-model stand-ins to satisfy the rules
  // of hooks.
  if (!specModel || !visibleSpec || !specDoc) {
    return (
      <nav className="mw-outline" aria-label="Navigator">
        <div className="mw-dnav">
          <NavTabStrip navTab={navTab} onNavTab={onNavTab} size={size} onSize={onSize} />
          <div className="mw-nav-sections">
            <div className="mw-out-head">
              <span className="t">{definition.cat === "lib" ? "Library" : "Measure"}</span>
            </div>
            <div className="mw-nav-foot"><span className="mw-spin" /> Reading the definitions…</div>
          </div>
        </div>
      </nav>
    );
  }

  return (
    <nav className="mw-outline" aria-label="Navigator">
      <NavPaneBody
        source={source}
        specModel={specModel} visibleSpec={visibleSpec} specDoc={specDoc}
        selected={selected} onSelectDefine={onSelectDefine} onAnchor={onAnchor}
        retrieves={retrieves} metrics={metrics} defineStatuses={defineStatuses}
        overviewSections={overviewSections} appendixSections={appendixSections}
        showEliminated={showEliminated} onShowEliminatedChange={onShowEliminatedChange}
        defineSort={defineSort} onDefineSortChange={onDefineSortChange}
        navTab={navTab} onNavTab={onNavTab}
        size={size} onSize={onSize} scrollerRef={scrollerRef} />
    </nav>
  );
}

/** Two arrangements of one specification, in the lens bar's language (colour + a straight accent
 *  underline, constant weight) rather than a segmented toggle: these switch a VIEW, and the bar's own
 *  view switcher is the control a reader has already learnt (design-system section 6, APP-PANE-TABS,
 *  APP-STABLE-LAYOUT).
 *
 *  The pane-size controls ride at the END of the same strip. They are NOT tabs, and `.mw-pane-ctl` has
 *  to out-specify `.mw .mw-nav-tabs button` (0,2,1) or the strip styles them as one — the `.mw-wb-tab`
 *  cascade failure in a new place, which is why `paneTabs.test.tsx` asserts it. */
export function NavTabStrip({ navTab, onNavTab, size, onSize }: {
  navTab: NavTab; onNavTab: (t: NavTab) => void; size: PaneSize; onSize: (s: PaneSize) => void;
}) {
  return (
    <div className="mw-nav-tabs" role="tablist" aria-label="Navigator view">
      {NAV_TAB_DEFS.map((t) => (
        <button key={t.id} role="tab" aria-selected={navTab === t.id} onClick={() => onNavTab(t.id)}>
          <t.icon size={13} aria-hidden="true" />{t.label}
        </button>
      ))}
      <PaneControls side="nav" size={size} onSize={onSize} />
    </div>
  );
}

/** The pane's contents once it is not collapsed and not showing a dependency.
 *
 *  Split from `NavPane` so the hooks below run unconditionally: the collapsed and dependency forms
 *  return early, and a hook after an early return is the rules-of-hooks violation that would follow
 *  from putting them all in one component. */
export function NavPaneBody({ source, specModel, visibleSpec, specDoc, selected, onSelectDefine, onAnchor, retrieves, metrics, defineStatuses, overviewSections, appendixSections, showEliminated, onShowEliminatedChange, defineSort, onDefineSortChange, navTab, onNavTab, size, onSize, scrollerRef }: {
  source: string;
  specModel: SpecModel; visibleSpec: SpecModel; specDoc: SpecDocument;
  selected: Selection | null;
  onSelectDefine: (sel: Selection) => void;
  onAnchor: (domId: string) => void;
  retrieves: SpecRetrieve[]; metrics: MetricSet | null;
  defineStatuses?: Record<string, DefineStatusValue>;
  overviewSections: OverviewSections; appendixSections: AppendixSections | undefined;
  showEliminated: boolean; onShowEliminatedChange: (v: boolean) => void;
  defineSort: SpecSortKey; onDefineSortChange: (v: SpecSortKey) => void;
  navTab: NavTab; onNavTab: (t: NavTab) => void;
  size: PaneSize; onSize: (s: PaneSize) => void;
  scrollerRef: React.RefObject<HTMLDivElement | null>;
}) {
  const filters = useNavFilters({ spec: visibleSpec, graph: visibleSpec.graph, source });

  // APP-SPEC-MODEL — ONE set of row renderers for BOTH arrangements (`navRows.tsx`). While the two
  // were closures in one component this held for free; across two files it has to be handed down.
  const rows = useMemo(() => buildNavRowKit({
    graph: visibleSpec.graph, selected, onSelect: onSelectDefine, metrics, doc: specDoc, defineStatuses,
  }), [visibleSpec.graph, selected, onSelectDefine, metrics, specDoc, defineStatuses]);

  // SPEC-NAV-COMPOSITION — the population-rooted trees, built from the model the pane is handed so its
  // order and visibility are the document's and the two tabs can never be looking at different
  // specifications. The facets describe DECLARATIONS (a Type, a population's closure, a resource a
  // define reads), so they apply to entries; a value set has none of those properties and is filtered
  // by the text box alone, riding otherwise with the define that names it (`pruneComposition`).
  const retrievesByDefine = useMemo(() => {
    const out = new Map<string, SpecRetrieve[]>();
    for (const r of retrieves) {
      for (const site of r.usedBy) out.set(site.define, [...(out.get(site.define) ?? []), r]);
    }
    return out;
  }, [retrieves]);
  const { matchesFilters, q } = filters;
  const composition = useMemo(() => {
    if (navTab !== "composition") return [];
    const needle = q.trim().toLowerCase();
    return pruneComposition(buildComposition(visibleSpec, retrievesByDefine), (s) => (
      s.kind === "entry" ? matchesFilters(s.entry)
        : !needle || subjectName(s).toLowerCase().includes(needle)
    ));
  }, [navTab, visibleSpec, retrievesByDefine, matchesFilters, q]);

  // SPEC-NAV-COMPOSITION — BRING THE SELECTED ROW INTO VIEW. The pane has always tinted it (the accent
  // wash and the 2px bar) and never revealed it, so a selection made in the document, arriving in a
  // pasted `?crit=` or reached by Back marked a row 600px below the fold and nothing moved.
  //
  // Manual `scrollTop`, NOT `scrollIntoView`: that scrolls every ancestor scroller, and here it would
  // race `scrollToCrit`'s smooth scroll of `.mw-doc` in the same frame. Arithmetic on the scroller
  // cannot touch an ancestor. One frame's delay, because a group that opens itself for this selection
  // (`useRevealOnSelect`) commits on the next render and the row is not in the DOM before it.
  useEffect(() => {
    if (!selected) return;
    const frame = requestAnimationFrame(() => {
      const box = scrollerRef.current;
      const el = box?.querySelector<HTMLElement>(".mw-node.active");
      if (!box || !el) return;
      const b = box.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      // Only when it is genuinely outside — an in-view row must not jump under the reader.
      if (r.top >= b.top && r.bottom <= b.bottom) return;
      box.scrollTop += r.top - b.top - box.clientHeight / 3;
    });
    return () => cancelAnimationFrame(frame);
  }, [selected, navTab, scrollerRef]);

  return (
    <div className="mw-dnav">
      <NavTabStrip navTab={navTab} onNavTab={onNavTab} size={size} onSize={onSize} />

      <NavFilterBar filters={filters} fullSpec={specModel}
        sort={defineSort} onSortChange={onDefineSortChange}
        showEliminated={showEliminated} onShowEliminatedChange={onShowEliminatedChange} />

      {/* The two views share the scroller: the filter bar above it stays put across a tab switch, so
          switching arrangement never also resets what the reader was looking for. */}
      <div className="mw-nav-sections" ref={scrollerRef}>
        {navTab === "composition" ? (
          <CompositionNav roots={composition} narrowed={filters.narrowed} rows={rows} />
        ) : (
          <DefineNav selected={selected} spec={visibleSpec} retrieves={retrieves}
            applyNavFilters={filters.applyNavFilters} hasQuery={filters.hasQuery}
            overviewSections={overviewSections} appendixSections={appendixSections}
            onAnchor={onAnchor} rows={rows} />
        )}
      </div>
    </div>
  );
}

/** APP-PANE-STATE — the size control at the end of a pane's tab strip.
 *
 *  ONE BUTTON, ONE GLYPH, THREE STATES, cycling normal → expanded → collapsed → normal. It was two
 *  buttons with two glyphs, and that was wrong in the way a control is wrong when it makes you read
 *  it: two icons that both mean "resize this pane" ask the reader to work out which of them does the
 *  thing they want, every time, for a control they use without looking. One target that advances
 *  through the states is the idiom a sidebar toggle already has.
 *
 *  The glyph ROTATES rather than changing, so the control is one object throughout: at `normal` it
 *  points out (expand), at `expanded` it points in (collapse to normal), at `collapsed` it points out
 *  again. `title` and `aria-label` say what the NEXT press does, because that is the question a
 *  reader has; `aria-label` also carries the current state so a screen reader is not told only about
 *  a future.
 *
 *  Always rendered, never disabled and never absent — a control that comes and goes shifts the strip
 *  under the pointer (`APP-STABLE-LAYOUT`). */
const PANE_CYCLE: Record<PaneSize, PaneSize> = {
  normal: "expanded", expanded: "collapsed", collapsed: "normal",
};
const PANE_NEXT_LABEL: Record<PaneSize, string> = {
  normal: "Expand pane", expanded: "Collapse pane", collapsed: "Restore pane",
};

export function PaneControls({ side, size, onSize }: {
  side: "nav" | "wb"; size: PaneSize; onSize: (s: PaneSize) => void;
}) {
  return (
    <span className="mw-pane-ctls">
      <button className="mw-pane-ctl" data-size={size} data-side={side}
        title={PANE_NEXT_LABEL[size]}
        aria-label={`Pane width: ${size}. ${PANE_NEXT_LABEL[size]}.`}
        onClick={() => onSize(PANE_CYCLE[size])}>
        <PanelWidth size={14} aria-hidden="true" />
      </button>
    </span>
  );
}

/** APP-PANE-STATE — what a COLLAPSED pane renders. Not clipped content: a strip of the pane's own tab
 *  glyphs, where clicking one both expands the pane and selects that tab, so the reader gets back to the
 *  arrangement they wanted in one click rather than two. No rotated text — a 44px column is not a place
 *  to read a word sideways. */
export function PaneRail({ side, tabs, active, onPick, onExpand }: {
  side: "nav" | "wb";
  tabs: { id: string; label: string; icon: typeof ListTree }[];
  active: string;
  onPick: (id: string) => void;
  onExpand: () => void;
}) {
  return (
    <div className="mw-pane-rail">
      <button className="mw-pane-ctl" data-size="collapsed" data-side={side}
        title="Restore pane" aria-label="Pane width: collapsed. Restore pane." onClick={onExpand}>
        <PanelWidth size={14} aria-hidden="true" />
      </button>
      {tabs.map((t) => (
        <button key={t.id} className={"mw-pane-ctl" + (active === t.id ? " on" : "")}
          title={t.label} aria-label={t.label} onClick={() => onPick(t.id)}>
          <t.icon size={14} />
        </button>
      ))}
    </div>
  );
}
