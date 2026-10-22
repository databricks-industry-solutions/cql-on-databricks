// APP-MEASURE-DOC / APP-LIVING-MEASURE — the Narrative view: the specification rendered as a
// published measure technical spec (spec register — serif on oat). Long-form and readable; toggled
// Live, run figures are computed in surgically: a compact figure + bar beside each population heading.
// Static reads as the pure published spec (no numbers).
//
// APP-SPEC-MODEL — WHAT the document contains is not decided here. This component renders the spec
// model it is handed (specModel.ts) — the same object the left nav renders — so the two surfaces cannot
// disagree. Overload groups arrive already collapsed to one entry carrying its variants, and eliminated
// defines are already filtered. NO dependency-graph chrome here — that lives in the classic authoring
// Narrative.
//
// SPEC-NARRATIVE-TAGS — every criterion answers THREE questions, not one. "What is this" alone is what
// made the old document unreadable to a clinician: `Initial Population` rendered as "the eligible
// cohort before any exclusions", which restates the name and tells a reviewer nothing they can act on.
// So a population, a Concept and a Rule all render the same triple — WHAT it is, WHY it matters, HOW
// TO READ IT — and the prose arrives already merged from the CQL's own tags and the Specification
// overlay (`specDocument.ts`), each field carrying its own provenance.
//
// PROVENANCE IS VISIBLE, ALWAYS (APP-NARRATIVE-PROVENANCE). Interpreted prose is led by an EXPLAIN
// chip; the author's own words carry a byline instead. A clinician reviewing a criterion has to know
// whether the sentence in front of them is the measure author's assertion or our reading of their code,
// and no amount of accuracy substitutes for saying which. The mark leads a RUN of fields rather than
// each field because every field of every shipped instance is narrated: a per-field chip fired on all of
// them and so distinguished nothing (`NarratedRun`).

