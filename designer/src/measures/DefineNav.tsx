// APP-SPEC-MODEL — the left pane's OUTLINE tab: the specification's table of contents, in the order
// it is published. The pane's other arrangement is the Composition (`CompositionNav.tsx`), which
// takes the same defines rooted at one population; both render the same `SpecEntry` objects through the
// same renderers (`navRows.tsx`) and select the same names, so switching tabs never changes what the
// pane is about — only how it is arranged.
//
// WHAT TO LIST IS NOT DECIDED HERE. The nav renders the spec model it is handed (`specModel.ts`) — the
// same object the Specification page renders — so a row exists exactly when a section does. The model's
// ELM-derived graph is consumed for RELATIONAL chrome only: dependency counts, closure highlighting, and
// the read vocabulary the Data-type facet offers.
//
// THE PANE SHELL OWNS THE CHROME, NOT THIS FILE (APP-MEASURE-FLOW). The tab strip, the text filter and
// the filter popover are in `NavPane` in `MeasureWorkspace.tsx` so both tabs share them. This component
// receives the already-narrowed predicate (`applyNavFilters`) and whether a filter is active (`narrowed`);
// it does not own them. Two things are
// still NOT applied here and both for the same reason — they change what the DOCUMENT is, not how this
// pane lists it: visibility of eliminated defines (`filterSpecModel`) and the catalogue's ORDER
// (`sortSpecModel`, SPEC-SHARED-ORDER).

import { useCallback, useState } from "react";
import { ChevronDown } from "../icons.tsx";
import { isRetrieveSelected, type Selection } from "./model.ts";
import { useRevealOnSelect, type NavRowKit } from "./navRows.tsx";
import type { SpecEntry, SpecLibrary, SpecModel } from "./specModel.ts";
import type { SpecRetrieve } from "./specRetrieves.ts";

// The Overview's headings, in document order. Ids match the `<h2>`s in MeasureNarrative — the nav
// scrolls to them, so the two lists are one contract and drift shows up as a dead row.
const OVERVIEW_SECTIONS = [
  { key: "description", id: "mw-ovr-desc", label: "Description", always: true },
  { key: "intent", id: "mw-ovr-intent", label: "Intent", always: false },
  { key: "rationale", id: "mw-ovr-rationale", label: "Clinical Rationale", always: false },
  { key: "recommendation", id: "mw-ovr-recommendation", label: "Clinical Recommendation", always: false },
  { key: "scope", id: "mw-ovr-scope", label: "Scope & Non-Goals", always: false },
  { key: "guidance", id: "mw-ovr-guidance", label: "Guidance", always: false },
  { key: "howToRead", id: "mw-ovr-howto", label: "How to read this document", always: true },
] as const;

/** Which optional Overview sections the open measure actually has. */
export type OverviewSections =
  Partial<Record<"intent" | "rationale" | "recommendation" | "scope" | "guidance", boolean>>;

/** Counts for the reference sections the Appendix dissolved into, each matching what its section
 *  lists — a nav row must never scroll to a heading carrying a different number.
 *
 *  `dataRequirements` is per RESOURCE, not a total, because that is the grain the section is grouped at
 *  and the grain a reader navigates at. `notices` names the headings the licence section actually
 *  carries, so a row exists exactly when a heading does. */
export interface AppendixSections {
  dataRequirements: { type: string; count: number }[];
  valuesets: number; codes: number; parameters: number;
  notices: { id: string; label: string }[];
}

