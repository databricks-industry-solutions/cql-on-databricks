// APP-NAV-FILTER — the left pane's filter bar: a TEXT filter with a FILTER ICON beside it, where the
// icon opens ONE popover listing every predefined category (Type / Population / Data type) plus Sort and
// Visibility. Each category is SINGLE-SELECT; selections combine ACROSS categories (multi) and AND
// together. A badge on the icon shows how many filter categories are active.
//
// IT SITS ABOVE THE TAB BODIES AND BELONGS TO NEITHER (APP-MEASURE-FLOW). The filter has to narrow
// both the Define and Composition arrangements and survive a switch between them, so this file provides
// a hook plus a presentation component:
//
//   `useNavFilters(...)` — the state and the predicate. Held by the shell, handed to both tabs.
//   `<NavFilterBar/>`    — the control. Rendered once by the shell, above the shared scroller.
//
// Two of the popover's rows are NOT this pane's state and are deliberately routed through to the
// workspace: Sort (SPEC-SHARED-ORDER) and "show eliminated defines" (APP-SPEC-MODEL). Both change what
// the DOCUMENT is rather than how this pane lists it, so the Specification page re-orders and re-filters
// with the nav instead of holding its own answer.

import { useEffect, useMemo, useRef, useState } from "react";
import { closureOf, type LogicGraph } from "../authoring/logicGraph.ts";
import { Check, Filter, Search, X } from "../icons.tsx";
import { eliminatedCount, type SpecEntry, type SpecEntryKind, type SpecModel, type SpecSortKey } from "./specModel.ts";

type TypeFacet = "all" | SpecEntryKind;

// The Type facet offers only what the nav can LIST — the spec model's entry kinds. Value sets and
// retrieves are graph leaves that feed the Data-type facet; they were never rows (APP-SPEC-MODEL).
const TYPE_LABEL: Record<SpecEntryKind, string> = {
  population: "Populations", define: "Defines", function: "Functions",
};
const TYPE_ORDER: SpecEntryKind[] = ["population", "define", "function"];

// SPEC-SHARED-ORDER — the sort KEYS are `SpecSortKey`, declared with the model that APPLIES them
// (`specModel.ts`), because the order is the document's and not this pane's; the nav owns the control and
// nothing else. `Declared` leads and is the default: it is the CQL's own statement order, which is the
// order the document is published in. The others are analytical re-orderings of it.
const SORTS: { id: SpecSortKey; label: string }[] = [
  { id: "declared", label: "Declared" }, { id: "topo", label: "Topological" },
  { id: "usedby", label: "Used by" }, { id: "uses", label: "Uses" }, { id: "az", label: "A–Z" },
];

export interface NavFilters {
  q: string;
  setQ: (v: string) => void;
  fType: TypeFacet; setFType: (v: TypeFacet) => void;
  fPop: string; setFPop: (v: string) => void;
  fData: string; setFData: (v: string) => void;
  /** The predicate, applied to the SHARED model's entries. Narrows the list and preserves the order it
   *  was handed — ordering is `sortSpecModel`'s and visibility is `filterSpecModel`'s. */
  matchesFilters: (e: SpecEntry) => boolean;
  applyNavFilters: (entries: SpecEntry[]) => SpecEntry[];
  /** True when anything is narrowing the pane — the Flow tab says so, because `pruneComposition` re-hangs
   *  survivors under their nearest surviving ancestor and the depth on screen stops being the real one. */
  narrowed: boolean;
  hasQuery: boolean;
  activeCount: number;
  resetFilters: () => void;
  typeOptions: { id: string; label: string }[];
  popOptions: { id: string; label: string }[];
  dataOptions: { id: string; label: string }[];
  hasPopulations: boolean;
  hasDataTypes: boolean;
}