import { createContext, Fragment, useCallback, useContext, useEffect, useLayoutEffect, useMemo,
  useRef, useState, type ReactNode, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import type { Definition } from "../catalog.ts";
import type { ElmLibraryInfo } from "../authoring/elmDefines.ts";
import { overlayKey, type NarratedText, type SpecBlock, type SpecDefineRef,
  type SpecificationOverlay, type SpecMetricRef, type SpecReadRef,
  type SpecParameterRef, type SpecSegment, type SpecTerminologyRef } from "../library-client.ts";
import type { SpecRetrieve } from "./specRetrieves.ts";
import { terminologyAnchor as termAnchor, useSpecLens, type ReadLens, type TermLens }
  from "./specLens.ts";
import { Markdown, MarkdownInline } from "../markdown.tsx";
import { CqlCode, type CqlLinker } from "../authoring/cqlHighlight.tsx";
import { Play, Copy, Focus, Sparkle, Explain, Trash, ChevronDown, Plus, OpenRef, Pin } from "../icons.tsx";
import { FunnelView } from "./Funnel.tsx";
import { isDefineSelected, isRetrieveSelected, selectionToken, type Funnel, type Selection } from "./model.ts";
import { formatShare, metricKey, metricTitle, resolveMetric, type MetricSet } from "./specMetrics.ts";
import { readValue } from "./defineValue.ts";
import { readoutText, scoringLabel, scoringMethodology } from "./specScoring.ts";
import { entriesByName, type SpecEntry, type SpecLibrary, type SpecModel } from "./specModel.ts";
// SPEC-REF-LENS — the lens renders the INSPECTOR's own body rather than a summary of it, so the pane's
// three tabs, its scope identity and its profile fetch are imported rather than re-expressed here. The
// edge is one-way (this document reads the inspector; the inspector never reads the document), which is
// what keeps it a shared component and not a cycle.
import { InspectorIdentity, InspectorScope, useInspectorTab,
  type InspectorContext, type ProfileScope } from "./Workbench.tsx";
import {
  isNarrated, proseRuns,
  type SpecDefineView, type SpecDocument,
  type SpecMetric, type SpecNarration, type SpecProse, type SpecScope,
} from "./specDocument.ts";

type SectionKey = "overview" | "libraries" | "datareq" | "terminology" | "terms" | `inc-${string}`;

/** SPEC-EXPLAIN-AGAIN — which section a re-explanation would be about.
 *
 *  `label` is what the reader called it and is always present; `define` is the addressable node, and it
 *  is ABSENT for a section that is not one (the measure's Summary). That distinction is not cosmetic:
 *  the assistant's tools address a node by `(library, version, define, source, period)`
 *  (`SVC-TOOL-ADDRESS`), so a target with no define scopes the conversation to the MEASURE, and
 *  inventing a define name for the Summary would prefill an argument no tool could resolve. */
export interface ExplainTarget {
  label: string;
  define?: string;
}

/** SPEC-EXPLAIN-AGAIN — the provenance badge's two verbs. See `MeasureNarrative`'s `explainAgain` for
 *  why this is a context rather than a prop. Either half may be absent: a surface with no assistant
 *  supplies neither, and the badge then renders its verb disabled with the reason. */
interface ExplainAgainVerbs {
  /** The badge was pressed: THIS section is now what "Explain again" is about. Opens nothing. */
  scope?: (target: ExplainTarget) => void;
  /** Re-explain it — hand the section to the assistant, scoped. */
  run?: (target: ExplainTarget) => void;
}

const ExplainAgainContext = createContext<ExplainAgainVerbs | null>(null);

export function MeasureNarrative({ definition, metrics, readCounts, funnel, live, selected, onSelect, onNavigate, onExplainScope, onExplainAgain, onRunCell, results, spec, elmInfo, overlay, doc, narratedLibraries, retrieves = [], namedTerminology, parameters = {}, profileScope = null, explain }: {
  definition: Definition;
  /** SPEC-REF-LENS / APP-DATA-PROFILE — which evaluation a reference lens profiles when a reader opens
   *  one. The lens renders the inspector's own tabs, and a profile is a fact about ONE evaluation, so the
   *  document has to be told which — exactly as the pane is.
   *
   *  NAMED `profileScope` RATHER THAN `scope`: this document already has a `scope`, and it is a different
   *  thing entirely (`SpecScope` — the measure's in-scope statement and its non-goals, rendered as prose).
   *  Two meanings for one word in one file is how the wrong one gets read.
   *
   *  Null with no data source selected, and then the lens asks for nothing and its tabs say so — the same
   *  degradation the pane makes, because it IS the pane. */
  profileScope?: ProfileScope | null;
  /** APP-SAMPLE-METRICS — every figure this document renders, from one payload. The nav reads the
   *  same set through the same resolver, so a row and its section cannot print different numbers. */
  metrics: MetricSet | null;
  /** SPEC-READ-REF — subjects behind each READ, keyed by the retrieve key. A read this evaluation did
   *  not materialize is ABSENT from the map rather than zero (`SPEC-METRIC`), which is what lets a
   *  bound phrase render its words with no chip instead of claiming nothing matched. */
  readCounts?: Map<string, number> | null;
  /** SPEC-REPORTED-RESULT — the population funnel, derived from the same figures. */
  funnel: Funnel | null;
  /** APP-LIVING-MEASURE — false = the pure published spec; true = figures computed into it. */
  live: boolean;
  /** APP-LOGIC-VIEW — the criterion selected across nav ↔ Spec (persistent). */
  selected: Selection | null;
  onSelect: (sel: Selection) => void;
  /** SPEC-REF-LENS — follow a reference: select it AND scroll to the section that renders it.
   *  Distinct from `onSelect`, which toggles selection in place — clicking a reference is navigation
   *  and leaving the reader where they were is the bug this exists to fix. */
  onNavigate: (sel: Selection) => void;
  /** SPEC-EXPLAIN-AGAIN — a section's Explain mark was pressed, so THAT section is what the toolbar's
   *  "Explain again" would re-explain. Scoping only; it opens nothing and moves nothing. */
  onExplainScope?: (target: ExplainTarget) => void;
  /** SPEC-EXPLAIN-AGAIN — re-explain this section: hand it to the assistant, scoped. Optional, and its
   *  absence renders the disclaimer's verb DISABLED with the reason — a surface with no drawer cannot
   *  re-explain anything, and a verb that fires into nothing is worse than one that says why it can't. */
  onExplainAgain?: (target: ExplainTarget) => void;
  /** APP-SPEC-CELLS — a cell "Run" (compute so this criterion's figure appears). */
  onRunCell: (name: string) => void;
  /** APP-DEBUG — the per-subject results from the run, keyed by subject id → define → value.
   *  Passed through to each SpecCell so it can render a "View results" output table. */
  results?: Record<string, Record<string, unknown>> | null;
  /** APP-SPEC-MODEL — the specification to render, already collapsed and filtered. The left nav
   *  renders this same object; nothing about membership is re-derived here. */
  spec: SpecModel;
  /** APP-CQL-AUTHORITATIVE — library identity from the compiler's ELM output. */
  elmInfo: ElmLibraryInfo;
  /** APP-NARRATIVE-OVERLAY — the Specification narration overlay (null when none is published). Only
   *  the Overview, the library overviews and the Appendix are read from it HERE; per-define prose has
   *  already been merged into `doc` by `buildSpecDocument`, so the two cannot disagree. */
  overlay?: SpecificationOverlay | null;
  /** SPEC-DOCUMENT — the derived structure: populations → Concepts → Rules, plus COMMON. Derived from
   *  `spec`, so the two cannot disagree (APP-SPEC-MODEL). */
  doc: SpecDocument;
  /** SPEC-LIBRARY-DENSITY — libraries with at least one narrated criterion, which is what decides
   *  whether a library opens Verbose or Compact.
   *
   *  Derived rather than listed by name: FHIRHelpers and other conversion helpers are quiet here not
   *  because their names are hard-coded but because nobody has written a clinical sentence about
   *  anything in them, and nothing in them would reward a full section. A shared exclusion library is
   *  narrated, so it opens. A name list goes stale the first time a library is narrated; this default
   *  moves with the narration.
   *
   *  Computed from the UNFILTERED overlay by the caller, deliberately — `overlay` here is already
   *  filtered by the Explain toggle, and a library must not collapse to rows just because the reader
   *  turned narration off. */
  narratedLibraries?: Set<string>;
  /** SPEC-DATA-REQUIREMENTS — every read the VISIBLE logic performs, one row per (type, profile,
   *  filter) triple, each carrying the reachable defines that issue it and the anchor back to their
   *  CQL. Derived from the ELM so elimination governs it, enriched with `mustSupport` from the
   *  published resource (`specRetrieves.ts`). */
  retrieves?: SpecRetrieve[];
  /** SPEC-DERIVED-APPENDIX — the terminology the VISIBLE logic names, `library::name`. Elimination
   *  governs terminology exactly as it governs defines and reads: a value set only an unreachable
   *  define mentions is one this measure does not use, and listing it invites a reader to expand
   *  codes for a rule that never fires.
   *
   *  REQUIRED, and deliberately so. It was optional with `?? true` behind it — "over-listing beats
   *  dropping a real binding" — and that reasoning was wrong in the way permissive defaults usually
   *  are: the prop was never wired to this component at all, the fallback listed all 34 declarations,
   *  and the page looked exactly as it would if the filter were working. The nav, which DID receive
   *  it, quietly disagreed. A required prop makes the same mistake a compile error. */
  namedTerminology: { valueSets: Set<string>; codes: Set<string> };
  /** SPEC-PARAMETER-REF — the execution parameters this document is scoped to, by CQL parameter name.
   *  Run INPUTS, not results (`APP-EXEC-PARAMS`), so prose citing one resolves with no data source. */
  parameters?: Record<string, string>;
  /** SPEC-EXPLAIN-VIEW — whether the interpreted layer is showing. The overlay handed in is ALREADY
   *  filtered by provenance, and this is the part of the toggle that filtering cannot express: what a
   *  section renders when its prose is gone.
   *
   *  Off, a section IS ITS CQL — the code opens (`SPEC-CQL-VIEW`) and the derived sentences that gloss
   *  it in English are dropped. Without this the Explain-off document was a page of headings over
   *  closed disclosures: the reader asked for the logic and got a "Show CQL" button under a sentence
   *  restating one line of it.
   *
   *  REQUIRED, for the same reason `namedTerminology` is: a default would make an unwired page look
   *  exactly like a working one, and the failure would be silent in the direction that matters least
   *  (always-on interpretation) until someone flipped the toggle and nothing moved. */
  explain: boolean;
}) {
  const header = { name: elmInfo.name, version: elmInfo.version, fhir: elmInfo.fhirVersion };
  const populations = spec.populations;
  const isMeasure = definition.cat !== "lib";

  // APP-SPEC-COLLAPSE — every top-level section (overview, populations, supporting, plumbing) is
  // individually collapsible, driven by one set of collapsed keys. The Overview always shows
  // (description + how-to lead the doc). There is no Collapse/Expand-all control — sections toggle one at a time.
  const [collapsed, setCollapsed] = useState<Set<SectionKey>>(new Set());
  const toggle = (k: SectionKey) => setCollapsed((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  // APP-MEASURE-FLOW — a selection must land on a VISIBLE section, so the card that holds it opens
  // when the selection is made: tinting a row the reader cannot see is not landing on it. Every define
  // and population renders under Libraries and every read under Data requirements, so the selection's
  // KIND names its card.
  //
  // KEYED ON THE SELECTION OBJECT, not on a holds-boolean transition (the nav's `useRevealOnSelect`).
  // Selection is persistent, so "a define is selected" is true almost always and a boolean edge would
  // fire only on the first selection ever made — collapse the card, pick a DIFFERENT define in the nav,
  // and nothing opened. The object is the workspace's memo over the URL token (`APP-URL-ROUTING`),
  // so its identity changes exactly when the SELECTION changes — between two criteria of one card
  // included, which is the case the boolean misses. Re-selecting the same criterion changes no state
  // at all (the URL is the state and it is already there); the reader can still shut a card that holds
  // the selection, and it stays shut until the selection next changes.
  //
  // A LAYOUT effect, as are the two reveals below it. The workspace scrolls to the selection one frame
  // after the click (`selectFromNav`), and a passive effect commits the expansion asynchronously —
  // sometimes after that frame, so the scroll either found nothing to scroll to or set off smoothly
  // toward a position the expansion then moved. A layout effect commits in the same flush as the
  // selection render, before anything paints or scrolls, so the scroll always measures the final page.
  const openSection = useCallback((k: SectionKey) => setCollapsed((s) => {
    if (!s.has(k)) return s;
    const n = new Set(s); n.delete(k); return n;
  }), []);
  useLayoutEffect(() => {
    if (selected) openSection(selected.kind === "retrieve" ? "datareq" : "libraries");
  }, [selected, openSection]);

  // SPEC-DEFINITIONS — a catalogue row's one-liner is the SAME narration the criteria sections show,
  // looked up library-qualified. One narration per define, reused wherever the define appears, so the
  // catalogue and the criteria can never state different things about it. There is no generic blurb
  // map behind this any more: a define nobody has narrated shows nothing, which is honest, where
  // "computed over the population by the measure logic" was noise dressed as information.
  const defBlurb = (library: string, name: string): string | null =>
    overlay?.elements[overlayKey(library, name)]?.description?.text ?? null;

  const overview = overlay?.overview;

  // SPEC-NARRATIVE-TAGS — Scope & Non-Goals is ONE lane of prose: `@scope` states the boundary and each
  // `@nonGoal` states a piece of it, in that order. Ordering them into one list is what lets them share
  // one EXPLAIN chip (`proseRuns`), and the `kind` is only how each item renders — a paragraph or a
  // bullet — never what it is for provenance.
  const scopeItems: ScopeItem[] = [
    ...(overview?.scope?.inScope ? [{ kind: "scope", prose: overview.scope.inScope } as const] : []),
    ...(overview?.scope?.nonGoals ?? []).map((prose) => ({ kind: "nonGoal", prose }) as const),
  ];
  const scopeRuns = proseRuns(scopeItems, (it) => toProse(it.prose));
  const firstNonGoalRun = scopeRuns.findIndex((r) => r.items.some((it) => it.kind === "nonGoal"));

  // SPEC-DEFINITIONS — the measure and what it includes are two different things to a reader, so they
  // are two sections. The root library is the artifact under review; the rest are dependencies.
  const rootLib = spec.libraries.find((l) => l.isRoot) ?? null;
  const includeLibs = spec.libraries.filter((l) => !l.isRoot);

  // SPEC-REF-LENS, APP-ASSISTANT-REFS — everything an inline reference resolves against, built by
  // the SHARED hook (`specLens.ts`) rather than here. It moved out when the assistant's answers began
  // carrying the same references: a define named in a sentence, in the CQL, and in an answer is one
  // object, so it gets one resolution — and a second copy of these resolvers is how two surfaces come
  // to disagree about what a reference means, with both still rendering a plausible link.
  const lens = useSpecLens({
    definitionId: definition.id, spec, doc, metrics, live, funnel, profileScope, retrieves,
    namedTerminology, readCounts: readCounts ?? null, parameters, selected, onSelect, onNavigate,
  });
  const { byName, defineEntry, resolveRetrieve, terminology, inspector: inspectorCtx,
    reads: readLens } = lens;


  // SPEC-EXPLAIN-AGAIN — the two verbs every provenance badge on the page needs, bundled once.
  //
  // A CONTEXT, AND THIS IS THE CASE THAT EARNS ONE. The badge renders on THREE independent chains —
  // an overview heading, a population's scope block, a define's heading — none of which otherwise
  // knows the assistant exists. Threading one optional callback through six intermediate components
  // that have no other use for it is how a prop comes to be forgotten on one of the three routes, and
  // a forgotten one here fails silently: the badge simply renders its verb disabled forever, which is
  // exactly what it did before this existed and therefore looks correct. The alternative considered
  // was passing it on `inspectorCtx`; that bundle is what a scoped READING needs, and a verb about
  // re-authoring prose is not that.
  const explainAgain = useMemo<ExplainAgainVerbs | null>(
    () => (onExplainScope || onExplainAgain ? { scope: onExplainScope, run: onExplainAgain } : null),
    [onExplainScope, onExplainAgain]);

  // SPEC-CQL-LINKS — a quoted name in the CQL is a reference to something the document renders, so
  // it is followable. Defines get the lens; a value set gets a jump to the Appendix row that states
  // its OID. A name that resolves to neither stays plain syntax rather than becoming a dead link.
  // Everything else the Appendix states: value sets, direct reference codes, parameters. Each is a
  // real declaration the reader can look up, so each is followable to the row that states its OID,
  // its code or its type.
  const appendixRefs = useMemo(() => {
    const out = new Map<string, string>();
    for (const v of doc.appendix.valuesets) out.set(v.name, "mw-app-vs");
    for (const c of doc.appendix.codes) out.set(c.name, "mw-app-codes");
    for (const p of doc.appendix.parameters) out.set(p.name, "mw-app-params");
    return out;
  }, [doc.appendix]);

  // SPEC-DERIVED-APPENDIX — Terminology is the REGISTRY: every code and value set the visible logic
  // names, with its OID stated once. Nothing is subtracted for also appearing under Data requirements
  // — the two answer different questions ("what terminology does this measure bind?" versus "what
  // does this read filter by?") and a value set legitimately appears in both. Hiding the filters here
  // made the registry incomplete, which is worse than the repetition it avoided.
  //
  // What IS subtracted is anything only unreachable logic mentions: elimination governs terminology
  // exactly as it governs defines and reads, so a value set no live rule names is not listed.
  // SPEC-DATA-REQUIREMENTS — grouped by RESOURCE, because that is the level a reader navigates at:
  // "what does this take from Condition" is the question, and forty ungrouped rows answer it only by
  // being read end to end. Grouping also gives the section real headings, which is what lets the nav
  // point INTO it rather than only at it.
  const retrievesByType = useMemo(() => {
    const out = new Map<string, SpecRetrieve[]>();
    for (const r of retrieves) out.set(r.type, [...(out.get(r.type) ?? []), r]);
    return [...out.entries()];
  }, [retrieves]);

  const cqlLink = useCallback<CqlLinker>((name, raw) => {
    const entry = byName.get(name);
    if (entry) {
      return (
        <DefineLink entry={entry} ctx={inspectorCtx} selected={isDefineSelected(selected, name)}
          onSelect={onSelect} className="cql-link">
          {raw}
        </DefineLink>
      );
    }
    const retrieve = resolveRetrieve(name);
    if (retrieve) {
      return <RetrieveLink token={raw} retrieve={retrieve} ctx={inspectorCtx}
        selected={isRetrieveSelected(selected, retrieve.key)} onSelect={onSelect} />;
    }
    const anchor = appendixRefs.get(name);
    if (anchor) {
      return (
        <button className="cql-link vs" title="Declared in the Appendix"
          onClick={(e) => {
            e.stopPropagation();
            document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}>
          {raw}
        </button>
      );
    }
    return null;
  }, [byName, appendixRefs, inspectorCtx, onSelect, selected, resolveRetrieve]);

  return (
    <ExplainAgainContext.Provider value={explainAgain}>
    <article className="mw-spec">
      <div className="mw-spec-eyebrow">
        {isMeasure ? "eCQM measure specification" : "CQL library specification"}
        {!live && <span className="mw-spec-static"> · static</span>}
      </div>
      <h1 className="mw-spec-title">{definition.title ?? definition.name}</h1>
      {/* SPEC-SCORING-METHODOLOGY — the scoring type is DECLARED, never assumed. This slot held the
          literal string "proportion" for every measure regardless of what it actually was: a false
          statement in a clinical document, printed with the same authority as the FHIR version beside
          it. It now prints `overlay.scoring` — `Measure.scoring` from the published resource, falling
          back to `Identity/@scoring` — and prints NOTHING where neither declares one, which is the
          rule the rest of this module already follows for a figure it cannot state honestly. */}
      <div className="mw-spec-meta">
        {[definition.src, header.fhir && `FHIR ${header.fhir}`, isMeasure && scoringLabel(overlay?.scoring)]
          .filter(Boolean).join("  ·  ")}
      </div>

      {/* APP-NARRATIVE-PROVENANCE — NO MASTHEAD LEGEND. The per-run signature carries the whole claim;
          the toolbar's Explain control carries the toggle. */}

      {/* ── Summary — what the measure IS and what it PRODUCED, the two things a reader wants first.
             (SPEC-NARRATIVE-TAGS, designer/docs/narrative-tags.md for the root-tag alignment.)

             THE EYEBROW IS "SUMMARY" AND THE CARD IS "OVERVIEW". The word `overview` names this prose
             everywhere it is modelled — `doc.overview`, `OverviewHeading`, `#mw-sec-overview` — and it means
             what it says: description, intent, rationale, guidance. The CARD keeps `Overview`; the section
             above it — which now holds the result as well — takes the broader name. `Summary` covers both:
             a summary figure and a summary description.

             THE RESULT MOVED IN HERE, from the ground above the section. It is the figure every reader of
             an eCQM specification comes for, and on the ground it was a card belonging to no section, which
             is also the one thing the reader could not fold away. One period, one rate: the TREND is
             Analyze's, and it is gated on accumulated run history this document does not have. ── */}
      <SpecSection id="overview" title="Summary" cards collapsed={collapsed.has("overview")} onToggle={toggle}>
      {/* NO CARD WRAPPER AND NO TITLE. `.mw-spec-result` is a card with same fill, border, radius and
          shadow, so `SpecCard` around it would draw a card inside a card. The figure and its label say
          what it is; a header is chrome introducing something already visible. It is the one card that
          folds only with its section. */}
      {isMeasure && <ReportedResult funnel={funnel} metrics={metrics} />}
      <SpecCard title="Overview">
        <OverviewHeading id="mw-ovr-desc" title="Description" prose={overview?.description} />
        {overview?.description
          ? <OverviewProse prose={overview.description} metrics={metrics} reads={readLens} />
          : <p>{definition.desc || "No description is recorded for this definition."}</p>}

        {/* SPEC-OVERVIEW-HEADINGS — one heading per question, all at the same level.
            Clinical rationale and clinical recommendation are separately authored fields answering
            different questions (why the measure exists vs which guideline it implements), and nesting
            them under "Intent" made two substantial pieces of the steward's own prose read as footnotes to a
            sentence we wrote. */}
        {overview?.intent?.statement && (
          <>
            <OverviewHeading id="mw-ovr-intent" title="Intent" prose={overview.intent.statement} />
            <OverviewProse prose={overview.intent.statement} metrics={metrics} reads={readLens} />
          </>
        )}
        {overview?.intent?.clinicalRationale && (
          <>
            <OverviewHeading id="mw-ovr-rationale" title="Clinical Rationale"
              prose={overview.intent.clinicalRationale} />
            <OverviewProse prose={overview.intent.clinicalRationale} metrics={metrics} reads={readLens} />
          </>
        )}
        {overview?.intent?.recommendation && (
          <>
            <OverviewHeading id="mw-ovr-recommendation" title="Clinical Recommendation"
              prose={overview.intent.recommendation} />
            <OverviewProse prose={overview.intent.recommendation} metrics={metrics} reads={readLens} />
          </>
        )}

        {/* @scope + @nonGoal — the boundary. An absent non-goal list means nobody wrote one down,
            which is not the same claim as "there are none", so the lane is omitted rather than empty. */}
        {scopeRuns.length > 0 && (
          <>
            {/* The mark answers for the SECTION, exactly as a criterion's does: the runs below may mix
                the author's boundary with one we inferred, and the heading says there is interpreted
                prose here — the run that is the author's still carries their byline. */}
            <OverviewHeading id="mw-ovr-scope" title="Scope & Non-Goals"
              prose={scopeRuns.find((r) => r.provenance === "narrated")?.items[0]?.prose} />
            {/* ONE SECTION, ONE RUN. `@scope` and the `@nonGoal`s are adjacent prose under one heading,
                so they group like any other adjacent prose — two chips here said one thing twice, which
                is the failure the run exists to fix, and the bullets under a chip of their own read as a
                second kind of claim when they are the other half of the same one.

                Each field keeps its OWN provenance and the run still breaks on it: a steward who states
                a boundary in the measure resource and one we inferred from the logic are different
                claims, so an authored `@nonGoal` leaves the run and the chip stops covering it. */}
            {scopeRuns.map((run, i) => (
              <NarratedRun key={i} run={run}>
                {run.items.filter((it) => it.kind === "scope").map((it, j) => (
                  <OverviewBody key={j} prose={it.prose} metrics={metrics} reads={readLens} />
                ))}
                {/* The label goes on the first bullet group in the SECTION, not the first in each run —
                    it names the list, and a run that happens to start midway has not started a new one. */}
                {run.items.some((it) => it.kind === "nonGoal") && (
                  <div className="mw-narr-block">
                    {i === firstNonGoalRun && <span className="mw-narr-label">Not what this measure does</span>}
                    <ul className="mw-nongoals">
                      {/* SPEC-PARAMETER-REF — a non-goal is a sentence like any other, so it renders
                          through the one inline renderer. It printed `prose.text`, which dropped both
                          its markdown and any reference it named. */}
                      {run.items.filter((it) => it.kind === "nonGoal").map((it, j) => (
                        <li key={j}>
                          <Segments segments={it.prose.segments ?? [{ kind: "text", text: it.prose.text }]}
                            metrics={metrics} reads={readLens} cite={noCite} />
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </NarratedRun>
            ))}
          </>
        )}

        {/* @guidance at ROOT level — the author's own instructions for applying the logic
            (`Measure.usage`). Typically the most useful authored field there is: an example reading
            measure's would state which readings may be counted at all. Kept apart from "How to read this
            document" below, which is the product's voice about the document, not the measure. */}
        {overview?.guidance && (
          <>
            <OverviewHeading id="mw-ovr-guidance" title="Guidance" prose={overview.guidance} />
            <OverviewProse prose={overview.guidance} metrics={metrics} reads={readLens} />
          </>
        )}

        {/* The heading says DOCUMENT, not "measure" — this section is about the page in front of the
            reader, not about the logic. It sat one heading below the measure's own `@guidance`, which
            IS about the measure, and the two read as a pair when they answer different questions.

            NO HARDCODED FALLBACK (`SPEC-NO-UI-PROSE`, 2026-08-08). Four paragraphs of product copy used
            to render here whenever `@howToRead` was absent, and it was defended as "copy about the page,
            not narration of a measure". That defence does not survive the reader's position: it landed
            in the reading column, in the document's own voice, under a heading, with no byline and no
            Explain mark — indistinguishable from the measure's authored prose, and correctable only by a
            deploy. It also went stale exactly where it claimed authority, still describing an "Explain
            chip" the document replaced with a heading badge and an <b>Appendix</b> section the merge
            renamed.

            The field has a real home — `@howToRead` at root level, `Overview/HowToRead` in the XSD
            (`designer/docs/narrative-tags.md`) — and every shipped Specification instance authors it. So
            the fallback was dead for all real content and live only where nobody had written anything,
            which is precisely where the product should say nothing. The heading renders when the field
            does, or when the derived scoring block below has something to state; with neither, the
            section is absent rather than empty. */}
        {(overview?.howToRead || scoringMethodology(overlay?.scoring ?? null, populations.map((p) => p.name))) && (
          <OverviewHeading id="mw-ovr-howto" title="How to read this document" prose={overview?.howToRead} />
        )}
        {overview?.howToRead && (
          <OverviewProse prose={overview.howToRead} metrics={metrics} reads={readLens} />
        )}
        {/* SPEC-SCORING-METHODOLOGY — the scoring rules, DERIVED from the populations the logic declares
            and identical for every measure of this shape. It sits inside "How to read this document"
            because that is what it is: how to read the figures the rest of the page reports. It is NOT
            narration, and `clinical-explain/SKILL.md` forbids writing it as prose — a measure that
            explains its own rate formula spends a clinical paragraph on arithmetic and invites getting
            the exception rule subtly wrong. Renders under Explain-off too: methodology is published, not
            interpreted. */}
        <ScoringBlock scoring={overlay?.scoring ?? null} populations={populations.map((p) => p.name)} />
        {/* NO PRECEDENCE PARAGRAPH. The instances author "The CQL is authoritative for what the measure
            computes. This Specification is a derived overlay…" — a true, load-bearing statement about
            THIS DOCUMENT rather than the measure. The Overview is where a clinician reads what the measure
            is. A reader who came for the numerator does not need a paragraph about which artifact wins a
            disagreement; reviewers and implementers meet the rule where enforced. The FIELD persists:
            `overlay.precedence` still arrives and survives `withoutNarrated` (`specDocument.ts`). Only
            the reading column stops printing it. */}
      </SpecCard>
      </SpecSection>

      {/* ── Libraries — EVERY library the artifact is made of, its own first (SPEC-DEFINITIONS).
             One section, mirroring the nav (APP-SPEC-MODEL): a row exists exactly when a section does,
             and two surfaces disagreeing about the document's SHAPE is the same defect as two disagreeing
             about a number. Provenance is a fact ABOUT a library, not a reason to give it a different shape.

             What the root keeps is FIRST PLACE: it is the artifact the reader opened. ── */}
      {/* APP-STYLE-GUIDE — `bare`, because EACH LIBRARY IS A CARD. Libraries is the one section whose
          children are cards, so it carries none of its own: a run of cards with a label on the ground
          above them. A card edge ranks the library label louder than a label inside a card could; the
          GROUP still reads as a group and the nav still has one target. */}
      <SpecSection id="libraries" title="Libraries" cards collapsed={collapsed.has("libraries")} onToggle={toggle}>
        {/* NO SECTION BLURB (`SPEC-NO-UI-PROSE`). A `.mw-desc` paragraph here would explain what
            the section holds, written in the document's own voice. The heading names it; what is
            under it says the rest. */}

        {/* The ROOT, as a library group like any other. It used to have no heading at all, on the
            reasoning that its name is the document's title two lines up — true, and it still cost the
            reader the alignment the merge is for.

            Its POPULATIONS lead, as `lead`, and are not subject to the density toggle: a population
            criterion is the document's spine and has no compact form. The toggle governs what it
            governs everywhere else — the library's ordinary defines — so the control means one thing
            on every group rather than being absent on one.

            NO library overview, deliberately: `Overview` at the top of the page IS the root's, so
            rendering `libraryOverviews[root]` here would describe the artifact a second time. Opens
            VERBOSE because the measure's own logic is what is under review (SPEC-LIBRARY-DENSITY
            decides density from narration coverage for the INCLUDES, which genuinely need the choice). */}
        {rootLib && (
          <LibraryDefines lib={rootLib} doc={doc} live={live} metrics={metrics} reads={readLens}
            overview={null} defaultVerbose explain={explain}
            count={doc.populations.length + rootLib.entries.length}
            leadHolds={doc.populations.some((p) => isDefineSelected(selected, p.name))}
            lead={doc.populations.map((scope, i) => (
              <ScopeBlock key={scope.name} scope={scope} live={live} metrics={metrics} reads={readLens}
                selected={selected} onSelect={onSelect} onRunCell={onRunCell} showAdd={i > 0}
                results={results}
                link={cqlLink} explain={explain} />
            ))}
            selected={selected} onSelect={onSelect} link={cqlLink} />
        )}

        {includeLibs.map((lib) => (
          <LibraryDefines key={lib.name} lib={lib} doc={doc} live={live} metrics={metrics} reads={readLens}
            overview={overlay?.libraryOverviews?.[lib.name] ?? null}
            defaultVerbose={narratedLibraries?.has(lib.name) ?? false}
            selected={selected} onSelect={onSelect}
            link={cqlLink} explain={explain} />
        ))}

      </SpecSection>

      {/* ── Data requirements — what the logic READS (SPEC-DATA-REQUIREMENTS).
             ONE section, not three. A value set that filters a read is stated INSIDE that read rather
             than in a table of its own, because "what does this measure take from Condition, and what
             counts as a hit" is one question and answering it in two places made the reader join them.
             What is left over — terminology the logic compares against but never reads with — keeps a
             section below, because folding it in here would delete it (see `comparedAgainst`). ── */}
      {(retrieves.length > 0 || doc.appendix.parameters.length > 0) && (
        <SpecSection id="datareq" title="Data requirements" cards collapsed={collapsed.has("datareq")} onToggle={toggle}>
        {/* NO SECTION BLURB (`SPEC-NO-UI-PROSE`, 2026-08-08). A `.mw-desc` paragraph stood here
            explaining what the section holds. It was written in a component, in the reading
            column, in the document's own voice — indistinguishable from the measure's authored
            prose to the reader it was for. The heading names the section; what is under it says
            the rest. */}
          {/* SPEC-DATA-REQUIREMENTS / APP-STYLE-GUIDE — THE READS GET A HEADING OF THEIR OWN.
              The section states its two halves at `h2` — the reads, and the parameters — and a
              resource is an `h3` INSIDE the first. The resource headings KEEP THEIR IDS: the nav points
              at `mw-dr-<type>` (APP-SPEC-MODEL — the nav points INTO the section, not merely at it).

              The value set that filters a read is stated in that read's own row, not in a table of its own.
              The heading names both halves of what a reader will find under it. */}
          {retrieves.length > 0 && (
            <SpecCard title="Retrieves" id="mw-dr-reads">
              {/* APP-STYLE-GUIDE — "Retrieves" is a CARD: one of the two kinds of requirement this section
                  states, alongside Parameters. A value set which filters a read is stated in that read's row,
                  and the rows show it (`code in …`). */}
              {retrievesByType.map(([type, rows]) => (
                <div key={type}>
                  {/* A HEADING, and it names the thing it looks like. It was briefly a mark earlier the
                      same day, on the reasoning that a resource type says where the rows came from — true,
                      and it lost the argument to a simpler one: a reader is scanning for `Condition` the
                      way they scan for a define, so it should read like one. What the mark was fixing (an
                      `h3` identical to the `h2` above it) is fixed instead by the thing that was actually
                      wrong — the `h2` is gone, and the eyebrow above marks the subdivision. */}
                  <h3 id={dataReqTypeId(type)}>{type}<span className="mw-dreq-n">{rows.length}</span></h3>
                  <div className="mw-dreq" role="list">
                    {rows.map((r) => (
                      <RetrieveRow key={r.key} retrieve={r} reads={readLens}
                        selected={isRetrieveSelected(selected, r.key)} onSelect={onSelect} />
                    ))}
                  </div>
                </div>
              ))}
            </SpecCard>
          )}

          {/* SPEC-DATA-REQUIREMENTS — PARAMETERS ARE A DATA REQUIREMENT (moved 2026-08-09; they were a
              bare heading at the foot of Libraries).
              WHY THEY MOVED. A parameter is a value the logic cannot run WITHOUT — a required binding
              supplied at execution, exactly like a read is a required binding supplied by the data. Under
              Libraries they answered a question that section does not ask ("what is this measure made
              of"), and they got there by a weaker argument: that they sit near the definitions they
              parameterise.
              WHAT IT FIXES BEYOND PLACEMENT. The heading had no section, so it was the one heading in the
              document that could not collapse and the one whose indent answered to nothing — a reader
              could fold every part of the specification except this table. A subsection of `datareq` is a
              home: it collapses with its section, it aligns with its siblings, and it is reachable by the
              same route. The table's own `Library` column still carries what the old placement was for —
              that these span more than one library.

              THE EYEBROW, NOT AN `h2`. Parameters is one of the card's two SUBDIVISIONS — requirements
              supplied at execution, against requirements supplied by data. The eyebrow marks that
              distinction. It keeps `mw-app-params`, which is a live nav target (`DefineNav`). */}
          {doc.appendix.parameters.length > 0 && (
            <SpecCard title="Parameters" id="mw-app-params">
              <table className="mw-narr-table">
                <thead><tr><th>Name</th><th>Type</th><th>Library</th></tr></thead>
                <tbody>
                  {doc.appendix.parameters.map((prm) => (
                    <tr key={`${prm.library}::${prm.name}`}>
                      <td>{prm.name}</td><td className="mono">{prm.type}</td><td>{prm.library}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </SpecCard>
          )}
        </SpecSection>
      )}

      {/* ── Terminology — DERIVED from the ELM declarations (SPEC-DERIVED-APPENDIX). Hand-written
             rows were how an OID went stale with nothing to catch it; these are read from the same
             compiled libraries the document above is built from, so they cannot drift from it. The
             Library column is not decoration: two libraries may bind one value-set name to different
             OIDs, and a table that collapsed them would hide the disagreement.

             WHAT IS LEFT AFTER THE MERGE. Terminology that FILTERS a read now lives in that read's own
             row under Data requirements. What remains here is terminology the logic COMPARES AGAINST:
             `"Example Reading A"` decides what a returned component means, and filters nothing. A published
             closure can declare many direct reference codes of which only a few filter a retrieve, so
             this section is where the rest would otherwise have been deleted. ── */}
      {(terminology.valuesets.length > 0 || terminology.codes.length > 0) && (
        <SpecSection id="terminology" title="Terminology" cards collapsed={collapsed.has("terminology")} onToggle={toggle}>
        {/* NO SECTION BLURB (`SPEC-NO-UI-PROSE`). A `.mw-desc` paragraph here would explain what
            the section holds, written in the document's own voice. The heading names it; what is
            under it says the rest. */}
          {terminology.codes.length > 0 && (
            <SpecCard title="Codes and concepts" id="mw-app-codes">
              <table className="mw-narr-table">
                <thead><tr><th>Name</th><th>System</th><th>Code</th><th>Library</th></tr></thead>
                <tbody>
                  {/* SPEC-TERMINOLOGY-REF — every ROW is an anchor, so a phrase bound to a code lands on
                      the code rather than at the top of the table (`SPEC-REF-LENS`). */}
                  {terminology.codes.map((c) => (
                    <tr key={`${c.library}::${c.name}`} id={termAnchor(c.library, c.name)}>
                      <td>{c.display ?? c.name}</td><td>{c.system}</td>
                      <td className="mono">{c.code}</td><td>{c.library}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </SpecCard>
          )}
          {terminology.valuesets.length > 0 && (
            <SpecCard title="Value sets" id="mw-app-vs">
              <table className="mw-narr-table">
                <thead><tr><th>Name</th><th>OID</th><th>Library</th></tr></thead>
                <tbody>
                  {terminology.valuesets.map((vs) => (
                    <tr key={`${vs.library}::${vs.name}`} id={termAnchor(vs.library, vs.name)}>
                      <td>{vs.name}</td><td className="mono">{vs.oid}</td><td>{vs.library}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </SpecCard>
          )}
        </SpecSection>
      )}

      {/* ── References & terms — the steward's citations and licence, verbatim (SPEC-LICENSE).
             Displayed, never summarised: for a steward's content the copyright notice states the terms
             under which the measure may be reproduced at all, so a document that carries the measure
             and drops the notice is not redistributable. ── */}
      {(overlay?.copyright || overlay?.disclaimer || overlay?.citations?.length) && (
        <SpecSection id="terms" title="Notices &amp; references" collapsed={collapsed.has("terms")} onToggle={toggle}>
          {/* The steward's citations sit with the steward's licence, not in a technical appendix:
              `relatedArtifact` is their evidence for the measure, the same kind of thing as their
              copyright notice, and neither is derived from the logic. */}
          {overlay?.citations && overlay.citations.length > 0 && (
            <>
              <h2 id="mw-app-refs">References</h2>
              <ul className="mw-refs">
                {overlay.citations.map((c, i) => (
                  <li key={i}>
                    <MarkdownInline text={c.citation ?? c.label ?? c.url ?? ""} />
                    {c.url && <> <a href={c.url} target="_blank" rel="noreferrer noopener">{c.label ?? "link"}</a></>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {overlay?.copyright && (
            <>
              <h2 id="mw-app-copyright">Copyright</h2>
              <Markdown text={overlay.copyright.text} className="mw-spec-terms" />
            </>
          )}
          {overlay?.disclaimer && (
            <>
              <h2 id="mw-app-disclaimer">Disclaimer</h2>
              <Markdown text={overlay.disclaimer.text} className="mw-spec-terms" />
            </>
          )}
        </SpecSection>
      )}
    </article>
    </ExplainAgainContext.Provider>
  );
}

/** SPEC-LIBRARY-DENSITY — the two-density control, DISABLED. Every library renders Verbose.
 *
 *  Compact hid authored prose: a Compact row shows narration as a one-liner, and the default
 *  measured narration coverage (ours) rather than authored coverage (theirs). Libraries with the
 *  most author prose opened Compact precisely because nobody had narrated them, so readers looking
 *  for authored text could not find it.
 *
 *  Rather than swap one heuristic for another, the control goes — libraries read at one density
 *  and readers collapse what they are not reading (`APP-SPEC-COLLAPSE`, which reaches every group
 *  and section now). Collapsing is the reader's own act on a section they can see; a density
 *  default is a guess made for them about one they cannot.
 *
 *  THE CODE STAYS BEHIND `DENSITY_CONTROL`. It is one flag flip to restore if needed. The compact
 *  row is still the right shape for large declaration groups. Nothing branches at runtime. */
const DENSITY_CONTROL = false;

/** SPEC-DEFINITIONS — one library group: its declarations, each as a full section.
 *
 *  Every define gets prose, its own CQL and its figure. A define is a clinical concept in its own right
 *  (a CDS tool can consume `Qualifying Reading` on its own) and a function carries its
 *  author's `@description` just as often, so neither is ranked as unimportant here.
 *
 *  COLLAPSIBLE, and that is what pays for the length (`APP-SPEC-COLLAPSE`). A group of 251 overloads
 *  of `ToString` is one line when the reader shuts it, and every declaration in full when they open it —
 *  which is the same choice Compact was making, made by the person reading rather than for them. Open by
 *  default, everywhere: a library that opens shut hides its contents exactly as a Compact row did. */
function LibraryDefines({ lib, overview, defaultVerbose, lead, count, leadHolds, selected, onSelect, doc, live, metrics, reads, link, explain }: {
  lib: SpecLibrary;
  overview: NarratedText | null;
  /** SPEC-LIBRARY-DENSITY — the density this library WOULD open at. Inert while `DENSITY_CONTROL` is
   *  off; kept wired so restoring the control is a flag flip rather than re-deriving its default. */
  defaultVerbose: boolean;
  /** Blocks rendered ABOVE this library's own declarations, outside the density control's reach.
   *
   *  The root's POPULATION CRITERIA, and nothing else so far. They belong to this library and lead it —
   *  a measure is read populations-first — but they are not subject to Compact: a population criterion
   *  is the document's spine and has no one-row form. Passing them in rather than special-casing the
   *  root inside this component keeps the control meaning ONE thing on every group.
   *
   *  They ARE inside the group's collapse, deliberately — collapsing a library group means "not reading
   *  this library", and the populations are that library's declarations. */
  lead?: ReactNode;
  /** What the group's heading COUNTS — everything it lists, `lead` included. Defaults to the library's
   *  own declarations, which is the whole list wherever there is no lead. Explicit for the root because
   *  its populations are listed here but held in `doc.populations`, and a heading that counted only
   *  `lib.entries` would say 2 over a group showing 5 — the nav counts them (APP-SPEC-MODEL). */
  count?: number;
  /** APP-MEASURE-FLOW — whether `lead` holds the selection. The reveal below opens a collapsed
   *  group when the selection lands inside it, and this group can only see its own `lib.entries`;
   *  the root's populations arrive rendered, so the caller states whether one of them is selected. */
  leadHolds?: boolean;
  selected: Selection | null; onSelect: (sel: Selection) => void;
  doc: SpecDocument; live: boolean; metrics: MetricSet | null;
  /** SPEC-READ-REF — passed through to each define's prose. */
  reads?: ReadLens;
  link: CqlLinker;
  /** SPEC-EXPLAIN-VIEW — passed through to each section's CQL, which is what a section is when the
   *  interpreted layer is off. */
  explain: boolean;
}) {
  const [verbose, setVerbose] = useState(DENSITY_CONTROL ? defaultVerbose : true);
  const [collapsed, setCollapsed] = useState(false);
  // APP-MEASURE-FLOW — open a collapsed group when a selection lands inside it. Without this a nav
  // click targets a section this collapse has unmounted, and the scroll finds nothing. Keyed on the
  // selection OBJECT and committed as a LAYOUT effect, for the reasons the card-level reveal states:
  // a boolean edge misses the selection moving between two defines of this same group, and a passive
  // commit races the workspace's scroll.
  useLayoutEffect(() => {
    if (leadHolds || lib.entries.some((e) => isDefineSelected(selected, e.name))) setCollapsed(false);
  }, [selected, leadHolds, lib]);
  // The split is by DENSITY, not by kind. Verbose used to keep functions as rows on the reasoning that
  // a function is machinery — which left the control doing nothing at all in FHIRHelpers and the other
  // helper libraries, the ones that are entirely functions and the exact ones a reader reaches for it
  // in. A toggle that silently no-ops on a third of the page is worse than no toggle.
  const sections = verbose ? lib.entries : [];
  const rows = verbose ? [] : lib.entries;
  return (
    <div className={"mw-defs-lib" + (collapsed ? " collapsed" : "")}>
      {/* THE CARD (`APP-STYLE-GUIDE`). A library is a SECTION of the specification — its own name,
          count, collapse, and nav target — rendered as a subdivision it does not belong to. As a card
          it gets the mechanism this design ranks sections by; the boundary becomes a card edge, not
          a label. THE CARD ALWAYS RENDERS; only its body collapses. The header carries NAME and
          anchor, both surviving a collapse. */}
      <div className="mw-defs-lib-card">
        {/* THE CARD HEADER, whose title is the library's name. `mw-defs-lib-title` is NOT A HEADING.
            A card title is not a rung in the document's outline ladder. THE ANCHOR rides the title
            span; the nav scrolls to `lib.domId`, and the header renders open or shut, so a collapsed
            library is still a scroll target. */}
        <div className="mw-defs-lib-head">
          <button className="mw-defs-lib-toggle" aria-expanded={!collapsed}
            onClick={() => setCollapsed((v) => !v)}>
            {/* The SAME caret the section's ground eyebrow draws, hung in the same gutter — the header is
                what knows where the content edge is, so the caret is placed against it (see the
                `position: static` note on `.mw-defs-lib-toggle`). */}
            <ChevronDown size={13} className={"mw-collapse-chevron" + (collapsed ? " collapsed" : "")}
              aria-hidden="true" />
            <span className="mw-defs-lib-title" id={lib.domId}>{lib.name}</span>
          </button>
          <span className="n">{count ?? lib.entries.length}</span>
          {/* SPEC-LIBRARY-DENSITY — disabled (see `DENSITY_CONTROL`). Kept in place, unrendered. */}
          {DENSITY_CONTROL && (
            <div className="mw-lib-actions" role="group" aria-label={`${lib.name} detail level`}>
              <button className="mw-seg-btn" aria-pressed={!verbose} onClick={() => setVerbose(false)}
                title="Compact — one row per declaration, detail on demand">Compact</button>
              <button className="mw-seg-btn" aria-pressed={verbose} onClick={() => setVerbose(true)}
                title="Verbose — a full section per define, with its prose and CQL">Verbose</button>
            </div>
          )}
        </div>
        {!collapsed && (
          <>
          {overview && (
            <NarratedRun run={toProse(overview)}>
              {/* SPEC-PARAMETER-REF — through the segments, for the same reason the Overview's fields are:
                  a library overview that names a read or the period had the reference flattened away. */}
              <p className="mw-desc">
                <Segments segments={overview.segments ?? [{ kind: "text", text: overview.text }]}
                  metrics={metrics} reads={reads} cite={noCite} />
              </p>
            </NarratedRun>
          )}
          {!lib.resolved && <p className="mw-desc">Source for <b>{lib.name}</b> is not in the registry yet.</p>}
          {lead}
          {sections.map((e) => (
            <DefineSection key={e.key} entry={e} view={doc.byDefine[e.key]} live={live} metrics={metrics} reads={reads}
              selected={isDefineSelected(selected, e.name)} onSelect={onSelect}
              link={link} explain={explain} />
          ))}
          {rows.length > 0 && (
            <div className="mw-defs-rows" role="list">
              {rows.map((e) => (
                <DefinitionRow key={e.key} entry={e} link={link}
                  summary={doc.byDefine[e.key]?.narration?.text?.text ?? null}
                  selected={isDefineSelected(selected, e.name)} onSelect={onSelect} />
              ))}
            </div>
          )}
          </>
        )}
      </div>
    </div>
  );
}

/** SPEC-DEFINE-SECTIONS — one define, rendered the way a population criterion is.
 *
 *  Every define is potentially a clinical concept and nothing here decides otherwise: an
 *  "intermediate" calculation is a judgement we are not in a position to make, and the corpus does
 *  not support the guess anyway — an example reading measure can express its entire selection chain as
 *  defines, not functions. So each gets the same treatment: what it is, what it returns, the CQL it
 *  is, and what it depends on. */
function DefineSection({ entry, view, live, metrics, reads, selected, onSelect, link, explain }: {
  entry: SpecEntry; view: SpecDefineView | undefined;
  live: boolean; metrics: MetricSet | null;
  /** SPEC-READ-REF — what a read named in this define's prose resolves against. */
  reads?: ReadLens;
  selected: boolean; onSelect: (sel: Selection) => void;
  link: CqlLinker;
  /** SPEC-EXPLAIN-VIEW — off, this section is its CQL and the block opens, UNLESS the author left prose
   *  of their own (see `CqlPane`). */
  explain: boolean;
}) {
  const cites = useCitations();
  // APP-SPEC-COLLAPSE — every define section is collapsible, and this is what pays for the density
  // control going away: a library group is now full sections all the way down, so the reader needs a
  // per-section shut as well as a per-group one. The HEADING STAYS VISIBLE when collapsed — the name,
  // the kind chip and the figure are the row a Compact reader had, so a shut section degrades to
  // exactly the catalogue line it replaced rather than to nothing.
  const [collapsed, setCollapsed] = useState(false);
  // APP-MEASURE-FLOW — a section shut by the reader opens again when the selection lands on it: a
  // nav click promises the criterion's content, and a heading-only row does not keep it. A boolean
  // edge suffices HERE, unlike at the card and group levels: this section is ONE name, so "is it me"
  // flips false when the selection leaves and true when it returns. Shutting it is untouched until
  // then, because the chevron changes no selection. A LAYOUT effect, so the body is in the page
  // before the workspace's scroll measures it (see the card-level reveal).
  useLayoutEffect(() => {
    if (selected) setCollapsed(false);
  }, [selected]);
  // A FUNCTION IS NOT A DEFINE, and the page said it was. The kind chip was hard-coded `def` while the
  // nav read the model and said `fn` for the same entry — two surfaces disagreeing about what a thing
  // IS, which is precisely what APP-SPEC-MODEL exists to prevent.
  const kind = entry.kind === "function" ? "fn" : "def";
  // SPEC-METRIC — a function has no figure and never will. It is evaluated per call site with
  // arguments, not once per subject, so there is no population it counts; "not computed" implied a
  // number was pending and would arrive after a run, and it never can.
  const countable = entry.kind !== "function";
  // The declared parameter list, stated the way the Compact row states it. `CqlPane` renders the BODY
  // ONLY — `params` is a separate field and `stripHeader` drops the `define …:` line the body was
  // sliced out of — so a function's signature showed at one density and vanished at the other, and
  // `ToString` read in Verbose as if it took nothing. Density decides how much of a declaration shows,
  // never what the declaration IS (SPEC-LIBRARY-DENSITY).
  const signature = entry.variants[0]?.params || null;
  // Copy the WHOLE entry — every declaration of the name, so an overload group copies as the group.
  const copy = () => {
    void navigator.clipboard?.writeText(
      entry.variants.map((v) => `define "${entry.name}"${v.params ? `(${v.params})` : ""}:\n  ${v.body}`.trim()).join("\n\n"),
    );
  };
  // APP-LOGIC-VIEW — a define section is selectable IN PLACE, the same gesture a population cell
  // takes. Selection is the thread that ties the nav, the page and the run figures together, and a
  // section that the nav can select but the page cannot is a one-way binding the reader has no way
  // to see. Inner controls (citations, CQL links, the reference lens) stop propagation, so clicking
  // one follows it rather than selecting the section around it.
  return (
    <section className={"mw-define" + (selected ? " selected" : "") + (entry.eliminated ? " eliminated" : "")
        + (collapsed ? " collapsed" : "")}
      id={entry.domId} data-crit-name={entry.name}
      onClick={() => onSelect({ kind: "define", name: entry.name })} aria-current={selected || undefined}>
      {/* APP-SPEC-CELLS — the same action bar a population carries. A define is a reusable clinical
          asset, so the actions apply: copy its CQL, focus it. Run is absent — a define is computed
          by running the measure. HIDDEN, pending a decision on the control. The bar surfaced on
          hover and stuck open on selection, making selected sections hard to read. Code and CSS stay
          in place; restoring is uncommenting, not rebuilding.
      <div className="mw-cell-actions" role="toolbar" aria-label={`${entry.name} actions`}
        onClick={(e) => e.stopPropagation()}>
        <button title="Copy CQL" onClick={copy}><Copy size={15} /></button>
        <button title="Focus" onClick={() => onSelect({ kind: "define", name: entry.name })}><Focus size={15} /></button>
        <button title="Ask Genie — assistant coming soon" disabled><Sparkle size={15} /></button>
      </div>
      */}
      <div className="mw-define-head">
        {/* APP-SPEC-COLLAPSE — the chevron leads the row, where the Compact catalogue row's does and
            where a top-level section's does, so one gesture means one thing everywhere in the document.
            It STOPS PROPAGATION: the section around it selects on click (`APP-LOGIC-VIEW`), and
            shutting a section is not a statement about which criterion the reader is reviewing. */}
        <button className="mw-define-toggle" aria-expanded={!collapsed}
          aria-label={`${collapsed ? "Expand" : "Collapse"} ${entry.name}`}
          onClick={(e) => { e.stopPropagation(); setCollapsed((v) => !v); }}>
          <ChevronDown size={12} className={"mw-collapse-chevron" + (collapsed ? " collapsed" : "")}
            aria-hidden="true" />
        </button>
        <span className={`mw-spec-kind ${kind}`}>{kind}</span>
        {/* SPEC-ELEMENT-GLOSS — THE HEADING IS THE DEFINE'S NAME, always. It used to be the narrated
            `Title`, which made the document rename the thing it describes: the nav row, the anchor, the
            selection key, the CQL and a reviewer's grep all say `Example Condition Diagnosis` while
            the heading said "identified example condition", so the label changed under a reader who clicked
            from one to the other. Measured across this instance the renames also LOST information — "at
            goal" for `Has Reading A Below Threshold` drops the threshold the criterion is
            about — and one changed the clinical claim, since *primary* condition is initial rather
            than secondary and *identified* means documented-and-ongoing. Serif, not mono: this is a
            heading, and the name is the element's name rather than a quoted code fragment. */}
        <h3 className="mw-define-title">{entry.name}</h3>
        {signature && <span className="sig">({signature})</span>}
        {/* APP-NARRATIVE-PROVENANCE — the provenance badge belongs on EVERY criterion's heading, not
            only a population's. Most of the interpreted prose in the document is in defines, so a badge
            that appeared on populations alone would leave the bulk of it unmarked. */}
        {isNarrated(view?.narration ?? null) && (
          <ExplainBadge method={narrationMethod(view?.narration ?? null)}
            target={{ label: entry.name, define: entry.name }} />
        )}
        {view && countable && <Metric metric={view.metric} metrics={metrics} library={entry.library} />}
        {entry.eliminated && (
          <span className="mw-define-unused" title="Not reachable from any population criterion">
            unused
          </span>
        )}
      </div>
      {/* SPEC-ELEMENT-GLOSS — the ONE LINE a reader needs that the name does not carry. It is where the
          old `Title` went, with a stricter contract: it may not paraphrase the name, so it is ABSENT for
          most elements and that is the correct output rather than a gap to fill. `encounterFor` earns one;
          `Has Reading A Below Threshold` does not. */}
      {!collapsed && view?.narration?.title && <div className="mw-define-gloss">{view.narration.title.text}</div>}
      {!collapsed && (
        <>
          <NarrationBody narration={view?.narration ?? null} lede="el" metrics={metrics} reads={reads} cite={cites.cite} />
          {/* APP-SPEC-MODEL — an overload group's declarations, the same table the Compact row opens.
              `CqlPane` renders `variants[0]` alone (its cited line spans are anchored to that one body), so
              without this the other declarations of the name — and the parameters and return types that are
              the only thing distinguishing them — had no surface at all in Verbose. */}
          {entry.variants.length > 1 && <VariantsTable entry={entry} />}
          <CqlPane entry={entry} link={link} cites={cites} explain={explain}
            authorNote={view?.narration?.comment != null} />
        </>
      )}
    </section>
  );
}

/** One catalogue row: kind chip · name · signature · variant count, expanding in place to its CQL. */
function DefinitionRow({ entry, summary, selected, onSelect, link }: {
  entry: SpecEntry; summary: string | null; selected: boolean; onSelect: (sel: Selection) => void;
  /** SPEC-CQL-LINKS — the SAME linker the full sections use. Compact rows rendered their CQL without
   *  one, so a name was followable in Verbose and dead in Compact — the density control silently
   *  changed what the document could DO, not just how much of it showed at once. */
  link: CqlLinker;
}) {
  const [open, setOpen] = useState(false);
  const kind = entry.kind === "function" ? "fn" : "def";
  const signature = entry.variants[0]?.params || null;
  return (
    <div className={"mw-defrow" + (selected ? " selected" : "") + (entry.eliminated ? " eliminated" : "")}
      id={entry.domId} data-crit-name={entry.name} role="listitem">
      <button className="mw-defrow-head" aria-expanded={open}
        onClick={() => { setOpen((v) => !v); onSelect({ kind: "define", name: entry.name }); }}>
        <ChevronDown size={12} className={"mw-collapse-chevron" + (open ? "" : " collapsed")} aria-hidden="true" />
        <span className={`mw-spec-kind ${kind}`}>{kind}</span>
        <span className="nm">{entry.name}</span>
        {signature && <span className="sig">({signature})</span>}
        {summary && <span className="sum">{summary}</span>}
        {entry.variants.length > 1 && <span className="vars">×{entry.variants.length}</span>}
      </button>
      {open && (
        <div className="mw-defrow-body">
          {entry.variants.length > 1 && <VariantsTable entry={entry} />}
          <CqlCode className="mw-spec-logic" link={link} source={entry.variants
            .map((v) => `define "${entry.name}"${v.params ? `(${v.params})` : ""}:\n  ${v.body}`.trim())
            .join("\n\n")} />
        </div>
      )}
    </div>
  );
}

/** SPEC-DOCUMENT — one population criterion (or COMMON) with its Concepts and Rules.
 *
 *  The population keeps the notebook-cell chrome (APP-SPEC-CELLS) because it is a criterion you can
 *  run; Concepts and Rules below it are reading surfaces, so they stay light. A population that merely
 *  re-states another renders its own CQL and nothing more — the decomposition lives in the section of
 *  the population it reaches, and is never repeated here (SPEC-PLACEMENT). */
function ScopeBlock({ scope, live, metrics, reads, selected, onSelect, onRunCell, showAdd, results, link, explain }: {
  scope: SpecScope;
  live: boolean; metrics: MetricSet | null;
  /** SPEC-READ-REF — what a read named in this population's prose resolves against. */
  reads?: ReadLens;
  selected: Selection | null; onSelect: (sel: Selection) => void; onRunCell: (name: string) => void;
  showAdd: boolean; results?: Record<string, Record<string, unknown>> | null;
  link: CqlLinker;
  /** SPEC-EXPLAIN-VIEW — off, this criterion IS its CQL: the block opens and the narration goes. */
  explain: boolean;
}) {
  const cites = useCitations();
  // SPEC-DEFINE-SECTIONS — a population states ITSELF: its prose, its own CQL, and the dependency
  // index that reaches everything below it. The Concept and Rule cards that used to nest here are
  // gone. Every define now has one full section of its own under its library, and the index is the
  // way in — which is why placement and COMMON no longer have anything to arbitrate: nothing is
  // duplicated across sections because nothing is rendered twice anywhere.
  //
  // NOTHING IS APPENDED UNDER THE CQL. A "Builds on" row is not here. It was an artifact the rest
  // of the document had no other instance of, met exactly once, in one shape nothing else used. The
  // `reaches` edge is still the ELM's and PLACEMENT still turns on it (`specDocument.ts`); it is not
  // restated as a row (`SPEC-NO-UI-PROSE`).
  if (!scope.population) return null;

  const e = scope.population;
  return (
    <SpecCell entry={e} kind="pop" selected={isDefineSelected(selected, e.name)} onSelect={onSelect}
      onRunCell={onRunCell} showAdd={showAdd}
      defineResults={live ? extractDefineResults(results, e.name) : null}>
      <h2>
        <span className="mw-spec-kind pop">pop</span>
        <span>{e.name}</span>
        {/* APP-NARRATIVE-PROVENANCE — on the heading row, right after the name: the chip on the left is
            a classification ("this is a population"), the mark on the right is state ("we wrote this"). */}
        {isNarrated(scope.narration) && (
          <span className="mw-el-badges">
            <ExplainBadge method={narrationMethod(scope.narration)} target={{ label: e.name, define: e.name }} />
          </span>
        )}
        {/* THE SAME `Metric` a define renders (`SPEC-ATTRITION`). A population had its own inline
            figure — a bar, a count and a percent of a base passed down as a bare number — which is how
            a population came to state its share against the initial population while a define below it
            stated one against its enclosing scope, in a different shape, from a different denominator.
            One component, one resolver, one base rule. */}
        {scope.metric && <Metric metric={scope.metric} metrics={metrics} library={e.library} />}
      </h2>
      <NarrationBody narration={scope.narration} lede="pop" metrics={metrics} reads={reads} cite={cites.cite} />
      {scope.population && <CqlPane entry={scope.population} link={link} cites={cites} explain={explain}
        authorNote={scope.narration?.comment != null} />}
    </SpecCell>
  );
}

/** SPEC-NARRATIVE-TAGS — the narration triple, rendered the same way for a population, a Concept and
 *  a Rule because a reader asks the same three questions of each.
 *
 *  The two labelled lanes are deliberately unmissable. "Why this matters" and "How to read it" are
 *  what a clinician cannot recover from the define name, and burying them in an even paragraph run
 *  is how the previous document managed to be long and still unreviewable.
 *
 *  APP-NARRATIVE-PROVENANCE — the fields are grouped into RUNS of one provenance and the run is
 *  what carries the mark (`NarratedRun`). Provenance is still read per field; only the disclosure is
 *  shared, and only across fields that genuinely share it. An `authored` field breaks the run and
 *  renders unmarked with its byline, so one EXPLAIN chip covers exactly the prose it is true of
 *  rather than being repeated three times per define. */
function NarrationBody({ narration, lede, metrics, reads, cite }: {
  narration: SpecNarration | null;
  /** `pop` gets the larger reading size; Concepts and Rules sit a step down. */
  lede: "pop" | "el";
  /** SPEC-METRIC-REF — the set a figure cited inside the prose resolves against. */
  metrics: MetricSet | null;
  /** SPEC-READ-REF — what a read named in the prose resolves against. */
  reads?: ReadLens;
  /** SPEC-NARRATIVE-DEVICES — renders a claim's citation into its CQL lines. */
  cite: (span?: string) => ReactNode;
}) {
  // Nothing narrated renders NOTHING. A sentence saying so is a line of chrome telling the reader
  // about an absence they can already see, on every un-narrated element in the document — and the
  // useful response to it is an action (tag it, Explain it), not prose.
  if (!isNarrated(narration)) return null;
  // Reporting order, which is also the order the tags are read in: WHAT, then WHY, then HOW.
  const present = NARRATION_FIELDS.filter((f) => narration[f] != null);
  return (
    <>
      {proseRuns(present, (f) => narration[f]!).map((run) => (
        <NarratedRun key={run.items[0]} run={run}>
          {run.items.map((field) => {
            const prose = narration[field]!;
            // WHAT it is: the lede, plus whatever devices the field carries. A field with devices but
            // no lead sentence is legitimate (a criterion whose answer IS the table), so the
            // paragraph is guarded and the devices are not.
            if (field === "text") {
              return (
                <Fragment key={field}>
                  {prose.text && (
                    <p className={lede === "pop" ? "mw-narr-lede" : "mw-concept-lede"}>
                      <ProseText prose={prose} metrics={metrics} reads={reads} cite={cite} />{cite(prose.sourceSpan)}
                    </p>
                  )}
                  <Devices blocks={prose.blocks} cite={cite} metrics={metrics} reads={reads} />
                </Fragment>
              );
            }
            const lane = NARRATION_LANES[field];
            return (
              <div key={field} className={`mw-narr-block ${lane.cls}`}>
                <span className="mw-narr-label">{lane.label}</span>
                <p><ProseText prose={prose} metrics={metrics} reads={reads} cite={cite} />{cite(prose.sourceSpan)}</p>
                <Devices blocks={prose.blocks} cite={cite} metrics={metrics} reads={reads} />
              </div>
            );
          })}
        </NarratedRun>
      ))}
    </>
  );
}

/** SPEC-SCORING-METHODOLOGY — how the measure is scored, derived and stated once.
 *
 *  NOT NARRATION, and that is the whole point. The formula is published methodology, identical across
 *  every measure of this shape, so a per-measure paragraph about it spends a clinical document's space on
 *  arithmetic and invites getting the exception rule wrong (`clinical-explain/SKILL.md` forbids it).
 *
 *  THE SCHEMATIC IS A DIAGRAM, NOT A CHART. Its proportions are fixed and it carries no figures at all —
 *  it explains how the populations nest and which of them the rate divides by, which is true before any
 *  run and unchanged by every run after. Putting this measure's own counts in it would make a methodology
 *  explainer look like a result, and `SPEC-ATTRITION` already reports the counts where they belong.
 *
 *  Renders nothing for a library, a cohort measure, or any shape `specScoring.ts` cannot state without
 *  `Measure.scoring` to confirm it — a wrong formula reads authoritative. */
function ScoringBlock({ scoring, populations }: { scoring: string | null; populations: string[] }) {
  const method = useMemo(() => scoringMethodology(scoring, populations), [scoring, populations]);
  if (!method) return null;
  const has = (name: string) => method.roles.some((r) => r.name === name);
  return (
    <section className="mw-scoring" aria-label="How this measure is scored">
      <span className="mw-scoring-eyebrow">How this measure is scored</span>
      <p className="mw-scoring-formula"><code>{method.formula}</code></p>

      {/* The nesting, as a schematic: each population sits inside the one it narrows. */}
      <div className="mw-scoring-fig" aria-hidden="true">
        {has("Initial Population") && (
          <div className="lvl outer"><span className="tag">Initial Population</span></div>
        )}
        <div className="lvl den">
          <span className="tag">Denominator</span>
          <div className="segs">
            <i className="met" style={{ width: "52%" }} />
            <i className="unmet" style={{ width: "33%" }} />
            {(has("Denominator Exclusions") || has("Denominator Exceptions")) && (
              <i className="out" style={{ width: "15%" }} />
            )}
          </div>
        </div>
        <div className="lvl legend">
          <span className="k met">Numerator — met</span>
          <span className="k unmet">in the base, not met</span>
          {(has("Denominator Exclusions") || has("Denominator Exceptions")) && (
            <span className="k out">removed from the base</span>
          )}
        </div>
      </div>

      <dl className="mw-scoring-roles">
        {method.roles.map((r) => (
          <Fragment key={r.name}>
            <dt className={"t-" + r.tone}>{r.name}</dt>
            <dd><b>{r.meaning}</b> — {r.effect}</dd>
          </Fragment>
        ))}
      </dl>
      {method.caveat && <p className="mw-scoring-caveat">{method.caveat}</p>}
    </section>
  );
}

/** SPEC-REF-LENS — the resolution bundle and the Appendix anchor now live in `specLens.ts`, because a
 *  reference is no longer only the document's (`APP-ASSISTANT-REFS`). Re-exported here so the call
 *  sites that have always imported them from the document keep working. */
export { terminologyAnchor } from "./specLens.ts";
export type { ReadLens, TermLens } from "./specLens.ts";


/** SPEC-METRIC-REF / SPEC-READ-REF — prose with its references resolved at render.
 *
 *  ONE PATH. Prose that references nothing arrives with no `segments`, and it is normalised to a single
 *  text run here rather than rendered by a second branch — so every sentence in the document goes
 *  through the same renderer whether or not it happens to carry a reference.
 *
 *  AN UNRESOLVED FIGURE RENDERS AN ABSENCE MARK, never a zero and never the define's name. With no
 *  data source the sentence still reads — "of the — encounters in the period" is visibly a figure that
 *  has not been computed, which is what a reader needs to know. A zero would be a claim.
 *
 *  AN UNRESOLVED READ RENDERS AS PLAIN PROSE. The two absences are unlike and are treated unlike: a
 *  `MetricRef` has no words of its own, so it must leave a mark or the sentence loses a noun; a `Read`
 *  IS the words, so a binding that resolves to nothing degrades to exactly the sentence a narrator
 *  wrote. Never wrong, only less. */
function ProseText({ prose, metrics, reads, cite }: {
  prose: NarratedText; metrics: MetricSet | null; reads?: ReadLens;
  /** SPEC-INLINE-ANCHOR — the section's citation binding, so an inline reference anchors ITSELF rather
   *  than leaning on the field's widened span. Same control the field's own superscript uses. */
  cite?: (span?: string) => ReactNode;
}) {
  const segments: SpecSegment[] = prose.segments ?? [{ kind: "text", text: prose.text }];
  return <Segments segments={segments} metrics={metrics} reads={reads} cite={cite} />;
}

/** Inline content grouped into PARAGRAPHS, for a field that has both breaks and references.
 *
 *  `paragraphs()` does this for plain text and cannot be used on a field carrying references, because
 *  flattening to `prose.text` is what drops them — which is exactly how the Overview lost the cited
 *  measurement year (`SPEC-PARAMETER-REF`): a `ParameterRef` projects to nothing in the plain text
 *  (`loader.py::_plain`), so *the 2026 measurement period* rendered as *the measurement period* and the
 *  reference was invisible rather than absent.
 *
 *  A BLANK LINE CAN ONLY FALL INSIDE A TEXT RUN — a reference is a token in a sentence, never a break
 *  between two — so the split is found there and only there. Runs are NOT trimmed: a run adjacent to a
 *  reference owns the space that separates them ("the " + `2026`), and trimming it would join the two
 *  words. Whitespace at a paragraph's own edges is dropped by CSS text processing, which is where the
 *  rest of this document already leaves it. */
function segmentParagraphs(segments: SpecSegment[]): SpecSegment[][] {
  const out: SpecSegment[][] = [];
  let para: SpecSegment[] = [];
  // An empty paragraph is dropped rather than pushed: leading and trailing blank lines in a quoted
  // markdown field are formatting, and `paragraphs()` drops them for the same reason.
  const close = () => { if (para.length > 0) out.push(para); para = []; };
  for (const seg of segments) {
    if (seg.kind !== "text") { para.push(seg); continue; }
    seg.text.split(/\n\s*\n/).forEach((part, i) => {
      if (i > 0) close();
      if (part.trim()) para.push({ kind: "text", text: part });
    });
  }
  close();
  return out;
}

/** The one renderer for inline content, wherever it appears — a lede paragraph, a bullet, or a
 *  sentence in an answer.
 *
 *  Extracted from `ProseText` when `SPEC-READ-REF` gave list ITEMS segments of their own: a read is
 *  most often named inside a bullet ("recorded on the problem list or as an encounter diagnosis"), and
 *  a bullet that rendered its segments by a second code path is a bullet whose chips could look and
 *  behave differently from the same chip one line above it.
 *
 *  EXPORTED for the assistant (`APP-ASSISTANT-REFS`), which is the same argument one scope wider: an
 *  answer's references are the document's references, so they render through this and not through a
 *  second implementation that would drift in exactly the way this extraction already prevented once. */
export function Segments({ segments, metrics, reads, cite }: {
  segments: SpecSegment[]; metrics: MetricSet | null; reads?: ReadLens;
  cite?: (span?: string) => ReactNode;
}) {
  return (
    <>
      {segments.map((seg, i) => (
        seg.kind === "text" ? <MarkdownInline key={i} text={seg.text} />
          : seg.kind === "read" ? <ReadPhrase key={i} ref_={seg} reads={reads} cite={cite} />
          : seg.kind === "define" ? <DefinePhrase key={i} ref_={seg} reads={reads} cite={cite} />
          : seg.kind === "parameter" ? <CitedParameter key={i} ref_={seg} reads={reads} />
          : seg.kind === "terminology" ? <TerminologyPhrase key={i} ref_={seg} reads={reads} cite={cite} />
          : <CitedFigure key={i} ref_={seg} metrics={metrics} reads={reads} />
      ))}
    </>
  );
}

/** SPEC-DEFINE-REF — a phrase in a clinical sentence that names another element.
 *
 *  IT IS THE SAME CONTROL THE CQL TOKEN USES — literally `DefineLink`, not a parallel implementation of
 *  it. A define referenced from prose and the same define referenced from code are one object, so they
 *  get one lens, one gesture and one set of verbs; building a second thing here is how the two come to
 *  disagree about what a reference does.
 *
 *  THE PHRASE STAYS PROSE. `DefineLink` renders whatever children it is given, so the sentence keeps its
 *  own words and its reading face — "primary example condition diagnosis", never the title-cased declared
 *  name. What marks it is the link ink the CQL token already uses.
 *
 *  UNRESOLVED, IT IS JUST WORDS — the degradation every binding in this document follows. A reference to
 *  a define this document does not render (an unreachable one, or a name that has moved) renders as
 *  exactly the sentence the narrator wrote. */
function DefinePhrase({ ref_, reads, cite }: {
  ref_: SpecDefineRef; reads?: ReadLens; cite?: (span?: string) => ReactNode;
}) {
  // The bundle carries the define resolver too (see `ReadLens`), so an unresolvable reference and an absent
  // bundle degrade the same way: to the words the narrator wrote.
  const entry = reads?.defineEntry(ref_.define);
  if (!reads || !entry) return <>{ref_.text}{cite?.(ref_.sourceSpan)}</>;
  return (
    <>
      <DefineLink entry={entry} ctx={reads.inspector} selected={reads.defineSelected(ref_.define)}
        onSelect={reads.onSelect} className="mw-defref">
        {ref_.text}
      </DefineLink>
      {/* ABSENT for a transitive mention — a phrase naming something two steps down the chain has no line
          in THIS body, and the reference itself is the anchor. */}
      {cite?.(ref_.sourceSpan)}
    </>
  );
}

/** SPEC-DEFINE-REF — a define named by its DECLARED name (a scope note's reach list, a data requirement's
 *  call site, a metric's subject), linked when this document renders it and plain words when it does not.
 *
 *  Four call sites had written this resolve-or-degrade dance out longhand, which is three chances for one
 *  of them to drift — and the shape of the drift would be a reference that renders as a link to a lens with
 *  nothing in it. `DefinePhrase` is the other half of the same idea and stays separate: it carries a
 *  narrator's OWN words plus a citation, where this carries the declared name. */
function DefineName({ name, reads, className = "mw-defref" }: {
  name: string; reads?: ReadLens; className?: string;
}) {
  const entry = reads?.defineEntry(name);
  if (!reads || !entry) return <>{name}</>;
  return (
    <DefineLink entry={entry} ctx={reads.inspector} selected={reads.defineSelected(name)}
      onSelect={reads.onSelect} className={className}>
      {name}
    </DefineLink>
  );
}

/** SPEC-READ-REF — a phrase in the prose that names a read: the words, and the data behind them.
 *
 *  THE PHRASE STAYS PROSE. It is set in the reading face at reading size and keeps its place in the
 *  sentence; what marks it is a dotted underline, the typographic convention for "there is more here"
 *  that costs a sentence nothing. The COUNT is a chip beside it, offset in tabular mono, because it is
 *  data and the sentence is not (`design-system.md`: mono is for code and figures).
 *
 *  THE GESTURE IS THE CQL TOKEN'S, and deliberately so. `RetrieveLink` previews on hover and pins on a
 *  press; a phrase naming the same read and answering the same press differently would be a second
 *  vocabulary for one object. So this pins too, and its tray carries the same two verbs — one of which
 *  scopes the inspector, because that pane is scoped by selection rather than by scroll position
 *  (`APP-WORKBENCH`).
 *
 *  THE PHRASE STILL SHOWS THE SCOPE IT SET. `.on` stays: it is how a reader sees which read the pane is
 *  describing without looking away from the sentence. What leaves is `aria-pressed` — the press now opens
 *  the lens rather than toggling the scope, and the toggle it used to announce is the tray's button.
 *
 *  UNBOUND, IT IS JUST WORDS. A binding naming a profile this measure does not retrieve resolves to
 *  nothing and the sentence renders as written — the degradation `SPEC-METRIC` asks for everywhere: no
 *  invented figure, no error in a clinical document, and no chip promising something to click. */
function ReadPhrase({ ref_, reads, cite }: {
  ref_: SpecReadRef; reads?: ReadLens; cite?: (span?: string) => ReactNode;
}) {
  const pop = useRefLens();
  const retrieve = reads?.resolve(ref_) ?? null;
  // Unbound, the words survive — and so does the anchor, because a phrase whose binding did not resolve
  // is still a claim about a line and a reader can still check it.
  if (!reads || !retrieve) return <>{ref_.text}{cite?.(ref_.sourceSpan)}</>;
  const count = reads.count(retrieve.key);
  const selected = reads.selectedKey === retrieve.key;
  const filter = retrieve.valueSet ?? retrieve.code;
  return (
    <span className="mw-reflens-wrap" ref={pop.wrap}>
      {/* NO `title` — the lens is the tooltip (see `DefineLink`). */}
      <button className={"mw-readref" + (selected ? " on" : "")} {...pop.trigger}>
        <span className="ph">{ref_.text}</span>
        {/* SPEC-METRIC — no figure, no chip. A "not computed" chip on every bound phrase would be four
            words of chrome inside a clinical sentence, repeated down the page; the compute pill states
            the document's data state once, in the toolbar. */}
        {count != null && <span className="ct tnum">{count.toLocaleString()}</span>}
      </button>
      {/* SPEC-INLINE-ANCHOR — the phrase's OWN line, outside the button so pressing the anchor opens the
          CQL rather than the lens. A bullet naming two reads used to carry one span over both, which
          cannot say which phrase is which line. */}
      {cite?.(ref_.sourceSpan)}
      {/* The SAME box the CQL token's lens opens (`RefLensBox`) — one read, one answer, whichever surface
          the reader pressed it from. These two had already drifted once, the prose lens stating a count
          while the code lens stated `mustSupport`; sharing the inspector's own read scope is the strongest
          form of the fix, because there is now no read-lens body for a second copy of to exist in. */}
      {pop.open && (
        <RefLensBox pop={pop} label={retrieve.type} ctx={reads.inspector} entry={null} read={retrieve}
          sel={{ kind: "retrieve", key: retrieve.key }} selected={selected} onSelect={reads.onSelect} />
      )}
    </span>
  );
}

/* `ReadLensBody` IS GONE. Both the prose phrase's lens and the CQL token's lens now render the
   inspector's `ReadScope` through `RefLensBox`, the same component the pane uses (`SPEC-REF-LENS`).
   The drift is impossible rather than fixed: there is no read-lens body left for a second copy.
   `ReadScope` says more than it did and one thing more — why the count is absent — which the lens
   could not tell (`SPEC-METRIC`) because it did not know which absence it was looking at. */

/** SPEC-TERMINOLOGY-REF — a phrase in a clinical sentence that names a value set or a code.
 *
 *  THE LAST OF THE INLINE REFERENCES, and the one closest to the reader's actual question. A clinician
 *  checking *example condition* wants to know which codes count, and until now that was the one
 *  linkable thing the narrative could only mention: the OID was in the Appendix and the sentence had no
 *  way to reach it, while the identical name inside the CQL block was followable. Same asymmetry
 *  `SPEC-DEFINE-REF` closed for defines (`SPEC-EXPLAIN-ACCOUNTING`).
 *
 *  THE PHRASE STAYS PROSE, with the one underline every lens-able phrase in this document shares
 *  (`SPEC-INLINE-ANCHOR`) — it is `.mw-defref`, literally the same mark a bound define carries, because
 *  *prose you can press* is one affordance and drawing it two ways for two reference kinds is the drift
 *  that rule exists to prevent.
 *
 *  ITS LENS HAS ONE VERB, and that is a real difference from the others rather than an omission. Open
 *  goes to the Appendix ROW that declares it — the row, never the table's heading, which is
 *  `SPEC-REF-LENS`'s rule that a reference's verb lands on the thing and not on the section listing it.
 *  There is no Pin, because Pin scopes the inspector and `Selection` is `define | retrieve`: a value set
 *  is not a thing the pane can be scoped to, and a disabled verb would promise one that is coming.
 *
 *  EVERYTHING IT STATES IS DERIVED, including WHICH KIND it is, so it reads identically in either Explain
 *  state and a narrator cannot mislabel a code as a value set. Unresolved — a name this closure does not
 *  declare, or an unqualified reference the loader dropped — it renders as the words that were written. */
function TerminologyPhrase({ ref_, reads, cite }: {
  ref_: SpecTerminologyRef; reads?: ReadLens; cite?: (span?: string) => ReactNode;
}) {
  const pop = useRefLens();
  const term = reads?.terminology(ref_.library, ref_.name) ?? null;
  if (!term) return <>{ref_.text}{cite?.(ref_.sourceSpan)}</>;
  const isVs = term.kind === "valueset";
  return (
    <span className="mw-reflens-wrap" ref={pop.wrap}>
      {/* NO `title` — the lens is the tooltip (see `DefineLink`). */}
      <button className="mw-defref" {...pop.trigger}>{ref_.text}</button>
      {cite?.(ref_.sourceSpan)}
      {/* PORTALED like every other lens, though its BODY is its own. There is one popover placement in this
          document (`useRefLens`), and a terminology lens left behind as an absolutely-positioned box would
          be the only reference in the page still clipped by the reading column. What it does NOT get is the
          inspector: a `Selection` is a define or a read, so a value set is not something the pane can be
          scoped to — which is also why this lens has one verb rather than two. */}
      {pop.open && pop.portal(
        <div className="mw-reflens" {...pop.lens} role="dialog" aria-label={term.name}>
          <span className="mw-reflens-head">
            <span className={"mw-spec-kind " + (isVs ? "vs" : "cd")}>{isVs ? "vs" : "cd"}</span>
            <b>{term.name}</b>
            <span className="mw-reflens-acts">
              <button aria-label="Go to the Appendix entry" title="Go to the Appendix entry"
                onClick={(e) => {
                  e.stopPropagation();
                  pop.close();
                  document.getElementById(term.anchor)?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}>
                <OpenRef size={14} />
              </button>
            </span>
          </span>
          <span className="mw-reflens-body">
            <span className="mw-reflens-lib">{term.library}</span>
            {/* The identifier, in mono because it is machine data (`design-system.md` section 1). A value
                set states its OID; a code states its code and the system that gives it meaning, which a
                code without one does not have. */}
            {term.id
              ? <span className="mw-reflens-type">{isVs ? "OID " : ""}{term.id}</span>
              : <span className="mw-reflens-note">
                  The published library declared no {isVs ? "OID" : "code"} for this.
                </span>}
            {term.system && <span className="mw-reflens-note">System {term.system}</span>}
            {term.display && term.display !== term.name && (
              <span className="mw-reflens-note">{term.display}</span>
            )}
            <span className="mw-reflens-note">
              {isVs
                ? "A value set is an EXTERNAL list, expanded from the terminology it is bound to — this "
                  + "document states which list, never its members."
                : "A direct reference code names one concept, stated inline in the logic rather than "
                  + "drawn from a list."}
            </span>
            {/* NO `LensPending` FOOTER. It read "pin it to see them here" under a lens with nothing to pin
                — this reference has one verb, and the pane it pointed at cannot be scoped to a value set.
                It was a notice about a different lens's limitation, printed here by inheritance. */}
          </span>
        </div>,
      )}
    </span>
  );
}

/** SPEC-PARAMETER-REF — an execution parameter's value, substituted into the sentence.
 *
 *  IT NEEDS NO RUN, and it is the only citation here that does not. A parameter is an execution INPUT
 *  (`APP-EXEC-PARAMS`) selected in the compute pill, so *the 2026 measurement period* reads the same
 *  with no data source attached as it does after a run — which is why it is safe in a document a reader
 *  may open cold, where a figure would be an absence mark.
 *
 *  MONO, because it is data in a sentence that is not (`design-system.md`: mono is code and figures) —
 *  the same treatment a cited figure gets, for the same reason. NOT followable: a figure names a define
 *  and has that define's lens behind it, while a parameter's whole content is the value already printed,
 *  so a press would open a panel restating the word next to it.
 *
 *  UNRESOLVED, IT RENDERS NOTHING and the sentence closes over the gap — write around the citation, the
 *  rule `SPEC-METRIC-REF` sets for every reference whose value may be absent. In practice the period
 *  always resolves (it is a selection with a default), so this is the degradation rather than the case. */
function CitedParameter({ ref_, reads }: { ref_: SpecParameterRef; reads?: ReadLens }) {
  const value = reads?.params[ref_.name];
  if (!value) return null;
  return <span className="mw-pref tnum" title={ref_.name}>{value}</span>;
}

/** SPEC-METRIC-REF — one figure cited in a sentence, and followable to the element it belongs to.
 *
 *  IT WAS THE ONE INLINE REFERENCE A READER COULD NOT FOLLOW. Reads and defines named in prose open the
 *  reference lens; a cited figure rendered as an inert span, so the most load-bearing token in a clinical
 *  sentence — the number — was the only one that answered nothing when pressed. A figure NAMES a define,
 *  so it gets that define's lens: what it returns, which library owns it, what it counted, and the same
 *  two verbs.
 *
 *  ITS ANCHOR IS THE ELEMENT, NOT A LINE. A `MetricRef` cites a define's OUTPUT, which is the whole
 *  define rather than any part of it, so there is no `sourceSpan` here and adding one would be false
 *  precision. Following the reference IS the anchor — the same argument that leaves a transitive
 *  `ConceptRef` unanchored.
 *
 *  Tabular mono, so a number inside prose still reads as data (`design-system.md`: mono is code and
 *  figures). An unresolved figure keeps the absence mark and stays INERT: there is nothing to look at,
 *  and a pressable em-dash promises otherwise. */
function CitedFigure({ ref_, metrics, reads }: {
  ref_: SpecMetricRef; metrics: MetricSet | null; reads?: ReadLens;
}) {
  const figure = metrics?.byKey.get(metricKey(ref_.library, ref_.define)) ?? null;
  const value = !figure ? null
    : ref_.grain === "element" ? figure.elements
    : figure.subjects;
  // `share` needs the define's base, and the base rule lives with placement — which this reference does
  // not carry. Resolved through the same counts the rest of the document reads, against the population
  // chain: a cited share whose base is unknown resolves to nothing rather than to a percentage of a
  // denominator nobody named (`SPEC-METRIC`).
  const share = ref_.as === "share" ? citedShare(metrics, ref_) : null;
  const text = ref_.as === "share"
    ? (share != null ? formatShare(share) : null)
    : (value != null ? value.toLocaleString() : null);
  if (text == null) {
    return <span className="mw-mref absent" title={`${ref_.define} — not computed`}>—</span>;
  }
  const entry = reads?.defineEntry(ref_.define);
  if (!reads || !entry) {
    return <span className="mw-mref tnum" title={`${ref_.define} · ${ref_.grain} grain`}>{text}</span>;
  }
  return (
    <DefineLink entry={entry} ctx={reads.inspector} selected={reads.defineSelected(ref_.define)}
      onSelect={reads.onSelect} className="mw-mref tnum">
      {text}
    </DefineLink>
  );
}

/** The base for a CITED share. The population chain, or the evaluated total. */
function citedShare(metrics: MetricSet | null, ref_: SpecMetricRef): number | null {
  if (!metrics) return null;
  const own = metrics.byKey.get(metricKey(ref_.library, ref_.define));
  if (!own) return null;
  const resolved = resolveMetric(metrics, {
    // A reference names a define; the base rule needs the define's placement, and the only part of a
    // `SpecMetric` this needs is the name to resolve against. Everything else is the renderer's.
    form: "count", subjectUnit: "patients", elementGrain: null, resultType: null, valueShape: "row",
    countOf: ref_.define, base: null,
  }, ref_.library);
  return resolved?.share ?? null;
}

/** SPEC-REPORTED-RESULT — the measure's own reported figure, at the head of the document.
 *
 *  WHY IT IS HERE AND NOT ONLY IN ANALYZE. The performance rate with its four legs is the figure every
 *  reader of an eCQM specification arrives looking for; a document that states forty criterion counts
 *  and not the one number the measure exists to report has buried its own conclusion.
 *
 *  THE BARS ARE THE POINT, and they are the SAME `FunnelView` the Analyze lens draws — numbers as text
 *  do not carry magnitude, and a reader cannot see from "10,318 / 1,204 / 6,187" that the exclusions
 *  are a sliver and the numerator is two thirds. One component, so the two surfaces cannot draw the
 *  same funnel differently (`APP-SPEC-MODEL`); the compact treatment is CSS in this context.
 *
 *  With no data source this renders NOTHING — not a placeholder. The compute pill states the document's
 *  data state once, in the toolbar, and a second "not computed" panel at the top of the page is chrome
 *  telling the reader about an absence the pill already reports. */
function ReportedResult({ funnel, metrics }: { funnel: Funnel | null; metrics: MetricSet | null }) {
  if (!funnel || !metrics || funnel.stages.length === 0) return null;
  const leg = (name: string) => metrics.counts[name];
  const legs = [
    { key: "initial", label: "initial pop", value: leg("Initial Population"), cls: "" },
    { key: "den", label: "denominator", value: leg("Denominator"), cls: "den" },
    { key: "exc", label: "exclusions", value: leg("Denominator Exclusions"), cls: "exc" },
    { key: "num", label: "numerator", value: leg("Numerator"), cls: "num" },
  ].filter((l) => l.value != null);
  const readout = readoutText(funnel.readout);

  return (
    <section className="mw-spec-result" aria-label="Reported result">
      <div className="mw-spec-result-head">
        {/* SPEC-SCORING-METHODOLOGY — the reported figure is whatever this measure's DECLARED scoring
            reports, and its caption names its own base. `SPEC-METRIC` requires the base everywhere and
            here most of all: a proportion's denominator is the denominator LESS the removals, which is
            not the figure printed two inches to the left under "denominator". The caption used to say
            so in fixed words, which was true for a proportion and printed over a ratio unchanged. */}
        {readout && (
          <div className="rate">
            <span className="big tnum">{readout.figure}</span>
            <span className="cap">{readout.caption}</span>
          </div>
        )}
        <div className="legs">
          {legs.map((l) => (
            <div key={l.key} className={"leg " + l.cls}>
              <span className="n tnum">{l.value!.toLocaleString()}</span>
              <span className="l">{l.label}</span>
            </div>
          ))}
        </div>
      </div>
      <FunnelView funnel={funnel} />
      <SampleFigures metrics={metrics} />
    </section>
  );
}

/** SPEC-SAMPLE-METRIC — the mark, wherever sample figures are. One per surface that shows them.
 *
 *  "The renderer MARKS it wherever it appears, as plainly as the EXPLAIN chip marks interpreted prose"
 *  is an obligation, not a nicety: a figure in a clinical specification is indistinguishable from a
 *  real one once drawn, and this is what keeps `APP-NO-UI-MOCK` enforceable while the run-backed
 *  derivation is built. It disappears on its own when the provenance on the payload becomes `run`. */
function SampleFigures({ metrics }: { metrics: MetricSet | null }) {
  if (!metrics || metrics.provenance !== "sample") return null;
  return (
    <p className="mw-sample-mark" role="note">
      <span className="tag">sample figures</span>
      {metrics.caveat ?? "No run has produced these."}
    </p>
  );
}

/** SPEC-NARRATIVE-TAGS — the body fields, in reporting order. `title` is absent deliberately: it
 *  renders as the element's heading, not as a paragraph, so it is not part of any run. */
const NARRATION_FIELDS = ["text", "comment", "rationale", "guidance"] as const;

/** SPEC-NARRATIVE-TAGS — the labelled lanes under the lede, and the word each one is called.
 *
 *  `@comment` SITS DIRECTLY UNDER THE DESCRIPTION IT CONTINUES, before the why and the how. It is the
 *  same author still answering "what is this" — a helper's interval function gives one line of
 *  `@description` and then a paragraph of `@comment` on which timing choice types it covers — so
 *  putting it after the two clinical lanes would break the answer in half around them.
 *
 *  ITS LABEL NAMES ITS SOURCE, not a question. "Why this matters" and "How to read it" are the two
 *  questions of the triple and their labels say so; `@comment` answers no fixed question, so a label
 *  inventing one for it would describe prose we did not read. "Author's note" is what it is, and the
 *  darker ink of `.mw-narr-authored` around it is already saying whose. */
const NARRATION_LANES = {
  comment: { cls: "note", label: "Author's note" },
  rationale: { cls: "why", label: "Why this matters" },
  guidance: { cls: "how", label: "How to read it" },
} as const;

/** One Overview field UNDER ITS OWN HEADING, so it is a run of one.
 *
 *  Most Overview fields are: Description, Intent, Clinical Rationale, Guidance and How to read all sit
 *  below an `h2` of their own, and there is nothing adjacent to group them with. Scope & Non-Goals is
 *  the exception — several fields under ONE heading — so it builds its run itself and renders the bodies
 *  with `OverviewBody`. This wrapper exists so the common case stays one call. */
/** APP-NARRATIVE-PROVENANCE — an Overview heading, carrying the provenance mark when the prose under
 *  it is OURS rather than the measure author's.
 *
 *  THE OVERVIEW WAS THE ONE PLACE NARRATION WENT UNMARKED. Provenance is stated once per criterion, on
 *  the heading row (`ExplainBadge`), and `NarratedRun` deliberately draws nothing — so prose
 *  reached through `OverviewProse` had no heading row to carry the mark and showed none at all. A reader
 *  moving down the page from Description and Intent to the criteria was told "we wrote this" for forty
 *  sections and told nothing for the four at the top, which reads as *those* being the author's words.
 *  Intent and How-to-read are frequently ours; getting them mistaken for the steward's is the exact confusion
 *  `SPEC-EXPLAIN-ACCOUNTING` and the authored/narrated split exist to prevent.
 *
 *  Same badge, same row position, same rule — one mark per heading, not one per run — so the Overview
 *  reads with the rest of the document instead of as an exception to it. */
function OverviewHeading({ id, title, prose }: { id: string; title: string; prose?: NarratedText | null }) {
  return (
    <h2 id={id}>
      {title}
      {prose && prose.provenance !== "authored" && (
        <span className="mw-el-badges">
          {/* NO `define` on the target: an overview heading names a section of the document, not an
              addressable node, so a re-explanation of it is scoped to the MEASURE (`ExplainTarget`). */}
          <ExplainBadge method={prose.method} target={{ label: title }} />
        </span>
      )}
    </h2>
  );
}

function OverviewProse({ prose, metrics = null, reads }: {
  prose: NarratedText;
  /** SPEC-PARAMETER-REF — the same resolvers the criteria sections use. An Overview sentence is a
   *  sentence, so a reference inside one resolves the same way or the document has two answers. */
  metrics?: MetricSet | null; reads?: ReadLens;
}) {
  return (
    <NarratedRun run={toProse(prose)}>
      <OverviewBody prose={prose} metrics={metrics} reads={reads} />
    </NarratedRun>
  );
}

/** One Overview field's prose, as its author wrote it — WITHOUT the provenance mark, so a caller that is
 *  already inside a run does not open a second one.
 *
 *  SPEC-MEASURE-METADATA — the text may be FHIR `markdown` quoted verbatim from the measure's own
 *  resource, so its paragraphs and links are rendered rather than flattened: reformatting someone
 *  else's published prose is a silent edit of it. Attribution follows the LAST paragraph so a quoted
 *  field says whose words it is, which is the whole reason quoting beats paraphrasing. */
function OverviewBody({ prose, metrics = null, reads }: {
  prose: NarratedText; metrics?: MetricSet | null; reads?: ReadLens;
}) {
  // SPEC-PARAMETER-REF — THROUGH THE SEGMENTS, not through `prose.text`. Rendering the plain projection
  // here is what made an Overview field the one place in the document where an inline reference silently
  // vanished: `How to read this document` cites the measurement period, and the reader saw the sentence
  // with the year deleted. Normalised to one text run when the field carries no references, so there is
  // still exactly one path — the same normalisation `ProseText` makes for the criteria.
  const paras = segmentParagraphs(prose.segments ?? [{ kind: "text", text: prose.text }]);
  return (
    <>
      {paras.map((para, i) => (
        <p key={i}>
          <Segments segments={para} metrics={metrics} reads={reads} cite={noCite} />
          {i === paras.length - 1 && <Attrib prose={prose} />}
        </p>
      ))}
      {/* SPEC-NARRATIVE-DEVICES — an Overview field carries the same devices a criterion does. "How to
          read this document" is a set of pointers, and a set of pointers is a list; written as a
          paragraph it is the one part of the page nobody finishes. Citations are NOT offered here:
          an Overview field is about the measure or the document, not about lines of one define, so a
          superscript would be false precision (explain/clinical-explain/SKILL.md, "Citations").

          The resolvers ARE offered, and for the same reason the paragraph above gets them: a bullet in
          "Where to go for what" names the same things its lead sentence does, and a reference that
          worked in one and not the other is the split `Segments` exists to prevent. */}
      <Devices blocks={prose.blocks} cite={noCite} metrics={metrics} reads={reads} />
    </>
  );
}

/** SPEC-NARRATIVE-TAGS — an item in the Scope & Non-Goals lane. `kind` decides only how the item RENDERS
 *  — `@scope` as paragraphs, each `@nonGoal` as a bullet — and carries no provenance meaning; that is
 *  read from the prose itself, so the run breaks on a boundary the author asserted either way. */
type ScopeItem = { kind: "scope" | "nonGoal"; prose: NarratedText };

/** Overview prose has no CQL pane to point at, so its devices render uncited. */
const noCite = () => null;

/** APP-NARRATIVE-PROVENANCE — WHO asserted it, for prose quoted from its author.
 *
 *  The counterpart of the Narrative chip and deliberately quieter: narration is the exceptional case
 *  a reader must not miss, attribution is the reassuring one. It is still stated, because "authored"
 *  with nobody behind it is not a claim anyone can weigh — and because the point of quoting a steward
 *  instead of paraphrasing them is lost if the page does not say who it is. */
function Attrib({ prose }: { prose: NarratedText }) {
  if (prose.provenance !== "authored" || !prose.author) return null;
  return (
    <span className="mw-spec-attrib" title={prose.sourceRef ? `Quoted from ${prose.sourceRef}` : undefined}>
      — {prose.author}
    </span>
  );
}

/** SPEC-NARRATIVE-DEVICES — the citation binding, owned once per section.
 *
 *  A claim carrying a `sourceSpan` gets a superscript. Clicking it opens the CQL pane and highlights
 *  the lines the claim is about — which is what "traceable" has to mean. Provenance already says
 *  whose words these are; without the anchor the reader is told a sentence is interpreted and given
 *  no way to check it against the thing it interprets.
 *
 *  Line-range citations are hand-authored in the instances we ship, and they break silently if the
 *  registry reformats the CQL. The generated form must take the span from the ELM locator of the node
 *  the claim is about; this is the mockup of the contract, not its implementation. */
function useCitations() {
  const [lines, setLines] = useState<string | null>(null);
  const cite = useCallback((span?: string): ReactNode => {
    if (!span) return null;
    return (
      <sup>
        <button className={"mw-cite" + (lines === span ? " on" : "")}
          title={`Show the CQL this is about (line${span.includes("-") ? "s" : ""} ${span})`}
          onClick={(e) => {
            e.stopPropagation();
            setLines(lines === span ? null : span);
          }}>
          {span}
        </button>
      </sup>
    );
  }, [lines]);
  return { cite, lines };
}

/** SPEC-NARRATIVE-DEVICES — the device blocks under a prose field.
 *
 *  Include and Exclude are two lists, never one paragraph alternating between them, because two
 *  blocks show a reader there are two decisions (the AHRQ convention, designer/docs/narrative-tags.md
 *  section 6). A table is the most checkable device there is: six rows can be diffed against the ELM
 *  literal and fail loudly when a seventh is added, where the same claim as a sentence can be checked
 *  against nothing. `checkAgainst` states what the rows must match so that diff can be written. */
function Devices({ blocks, cite, metrics = null, reads }: {
  blocks?: SpecBlock[]; cite: (span?: string) => ReactNode;
  /** SPEC-METRIC-REF / SPEC-READ-REF — an ITEM carries segments now, so a device block resolves
   *  references exactly as the paragraph above it does. Defaulted only so a caller with nothing to
   *  resolve against need not say so: every caller in this file passes them, including `OverviewBody`,
   *  whose fields cite the measurement period (`SPEC-PARAMETER-REF`). The earlier note here — that an
   *  Overview device is about the measure and has none to resolve — was the assumption that let those
   *  references render as nothing. */
  metrics?: MetricSet | null; reads?: ReadLens;
}) {
  if (!blocks?.length) return null;
  return (
    <>
      {blocks.map((b, i) => b.block === "list" ? (
        <div key={i} className={`mw-dev-list ${b.kind}`}>
          {b.title && <span className="mw-narr-label">{b.title}</span>}
          <ul>
            {b.items.map((item, j) => (
              <li key={j}>
                {item.segments
                  ? <Segments segments={item.segments} metrics={metrics} reads={reads} cite={cite} />
                  : <MarkdownInline text={item.text} />}
                {cite(item.sourceSpan)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        /* `checkAgainst` is AUTHORING metadata, not reader content. It names what the rows must match
           so a lint can diff them against the ELM, and rendering it produced "checked against the ELM
           code literal on line 9" under a clinical table — a sentence about our build process, in a
           document for a clinician, using two words the voice bans outright. The attribute stays (it
           is the checkable contract, and the citation on each row is the reader-facing half of it);
           it now rides on the table's tooltip instead of occupying a line. */
        <div key={i} className="mw-dev-table"
          title={b.checkAgainst ? `These rows must match ${b.checkAgainst}` : undefined}>
          {b.title && <span className="mw-narr-label">{b.title}</span>}
          <div className="mw-dev-tablewrap">
            <table className="mw-narr-table">
              <thead><tr>{b.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {b.rows.map((row, j) => (
                  <tr key={j}>
                    {row.map((cell, k) => (
                      <td key={k} className={k === 0 ? "mono" : undefined}>
                        {cell}{k === row.length - 1 && cite(b.rowSpans?.[j] ?? undefined)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  );
}

/** APP-NARRATIVE-PROVENANCE / SPEC-EXPLAIN-VIEW — whose words these are, stated over the block
 *  rather than inside it.
 *
 *  A run of `narrated` prose is led by an **EXPLAIN** chip; `authored` prose renders unmarked with its
 *  byline (`Attrib`). That asymmetry is the point: the CQL's own tags are the expected case, and marking
 *  both would leave nothing marked.
 *
 *  ONE MARK PER RUN, ON ITS OWN LINE. The chip used to be per field and inline, so a reader parsed a
 *  metadata token before every clinical claim — three or four times per define, on a page with forty
 *  defines, and no shipped instance has a single `authored` field to make the mark exceptional. Leading
 *  the run instead states the same thing once and leaves the prose to start with prose. It also gives
 *  the Explain toggle something legible to do — it removes labelled groups, not a sprinkle of tokens.
 *
 *  NO WELL. A filled, bordered container was tried and dropped: stacked against the CQL block below it,
 *  two framed boxes per define read as competing structure rather than as prose and its code, and a
 *  background carries no meaning on paper — a specification has to print.
 *
 *  WHY "EXPLAIN" AND NOT "NARRATIVE". The word already belongs to this layer everywhere else in the
 *  product — `cql_databricks_designer/explain/`, "EXPLAIN output", `SPEC-EXPLAIN-VIEW`, `SPEC-EXPLAIN-VOICE` —
 *  and the toolbar toggle that shows and hides these runs is called Explain. `Narrative` was a third
 *  word for one thing, which is how a reader ends up wondering whether they are three things. It also
 *  reads exactly right to this audience: Spark's `EXPLAIN` returns the engine's reading of a query, not
 *  the query, which is precisely what this prose is to the CQL beneath it. A verb ("Narrate") was the
 *  other candidate and is the one label to avoid — it names an ACTION, next to a Sparkle glyph, in a
 *  product that really does generate narration, so it would read as a button that writes prose.
 *
 *  `narrated` stays the MODEL's word (the wire's provenance value, the XSD, the `.mw-narr-*` classes) —
 *  that vocabulary is the schema's and is not the reader's to see.
 *
 *  THE CHIP IS ONE WORD. The METHOD ("AI-drafted" / "Written by a person") rode beside it as visible text
 *  and came back off: on a page where every run is narrated it repeated the same four words down the
 *  whole document, which is the per-field chip's failure in a second costume. It leads the tooltip
 *  instead, where a reviewer who wants to weigh a particular sentence will look and where it costs the
 *  reading column nothing. The `@`-tag each field belongs to is not restated either: by
 *  `SPEC-NARRATIVE-TAGS` the rendered lane name and the tag name are the same word, so "Why this
 *  matters" already says `@rationale`. */
function NarratedRun({ run, children }: { run: RunProvenance; children: ReactNode }) {
  // Authored prose is not wrapped in a box — the class is a STYLING HOOK ONLY (`display: contents`), so
  // the author's words can be set in fuller ink than our reading of them without a container that would
  // put a second structure into the reading column.
  if (run.provenance !== "narrated") return <div className="mw-narr-authored">{children}</div>;
  return (
    <div className="mw-narr-run">
      {children}
      {/* NO MARK ON THE RUN. Provenance is stated ONCE PER CRITERION, by the speech-bubble badge on the
          heading row (`ExplainBadge`) — a discoverable target with a tooltip, where a reader looks first.
          What stood here, in order: the EXPLAIN chip leading every run; a line of words signing every run;
          a jagged strip in the right margin; a glyph over a hairline in the right margin. The first two
          repeated (29 marks against 4 author bylines); the second two could not be understood without
          being told what they meant. */}
    </div>
  );
}

/** All `NarratedRun` needs: the provenance the block asserts and how the prose was produced. A
 *  `ProseRun` satisfies it, and so does a single `SpecProse` — a lone field is a run of one, and
 *  saying so in the type is cheaper than wrapping it in an array to get one back. */
type RunProvenance = Pick<SpecProse, "provenance" | "method">;

/** The wire's `NarratedText` as the document's `SpecProse`. The overlay may legitimately carry
 *  `authored` prose (a measure we own, whose tags were already accepted), so provenance is read, not
 *  assumed. */
function toProse(t: NarratedText): SpecProse {
  return {
    text: t.text,
    provenance: t.provenance === "authored" ? "authored" : "narrated",
    method: t.method,
    sourceTag: t.sourceTag,
  };
}

/** SPEC-REF-LENS — the open state every reference in the document shares. ONE state, opened by a PRESS.
 *
 *  HOVER DOES NOT OPEN IT. Hover shows that the name is a LINK — that is the whole job of a hover, and
 *  it is the underline's to do. A lens that also opened on hover made two gestures compete for one
 *  word: it appeared over the sentence a reader was still reading, it fought the browser's own tooltip,
 *  and it forced the box to stay small and untouchable so it could be dismissed by moving away. Opening
 *  on the press alone is what lets the lens be a real surface — big enough to read, with controls that
 *  can be pressed.
 *
 *  WHY THE PRESS NO LONGER JUMPS. Following a reference is the expensive gesture — it moves the reader
 *  off the criterion they are reviewing AND moves the selection that scopes the inspector
 *  (`APP-INSPECTOR`) — and it was what a single press bought, so a reader who only wanted to know
 *  what a name MEANT paid for a trip they did not ask for. Both verbs are now controls in the lens's own
 *  header (`LensActions`). This is also the only thing that works on TOUCH, where there is no hover to
 *  open anything with.
 *
 *  Keyboard needs no special case — the trigger is a `button`, so Enter and Space are the same press a
 *  mouse makes, and the lens follows the trigger in DOM order, so Tab lands in its controls next.
 *
 *  DISMISSAL IS THE APP'S ONE POPOVER CONTRACT (`design-system.md` section 6): Escape, or a press
 *  outside. The listeners are bound ONLY while open — a document listener per reference would be
 *  hundreds of them on a page of CQL, for a state at most one of them is in. */
/** SPEC-REF-LENS — the gap the lens keeps from the viewport's edges, and from its own anchor. */
const LENS_EDGE_PAD = 12;
const LENS_GAP = 6;

/** SPEC-REF-LENS — the ONE host every lens in the document renders into, created on first use.
 *
 *  WHY THE LENS RENDERS INTO THE BODY. Being `position: absolute` inside its anchor put it inside
 *  `.mw-doc` — a SCROLLPORT — and nothing escapes an ancestor's clip. It cost a left-clamp to keep
 *  the box inside the reading column, handling only the HORIZONTAL axis. The lens now holds the
 *  inspector's three tabs — several hundred pixels tall — so the vertical case stopped being
 *  theoretical: a reference pressed low opened a box whose lower half was below the scrollport,
 *  and reaching it meant scrolling the document the lens exists to avoid. Rendering into the body
 *  means the box is clipped by nothing, is free to be wider than the column, and is placed by
 *  measurement in BOTH axes. The clamp retires with it.
 *
 *  IT CARRIES `mw`, AND THAT IS LOAD-BEARING RATHER THAN COSMETIC. The workspace's `.mw button` reset is
 *  (0,1,1) and every rule written against it is `.mw`-qualified to (0,2,1) — including the lens's own
 *  action corner, which `refLens.test.tsx` guards. A host outside `.mw` would drop the reset AND the rules
 *  that out-specify it at the same time, so every control in the lens would fall back to the browser's own
 *  button chrome. The class is what makes leaving the document's tree invisible to the cascade. */
let lensHost: HTMLElement | null = null;
function lensLayer(): HTMLElement {
  if (lensHost?.isConnected) return lensHost;
  lensHost = document.createElement("div");
  lensHost.className = "mw mw-lens-layer";
  document.body.appendChild(lensHost);
  return lensHost;
}

/** SPEC-REF-LENS — the region a lens is allowed to occupy: the viewport, MINUS the inspector pane.
 *
 *  THE LENS MUST NOT COVER THE INSPECTOR, and this is the substantive half of the placement (added
 *  2026-08-08). The box is 640px wide and the pane floors at 300px on the right of the screen, so clamping
 *  only to the viewport put the lens squarely on top of the pane for any reference in the right half of a
 *  line — which is most of them. That is bad in two compounding ways. It hides the pinned reading the lens
 *  is there to be compared AGAINST; and because the lens now renders the pane's own markup in the pane's
 *  own material (`--panel` on `--panel`, a separation problem `design-system.md` section 6 already tracks),
 *  a lens sitting over the pane is visually indistinguishable from the pane itself — it was reported as the
 *  lens and the pane sharing one tab state, which they never did.
 *
 *  MEASURED, NOT ASSUMED: `--wb-w` is a `clamp()` over `vw` with two media queries and three pane states,
 *  so the only honest way to know where the pane starts is to ask it. With no pane on screen the field is
 *  the whole viewport, which is the same expression rather than a second path. */
function lensField(): { left: number; right: number; top: number; bottom: number } {
  const pane = document.querySelector(".mw-workbench")?.getBoundingClientRect();
  return {
    left: LENS_EDGE_PAD,
    // The RAIL counts too: collapsed, the pane is 44px of this pane's own tab glyphs, and covering those
    // is the same mistake in miniature.
    right: Math.min(window.innerWidth, pane?.left ?? Infinity) - LENS_EDGE_PAD,
    top: LENS_EDGE_PAD,
    bottom: window.innerHeight - LENS_EDGE_PAD,
  };
}

/** SPEC-REF-LENS — where a lens of `w`×`h` goes for an anchor at `a`, inside `field`.
 *
 *  BELOW THE ANCHOR BY PREFERENCE, ABOVE IT WHEN THERE IS MORE ROOM THERE — the reading order a reader
 *  expects, with one flip rather than a cascade of special cases. Both axes then CLAMP into the field,
 *  which is what makes the box reachable for an anchor in the last line of a long page or the last word of
 *  a wide one.
 *
 *  A FIELD NARROWER THAN THE BOX degrades to the field's left edge and overhangs rather than being pushed
 *  off-screen: on a window too small to hold the reading column and a 640px lens side by side, covering
 *  part of the pane is the lesser loss and the header — the identity and both verbs — stays put. Exported
 *  for its own test: the arithmetic is the whole behaviour, and asserting it through a rendered popover
 *  means asserting jsdom's layout instead. */
export function lensPlacement(a: { top: number; bottom: number; left: number }, w: number, h: number,
  field: { left: number; right: number; top: number; bottom: number }): { left: number; top: number } {
  const roomBelow = field.bottom - a.bottom - LENS_GAP;
  const roomAbove = a.top - field.top - LENS_GAP;
  const top = roomBelow >= h || roomBelow >= roomAbove ? a.bottom + LENS_GAP : a.top - LENS_GAP - h;
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi));
  return {
    left: clamp(a.left, field.left, field.right - w),
    top: clamp(top, field.top, field.bottom - h),
  };
}

function useRefLens() {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement | null>(null);
  const lens = useRef<HTMLDivElement | null>(null);
  /** The measured viewport placement. Null until the box has been laid out once — it renders `hidden`
   *  until then rather than at the wrong coordinates, because a box that appears at the top-left and jumps
   *  is worse than one that appears a frame later where it belongs. */
  const [at, setAt] = useState<{ left: number; top: number } | null>(null);

  /* Measured in a LAYOUT effect so the placement is applied before paint. It re-runs on scroll and on
     resize because the box is `fixed`: its anchor moves with the document while it does not, and a lens
     that stayed put while the sentence it explains scrolled away would be pointing at nothing. Scroll is
     captured, since the scrolling element is `.mw-doc` rather than the window. */
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = wrap.current, box = lens.current;
      if (!anchor || !box) return;
      setAt(lensPlacement(anchor.getBoundingClientRect(), box.offsetWidth, box.offsetHeight,
        lensField()));
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    // THE LENS IS NO LONGER INSIDE ITS ANCHOR, so "outside" has two parts: a press in the box itself must
    // not dismiss it (its tabs and its verbs are controls), and neither must a press on the trigger, which
    // toggles. Testing the anchor alone — which was enough while the box was a descendant of it — would
    // now close the lens on the first tab a reader clicked.
    const onDown = (e: Event) => {
      const t = e.target as Node;
      if (!wrap.current?.contains(t) && !lens.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return {
    open,
    wrap,
    /** The LENS BOX's own props — spread onto `.mw-reflens` by every lens, so there is one popover
     *  placement AND one containment in the document rather than one per kind of reference. */
    lens: {
      ref: lens,
      style: { left: at?.left ?? 0, top: at?.top ?? 0, visibility: at ? undefined : ("hidden" as const) },
      /* THE LENS IS A PORTAL, AND REACT BUBBLES EVENTS THROUGH THE REACT TREE RATHER THAN THE DOM TREE.
         So a click inside this box — which is on `document.body` in the DOM — still travels up through the
         reference that opened it and into whatever contains that reference. What contains it is a define
         SECTION, whose `onClick` selects, and selection TOGGLES (`selectInSpec`): pinning `Initial
         Population`, opening a lens on a reference inside it and pressing a tab therefore re-fired the
         section's own select and turned the pin OFF, leaving the pane on "Whole document". Reported
         2026-08-08 as the lens and the pane sharing a tab state; the tabs were never shared, the pin was
         being cleared underneath.
         STOPPED HERE, AT THE BOUNDARY, rather than on each control inside. The hand-written ones
         (`trigger`, `LensActions`) already stop it individually, which worked only for as long as every
         control in the lens was written here — `InspectorScope`'s tab strip is the pane's, and it must not
         grow lens-specific event handling for a problem the pane does not have. One stop at the box is
         also what makes this hold for the NEXT thing put inside a lens. */
      onClick: (e: MouseEvent) => e.stopPropagation(),
    },
    /** Renders the box into the shared `.mw` host (see `lensLayer`). */
    portal: (node: ReactNode) => createPortal(node, lensLayer()),
    /** The TRIGGER's own props. The press opens, and pressing again closes, so the gesture that opened
     *  it also dismisses it. NO focus handler: focus is not a request to see the lens, and opening a
     *  popover under every Tab stop would make tabbing through a CQL block unusable. */
    trigger: {
      onClick: (e: MouseEvent) => { e.stopPropagation(); setOpen((v) => !v); },
      "aria-haspopup": "dialog" as const,
      "aria-expanded": open,
    },
    close: () => setOpen(false),
  };
}

/** SPEC-REF-LENS — the lens's two verbs, in its header's top-right corner.
 *
 *  ICONS, NOT LABELLED CHIPS. They sit on the title row rather than in a footer tray, which is the
 *  window-control position every reader already knows: the actions ON this box, in the corner of the box,
 *  out of the way of the facts. Two words of button text under a four-line lens read as the lens's
 *  content rather than as its chrome — and at this size chrome that competes with content is what makes
 *  a popover feel like a page.
 *
 *  TWO VERBS, because a reference asks two questions that cost different amounts. **Open** goes to the
 *  reference's own section — the expensive answer, now asked for by name. **Pin** scopes the right pane
 *  (`APP-INSPECTOR`) WITHOUT moving the reader, which is the whole reason for asking from inside a
 *  criterion rather than leaving it.
 *
 *  PIN IS A TOGGLE AND SAYS SO. Selection is ONE value, persisted to `?open=`, and it doubles as the
 *  document's you-are-here mark — so this cannot "add" a scope, and an action that silently replaced the
 *  reader's place-marker is exactly the confusion this design exists to avoid. It carries the same
 *  `aria-pressed` a bound read phrase used to carry itself.
 *
 *  BOTH DISMISS (revised 2026-08-07). Pressing either is a decision, and once taken the lens has nothing
 *  left to say: Open moves the reader, and Pin hands the subject to the inspector, which is now the
 *  surface showing it. A popover left open over the answer it just delegated is litter in both cases.
 *  The earlier reasoning — that a toggle a reader may press twice must not close on the first press —
 *  is withdrawn: the toggle is still reachable by reopening the lens, and keeping a panel open on top of
 *  the pane it just populated hid the very thing it had been asked for.
 *
 *  An icon-only control is NAMED (`aria-label` + `title`) — the glyphs are conventional, not
 *  self-explanatory, and a verb nobody can read is not a verb.
 *
 *  THE CORNER HOLDS A PAIR OF VERBS. Two icons are a pair a reader takes in at a glance; three are
 *  a toolbar. A lens whose chrome has to be read is the "popover that feels like a page" this design
 *  rejects. The assistant reaches this reference through the pane (Pin to inspect, then ask), not
 *  through the lens. One entry point per surface; the lens's is the pane. */
function LensActions({ sel, selected, onNavigate, onSelect, onDone }: {
  sel: Selection; selected: boolean;
  onNavigate: (sel: Selection) => void; onSelect: (sel: Selection) => void;
  onDone: () => void;
}) {
  return (
    <span className="mw-reflens-acts">
      <button aria-label="Open this section" title="Open this section"
        onClick={(e) => { e.stopPropagation(); onDone(); onNavigate(sel); }}>
        <OpenRef size={13} aria-hidden="true" />
      </button>
      <button aria-pressed={selected}
        aria-label="Pin to the inspector" title="Pin to the inspector"
        onClick={(e) => { e.stopPropagation(); onDone(); onSelect(sel); }}>
        <Pin size={13} aria-hidden="true" />
      </button>
      {/* NO ASSISTANT VERB. See the header note: the `Sparkle` that opened the drawer scoped to this
          reference was dropped, and the corner is a PAIR again. */}
    </span>
  );
}

/** SPEC-REF-LENS — the lens box: an identity line with the two verbs, over the INSPECTOR's own body.
 *
 *  THE LENS IS THE INSPECTOR NOW. The point is what a reader does NOT have to spend to use it.
 *  Pin moves the selection, which is also the document's you-are-here mark. A lens that opens
 *  inside a criterion exists precisely so reading it does not move the reader. Here it does both:
 *  opens the answer and persists it to the pane.
 *
 *  IT IS THE SAME COMPONENT, NOT A MATCHING ONE (`InspectorScope`). Pinned and popped are one
 *  rendering of one payload, so the two surfaces cannot report different figures for one name.
 *
 *  WHAT THE HEADER KEEPS IS IDENTITY AND VERBS. The library, return type, metric and eliminated
 *  note all left it — every one is what the Details tab leads with. The same figure rendered by
 *  two components in one box is the drift `APP-SPEC-MODEL` forbids. `InspectorIdentity` is the
 *  pane's own scope line, so a population reads `pop` in both.
 *
 *  PIN STILL MEANS WHAT IT SAYS. It no longer buys the answer — the lens already has it — it
 *  makes the scope PERSIST past dismissal. Both verbs still dismiss (`LensActions`). */
function RefLensBox({ pop, label, ctx, entry, read, sel, selected, onSelect }: {
  pop: ReturnType<typeof useRefLens>;
  /** The dialog's accessible name — the define's name, or the read's resource type. */
  label: string;
  ctx: InspectorContext;
  entry: SpecEntry | null;
  read: SpecRetrieve | null;
  sel: Selection;
  selected: boolean;
  onSelect: (sel: Selection) => void;
}) {
  // Mounted with the lens, so a freshly opened one starts on Details rather than on whichever tab the last
  // reference happened to leave open — the tab is a fact about this reading, not about the document.
  const { tab, setTab } = useInspectorTab();
  return pop.portal(
    <div className="mw-reflens" {...pop.lens} role="dialog" aria-label={label}>
      <div className="mw-reflens-head">
        <InspectorIdentity entry={entry} read={read} onNavigate={ctx.onNavigate} />
        <LensActions sel={sel} selected={selected}
          onNavigate={ctx.onNavigate} onSelect={onSelect} onDone={pop.close} />
      </div>
      <InspectorScope ctx={ctx} entry={entry} read={read} tab={tab} onTab={setTab} />
    </div>,
  );
}

/** SPEC-REF-LENS — a reference to another define: read the inspector on it without leaving, or go to it.
 *
 *  What the define IS and what it PRODUCED is usually the whole question ("how many patients had an
 *  encounter"), and answering it in place is what keeps a reader in the criterion they are
 *  actually reviewing. Since 2026-08-08 the answer is the inspector's own three tabs rather than a summary
 *  of them (`RefLensBox`), so the reader no longer trades their place for the distribution. Going there is
 *  still a labelled action rather than the cost of asking (`useRefLens`).
 *
 *  Everything the header states is DERIVED, so it is identical in either Explain state; the tab body is
 *  data about an evaluation and states its own provenance (`APP-NO-UI-MOCK`). */
function DefineLink({ entry, ctx, selected, onSelect, className, children }: {
  entry: SpecEntry; ctx: InspectorContext; selected: boolean;
  onSelect: (sel: Selection) => void;
  className?: string; children?: ReactNode;
}) {
  const pop = useRefLens();
  return (
    <span className="mw-reflens-wrap" ref={pop.wrap}>
      {/* NO `title` — the lens is this reference's tooltip, and a native one would paint the browser's
          own box over it. It used to read "Go to {name}", which described a press that no longer
          navigates. The button's text names it, so nothing is lost for a screen reader. */}
      <button className={className} {...pop.trigger}>
        {children ?? entry.name}
      </button>
      {pop.open && (
        <RefLensBox pop={pop} label={entry.name} ctx={ctx} entry={entry} read={null}
          sel={{ kind: "define", name: entry.name }} selected={selected} onSelect={onSelect} />
      )}
    </span>
  );
}

/** The anchor for one resource's group of reads, so the nav can scroll into the section. */
function dataReqTypeId(type: string): string {
  return `mw-dr-${type.toLowerCase()}`;
}

/** SPEC-DATA-REQUIREMENTS — one read, expandable to what filters it and who asks for it.
 *
 *  RESOURCE IS THE PARENT because that is the shape of the question. A reader arrives asking "what
 *  does this measure take from Condition?" — not "which value sets exist" — and the profile and the
 *  filter are how one Condition read differs from another. Collapsed, the row is the answer; expanded,
 *  it is the evidence.
 *
 *  THE CALL SITES ARE THE POINT. A data requirement that cannot be traced to the logic that asked for
 *  it is a fact with no owner: a reader who disputes a row has nowhere to go. Each site links to its
 *  define and carries the span of the clause within it, so following one lands on the retrieve itself.
 *
 *  `mustSupport` is stated only when the published resource carried it. Some ingested library copies
 *  do not, and an empty list is not the same claim as "the logic touches nothing". */
function RetrieveRow({ retrieve, reads, selected, onSelect }: {
  retrieve: SpecRetrieve;
  /** SPEC-DEFINE-REF — the call sites are define references, so they resolve through the same bundle
   *  every other reference in the document resolves through. */
  reads: ReadLens;
  /** SPEC-NAV-COMPOSITION — a read is SELECTABLE, like a define: it is the leaf both nav arrangements
   *  bottom out at, so the thread from a nav row to the document has to reach it. Same three marks
   *  selection carries everywhere — the margin bar is the one that reads at a glance. */
  selected: boolean; onSelect: (sel: Selection) => void;
}) {
  const [open, setOpen] = useState(false);
  const filter = retrieve.valueSet ?? retrieve.code;
  return (
    <div className={"mw-dreq-row" + (selected ? " selected" : "")} role="listitem"
      data-crit-name={selectionToken({ kind: "retrieve", key: retrieve.key })}
      onClick={() => onSelect({ kind: "retrieve", key: retrieve.key })} aria-selected={selected}>
      {/* The head TOGGLES and lets the click through to the row, which SELECTS. Two effects from one
          press is right here and wrong in the nav's tree: there, the twisty and the row are separate
          targets because collapsing a population must not move the document. This row IS the thing
          being selected — a reader opening it to read its evidence has, by opening it, told the pane
          which read they are looking at, and swallowing that left the workbench scoped to whatever was
          selected before. */}
      <button className="mw-dreq-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <ChevronDown size={12} className={"mw-collapse-chevron" + (open ? "" : " collapsed")} aria-hidden="true" />
        <span className="mw-spec-kind rd">rd</span>
        {/* NO resource type here: the heading above the group states it, and repeating it on every
            row under `Condition` is the noise grouping was supposed to remove. */}
        {filter
          ? <span className="fl"><span className="op">{retrieve.codeProperty ?? "code"} in</span> {filter}</span>
          : <span className="fl unfiltered">every record</span>}
        <span className="ct">{retrieve.usedBy.length === 1 ? "1 use" : `${retrieve.usedBy.length} uses`}</span>
      </button>
      {open && (
        <div className="mw-dreq-body">
          {/* NO PROFILE ROW. `templateId` gives one, and it is derived and kept on the model, but it
              is not shown until we are confident it means what it appears to — a profile a reader
              trusts and acts on is worse than one they never saw if it is wrong. */}
          <dl className="mw-dreq-facts">
            <dt>Elements read</dt>
            <dd className="mono">
              {retrieve.mustSupport.length
                ? retrieve.mustSupport.join(", ")
                : <span className="mw-dreq-none">not published for this library</span>}
            </dd>
            <dt>Read by</dt>
            {/* SPEC-DEFINE-REF / SPEC-REF-LENS — a call site is a define reference like any other, so it
                is `DefineLink` under the one inline mark, not a second way to reach a define. NO `title`:
                the lens already leads with the library this name lives in, and a native tooltip would
                paint the browser's own box over it. */}
            <dd>
              {retrieve.usedBy.map((site, i) => (
                <span key={`${site.library}::${site.define}::${i}`}>
                  {i > 0 && ", "}
                  <DefineName name={site.define} reads={reads} />
                  {site.span && <span className="mw-dreq-span"> line {site.span}</span>}
                </span>
              ))}
            </dd>
          </dl>
        </div>
      )}
    </div>
  );
}

/** SPEC-RETRIEVE-LENS / SPEC-REF-LENS — a retrieve token in the CQL, and the lens over what it reads.
 *
 *  ONE GESTURE VOCABULARY WITH `DefineLink`, deliberately: hover previews, a press pins, and the pinned
 *  tray holds both verbs. A retrieve token and a define token are two references in one code block, and
 *  making them answer the same press differently is a second vocabulary for one job.
 *
 *  Its verbs land on the READ (`SPEC-NAV-COMPOSITION`), never on the section: `Jump to section` scrolls to
 *  the retrieve's own row and selects it, which is what a link naming a specific binding owes the reader.
 *  Scrolling to the Data requirements section and selecting nothing — what this did before the lens — left
 *  them at the top of a list of ten Condition reads with no mark on the one they pressed.
 *
 *  The token keeps its plain syntax colour. A retrieve is not a reference to prose, and colouring it like
 *  a define reference would promise more to read than there is. */
function RetrieveLink({ token, retrieve, ctx, selected, onSelect }: {
  token: string; retrieve: SpecRetrieve; ctx: InspectorContext; selected: boolean;
  onSelect: (sel: Selection) => void;
}) {
  const pop = useRefLens();
  return (
    <span className="mw-reflens-wrap" ref={pop.wrap}>
      {/* NO `title` — the lens is the tooltip (see `DefineLink`), and it already leads with the type. */}
      <button className="cql-link rd" {...pop.trigger}>
        {token}
      </button>
      {pop.open && (
        <RefLensBox pop={pop} label={retrieve.type} ctx={ctx} entry={null} read={retrieve}
          sel={{ kind: "retrieve", key: retrieve.key }} selected={selected} onSelect={onSelect} />
      )}
    </span>
  );
}

/** What the lens can say about a referenced define — all of it derived. */
interface RefLens {
  library: string;
  kind: SpecEntry["kind"];
  metric: SpecMetric | null;
  returns: string | null;
  eliminated: boolean;
  /** SPEC-METRIC-REF — the same set the section renders from, so the lens cannot show a different
   *  figure from the heading it is a preview of. */
  metrics: MetricSet | null;
}

/** SPEC-CQL-VIEW — the define's own CQL, cited and followable.
 *
 *  There was a Composition tab here — the dependency index as a peer view. It is gone. It was
 *  complete only for defines that reference other defines, so it went empty on exactly the logic
 *  that needed explaining, and a reader has no way to know which case they are in. Plain language
 *  answers what the logic means; the CQL answers what it does; the data answers what it counted.
 *  A fourth layer between them helped where help was least needed.
 *
 *  The language label sits INSIDE the block, top-right, the way a fenced code block is labelled
 *  everywhere else a reader meets one. It was a tab above the block, left over from when there were
 *  two panes to switch between; with one pane a tab is a heading for a section of one, and it read as
 *  structure the document does not have. */
function CqlPane({ entry, link, cites, explain, authorNote = false }: {
  entry: SpecEntry; link: CqlLinker; cites: ReturnType<typeof useCitations>;
  /** SPEC-EXPLAIN-VIEW — which layer the reader is in, and therefore whether this block is what the
   *  section has to say. */
  explain: boolean;
  /** SPEC-NARRATIVE-TAGS — the author left an `@comment` on this define, so the section says something
   *  in their own words with Explain off. Defaults false: a caller that does not know renders the
   *  Explain-off default, which is the same behaviour this had before the flag existed. */
  authorNote?: boolean;
}) {
  // SPEC-CQL-VIEW — CLOSED BY DEFAULT.
  //
  // The document is a CLINICAL specification; prose answers "what does the measure mean?", CQL
  // answers "show me the code". Open by default, the CQL block doubled the page length and put the
  // next sentence a scroll away. CITATIONS OPEN IT — clicking the line number after a sentence opens
  // the code, the highlight lands where it always did, and `open` is derived, not stored, so
  // clicking a citation does not close the block under a reader who opened it. (`SPEC-NARRATIVE-DEVICES`)
  //
  // The default follows the EXPLAIN LAYER. Explain ON, prose is read-first, code waits for a click.
  // Explain OFF, there is no prose — the section IS its CQL, so it opens. An AUTHOR'S NOTE overrides
  // this: a `@comment` in the CQL is the author's own prose (`withoutNarrated` preserves tags); it
  // survives the Explain OFF filter. Opening the code inverts the default's logic here because the
  // section would otherwise be empty. (SPEC-NARRATIVE-TAGS)
  //
  // The reader's own click wins, EITHER direction and EITHER state. `toggled` is tri-state, not
  // boolean, so "not touched" is distinguishable from "closed" — the default moves under readers who
  // have not expressed a preference, stays under readers who have.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const body = entry.variants[0]?.body ?? "";
  if (!body.trim()) return null;
  const open = cites.lines != null || (toggled ?? (!explain && !authorNote));
  return (
    <div className={"mw-cqlblock" + (cites.lines ? " cited" : "") + (open ? " open" : "")}>
      <button className="mw-cqlblock-toggle" aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setToggled(!open); }}>
        <ChevronDown size={12} className={"mw-collapse-chevron" + (open ? "" : " collapsed")}
          aria-hidden="true" />
        {/* ONE LABEL. It read `CQL  Show the logic` — a mono tag and a sentence fragment naming the same
            thing twice. `Show CQL` is the noun and the verb in two words. */}
        <span className="hint">{open ? "Hide CQL" : "Show CQL"}</span>
      </button>
      {open && <CqlCode source={body} link={link} highlight={cites.lines} />}
    </div>
  );
}

/** SPEC-ATTRITION — the criterion's figure, stated as what it DID rather than as a count.
 *
 *  A criterion is a filter, so the number that reads as a criterion is the one beside its base:
 *  `10,318 → 8,204 · 79%`. The count alone makes the reader find the denominator themselves and do the
 *  subtraction, which on a page of forty criteria nobody does.
 *
 *  THE DELTA IS NEUTRAL INK, NOT `crit`. Narrowing is not a failure state, and the semantic hues are
 *  reserved for real state (`design-system.md` section 2). The one exception is a criterion that
 *  REMOVES — an exclusion — which is the exclusion role the funnel already draws in `crit`.
 *
 *  WITH NO FIGURE IT RENDERS NOTHING. There is no "not computed" pill: `SPEC-SAMPLE-METRIC`'s own
 *  argument is that "a specification full of not computed cannot be reviewed as a document", and that
 *  was exactly what a per-element absence marker produced — three or four words of chrome beside every
 *  clinical claim, forty times a page. The compute pill states the data state once, in the toolbar. */
function Metric({ metric, metrics, library }: {
  metric: SpecMetric; metrics: MetricSet | null; library: string;
}) {
  const figure = resolveMetric(metrics, metric, library);
  if (!figure) return null;
  const grain = metric.elementGrain ? metric.elementGrain.toLowerCase() + "s" : null;
  return (
    <span className={"mw-el-metric" + (figure.removes ? " removes" : "")}
      title={metricTitle(figure, metric.countOf)}>
      {figure.share != null && (
        <i className="mw-el-bar" aria-hidden="true">
          <i style={{ width: `${Math.min(figure.share * 100, 100)}%` }} />
        </i>
      )}
      {/* The base as the "from" side of the arrow. Omitted where the base IS this figure — a Denominator
          equal to its Initial Population would otherwise read "10,318 → 10,318". */}
      {figure.base && figure.base.subjects !== figure.subjects && !figure.removes && (
        <span className="from tnum">{figure.base.subjects.toLocaleString()}<span className="arrow">→</span></span>
      )}
      <b className="tnum">{figure.subjects.toLocaleString()}</b>
      <span className="unit">{metric.subjectUnit}</span>
      {/* SPEC-POPULATION-CASCADE — the counted figure leads and the share is of it; MET, the define
          true across every evaluated patient, sits beside it so a reader can see what the cascade
          left out. */}
      <span className="unit">{figure.removes ? "removed" : "counted"}</span>
      <span className="second tnum">· {figure.met.toLocaleString()} met</span>
      {figure.elements != null && grain && (
        <span className="second tnum">· {figure.elements.toLocaleString()} {grain}</span>
      )}
      {figure.share != null && <span className="share tnum">· {formatShare(figure.share)}</span>}
    </span>
  );
}

/** How the criterion's narrated prose was produced — the first narrated field's `method`, since a
 *  criterion's fields are narrated in one pass. `MethodKind` allows `ai-drafted` and `human-written`; a
 *  badge hardcoded to "with AI" would print a false claim the day the second one arrives. */
function narrationMethod(n: SpecNarration | null): string | undefined {
  if (!n) return undefined;
  for (const f of NARRATION_FIELDS) {
    const prose = n[f];
    if (prose?.provenance === "narrated") return prose.method;
  }
  return undefined;
}

/** APP-NARRATIVE-PROVENANCE — the provenance mark, on the heading row.
 *
 *  IT IS THE SAME GLYPH AS THE TOOLBAR TOGGLE — the speech bubble (`Explain`), so the mark and the
 *  control that governs it stay one sign. The badge follows the toggle because the toggle is what a
 *  reader presses to make this prose appear. This replaces the right-margin mark (unreadable — a
 *  shape in the gutter with no way to learn its meaning). A disc on the heading is discoverable
 *  (a target with a tooltip), one per criterion rather than per run, and sits where a reader looks
 *  first.
 *
 *  WHAT IS GIVEN UP, deliberately: per-run extent. The margin mark could span exactly the prose
 *  `proseRuns()` grouped, so a criterion that mixed the author's words with ours marked only ours. The
 *  heading badge says "there is interpreted prose in this criterion" and the author's own paragraphs still
 *  carry their byline (`Attrib`) — so the finer claim is still available where the two voices actually
 *  meet, which is the only place it matters.
 *
 *  IT IS ALSO WHAT AIMS "EXPLAIN AGAIN" (`SPEC-EXPLAIN-AGAIN`). Pressing it scopes the toolbar's
 *  re-explain to THIS section. Re-explaining is an act on one criterion; the bar had no way to say
 *  which one. The badge is the natural aim — the mark that says *this prose is ours*, and the
 *  reader is already pointing at it. Scoping is the ONLY thing the press does beyond opening the
 *  disclaimer: no selection, no move, no conversation. */
function ExplainBadge({ method, target }: { method?: string; target: ExplainTarget }) {
  const how = method === "human-written" ? "Explained by a person" : "Explained with AI";
  const pop = useRefLens();
  const verbs = useContext(ExplainAgainContext);
  return (
    <span className="mw-badge-wrap" ref={pop.wrap}>
      <button className="mw-badge st-explain" {...pop.trigger}
        aria-label={`${how}: not the measure author's words`}
        title={`${how} — not the measure author's words. This is our reading of the logic, and where the two could differ the CQL governs the result.`}
        onClick={(e) => { e.stopPropagation(); verbs?.scope?.(target); pop.trigger.onClick(e); }}>
        {/* 13px — THE EXACT SIZE THE EXPLAIN TOGGLE'S OWN GLYPH IS (`ExplainMenu`). The mark and the
            control that governs it are one sign (`APP-NARRATIVE-PROVENANCE`), and a sign drawn at two
            sizes is two signs; the toolbar is the one a reader learns it from, so the toolbar sets the
            size. */}
        <Explain size={13} aria-hidden="true" />
      </button>
      {/* THE DISCLAIMER IS A SURFACE, not only a tooltip: a tooltip cannot be read at leisure, cannot be
          reached on touch, and cannot hold a verb. The popover states the claim in full and offers the one
          action a reader wants next — and says plainly that it is not wired, rather than pretending.
          Dismissal is the app's one popover contract (Escape or a press outside, `useRefLens`).

          PORTALED AND PLACED BY MEASUREMENT, for the reason the reference lens is (`SPEC-REF-LENS`): it
          used to be `position: absolute` with `right: -6px`, which opened the box LEFTWARDS from the badge
          inside `.mw-doc` — a scrollport, so the overhang was clipped rather than scrolled to. A badge sits
          right after its criterion's name, so how much room there is to its left is a fact about the length
          of that name: `Denominator` clipped, `Has Reading A Below Threshold` did not, which is
          the "sometimes" it was reported as. It now takes the same `portal` + `lens` props every lens does,
          so ONE placement path serves both boxes and the clamp cannot be right in one and wrong in the
          other. */}
      {pop.open && pop.portal(
        // A `div`, not the `span` it was: the box no longer sits inside a heading's inline flow, and the
        // shared `lens` props carry the placement ref every lens is measured through.
        <div className="mw-badge-pop" role="dialog" aria-label={how} {...pop.lens}>
          <span className="hd"><Explain size={12} aria-hidden="true" /><b>{how}</b></span>
          <span className="bd">
            Not the measure author’s words. This is our reading of the logic — the CQL governs the result,
            and every claim here cites the lines it came from.
          </span>
          {/* SPEC-EXPLAIN-AGAIN — the one verb, now LIVE where it was a stub reading "narration cannot
              be regenerated from CQL on Databricks yet". What it does is bounded and says so: it hands this section
              to the assistant, scoped, and the assistant writes a fresh reading against the same
              contract this prose was written to (`clinical-explain`, served over MCP by `get_skill`).
              What it does NOT do is replace the paragraph above it — PERSISTING a narration is
              Designer's own act, not something a conversation may do to a clinical document behind the
              reader's back, and the tool surface is read-only for exactly that reason.

              It is the SAME verb the toolbar's Explain menu holds, firing the same callback with the
              same target. Two call sites for one act, which is the Open/Pin pattern the reference lens
              and the nav already share; the alternative — a verb here and a differently-worded one in
              the bar — is two answers to one question. */}
          <span className="ac">
            <button disabled={!verbs?.run}
              title={verbs?.run
                ? `Ask the assistant to explain ${target.label} again`
                : "Re-explain — this surface has no assistant to ask"}
              onClick={(e) => { e.stopPropagation(); pop.close(); verbs?.run?.(target); }}>
              Explain again
            </button>
          </span>
        </div>,
      )}
    </span>
  );
}

/** APP-STYLE-GUIDE — a CARD WITH A TITLE, for a section that holds more than one.
 *
 *  A GROUND EYEBROW names a section (UPPERCASE); a CARD TITLE names a card (SENTENCE CASE).
 *  Case is the whole difference — eyebrow sits on canvas with no fill or rule; title sits on a
 *  bled, ruled header inside the card. Same step, ink, family; different case, different register.
 *  A LIBRARY's card title is mono (`.mw-defs-lib-title`): the string is an identifier. Sentence
 *  case is what they have in common. NOT COLLAPSIBLE, unlike a library card. These hold a table
 *  or list; the section above already collapses. A caret per card is chrome for a fold nobody needs. */
function SpecCard({ title, id, children }: { title: string; id?: string; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className={"mw-spec-card" + (collapsed ? " collapsed" : "")}>
      {/* APP-SPEC-COLLAPSE — the header IS the control, the same idiom as a library card's and a
          section's: a caret in the gutter that rotates, the title's own type, and no resting affordance.
          EVERY titled card folds, not only the ones asked for. Retrieves and Value sets were the two named,
          and their peers — Parameters, Codes and concepts, Overview — are the same object holding the same
          kind of content; a fold that worked on one table and not the one beside it would read as a bug. */}
      <div className="mw-spec-card-head">
        <button className="mw-spec-card-toggle" aria-expanded={!collapsed}
          onClick={() => setCollapsed((v) => !v)}>
          <ChevronDown size={13} className={"mw-collapse-chevron" + (collapsed ? " collapsed" : "")}
            aria-hidden="true" />
          {/* THE ANCHOR rides the title, and the header renders open or shut, so a folded card is still a
              scroll target for the nav. */}
          <span className="mw-spec-card-title" id={id}>{title}</span>
        </button>
      </div>
      {!collapsed && children}
    </div>
  );
}

// APP-SPEC-COLLAPSE — a top-level spec section: a GROUND EYEBROW naming it, a caret marking state, and
// a card holding its content. Collapse/Expand-all lives in the article header and drives every section.
function SpecSection({ id, title, className, collapsed, onToggle, cards, children }: {
  id: SectionKey; title: string; className?: string; collapsed: boolean;
  onToggle: (k: SectionKey) => void;
  /** APP-STYLE-GUIDE — the section's children ARE cards, so it gets no card body of its own. A card
      inside a card is the "out-ranks its parent" trap in reverse: two nested containments, and the inner one
      wins for being nearer the content. Used by Libraries, where each library is its own card. */
  cards?: boolean;
  children: ReactNode;
}) {
  return (
    <section id={`mw-sec-${id}`}
      className={"mw-spec-sec" + (className ? " " + className : "") + (collapsed ? " collapsed" : "")}>
      {/* THE GROUND EYEBROW — the section's name, on the canvas, introducing the card below it. It used to
          be a header INSIDE that card; it moved out when a library became a card and needed its own header,
          at which point "LIBRARIES" and `ExampleMeasure` were the same object at two depths.
          One rule now covers both shapes: a ground eyebrow names the GROUP, and a card header names a CARD
          when a group holds more than one. */}
      <button className="mw-spec-sec-head" onClick={() => onToggle(id)} aria-expanded={!collapsed}>
        <ChevronDown className="mw-spec-caret" size={13} aria-hidden="true" />
        <span className="mw-spec-sec-title">{title}</span>
      </button>
      {!collapsed && (cards ? children : <div className="mw-spec-sec-body">{children}</div>)}
    </section>
  );
}

// APP-SPEC-CELLS — a criterion section behaves like a NOTEBOOK CELL. Hovering marks it in the left
// margin with a faint hairline bar (the candidate) and reveals a floating action bar; a between-cell Add
// invokes the assistant. Selecting (click, or the nav) turns that same bar accent — one mark, two colours,
// and nothing paints behind the prose (APP-LOGIC-VIEW). The chrome is absolutely positioned so it never
// reflows the reading column (APP-STABLE-LAYOUT). Copy / Run / Focus are wired; Genie / Add / Delete await the assistant home
// (APP-ASSISTANT-PANE) / editing and are present-but-disabled.
function SpecCell({ entry, kind, extra, selected, onSelect, onRunCell, showAdd, defineResults, children }: {
  entry: SpecEntry; kind: "pop" | "def"; extra?: string;
  selected: boolean; onSelect: (sel: Selection) => void; onRunCell: (name: string) => void;
  showAdd?: boolean;
  /** Per-subject values for THIS define, extracted from the run results. */
  defineResults?: { subject: string; value: unknown }[] | null;
  children: ReactNode;
}) {
  const name = entry.name;
  const base = kind === "pop" ? "mw-spec-pop" : "mw-spec-def";
  const stop = (fn: () => void) => (e: MouseEvent) => { e.stopPropagation(); fn(); };
  // Copy the WHOLE entry — every declaration of the name, so an overload group copies as the group.
  const copy = () => {
    void navigator.clipboard?.writeText(
      entry.variants.map((v) => `define "${name}"${v.params ? `(${v.params})` : ""}:\n  ${v.body}`.trim()).join("\n\n"),
    );
  };
  return (
    <>
      {showAdd && (
        <div className="mw-cell-add" aria-hidden="true">
          <button className="mw-cell-add-btn" disabled title="Add a criterion with Genie — assistant coming soon"><Plus size={13} /> Add with Genie</button>
        </div>
      )}
      <div className={`mw-spec-cell ${base}${extra ? " " + extra : ""}${selected ? " selected" : ""}`}
        id={entry.domId} data-crit-name={name} onClick={() => onSelect({ kind: "define", name })}
        aria-selected={selected} role="listitem">
        {/* HIDDEN — see the twin bar on the define section above. Code and CSS stay; restoring is
            uncommenting.
        <div className="mw-cell-actions" role="toolbar" aria-label={`${name} actions`}>
          <button title="Run this criterion" onClick={stop(() => onRunCell(name))}><Play size={15} /></button>
          <button title="Copy CQL" onClick={stop(copy)}><Copy size={15} /></button>
          <button title="Focus" onClick={stop(() => onSelect({ kind: "define", name }))}><Focus size={15} /></button>
          <button title="Ask Genie — assistant coming soon" disabled><Sparkle size={15} /></button>
          <button title="Delete — read-only specification" disabled><Trash size={15} /></button>
        </div>
        */}
        {children}
        {defineResults && defineResults.length > 0 && (
          <details className="mw-spec-viewresults" onClick={(e) => e.stopPropagation()}>
            <summary>View results ({defineResults.length} subjects)</summary>
            <DefineResultsTable rows={defineResults} defineName={name} />
          </details>
        )}
      </div>
    </>
  );
}

/** APP-SPEC-MODEL — an overload group's declarations, enumerated. A name declared many times
 *  (FHIRHelpers' `ToString`, once per FHIR type) is ONE concept in the specification, so it reads as one
 *  section; the variants are real and enumerable, so they are a table rather than 251 repeated sections.
 *  Large groups open on demand — the count is the information most readers want. */
function VariantsTable({ entry }: { entry: SpecEntry }) {
  const hasReturns = entry.variants.some((v) => v.returns);
  const table = (
    <div className="mw-spec-variants-wrap">
      <table className="mw-narr-table mw-spec-variants">
        <thead>
          <tr><th>Parameters</th>{hasReturns && <th>Returns</th>}</tr>
        </thead>
        <tbody>
          {entry.variants.map((v, i) => (
            <tr key={i}>
              <td className="mono">{v.params || "none"}</td>
              {hasReturns && <td className="mono">{v.returns ?? "—"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  if (entry.variants.length <= VARIANTS_INLINE_MAX) return table;
  return (
    <details className="mw-spec-viewsrc mw-spec-variants-details" onClick={(e) => e.stopPropagation()}>
      <summary>Variants ({entry.variants.length})</summary>
      {table}
    </details>
  );
}

/** Above this many declarations the variants table is a disclosure rather than always-open — a long
 *  table would otherwise dominate the reading column. */
const VARIANTS_INLINE_MAX = 8;

// ── Per-define results table (APP-DEBUG) ──────────────────────────────────
// Extract per-subject values for a single define from the run results.
function extractDefineResults(
  results: Record<string, Record<string, unknown>> | null | undefined,
  defineName: string,
): { subject: string; value: unknown }[] | null {
  if (!results) return null;
  const rows: { subject: string; value: unknown }[] = [];
  for (const [subject, defines] of Object.entries(results)) {
    if (defineName in defines) {
      rows.push({ subject, value: defines[defineName] });
    }
  }
  return rows.length > 0 ? rows : null;
}

/** APP-VALUE-READABLE — the cell's own clamp, and nothing else.
 *
 *  The reading itself is `defineValue`'s, shared with the Workbench's top-N block so one Quantity cannot
 *  read as `{"value":139,"unit":"mm[Hg]"}` on one surface and `139 mm[Hg]` on the other. Only the LENGTH
 *  is local: this cell has no `text-overflow` of its own (the table is a notebook output, not a fixed
 *  grid), so a resource summary would set the column's width for every row. The full value is on the
 *  cell's tooltip. */
const CELL_MAX = 120;
function clampCell(text: string): string {
  return text.length > CELL_MAX ? text.slice(0, CELL_MAX - 1) + "…" : text;
}

/** Short label for a subject id (same as model.ts shortLabel, duplicated to avoid a cross-import). */
function shortSubject(id: string): string {
  if (id === "*") return "Unfiltered";
  return id.length > 16 ? id.slice(0, 8) + "…" + id.slice(-6) : id;
}

const MAX_VISIBLE_ROWS = 50;

/** APP-DEBUG — the per-define output table, rendered as a notebook cell output below the criterion.
 *  Shows subject × value in a compact scrollable table with a truth summary bar at the top. */
function DefineResultsTable({ rows, defineName }: { rows: { subject: string; value: unknown }[]; defineName: string }) {
  const truthy = rows.filter((r) => {
    const v = r.value;
    if (v === true) return true;
    if (typeof v === "boolean") return false;
    return v != null;
  }).length;
  const total = rows.length;
  const pct = total > 0 ? Math.round((truthy / total) * 1000) / 10 : 0;
  const visible = rows.slice(0, MAX_VISIBLE_ROWS);
  const truncated = rows.length > MAX_VISIBLE_ROWS;

  return (
    <div className="mw-define-results" onClick={(e) => e.stopPropagation()}>
      <div className="mw-dr-summary">
        <span className="mw-dr-bar" aria-hidden="true">
          <i style={{ width: `${Math.min(pct, 100)}%` }} />
        </span>
        <span className="mw-dr-stat"><b>{truthy}</b> / {total} truthy ({pct}%)</span>
        <span className="mw-dr-name">{defineName}</span>
      </div>
      <div className="mw-dr-table-wrap">
        <table className="mw-dr-table">
          <thead><tr><th>Subject</th><th>Value</th></tr></thead>
          <tbody>
            {visible.map((r) => {
              const read = readValue(r.value);
              return (
                <tr key={r.subject} className={r.value === true ? "t" : r.value === false || r.value == null ? "f" : ""}>
                  <td className="sid" title={r.subject}>{shortSubject(r.subject)}</td>
                  <td className="val" title={read.raw}>{clampCell(read.summary)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {truncated && <div className="mw-dr-trunc">{rows.length - MAX_VISIBLE_ROWS} more rows not shown</div>}
      </div>
    </div>
  );
}