export function DefineNav({ selected, spec, retrieves = [], applyNavFilters, hasQuery, overviewSections, appendixSections, onAnchor, rows }: {
  /** APP-LOGIC-VIEW — a define OR one of the reads it issues (`model.ts`). */
  selected: Selection | null;
  /** APP-SPEC-MODEL — the specification to list, already collapsed, filtered and ordered. The
   *  Specification page renders this same object. */
  spec: SpecModel;
  /** SPEC-DATA-REQUIREMENTS — every read the visible logic performs, the same list the page renders. */
  retrieves?: SpecRetrieve[];
  /** APP-NAV-FILTER — the pane shell's predicate (`NavFilterBar.tsx`). Owned there because the Flow
   *  tab narrows by the same one. */
  applyNavFilters: (entries: SpecEntry[]) => SpecEntry[];
  /** True when a text filter is active — a library with no matches then hides entirely. */
  hasQuery: boolean;
  /** SPEC-NARRATIVE-TAGS — which optional Overview sections the overlay supplied, so the nav lists a
   *  row exactly when the page has a heading for it. */
  overviewSections?: OverviewSections;
  appendixSections?: AppendixSections;
  /** APP-URL-ROUTING — land the reader on a document anchor. The workspace owns it because the
   *  Specification is only mounted under the Define lens and the pane is not (APP-MEASURE-FLOW), so
   *  reaching a heading can mean changing lens first. */
  onAnchor: (domId: string) => void;
  rows: NavRowKit;
}) {
  // Collapsible sections — all open by default.
  const [ovrOpen, setOvrOpen] = useState(true);
  // ONE section for every library, the measure's own included (`libsOpen`).
  const [libsOpen, setLibsOpen] = useState(true);
  const [appOpen, setAppOpen] = useState(true);
  const [reqOpen, setReqOpen] = useState(true);
  const [termsOpen, setTermsOpen] = useState(true);

  // Population criteria keep eCQM ORDER — Initial Population, Denominator, exclusions, Numerator —
  // which is the order the page states them in and the order a reader reasons in. Nothing re-projects
  // them here: the ordering carve-out that protects them lives in `sortSpecModel`, which never sorts
  // populations, and filtering preserves the order it is handed (SPEC-SHARED-ORDER).
  const popEntries = applyNavFilters(spec.populations);
  const rootLib = spec.libraries.find((l) => l.isRoot);
  const rootEntries = applyNavFilters(rootLib?.entries ?? []);
  // Every library, ROOT FIRST. One list because the nav treats them alike (see the Libraries section
  // below); root-first because it is the artifact the reader opened, which is the one part of "the
  // measure's library is special" that survives. The includes keep the model's own order after it.
  const allLibs = [...spec.libraries.filter((l) => l.isRoot), ...spec.libraries.filter((l) => !l.isRoot)];

  return (
    <>
      {/* Overview */}
      <button className="mw-dag-group mw-collapsible" onClick={() => setOvrOpen((v) => !v)} aria-expanded={ovrOpen}>
        <ChevronDown size={12} className={"mw-collapse-chevron" + (ovrOpen ? "" : " collapsed")} aria-hidden="true" />
        Overview
      </button>
      {ovrOpen && (
        <div className="mw-nav-nested">
          {/* One row per Overview heading, in the document's own order. Intent and Scope appear only
              when the measure actually carries them — the caller decides that from the overlay, so a
              nav row can never scroll nowhere (SPEC-NARRATIVE-TAGS, designer/docs/narrative-tags.md). */}
          {OVERVIEW_SECTIONS.filter((s) => s.always || overviewSections?.[s.key]).map(({ id, label }) => (
            <button key={id} className="mw-node" onClick={() => onAnchor(id)}>
              <span className="lab">{label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Libraries — EVERY library the artifact is made of, the measure's own among them.
          `Definitions` and `Included` used to be two sections, on the reasoning that an include is a
          different KIND of thing to the reader: other people's libraries, versioned separately. That
          distinction is real but it is not STRUCTURAL — a define is reached, named and counted the same
          way whichever library declares it, and splitting on provenance meant the root's defines sat at
          one depth while an include's sat one level deeper under a name. The reader paid for the
          distinction in indentation on every row.

          So: ONE section, one depth rule, and the root is a library group like any other. What it keeps
          is FIRST PLACE, which is the part of "this one is different" that is actually true — it is the
          artifact the reader opened.

          The count is of LIBRARIES, not of defines: it is what this section's own rows are (each row a
          library group), and the per-library counts live on the groups where their entries do. */}
      {allLibs.length > 0 && (
        <>
          <button className="mw-dag-group mw-collapsible" onClick={() => setLibsOpen((v) => !v)} aria-expanded={libsOpen}>
            <ChevronDown size={12} className={"mw-collapse-chevron" + (libsOpen ? "" : " collapsed")} aria-hidden="true" />
            Libraries · {allLibs.length}
          </button>
          {libsOpen && (
            <div className="mw-nav-nested">
              {allLibs.map((lib) => (
                <LibraryGroup key={lib.name} lib={lib}
                  /* Populations LEAD their declaring library, in eCQM reporting order, and are not
                     subject to the Sort facet (SPEC-SHARED-ORDER). They are the root's by construction,
                     which is why only the root group is handed them. */
                  entries={lib.isRoot ? [...popEntries, ...rootEntries] : applyNavFilters(lib.entries)}
                  hasQuery={hasQuery} selected={selected} rows={rows} />
              ))}
            </div>
          )}
        </>
      )}

      {/* Data requirements and Terminology are TOP-LEVEL rows, because they are top-level sections on
          the page. They were nested one level under a "Reference" group that the document has no
          heading for, so the nav claimed a hierarchy the page does not have — the disagreement
          APP-SPEC-MODEL exists to prevent, and the reason Terminology appeared to be missing
          entirely: it was there, two levels down, under a name nobody was looking for. */}
      {appendixSections && (appendixSections.valuesets > 0 || appendixSections.codes > 0) && (
        <>
          <button className="mw-dag-group mw-collapsible" onClick={() => setAppOpen((v) => !v)} aria-expanded={appOpen}>
            <ChevronDown size={12} className={"mw-collapse-chevron" + (appOpen ? "" : " collapsed")} aria-hidden="true" />
            Terminology
          </button>
          {appOpen && (
            <div className="mw-nav-nested">
              {appendixSections.codes > 0 && (
                <button className="mw-node" onClick={() => onAnchor("mw-app-codes")}>
                  <span className="lab">Codes and concepts · {appendixSections.codes}</span>
                </button>
              )}
              {appendixSections.valuesets > 0 && (
                <button className="mw-node" onClick={() => onAnchor("mw-app-vs")}>
                  <span className="lab">Value sets · {appendixSections.valuesets}</span>
                </button>
              )}
            </div>
          )}
        </>
      )}

      {/* The group appears when the SECTION does, and the section is either half of it: a measure whose
          reads were all eliminated still declares parameters, and the page renders Data requirements for
          them alone (MeasureNarrative). A nav that gated on the reads would drop the group and take the
          only route to the parameters with it. */}
      {appendixSections
        && (appendixSections.dataRequirements.length > 0 || appendixSections.parameters > 0) && (
        <>
          <button className="mw-dag-group mw-collapsible" onClick={() => setReqOpen((v) => !v)} aria-expanded={reqOpen}>
            <ChevronDown size={12} className={"mw-collapse-chevron" + (reqOpen ? "" : " collapsed")} aria-hidden="true" />
            Data requirements
          </button>
          {reqOpen && (
            <div className="mw-nav-nested">
              {/* The READS themselves, not just their resource. A read is selectable in BOTH
                  arrangements, and it has to be the same object in both or the two tabs would select
                  different things by the same gesture (APP-SPEC-MODEL). The resource stays as the
                  group it hangs under, which is the grain the page's own section is grouped at. */}
              {appendixSections.dataRequirements.map(({ type, count }) => (
                <ReadGroup key={type} type={type} count={count}
                  reads={retrieves.filter((r) => r.type === type)}
                  selected={selected} rows={rows} onAnchor={onAnchor} />
              ))}
              {/* SPEC-DATA-REQUIREMENTS — Parameters, the section's other half.
                  A ROW, not a group: the reads nest because a resource has reads under it, and a
                  parameter table has nothing under it to open. Not wrapped in a row for the reads'
                  `Retrieves and terminology` heading either — that heading's entire content is the
                  groups already listed here (APP-SPEC-MODEL). */}
              {appendixSections.parameters > 0 && (
                <button className="mw-node" onClick={() => onAnchor("mw-app-params")}>
                  <span className="lab">Parameters · {appendixSections.parameters}</span>
                </button>
              )}
            </div>
          )}
        </>
      )}

      {/* NOT "Terms" — it sat directly under "Terminology", two headings a reader has to tell apart by
          their fourth syllable. This section carries the steward's copyright, their disclaimer and the
          evidence they cite; "Notices & references" is what is actually in it. */}
      {appendixSections && appendixSections.notices.length > 0 && (
        <>
          <button className="mw-dag-group mw-collapsible" onClick={() => setTermsOpen((v) => !v)} aria-expanded={termsOpen}>
            <ChevronDown size={12} className={"mw-collapse-chevron" + (termsOpen ? "" : " collapsed")} aria-hidden="true" />
            Notices &amp; references
          </button>
          {termsOpen && (
            <div className="mw-nav-nested">
              {appendixSections.notices.map(({ id, label }) => (
                <button key={id} className="mw-node" onClick={() => onAnchor(id)}>
                  <span className="lab">{label}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

/** SPEC-DATA-REQUIREMENTS — one resource and the reads the measure issues against it. The resource is
 *  the group because that is the shape of the question ("what does this measure take from Condition?")
 *  and the grain the page's own section is grouped at; the READS under it are the selectable rows. */
function ReadGroup({ type, count, reads, selected, rows, onAnchor }: {
  type: string; count: number; reads: SpecRetrieve[];
  selected: Selection | null;
  rows: NavRowKit;
  onAnchor: (domId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const holdsSelection = reads.some((r) => isRetrieveSelected(selected, r.key));
  // This group DEFAULTS CLOSED, so before the reveal a selected read was hidden every time
  // (APP-MEASURE-FLOW). The tint below is what it did instead, and it is kept for the case where the
  // reader has closed the group again with the selection still inside.
  useRevealOnSelect(holdsSelection, useCallback(() => setOpen(true), []));
  return (
    <>
      <div className={"mw-node mw-node-open" + (holdsSelection && !open ? " rel-down" : "")}>
        <button className="mw-twisty" aria-expanded={open} onClick={() => setOpen((v) => !v)}
          aria-label={`${open ? "Collapse" : "Expand"} ${type} reads`}>
          <ChevronDown size={12} className={"mw-collapse-chevron" + (open ? "" : " collapsed")} aria-hidden="true" />
        </button>
        <button className="sel" title={type} onClick={() => onAnchor(`mw-dr-${type.toLowerCase()}`)}>
          <span className="k rd">rd</span>
          <span className="lab">{type} · {count}</span>
        </button>
      </div>
      {open && reads.length > 0 && (
        <div className="mw-nav-nested">
          {reads.map((r) => rows.renderRetrieve(r, { key: r.key, repeat: false }))}
        </div>
      )}
    </>
  );
}

/** SPEC-DEFINITIONS / APP-SPEC-MODEL — a collapsible sub-group inside the Libraries section
 *  for ONE library, the measure's own included. The entries are the SHARED model's — this group only
 *  decides open/closed. The collapse header names the library, so no "lib" row repeats it.
 *
 *  Was `IncludeGroup`, and rendered only the includes while the root's defines sat flat in a section of
 *  their own. Every library is a group now: the root declares defines exactly the way an include does,
 *  and the old split bought a provenance distinction at the price of putting two kinds of row at two
 *  depths for a reader who reaches them identically.
 *
 *  APP-NAV-DEPTH — the entries go in their OWN `.mw-nav-nested`. They used to be siblings of the
 *  header, which put a library's own defines one step LEFT of the library that declares them: the
 *  deepest rows in the nav read as the shallowest. */
function LibraryGroup({ lib, entries, hasQuery, selected, rows }: {
  lib: SpecLibrary;
  entries: SpecEntry[];
  hasQuery: boolean;
  selected: Selection | null;
  rows: NavRowKit;
}) {
  const [open, setOpen] = useState(true);
  const holdsSelection = selected?.kind === "define" && entries.some((e) => e.name === selected.name);
  useRevealOnSelect(holdsSelection, useCallback(() => setOpen(true), []));
  if (hasQuery && entries.length === 0) return null;
  return (
    <>
      <button className="mw-dag-group mw-sub mw-collapsible" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <ChevronDown size={12} className={"mw-collapse-chevron" + (open ? "" : " collapsed")} aria-hidden="true" />
        {lib.name} · {entries.length}
      </button>
      {open && (
        <div className="mw-nav-nested">
          {entries.map((e) => rows.renderEntry(e))}
          {!lib.resolved && <div className="mw-nav-foot">Source not in the registry.</div>}
        </div>
      )}
    </>
  );
}