export function useNavFilters({ spec, graph, source }: {
  spec: SpecModel; graph: LogicGraph;
  /** Only used to reset the facets when the artifact changes — node ids belong to the old graph. */
  source: string;
}): NavFilters {
  const [q, setQ] = useState("");
  const [fType, setFType] = useState<TypeFacet>("all");
  const [fPop, setFPop] = useState("all");
  const [fData, setFData] = useState("all");

  useEffect(() => { setFType("all"); setFPop("all"); setFData("all"); setQ(""); }, [source]);

  const populations = useMemo(() => graph.nodes.filter((n) => n.type === "population"), [graph]);
  const dataTypes = useMemo(
    () => [...new Set(graph.nodes.filter((n) => n.type === "retrieve").map((n) => n.label))].sort(),
    [graph],
  );
  // Counted over the MODEL, so a facet is offered exactly when it would list something.
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    const bump = (e: SpecEntry) => { c[e.kind] = (c[e.kind] ?? 0) + 1; };
    spec.populations.forEach(bump);
    for (const lib of spec.libraries) lib.entries.forEach(bump);
    return c;
  }, [spec]);

  const matchesFilters = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const popClosure = fPop !== "all" ? closureOf(graph, fPop) : null;
    const nodeIdOf = (e: SpecEntry) => (e.kind === "function" ? `fn:${e.name}` : `def:${e.name}`);
    const touchesData = (id: string, resource: string): boolean => {
      if (graph.byId[id]?.type === "retrieve") return graph.byId[id]?.label === resource;
      for (const dep of closureOf(graph, id)) {
        if (graph.byId[dep]?.type === "retrieve" && graph.byId[dep]?.label === resource) return true;
      }
      return false;
    };
    return (e: SpecEntry): boolean => {
      if (fType !== "all" && e.kind !== fType) return false;
      if (needle && !e.name.toLowerCase().includes(needle)) return false;
      const node = graph.byId[nodeIdOf(e)];
      if (popClosure && !(node && (node.id === fPop || popClosure.has(node.id)))) return false;
      if (fData !== "all" && !(node && touchesData(node.id, fData))) return false;
      return true;
    };
  }, [graph, fType, fPop, fData, q]);

  const applyNavFilters = useMemo(
    () => (entries: SpecEntry[]): SpecEntry[] => entries.filter(matchesFilters), [matchesFilters]);

  // Active FILTER categories (Sort is a sort, not a filter, so it isn't counted).
  const activeCount = [fType !== "all", fPop !== "all", fData !== "all"].filter(Boolean).length;
  const hasQuery = q.trim().length > 0;

  return {
    q, setQ, fType, setFType, fPop, setFPop, fData, setFData,
    matchesFilters, applyNavFilters,
    narrowed: activeCount > 0 || hasQuery,
    hasQuery, activeCount,
    resetFilters: () => { setFType("all"); setFPop("all"); setFData("all"); },
    typeOptions: [
      { id: "all", label: "All types" },
      ...TYPE_ORDER.filter((t) => (typeCounts[t] ?? 0) > 0).map((t) => ({ id: t, label: TYPE_LABEL[t] })),
    ],
    popOptions: [{ id: "all", label: "All populations" }, ...populations.map((p) => ({ id: p.id, label: p.label }))],
    dataOptions: [{ id: "all", label: "All resources" }, ...dataTypes.map((d) => ({ id: d, label: d }))],
    hasPopulations: populations.length > 0,
    hasDataTypes: dataTypes.length > 0,
  };
}

export function NavFilterBar({ filters, fullSpec, sort, onSortChange, showEliminated, onShowEliminatedChange }: {
  filters: NavFilters;
  /** The UNFILTERED model — only for reporting how many entries the visibility toggle is hiding. */
  fullSpec: SpecModel;
  sort: SpecSortKey;
  onSortChange: (v: SpecSortKey) => void;
  showEliminated: boolean;
  onShowEliminatedChange: (v: boolean) => void;
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const { q, setQ, activeCount, resetFilters } = filters;
  const hiddenCount = eliminatedCount(fullSpec);

  // Position the filter popup as fixed so it escapes the sidebar's overflow clipping.
  function popStyle(): React.CSSProperties {
    if (!filterRef.current) return {};
    const r = filterRef.current.getBoundingClientRect();
    return { position: "fixed", top: r.bottom + 5, left: Math.max(4, r.left), width: 360 };
  }

  useEffect(() => {
    if (!filterOpen) return;
    const onDoc = (e: MouseEvent) => { if (filterRef.current && !filterRef.current.contains(e.target as Node)) setFilterOpen(false); };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setFilterOpen(false); };
    document.addEventListener("mousedown", onDoc); document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [filterOpen]);

  return (
    <div className="mw-filterbar">
      <div className="mw-filter"><Search size={14} aria-hidden="true" />
        <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Filter criteria…" aria-label="Filter criteria"
          onKeyDown={(e) => { if (e.key === "Escape" && q) { e.stopPropagation(); setQ(""); } }} />
        {q && (
          <button className="mw-filter-x" aria-label="Clear filter"
            onClick={() => { setQ(""); searchRef.current?.focus(); }}>
            <X size={12} aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="mw-filter-menu-anchor" ref={filterRef}>
        <button className="mw-filter-btn" data-active={activeCount > 0 || undefined}
          aria-haspopup="menu" aria-expanded={filterOpen} aria-label="Filters"
          onClick={() => setFilterOpen((v) => !v)}>
          <Filter size={15} />
          {activeCount > 0 && <span className="badge">{activeCount}</span>}
        </button>
        {filterOpen && (
          <div className="mw-filter-pop" role="menu" style={popStyle()}>
            <div className="mw-filter-pop-head">
              <span className="ttl">Filters</span>
              <button className="mw-filter-clear" disabled={activeCount === 0} onClick={resetFilters}>
                <X size={12} aria-hidden="true" />Clear all
              </button>
            </div>
            <div className="mw-filter-grid">
              <FilterGroup title="Type" value={filters.fType} options={filters.typeOptions}
                onPick={(v) => filters.setFType(v as TypeFacet)} />
              {filters.hasPopulations && (
                <FilterGroup title="Population" value={filters.fPop} options={filters.popOptions}
                  onPick={filters.setFPop} />
              )}
              {filters.hasDataTypes && (
                <FilterGroup title="Data type" value={filters.fData} options={filters.dataOptions}
                  onPick={filters.setFData} />
              )}
              {/* SPEC-SHARED-ORDER — order is a property of the DOCUMENT, so this drives the shared
                  model too: the Specification page re-orders with the nav rather than holding its own
                  declaration order while the nav shows another. */}
              <FilterGroup title="Sort" value={sort} options={SORTS} onPick={(v) => onSortChange(v as SpecSortKey)} />
              {/* APP-SPEC-MODEL — visibility is a property of the DOCUMENT, so this drives the
                  shared model and the Specification page changes with the nav. */}
              <div className="mw-fgroup" role="group" aria-label="Visibility">
                <div className="mw-fgroup-t">Visibility</div>
                <button className={"mw-fopt" + (showEliminated ? " on" : "")}
                  onClick={() => onShowEliminatedChange(!showEliminated)}>
                  <span className="lb">Show eliminated defines{hiddenCount > 0 && ` · ${hiddenCount}`}</span>
                  {showEliminated && <Check size={13} className="chk" />}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** A single-select group inside the filter popover: a caption + option rows (radio semantics — one
 *  choice per category). Selecting an option replaces the category's value; the check marks the current. */
function FilterGroup({ title, value, options, onPick }: {
  title: string; value: string; options: { id: string; label: string }[]; onPick: (id: string) => void;
}) {
  return (
    <div className="mw-fgroup" role="group" aria-label={title}>
      <div className="mw-fgroup-t">{title}</div>
      {options.map((o) => (
        <button key={o.id} className={"mw-fopt" + (o.id === value ? " on" : "")} role="menuitemradio"
          aria-checked={o.id === value} onClick={() => onPick(o.id)} title={o.label}>
          <span className="lb">{o.label}</span>
          {o.id === value && <Check size={13} className="chk" />}
        </button>
      ))}
    </div>
  );
}
