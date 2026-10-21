// APP-WORKBENCH / APP-INSPECTOR — the right pane.
//
// THE RULE: this pane holds assertions ABOUT the document, never a second rendering OF it.
//
// ITS SCOPE IS DATA ABOUT THE SELECTION, AND ONLY THAT. Neither occupant is about the selected
// thing's data: the PATIENT LENS was a member list whose "glass-box as a MODE, not a panel"
// (`APP-PATIENT-FOCUS`) contradicted this panel's model; the ASSISTANT was a disabled shell
// reserving tab space for conversations not wired yet. Neither is relocated because the patient
// lens was per-member run tracing (runs have their own surface) and the assistant needs a home
// elsewhere when the substrate lands.
//
// WHAT TOOK THE STRIP IS THE DATA ITSELF. The Data tab had grown into a scroll of five unlike blocks —
// values, code hits, a temporal spread, demographic cuts, departures — and a reader looking for the age
// band scrolled past three charts to reach it. Those blocks ARE the pane's tabs now, which is the same
// content reachable in one click instead of one scroll, and it costs no new derivation: every tab reads
// the SAME `DefineProfile` payload the one tab did. A tab whose block the profile does not carry is
// DISABLED rather than absent (`APP-STABLE-LAYOUT` — a strip that changes shape with the selection
// makes the reader re-find every tab), and says why when you land on it.
//
// It was drafted as an INSPECTOR keyed to the criterion selected in the Specification. That model died
// of the Specification's own success: a selected define now shows, in the reading column and at the
// reader's eye, everything an inspector would have repeated — its what/why/how triple with per-field
// provenance, its narrative devices (SPEC-NARRATIVE-DEVICES), its CQL sliced at the ELM locator with
// every claim cited to a line span, its refs as real links (SPEC-ANCHOR), its metric and its badges.
// A mirror of that has nothing left to say, and two surfaces rendering one element is precisely the
// failure APP-SPEC-MODEL exists to prevent (the page called an entry `def` while the nav called the
// same entry `fn`, because each decided for itself what it was).
//
// So selection SCOPES this pane. It does not POPULATE it. The scope is resolved ONCE, in the chip at
// the pane's head, and both tabs read it — rather than each tab re-deciding what "selected" means.
//
// The tabs, each holding something the reading column is constrained against holding:
//
//   Details      what the define RETURNS (from the ELM — real today) and what it actually produced: the
//                top values with their shares, the null rate, the elements-per-subject spread. `Preview`
//                was rejected because with a Data tab beside it, it reads as the place the data was —
//                which is the one thing it is not. `Overview` was rejected — the Specification already
//                has an Overview section (`SPEC-OVERVIEW-HEADINGS`) and the word is spent.
//   Demographics who the subjects are — sex, age band, and whatever else the Patient binding gives.
//                EVERY FIGURE IN IT IS REAL (`insights/profile.py` derives the strata from this
//                evaluation). The name promises only this content; it held the temporal spread once,
//                which was fabricated (the ENGINE knows date_property per type; this layer would guess),
//                so the name shifted to match the honest content. Temporal is kept, exported and
//                unmounted, for when the retrieve contract surfaces `date_property`. Held
//                APART from the element-grain cuts by `APP-PROFILE-STRATA`: one patient with nine
//                encounters contributes nine to an element cut and one to a subject cut, so a single
//                list would invite comparing a bar in one against a bar in the other.
//   Terminology  what the selection binds — its value sets, their code hits (`APP-LIVING-CHARTS`)
//                and any direct reference codes. Structural first, hits as enrichment, so a value set
//                bound but never exercised still appears; that is a finding, not an absence.
//
// DEPARTURES IS EXPORTED AND UNMOUNTED (`APP-ANALYTICS`). It answers operational questions about the
// POPULATION rather than readings of the document, and is specified into Analytics. It stays to keep
// `APP-DECISIVE-COUNT` wired (the only implementation) rather than deleting a working surface. Do not
// EXTEND it in the meantime — the `passed`-vs-`reached` base mismatch travels with it.
//
// The IDENTITY CARD, IMPACT, and REVIEW tabs are GONE. The identity card held read-once facts now on
// the page; IMPACT restated navigation already shown on nav rows; REVIEW held coverage metrics and a
// placeholder for APP-NARRATIVE-VALIDATE, which is specified but unbuilt. `Returns` replaced the card:
// equally compact, and it changes with the selection. When the validation check lands it needs a
// surface; nothing here reserves the slot for it in the meantime.

import { useEffect, useMemo, useRef, useState } from "react";
import type { Definition } from "../catalog.ts";
import { fetchDefineProfile, peekDefineProfile, type ResultReadScope } from "../library-client.ts";
import { useRevision } from "../use-revision.ts";
import { closureOf, type LogicNode } from "../authoring/logicGraph.ts";
// `Users` / `CalendarRange` were the Demographics / Timing tab glyphs; both are groups of the Data tab
// now. `Hash` came back as TERMINOLOGY's glyph — it was Codes', which is what that tab is made of.
import { ArrowDown, ArrowUp, Database, Hash, List, Minus, Plus,
  TableProperties, Users } from "../icons.tsx";
import type {
  DefineProfile, ProfileBin, ProfileBlock, ProfileSample, ProfileStratum, ProfileValueSet,
} from "../wire.ts";
import { readValue } from "./defineValue.ts";
import type { Funnel, Selection } from "./model.ts";
import { entryForName, type SpecEntry, type SpecModel } from "./specModel.ts";
import { valueShapeOf, type MetricValueShape, type SpecDocument, type SpecMetric } from "./specDocument.ts";
import type { ReferenceSet, SpecRetrieve } from "./specRetrieves.ts";
import { formatShare, metricTitle, resolveMetric, type MetricSet } from "./specMetrics.ts";
import { PaneControls, PaneRail } from "./NavPane.tsx";
import type { PaneSize } from "./model.ts";
import { partitions, rankedCriteria, type FlowGateDto } from "./specFlow.ts";

/* THREE TABS: Details · Data · Terminology.
   `APP-INSPECTOR` had split one Data tab into five — Preview · Demographics · Codes · Timing ·
   Departures — which measured ~450px of labelled tabs in a pane whose `normal` width floors at 300px.
   The strip could not show them all, so the pane-state control was pushed off the edge entirely and the
   tabs a reader could see depended on how wide their window happened to be. A tab strip that does not
   fit is not a tab strip.

   The grouping follows what each tab is ABOUT, and each has a different source. **Details** reads the
   ELM (real with no run). **Data** is distribution — the profile's subject-grain strata and its temporal
   bins. **Terminology** is what the selection BINDS, off the retrieves, with the profile's code hits
   layered on. Codes moved out of Data and into Terminology because a value set is not a distribution:
   it is part of what the logic SAYS, and it belongs beside the direct codes it is an alternative to.
   Departures left the pane entirely (`APP-ANALYTICS`). */
type WorkbenchTab = "details" | "demographics" | "terminology";

const TABS: { id: WorkbenchTab; label: string; icon: typeof Database }[] = [
  { id: "details", label: "Details", icon: TableProperties },
  { id: "demographics", label: "Demographics", icon: Users },
  { id: "terminology", label: "Terminology", icon: Hash },
];

/** The graph namespaces its ids by kind so a value set and a define never collide. */
const nodeIdOf = (e: SpecEntry) => (e.kind === "function" ? `fn:${e.name}` : `def:${e.name}`);

/** APP-DATA-PROFILE — the evaluation a profile is a fact ABOUT. */
export interface ProfileScope extends ResultReadScope {
  dataSourceId: string;
  periodStart?: string;
  periodEnd?: string;
  /** RUN-ADDRESS-BY-LIBRARY — the run's draft key, when the CQL run is not the published version. */
  draft?: string;
}

/** SPEC-REF-LENS / APP-INSPECTOR — everything the inspector's body needs, as ONE object.
 *
 *  It is a bundle for the reason `ReadLens` is one: the second surface that renders this body is the
 *  reference lens, which lives inside running text and is reached through half a dozen prose renderers.
 *  Eight props threaded separately down that path is how one of them comes to be forgotten on one route —
 *  and a forgotten `scope` here does not fail loudly, it makes a lens quietly say "no profile". */
export interface InspectorContext {
  /** `name@version` — the measure the profile is fetched under. */
  definitionId: string;
  spec: SpecModel | null;
  doc: SpecDocument | null;
  /** APP-SAMPLE-METRICS — the same figures the document and the nav render, so no two surfaces
   *  report a different count for one define. */
  metrics: MetricSet | null;
  live: boolean;
  funnel: Funnel | null;
  /** Null with no data source selected, and then nothing is asked for: a profile without a scope has
   *  several different true answers, and the compute pill states that absence once for the document. */
  scope: ProfileScope | null;
  onNavigate: (sel: Selection) => void;
  /* NO `onAsk`. The corner holds a PAIR of verbs; a third is a toolbar. The assistant reaches this
     reference through the pane (Pin to inspect, then ask), not through the lens. */
}

/** APP-DATA-PROFILE — the profile for one scoped define, and the ONE place it is fetched.
 *
 *  BOTH SURFACES THAT SHOW A PROFILE CALL THIS. The inspector pane fetches for its selection and the
 *  reference lens fetches for the name a reader pressed (`SPEC-REF-LENS`), and they must not be two
 *  request patterns with two answers to "is it loading" — that is how the pane and a popover over it come
 *  to disagree about the same define.
 *
 *  CACHED IN THE DATA LAYER (`PERF-CLIENT-REVISION`), and this comment used to refuse a cache here —
 *  correctly, for the conditions that held when it was written. It said a `Map` "would reintroduce
 *  exactly the defect `PERF-CACHE-REVISION` was written to remove: a define's profile stayed stale for a
 *  minute AFTER the run that changed it, with nothing able to invalidate it". **That was true of every
 *  client cache available at the time**, because there was no invalidation signal a client could read:
 *  the revision existed only inside an `ETag`, which is addressed to the browser's cache and unreadable
 *  from the SPA. With nothing to bust against, any client cache really was a TTL by another name, and
 *  refusing one was the right call.
 *
 *  The revision is now published to the application (`X-CqlDatabricks-Revision`), so a cache can be
 *  validated by it rather than by a clock — `data-cache.ts` has no clock at all, and an entry lives
 *  exactly as long as the SERVER says it stands. This resolves the old concern about stale entries.
 *
 *  The old reasoning was also right that the server work was already gone — this route is on the
 *  middleware's allowlist, so a duplicate open was a bodyless 304 decided before the handler ran. What
 *  it could not have known is that the saving was invisible to the reader: `no-cache` FORBIDS the
 *  browser from answering without a round trip, so the pane still blanked and still waited.
 *
 *  `peek` is therefore the initial state, not a fetch result: on a remount that the revision has not
 *  invalidated, the first render already has the profile and there is no frame in which the pane is
 *  empty. That is the whole point — clicking away and back re-renders immediately.
 *
 *  NO SCOPE, NO REQUEST — see `InspectorContext.scope`. */
export function useDefineProfile(definitionId: string, entry: SpecEntry | null, scope: ProfileScope | null) {
  /** What the data cache already holds for this scope, or `undefined` on a miss. Computed on EVERY
   *  render rather than once, because the effect below runs AFTER the first paint: a value read only at
   *  mount would still leave one blank frame on the render that follows a scope change. */
  const cached = (() => {
    const [name, version] = definitionId.split("@");
    if (!entry || !name || !version || !scope) return undefined;
    return peekDefineProfile(name, version, entry.name, entry.library, scope);
  })();

  const [profile, setProfile] = useState<DefineProfile | null>(cached ?? null);
  const [loading, setLoading] = useState(false);

  /** PERF-CLIENT-REVISION — a run finishing anywhere reruns this effect. Named as a DEPENDENCY rather
   *  than handled with a subscription of its own: the cache has already cleared by the time this
   *  changes, so the rerun misses, refetches through the ordinary path, and cannot diverge from what a
   *  mount would have done. */
  const revision = useRevision();

  /** WHICH QUESTION this is. Blanking is correct when the question changes and WRONG when only its
   *  answer might have: showing the previous define's profile under this one's name is the
   *  two-surfaces-disagreeing failure, but blanking a still-accurate figure because a scheduled run
   *  touched something else is a flash for nothing. So the two cases are told apart explicitly. */
  const question = `${definitionId}|${entry?.key ?? ""}|${scope?.dataSourceId ?? ""}`
    + `|${scope?.periodStart ?? ""}|${scope?.periodEnd ?? ""}`;
  const asked = useRef(question);

  useEffect(() => {
    const changedQuestion = asked.current !== question;
    asked.current = question;

    const [name, version] = definitionId.split("@");
    if (!entry || !name || !version || !scope) {
      setProfile(null); setLoading(false); return;
    }
    // A HIT PAINTS AND STOPS. The entry only exists while the server's revision says it stands
    // (`data-cache.ts`), so there is nothing to revalidate — asking again would re-run the read to be
    // told what the revision already told us.
    const hit = peekDefineProfile(name, version, entry.name, entry.library, scope);
    if (hit !== undefined) { setProfile(hit); setLoading(false); return; }

    let live = true;
    setLoading(true);
    // Cleared ONLY for a new question. A revision-driven rerun keeps the figure on screen and replaces
    // it when the new one lands — stale for one round trip, which is what it already was a moment ago,
    // rather than blank for one round trip, which it never needed to be.
    if (changedQuestion) setProfile(null);
    fetchDefineProfile(name, version, entry.name, entry.library, scope)
      .then((p) => { if (live) setProfile(p); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [question, revision]); // eslint-disable-line react-hooks/exhaustive-deps
  return { profile, loading };
}

/** APP-PANE-TABS — which inspector tab is open. Held by the CALLER rather than by `InspectorScope`
 *  because the pane needs it for a second control: collapsed, the pane is a RAIL of these same glyphs and
 *  it has to mark the active one (`APP-PANE-STATE`), which it cannot do for state it does not hold. */
export function useInspectorTab() {
  const [tab, setTab] = useState<WorkbenchTab>("details");
  return { tab, setTab };
}

/** APP-INSPECTOR — the chip, the name and the owning library of whatever is scoped: the pane's
 *  identity, and the lens's.
 *
 *  ONE IDENTITY LINE FOR BOTH SURFACES. The pane puts its pane-state control after it and the lens puts
 *  its two verbs there (`SPEC-REF-LENS`), so the trailing controls are the caller's — but which chip a
 *  population gets, and the fact that the name navigates, are facts about the SELECTION rather than about
 *  either surface, and a second copy would be free to render `def` where the other rendered `pop`.
 *
 *  THE LIBRARY IS AN EYEBROW UNDER THE NAME, in both pinned and popped readings. A name is only
 *  unique WITHIN a library — the model qualifies its own keys `library + name` (`specModel.ts`) — so
 *  a scope line printing the name alone states an identity it cannot guarantee. A measure with includes
 *  routinely carries two `Denominator`s. What the Details tab reports is what this define RETURNED
 *  (changing with the evaluation); which library DECLARES it (unchanging). **A figure is not an
 *  identity.** The declaration belongs where the name is, not one tab-press away. The pane costs a
 *  press; the popped reading, whose whole point is answering without one, cannot afford the same.
 *
 *  UNDER the name, not beside it. Beside it, the library competes with the name for the row a reader
 *  scans first and pushes the trailing controls in by its own length; under it, it reads as the
 *  qualification it is. It takes `.mw-reflens-lib`'s declaration verbatim — the mono micro eyebrow the
 *  terminology lens already spends on this exact fact — rather than a second one of its own: "which
 *  library owns this" appearing in two registers inside one popover is the drift the shared
 *  `InspectorIdentity` exists to prevent, one level down. Mono because a library name is a machine
 *  identifier rather than a word of prose, which is the one sanctioned mono use in chrome
 *  (`design-system.md` section 1), and weight 500 at most — DM Mono has no bold face.
 *
 *  A READ HAS NO LIBRARY OF ITS OWN, and none is invented. A retrieve is declared by the clauses that
 *  ask for it, which may sit in several libraries at once (`ReadScope`'s "Asked for by"), so an eyebrow
 *  here would have to pick one and would be wrong whenever it mattered (`SPEC-METRIC`: an absence is
 *  stated, never filled in). */
export function InspectorIdentity({ entry, read, onNavigate }: {
  entry: SpecEntry | null; read: SpecRetrieve | null; onNavigate: (sel: Selection) => void;
}) {
  if (entry) {
    const kind = entry.kind === "function" ? "fn" : entry.kind === "population" ? "pop" : "def";
    return (
      <>
        <span className={"mw-spec-kind " + kind}>{kind}</span>
        <button className="nm" onClick={() => onNavigate({ kind: "define", name: entry.name })}
          title="Go to this definition in the Specification">{entry.name}</button>
        <span className="mw-ident-lib" title={`Declared in ${entry.library}`}>{entry.library}</span>
      </>
    );
  }
  if (read) {
    return (
      <>
        <span className="mw-spec-kind rd">rd</span>
        <button className="nm" onClick={() => onNavigate({ kind: "retrieve", key: read.key })}
          title="Go to this read in Data requirements">{read.type}</button>
      </>
    );
  }
  return <span className="all">Whole document</span>;
}

/** APP-INSPECTOR / SPEC-REF-LENS — the tab strip and the tab body, over one scoped thing.
 *
 *  THIS IS THE INSPECTOR, AND IT IS NOT THE PANE. The pane is a shell — an aside, a width, a scope row, a
 *  collapse rail — and what a reader actually reads inside it is this. Splitting them is what lets the
 *  reference lens BE the inspector rather than resemble one: a popover cannot host a pane, but it can host
 *  this, and then "pinned and popped show the same thing" is a property of there being one component
 *  rather than a promise two components keep by hand.
 *
 *  WHY THAT MATTERED ENOUGH TO SHARE. The lens used to state the absence instead — *"Distributions and
 *  cuts for this reference are in the inspector — pin it to see them here"* — which was honest but made a
 *  reader pay the pane's price for the pane's answer: pinning MOVES the selection, and the selection is
 *  also the document's you-are-here mark, so asking what a name produced cost them their place. The whole
 *  point of a lens that opens inside a criterion is that it does not do that.
 *
 *  It owns the FETCH and the availability rule; it does not own which tab is open (`useInspectorTab`). */
export function InspectorScope({ ctx, entry, read, tab, onTab }: {
  ctx: InspectorContext;
  entry: SpecEntry | null;
  /** SPEC-NAV-COMPOSITION — the other thing a reader can select. It reaches Details, like a define does,
   *  and the tabs it cannot answer disable rather than disappear. */
  read: SpecRetrieve | null;
  tab: WorkbenchTab;
  onTab: (t: WorkbenchTab) => void;
}) {
  const { profile, loading } = useDefineProfile(ctx.definitionId, entry, ctx.scope);

  // EVERY TAB IS ALWAYS SELECTABLE. Empty tabs stay in the strip because a disabled control that
  // does not say why is indistinguishable from a broken one (`design-system.md` section 2.1). The tab
  // body states why it is empty — better than a hover title could — so there is no reason to hide a
  // tab's tab. The strip is now identical in shape AND behaviour for every selection (`APP-STABLE-LAYOUT`).
  // With no data source at all, Demographics and Details blame the definition, not the lack of scope,
  // so `hasScope` splits the missing-data message from the missing-selection message (`SPEC-METRIC`).
  // This is safe because the two truths apart are unambiguous.

  // Whether anything is scoped. The tab strip describes a SELECTION, so with none it has nothing to
  // describe — see the strip's own comment for why that is not a `APP-STABLE-LAYOUT` exception.
  const scoped = Boolean(entry || read);

  return (
    <>
      {/* APP-PANE-TABS — the shared pane tab strip, the same control the Define nav's
          Outline · Composition uses. The rows are plain buttons: the strip styles `.mw-wb-tabs button`,
          so there is no per-tab class to keep in step with the nav's.

          NO TAB IS EVER DISABLED (see above). Every one opens, and its body states what it has or why it
          has nothing — which is more than a disabled tab could ever convey and the same in the pane as in
          the reference lens.

          NO SELECTION IS THE ONE CASE THE STRIP IS ABSENT. The tabs describe a selection; with none,
          the strip would be three words over a body already explaining the next move. This is not an
          `APP-STABLE-LAYOUT` exception — the requirement is about a control that MOVES under a reader
          who is USING it. This one is absent when it has nothing to act on, and returns whole the
          moment a selection exists. */}
      {scoped && <div className="mw-wb-tabs" role="tablist" aria-label="Inspector">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} title={t.label}
            onClick={() => onTab(t.id)}>
            <t.icon size={13} aria-hidden="true" />{t.label}
          </button>
        ))}
      </div>}

      <div className="mw-wb-body" role="tabpanel">
        <TabBody tab={tab} spec={ctx.spec} doc={ctx.doc} entry={entry} read={read} metrics={ctx.metrics}
          live={ctx.live} profile={profile} loading={loading} hasScope={ctx.scope != null}
          funnel={ctx.funnel} onNavigate={ctx.onNavigate} />
      </div>
    </>
  );
}

export function Workbench({
  definition, spec, doc, selected, onSelect, onNavigate, retrieves = [], referenceSets = [],
  metrics, live, scope, funnel, size, onSize,
}: {
  /** APP-PANE-STATE — this pane's width, and the setter its strip's controls drive. */
  size: PaneSize;
  onSize: (s: PaneSize) => void;
  definition: Definition;
  spec: SpecModel | null;
  doc: SpecDocument | null;
  selected: Selection | null;
  onSelect: (sel: Selection | null) => void;
  onNavigate: (sel: Selection) => void;
  /** SPEC-DATA-REQUIREMENTS — every read the visible logic performs, so a selected READ resolves here
   *  against the same list the nav and the page render (APP-SPEC-MODEL). */
  retrieves?: SpecRetrieve[];
  /** SPEC-EXPLAIN-INPUTS — the server's per-define reference lists, which carry terminology a
   *  retrieve filter does not (`terminologyForDefine`). */
  referenceSets?: ReferenceSet[];
  /** APP-SAMPLE-METRICS — the same figures the document and the nav render, so the pane cannot
   *  report a different count for the define whose heading the reader just left. */
  metrics: MetricSet | null;
  live: boolean;
  /** APP-DATA-PROFILE — the evaluation the profile is a fact ABOUT. Null with no data source
   *  selected, and the pane asks for nothing: a profile without a scope has several different true
   *  answers, and the compute pill already states that absence once for the whole document. */
  scope: ProfileScope | null;
  funnel: Funnel | null;
}) {
  const { tab, setTab } = useInspectorTab();

  // The selected NAME resolves to the model's entry, which is what carries kind, library and CQL, through
  // the model's own one resolution of a bare name (`entryForName`) — the same one the Specification's
  // reference lenses resolve through, so the pane and a lens over one name cannot scope to two defines.
  const entry = useMemo(() => {
    if (selected?.kind !== "define" || !spec) return null;
    return entryForName(spec, selected.name);
  }, [selected, spec]);

  // SPEC-NAV-COMPOSITION — the other thing a reader can now select: a READ. It is not an entry and has
  // no CQL of its own, so it resolves against the retrieve list rather than the model's declarations.
  const read = useMemo(() => {
    if (selected?.kind !== "retrieve") return null;
    return retrieves.find((r) => r.key === selected.key) ?? null;
  }, [selected, retrieves]);

  // SPEC-REF-LENS — the bundle the inspector's body reads. The reference lens builds the SAME object from
  // the Specification's own props, which is what makes a pinned scope and a popped one one rendering.
  const ctx = useMemo<InspectorContext>(() => ({
    definitionId: definition.id, spec, doc, metrics, live, funnel, scope, onNavigate,
  }), [definition.id, spec, doc, metrics, live, funnel, scope, onNavigate]);

  // THE PROFILE FETCH, THE AVAILABILITY RULE AND THE TAB STRIP LIVE IN `InspectorScope`, which the
  // reference lens also renders (`SPEC-REF-LENS`). "Pinned and popped show the same thing" holds
  // because there is one component, not two kept in step. What is left here is the PANE: an aside,
  // a width, a scope row, and a collapse rail.

  // A SELECTED READ KEEPS THE PANE'S SHELL. A read has no profile and no metric, so the tabs are
  // empty for it — as they are for many defines. `APP-STABLE-LAYOUT` forbids a strip that changes
  // shape; removing it entirely was the strongest form of that violation and read as a bug (controls
  // disappear with no explanation). The read's content is now the PREVIEW tab's body, the other tabs
  // behave as they do for a boolean define, and absence is stated in words, never implied
  // (`SPEC-METRIC`).

  // APP-PANE-STATE — collapsed is a RAIL of this pane's own tab glyphs, not clipped content;
  // clicking one expands the pane and selects that tab in a single click.
  if (size === "collapsed") {
    return (
      <aside className="mw-workbench" aria-label="Workbench">
        <PaneRail side="wb" tabs={TABS} active={tab}
          onPick={(t) => { setTab(t as WorkbenchTab); onSize("normal"); }}
          onExpand={() => onSize("normal")} />
      </aside>
    );
  }

  return (
    <aside className="mw-workbench" aria-label="Workbench">
      {/* NO identity card. Steward / Includes / Defines / Value sets was read-once information holding
          permanent real estate, and all of it is on the page already. What replaced it is `Returns`
          inside the Data tab — equally compact, and it actually changes with the selection. */}

      {/* One scope chip for the whole pane. Every tab reads it; none re-derives it — and a READ
          resolves here too, not carrying a second chip in its own body.

          THE PANE CONTROL LIVES HERE. An X at the end of a pane's header row reads as CLOSE THIS PANE,
          but clearing a selection is a different act — one this pane has no claim on (selection is
          made in the nav and document, unmade by selecting something else). This position now holds
          the pane control, and the tab strip loses its trailing control. */}
      {/* The chip and the name are `InspectorIdentity` — the same line the reference lens heads its box
          with, so one selection cannot be a `pop` here and a `def` there. What stays the PANE's is what
          follows it: the pane-state control. NO pane control in the tab strip any more — it moved up here,
          where the X used to sit, and two of them would be two targets for one job. The nav's strip keeps
          its own: that pane has no scope row to move it to. */}
      <div className="mw-wb-scope">
        <InspectorIdentity entry={entry} read={read} onNavigate={onNavigate} />
        <PaneControls side="wb" size={size} onSize={onSize} />
      </div>

      <InspectorScope ctx={ctx} entry={entry} read={read} tab={tab} onTab={setTab} />

      {/* APP-DEBUG IS NOT HERE. It was a `Run output` disclosure at the pane's foot with the raw
          engine result. Because it sat BELOW the tab body rather than inside it, it appeared under
          every tab (reported as "why does Terminology show Run output?"). Details, Demographics and
          Terminology now report what a run produced, so a JSON dump is a developer's console, not a
          clinical surface. The `run` payload is unchanged and unchanged code is `git revert` away. */}
    </aside>
  );
}

/** SPEC-NAV-COMPOSITION — the pane, scoped to a READ.
 *
 *  WHAT THIS CAN HONESTLY SAY, AND WHAT IT CANNOT. Everything here is DERIVED and exact: the resource,
 *  the terminology that filters it, the `mustSupport` paths the translator says the logic touches, and
 *  every call site with the line span of the clause that asked (`SPEC-DATA-REQUIREMENTS`). What is NOT
 *  here is a row or subject COUNT.
 *
 *  ITS ABSENCE IS SETTLED. Read materialization is retired (`SPEC-READ-METRICS`), so there are no
 *  per-read tables to count. The engine's `retrieve_<Type>` mount is per TYPE with ADVISORY predicates
 *  (`DATA-RETRIEVE-VIEW`); applying the value-set filter here would re-implement membership semantics
 *  the engine owns, which `APP-DECOUPLE` forbids. This states what the read ASKS FOR and plainly that
 *  nobody counted — absence stated once, never fabricated (`SPEC-METRIC`). */
function ReadScope({ read, onNavigate }: {
  read: SpecRetrieve; onNavigate: (sel: Selection) => void;
}) {
  const filter = read.valueSet ?? read.code;
  return (
    <>
      {/* No scope chip of its own — the pane's shared one names the read now, the same one that names a
          define. Two chips for one selection is the drift `APP-SPEC-MODEL` exists to prevent. */}
      <Lead>
        A <b>read</b>: what this measure goes and gets from <b>{read.type}</b>, and what counts as a hit.
      </Lead>
      <Group label="Binding">
        <div className="mw-wb-kv">
          <div className="r"><span>Resource</span><span className="v">{read.type}</span></div>
          <div className="r"><span>{read.codeProperty ?? "code"} in</span>
            <span className={"v " + (filter ? "" : "none")}>{filter ?? "every record"}</span></div>
        </div>
      </Group>
      <Group label={`Must support · ${read.mustSupport.length}`}>
        {read.mustSupport.length === 0
          ? <p className="mw-wb-lead">The published library carried no <b>dataRequirement</b> for this
              read. That is not the same claim as "the logic touches nothing".</p>
          : <div className="mw-wb-chips">
              {read.mustSupport.map((path) => (
                <span key={path} className="mw-wb-chip st">{path}</span>
              ))}
            </div>}
      </Group>
      <Group label={`Asked for by · ${read.usedBy.length}`}>
        <div className="mw-wb-chips">
          {read.usedBy.map((site, i) => (
            <button key={`${site.library}::${site.define}::${i}`} className="mw-wb-chip"
              title={`${site.library} · ${site.define}`}
              onClick={() => onNavigate({ kind: "define", name: site.define })}>{site.define}</button>
          ))}
        </div>
      </Group>
      <Group label="Produced">
        {/* SPEC-METRIC — absence is STATED, never a zero and never a guess. This is the ONE place the
            product states it: the reference lens over the same read shows what it has and stays silent
            about what it does not, because from there it cannot tell which absence it is looking at. */}
        {/* NO REQUIREMENT ID IN READER-FACING PROSE. The REASON belongs on the page; the id belongs
            in this comment, where the next engineer reads it (`SPEC-READ-METRICS`). */}
        <p className="mw-wb-lead">
          Not counted, and not countable here. A read is not stored as its own table, so there is nothing
          to count rows of. Counting <em>this</em> read would mean re-applying its filter, and what that
          filter means is the engine&rsquo;s to decide, not this app&rsquo;s. Volume questions belong to
          a define&rsquo;s own figure, above.
        </p>
      </Group>
    </>
  );
}

// ── the data views ────────────────────────────────────────────────────────────────────────────────

/** APP-INSPECTOR — which view is open, over ONE payload.
 *
 *  ONE FETCH, SEVERAL VIEWS. Every tab renders a slice of the same `DefineProfile` the pane already
 *  fetches for the selection, so switching tabs costs nothing and no two tabs can disagree about the
 *  define they are describing. This is a rearrangement of what the single Data tab scrolled through,
 *  not new data.
 *
 *  TWO SOURCES BEHIND IT, AND THE SPLIT IS DELIBERATE. `Returns` is derived CLIENT-SIDE from the ELM:
 *  the translator emits result types (`EnableResultTypes`) and `elmDefines.ts` already parses them, so
 *  it is real today and a server-side second copy could disagree with it (`APP-SPEC-MODEL`, one
 *  model per determination). Everything else requires a RUN and comes from `/defines/{define}/profile`,
 *  which serves SAMPLE data stamped `provenance: "sample"` until the derivation is wired
 *  (`APP-DATA-PROFILE`).
 *
 *  The sample mark is not decoration. A distribution is indistinguishable from a plausible invention
 *  once it is drawn as a bar, so the provenance travels on the wire and is rendered wherever the
 *  figures are — that is what keeps `APP-NO-UI-MOCK` enforceable while the pane is designed. */
function TabBody({ tab, spec, doc, entry, read, metrics, live, profile, loading, hasScope, funnel,
  onNavigate }: {
  tab: WorkbenchTab;
  spec: SpecModel | null; doc: SpecDocument | null; entry: SpecEntry | null;
  /** SPEC-NAV-COMPOSITION — the other thing a reader can select. It reaches the DETAILS tab, like a
   *  define does; the tabs it cannot answer open and say so. */
  read: SpecRetrieve | null;
  metrics: MetricSet | null; live: boolean;
  profile: DefineProfile | null; loading: boolean;
  /** Whether a data source is selected at all (`APP-DATA-PROFILE`). It separates two absences a null
   *  profile cannot: nobody has chosen an evaluation, versus this evaluation produced nothing for this
   *  define. Before every tab was made selectable the difference was hidden by a disabled tab; now a
   *  reader lands on the body and it has to be honest about which one it is (`SPEC-METRIC`). */
  hasScope: boolean;
  funnel: Funnel | null;
  onNavigate: (sel: Selection) => void;
}) {
  if (!spec) return <Pending>Reading the model…</Pending>;
  if (read) return <ReadScope read={read} onNavigate={onNavigate} />;
  if (!entry) return <NoScope spec={spec} funnel={funnel} />;

  if (tab === "demographics") {
    return <Demographics profile={profile} loading={loading} hasScope={hasScope} />;
  }
  if (tab === "terminology") {
    return <Terminology profile={profile} loading={loading} hasScope={hasScope} />;
  }
  return (
    <PreviewTab spec={spec} doc={doc} entry={entry} metrics={metrics} live={live}
      profile={profile} loading={loading} hasScope={hasScope} onNavigate={onNavigate} />
  );
}

/** APP-DATA-PROFILE / SPEC-METRIC — no data source is selected, so nothing was asked for.
 *
 *  ONE SENTENCE, ONE PLACE. Three tabs reach this state and the wording must not drift into three
 *  different accounts of one condition — which is exactly how "No profile for this definition" came to be
 *  printed at a reader whose definition was fine and whose data source was unset. It names the control that
 *  fixes it, because an absence a reader cannot act on is only half stated. */
const NoData = () => (
  <Empty>No data source is selected, so nothing has been profiled yet — choose one in the
    <b> Compute</b> menu and this fills in.</Empty>
);

/** Nothing selected. The pane is scoped BY selection, so this is the pane's resting state rather than
 *  an error, and it says what a selection would buy. */
function NoScope({ spec, funnel }: { spec: SpecModel; funnel: Funnel | null }) {
  return (
    <>
      <Lead>
        Nothing is selected. Pick a definition in the nav or the Specification and this reads what it
        returns, what it produced, and how that is distributed.
      </Lead>
      <Group label="Evaluated">
        <div className="mw-wb-kv">
          <div className="r"><span>Subjects</span>
            <span className={"v " + (funnel ? "tnum" : "none")}>
              {funnel ? funnel.total.toLocaleString() : "not computed"}
            </span></div>
          <div className="r"><span>Definitions</span>
            <span className="v tnum">{spec.libraries.reduce((n, l) => n + l.entries.length, 0)}</span></div>
        </div>
      </Group>
    </>
  );
}

/** DEMOGRAPHICS — who the subjects are: sex, age band, and whatever else the Patient binding gives.
 *
 *  EVERY FIGURE HERE IS REAL. `insights/profile.py` derives the subject strata from THIS evaluation, by
 *  joining the bound Patient retrieve to the subjects the define is true of — the same binding that is
 *  already the authority for the evaluated-subject total, so the cohort and its breakdowns cannot
 *  disagree. That is why the tab is named for what it holds rather than the generic "Data" it inherited:
 *  the name now promises exactly one thing, and the promise is kept.
 *
 *  THE TEMPORAL SPREAD IS NOT HERE. It was the one fabricated block — `insights/sample.py` invents it,
 *  because the spread needs date_property per FHIR type, which the ENGINE knows and this layer would
 *  guess (`APP-DECOUPLE`). A tab that is real except for one invented histogram cannot be described
 *  honestly. `Temporal` is kept, exported and unmounted, for when the retrieve contract surfaces
 *  `date_property`.
 *
 *  Element-grain cuts stay out, as before: one patient with nine encounters contributes nine to an
 *  element cut and one to a subject cut, so a single list would invite comparing a bar in one against a
 *  bar in the other — the `SPEC-METRIC` two-grains problem in chart form. They live under Details,
 *  beside the values they are cuts OF. */
function Demographics({ profile, loading, hasScope }: {
  profile: DefineProfile | null; loading: boolean; hasScope: boolean;
}) {
  if (loading) return <Pending>Profiling…</Pending>;
  if (!hasScope) return <NoData />;
  if (!profile) return <Empty>No profile came back for this definition on this evaluation.</Empty>;

  const cuts = (profile.strata ?? []).filter((s) => s.grain === "subject");
  if (cuts.length === 0) {
    return <Empty>No subject-level cuts for this definition — sex and age band are read from the bound
      Patient relation, which this evaluation has not joined.</Empty>;
  }
  return (
    <>
      <SampleMark profile={profile} />
      <Group label={`By ${cuts[0]?.unit ?? "subject"}`}
        hint="Cuts over the subjects this definition is true of.">
        {cuts.map((s) => (
          <StratumBlock key={s.id} stratum={s} fallback={isSampled(profile, "strata")} />
        ))}
      </Group>
    </>
  );
}

/** TERMINOLOGY — the coded values the selection's records actually carry, one block per curated path.
 *
 *  SPEC-TERMINOLOGY-EXTRACT. The server curates which coded fields are worth reading per (data model,
 *  resource type) and counts them off the define's own result relation — so a Condition reads as
 *  Diagnosis · Category · Clinical status · Body site rather than as one undifferentiated pile of codes.
 *  THE PATH NAMES THE PART OF THE RECORD, which is the whole reason the sections exist: `clinicalStatus
 *  = active` on every row is visibly administrative because it is labelled as clinical status, instead of
 *  sitting next to a SNOMED diagnosis looking like a peer.
 *
 *  WHAT THIS IS NOT. It does not report which value set qualified a record. That needs the filter
 *  re-applied, which this layer may not do (`SPEC-CLAUSE-PROBE`, rejected) — and it does not need to,
 *  because a returned record survived every filter by construction, so its codes qualified whether the
 *  test lived in the retrieve or in a `where`. The value-set coverage question (declared against fired)
 *  is a corpus report and belongs to Data Readiness, not to a reading pane.
 *
 *  AN EMPTY SECTION IS RENDERED, not dropped. The server named this path as worth reading, so finding
 *  nothing at it is a finding — the opposite call from a discovery design, and the one curation earns. */
function Terminology({ profile, loading, hasScope }: {
  profile: DefineProfile | null; loading: boolean; hasScope: boolean;
}) {
  const sections = profile?.terminology ?? [];
  if (loading) return <Pending>Reading the codes…</Pending>;
  if (!hasScope) return <NoData />;
  if (!profile) return <Empty>No profile came back for this definition on this evaluation.</Empty>;
  if (sections.length === 0) {
    return <Empty>Nothing coded to read here — this definition returns a value rather than records, so
      there is no resource carrying codes.</Empty>;
  }
  // Grouped by resource type only when there is more than one: a relation holding two types needs the
  // heading to say which, and one type would make every block carry a redundant word.
  const types = [...new Set(sections.map((s) => s.resourceType))];
  return (
    <>
      {sections.map((sec) => (
        <Group key={`${sec.resourceType}::${sec.path}`}
          label={types.length > 1 ? `${sec.resourceType} · ${sec.label}` : sec.label}>
          {sec.codes.length === 0 ? (
            <div className="mw-wb-flag">No codes returned.</div>
          ) : (
            /* THE SAME DISTRIBUTION THE DEMOGRAPHIC CUTS DRAW, and for the same reason: a coded field
               is a distribution over a population, so drawing it as a table asked the reader to compare
               numbers by eye where a bar does it for them. `share` is of the SECTION's own total, which
               is what `Bin` means everywhere else — never of the measure (`SPEC-METRIC`: a share states
               its base, and two bases in one pane is the failure that rule exists for). The display
               leads and the code trails it, because a clinician reads the words. */
            <Histogram label={null} bins={sec.codes.map((c) => ({
              label: c.display || c.code || "—",
              count: c.subjects,
              share: c.subjects / Math.max(sec.codes.reduce((n, x) => n + x.subjects, 0), 1),
            }))} />
          )}
        </Group>
      ))}
    </>
  );
}

/** A define is a query, and this reads it as one: what it returns, and what it produced. */
function PreviewTab({ spec, doc, entry, metrics, live, profile, loading, hasScope, onNavigate }: {
  /** Non-null by construction: `TabBody` returns before reaching here without a model and a scope, so
   *  this component has no empty states of its own to render. */
  spec: SpecModel; doc: SpecDocument | null; entry: SpecEntry;
  metrics: MetricSet | null; live: boolean;
  profile: DefineProfile | null; loading: boolean;
  /** Passed through to the Preview block, which is the one part of this tab that needs a run. Everything
   *  above it (`Returns`, the dependency degrees, the reads) is ELM-derived and real with no data at all —
   *  which is why this tab is the safe landing tab. */
  hasScope: boolean;
  onNavigate: (sel: Selection) => void;
}) {
  const graph = spec.graph ?? null;

  // Which population criteria reach this define. The one thing the retired Impact tab said that the
  // nav cannot: SPEC-PLACEMENT renders a define once at its lowest common dominator, so the section it
  // sits in does not name the populations depending on it, and the nav's closure tint shows the shape
  // of the dependency without naming its endpoints.
  const populations = useMemo(() => {
    if (!graph || !entry || !doc) return [];
    const id = entry.kind === "function" ? `fn:${entry.name}` : `def:${entry.name}`;
    return doc.populations
      .filter((p) => p.population && closureOf(graph, `def:${p.population.name}`).has(id))
      .map((p) => p.name);
  }, [graph, entry, doc]);

  // The data this define demands, from the leaves of its own closure.
  const reads = useMemo(() => {
    if (!graph || !entry) return null;
    const id = entry.kind === "function" ? `fn:${entry.name}` : `def:${entry.name}`;
    if (!graph.byId[id]) return null;
    const nodes = [...closureOf(graph, id)].flatMap((d) => graph.byId[d] ?? []);
    return {
      retrieves: nodes.filter((n: LogicNode) => n.type === "retrieve"),
      valueSets: nodes.filter((n: LogicNode) => n.type === "valueset"),
    };
  }, [graph, entry]);

  // APP-DEP-COUNTS — the define's DIRECT degree in the logic graph, in both directions. One place
  // with room to name the direction in words instead of an arrow glyph.
  const node = graph && entry ? graph.byId[entry.kind === "function" ? `fn:${entry.name}` : `def:${entry.name}`] ?? null : null;

  const metric = doc ? doc.byDefine[entry.key]?.metric ?? null : null;

  // ONE resolution of the define's result type for the whole tab. `Returns` used to resolve it privately
  // and the Preview block now needs the same answer — two resolutions of one type is how a pane comes to
  // state a type in one block and dispatch off a different one in the next (`APP-SPEC-MODEL`).
  const resultType = metric?.resultType ?? entry.variants[0]?.returns ?? null;

  return (
    <>
      {/* RETURNS — the real thing, from the ELM. Replaces the identity card: equally compact, and it
          changes with the selection, which is what the old card never did. */}
      <Returns entry={entry} metric={metric} resultType={resultType} metrics={metrics} live={live} />

      {/* The top-N sample sits directly under the type, because the type is a promise and the sample
          is the evidence for it. */}
      <SampleValues profile={profile} loading={loading} hasScope={hasScope} entry={entry}
        shape={valueShapeOf(resultType)} />

      {/* APP-DEP-COUNTS — DIRECT degree, and it says so: these are the definitions this one names and
          the ones that name it, not the transitive closures. The closure is what `Reads` below and the
          nav's selection tint show, and conflating a degree with a closure is how a reader comes to
          expect the two to agree. Colours are the closure tint's own pairing — downstream accent-ink,
          upstream info — so the direction means one thing in the nav and in this pane. */}
      {node && (
        <Group label="Dependencies">
          <div className="mw-wb-stats">
            <div className="s" title={`${entry.name} directly names ${node.deps.length} definition(s)`}>
              <span className="ic dn" aria-hidden="true"><ArrowDown size={14} /></span>
              <span className="n tnum">{node.deps.length}</span>
              <span className="l">uses</span>
            </div>
            <div className="s" title={`${node.dependents.length} definition(s) directly name ${entry.name}`}>
              <span className="ic up" aria-hidden="true"><ArrowUp size={14} /></span>
              <span className="n tnum">{node.dependents.length}</span>
              <span className="l">used by</span>
            </div>
          </div>
        </Group>
      )}

      {populations.length > 0 && (
        <Group label="Populations reached">
          <div className="mw-wb-chips">
            {populations.map((p) => (
              <button key={p} className="mw-wb-chip" onClick={() => onNavigate({ kind: "define", name: p })}>{p}</button>
            ))}
          </div>
        </Group>
      )}

      <details className="mw-wb-details">
        <summary>
          Reads
          <span className="ct tnum">
            {(reads?.retrieves.length ?? 0) + (reads?.valueSets.length ?? 0)}
          </span>
        </summary>
        {!reads || (reads.retrieves.length === 0 && reads.valueSets.length === 0)
          ? <Empty>Reads nothing directly.</Empty>
          : <div className="mw-wb-chips">
            {reads.retrieves.map((n) => (
              <span key={n.id} className="mw-wb-chip st"><Database size={11} aria-hidden="true" />{n.label}</span>
            ))}
            {reads.valueSets.map((n) => (
              <span key={n.id} className="mw-wb-chip st"><List size={11} aria-hidden="true" />{n.label}</span>
            ))}
          </div>}
      </details>

      {loading && <Pending>Profiling…</Pending>}

      {/* The VALUE profile and the ELEMENT-grain cuts, which belong beside the values they are cuts OF.
          Code hits, the temporal spread and the subject-grain cuts have tabs of their own — they are
          what a reader comes to the pane looking for, and they were three scrolls down. */}
      {profile && (
        <>
          <SampleMark profile={profile} />
          {profile.values && <ValuesBlock values={profile.values} />}
          <ElementCuts strata={profile.strata ?? []} sampled={isSampled(profile, "strata")} />
        </>
      )}
    </>
  );
}

/** SPEC-METRIC — the define's contract, from the ELM.
 *
 *  The italic bound is load-bearing, not a footnote: a define returning whole resources yields only
 *  their IDs, so the profile below can count elements and size each subject's list and cannot
 *  distribute over the resources' fields. Saying so where the type is stated stops a reader asking the
 *  charts for something no run can answer. */
function Returns({ entry, metric, resultType, metrics, live }: {
  entry: SpecEntry; metric: SpecMetric | null;
  /** Resolved once by `PreviewTab`, because the Preview block dispatches off the same type. */
  resultType: string | null;
  metrics: MetricSet | null; live: boolean;
}) {
  const type = resultType;
  const figure = resolveMetric(metrics, metric, entry.library);
  const value = figure?.subjects ?? null;
  const isFunction = entry.kind === "function";
  return (
    <div className="mw-wb-returns">
      <div className="top">
        <span className="lb">Returns</span>
        <span className="t">{type ?? "unknown"}</span>
      </div>
      {metric && !isFunction && (
        <div className="mw-wb-grains">
          <span className="grain">subject <b>{metric.subjectUnit}</b></span>
          {metric.elementGrain && <span className="grain">element <b>{metric.elementGrain}</b></span>}
          {metric.form === "cardinality-histogram" && <span className="grain">many per subject</span>}
          <span className="grain">
            {!live ? "not computed" : value == null ? "not returned" : `${value.toLocaleString()} met`}
          </span>
          {/* SPEC-ATTRITION — the share, against the base the document states beside the same figure.
              Read here so a reader who came from the heading sees the pane agreeing with it. */}
          {figure?.share != null && figure.base && (
            <span className="grain" title={metricTitle(figure, entry.name)}>
              <b>{formatShare(figure.share)}</b> of {figure.base.name}
            </span>
          )}
        </div>
      )}
      {isFunction && (
        <p className="mw-wb-bound">
          A function is evaluated per call site with arguments, not once per subject, so it has no
          population to count.
        </p>
      )}
      {!type && (
        <p className="mw-wb-bound">
          No result type in the ELM for this define — the translator emits them only with
          <code> EnableResultTypes</code>.
        </p>
      )}
      {metric?.elementGrain && (
        <p className="mw-wb-bound">
          Resources come back as IDs only — countable and sizeable, not distributable over their fields.
        </p>
      )}
    </div>
  );
}

/** PREVIEW — the top-N returned values. Evidence for the type stated above it.
 *
 *  The label is "Preview" rather than "Top values" because that is what a reader came for: the first
 *  thing anyone does with a query is look at what comes back. "Top values" described the SQL that
 *  produced the block (a `GROUP BY` ordered by count) rather than what a reader does with it, and the
 *  counts and bars beside each row already say that the block is ranked. */
function SampleValues({ profile, loading, hasScope, entry, shape }: {
  profile: DefineProfile | null; loading: boolean; hasScope: boolean; entry: SpecEntry;
  /** APP-VALUE-READABLE — whether these values AGGREGATE. See `SampleRow`. */
  shape: MetricValueShape;
}) {
  const sample = profile?.sample ?? [];
  if (entry.kind === "function") return null;
  return (
    <Group label={`Preview${sample.length ? ` · ${sample.length}` : ""}`}>
      {loading ? <Pending>Sampling…</Pending>
        /* "No sample for this definition" blamed the DEFINITION for an unset data source, on the tab a
           reader always lands on — so it was the most-read wrong sentence in the pane. */
        : !hasScope ? <NoData />
          : sample.length === 0 ? <Empty>This evaluation returned no values for this definition.</Empty>
            : sample.map((s) => <SampleRow key={s.value} sample={s} shape={shape} />)}
    </Group>
  );
}

/** APP-VALUE-READABLE — one returned value, read rather than dumped.
 *
 *  Exported for its own test: the row is what has to stop printing JSON, and asserting that through the
 *  whole pane would mean standing up a spec model, a document, a funnel and a run to reach one label.
 *
 *  The wire carries the value as a STRING and a STRUCT one arrives as `to_json` output, which is the
 *  engine boundary's decision, not an oversight (`insights/profile.py:_top_values`,
 *  `APP-DECOUPLE`) — so the pane is where a resource becomes a line a clinician can read.
 *  `defineValue` does that reading; this row is the layout of its three answers:
 *  the summary on the label, the untouched value on the tooltip, and the value's own non-empty fields
 *  under it.
 *
 *  THE SHARE BAR IS DRAWN ONLY OVER VALUES THAT AGGREGATE (`shape === "value"`). `true` in 1,204 of 1,288
 *  subjects is a distribution and the bar states it; an Encounter returned once is not one. Resources are
 *  near-unique by construction, so over rows the bar drew eight near-equal segments and invited a reader
 *  to compare frequencies that measured only how many distinct records came back — a claim nobody made and
 *  the data cannot support. The COUNT stays on every row either way: "this value came back for N subjects"
 *  What goes on `row` shape is the bar AND the count: the bar asserts a distribution that a set of records
 *  does not have, and the count is 1 on every line, which reads as data and carries none. (An earlier note
 *  here kept the count on the grounds that "a column of 1s is itself worth reading" — withdrawn, because
 *  what it tells a reader is that the define returns records, which the rows already show by being records.) The shape is dispatched off the ELM result type
 *  (`valueShapeOf`), not off each row's contents — see there for why.
 *
 *  THE WHOLE ROW IS THE CONTROL, marked by a leading `+` that becomes a `−`. It was a "FIELDS" disclosure
 *  underneath, and that was two mistakes in one: it put a second labelled control inside a row that is
 *  already one thing, and it made the target a word at the row's left edge rather than the row a reader
 *  is looking at. A row that expands in place needs no name for the act of expanding.
 *
 *  The mark LEADS the row rather than trailing it: a reader scanning a block of these looks down the left
 *  edge, and a `+` at the right edge is found only after reading past the thing it acts on.
 *
 *  A row with nothing to expand is a DISABLED button, not a plain div — the icon slot still holds its
 *  width, so the counts stay in one column down the block (the lesson `APP-DEP-COUNTS` recorded: a
 *  figure whose column wanders cannot be read down a pane), and the missing hover says there is nothing
 *  under this one without spending a word saying so. */
export function SampleRow({ sample, shape }: { sample: ProfileSample; shape: MetricValueShape }) {
  const read = readValue(sample.value);
  const [open, setOpen] = useState(false);
  const expandable = read.fields.length > 0;
  return (
    <div className={"mw-wb-meter" + (open ? " open" : "")}>
      <button className="row" disabled={!expandable} aria-expanded={expandable ? open : undefined}
        onClick={() => setOpen((o) => !o)}
        title={expandable ? (open ? "Hide this value's fields" : `Show this value's ${read.fields.length} fields`) : undefined}>
        <span className="hd2">
          {/* The expander LEADS the row. It is the row's affordance, and a reader scanning a block of them
              looks down the left edge — a mark at the right edge is found only after reading past the
              thing it acts on. It also puts the disclosure column beside the nesting it discloses. */}
          <span className="ex" aria-hidden="true">
            {expandable && (open ? <Minus size={12} /> : <Plus size={12} />)}
          </span>
          <span className="lb" title={read.raw}>{read.summary}</span>
          {/* A COUNT ONLY WHERE IT COUNTS SOMETHING. On `value` shape the rows are repeated values and the
              figure is the distribution. On `row` shape each row is one RECORD, so the count is 1 on every
              line — a column of 1s that reads as data and carries none. The earlier note here argued the
              opposite ("a column of 1s is itself worth reading"); it is withdrawn: what that column tells
              a reader is that this define returns records, which the rows already show by being records. */}
          {shape === "value" && <span className="ct tnum">{sample.count.toLocaleString()}</span>}
        </span>
        {shape === "value" && (
          <span className="mw-wb-bar"><span style={{ width: `${Math.round(sample.share * 100)}%` }} /></span>
        )}
      </button>
      {open && (
        <div className="mw-wb-kv">
          {read.fields.map((f) => (
            <div className="r" key={f.label}>
              <span>{f.label}</span>
              <span className={f.code ? "v mono" : "v"}>{f.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** APP-NO-UI-MOCK — is THIS block fabricated on this payload?
 *
 *  Per block, not per payload. The value profile and the top values are read from the evaluation while
 *  code hits and the temporal spread are still invented, and one word for the whole payload can only be
 *  wrong in one direction or the other: `run` strips the mark off invented bars, `sample` puts a false
 *  warning on real figures. A payload with no `sampled` list at all is read the old way — the whole
 *  thing is sample unless it says `run`. */
export function isSampled(profile: DefineProfile, block: ProfileBlock): boolean {
  if (profile.sampled) return profile.sampled.includes(block);
  return profile.provenance !== "run";
}

/** APP-DATA-PROFILE — the mark that heads a MIXED payload: what in it is real, and what is not.
 *
 *  It states the split once, at the top, and each invented block carries its own chip below — the
 *  caveat is the sentence, the chips are the pointing. Rendered wherever anything is fabricated, never
 *  suppressed. */
function SampleMark({ profile }: { profile: DefineProfile }) {
  const sampled = profile.sampled ?? (profile.provenance === "run" ? [] : ["values"]);
  if (sampled.length === 0) return null;
  return (
    <div className="mw-wb-sample">
      <span className="mw-pill wait">{profile.provenance === "mixed" ? "part sample" : "sample"}</span>
      <p>{profile.caveat ?? "Sample figures — no run has produced these."}</p>
    </div>
  );
}

/** The per-block chip. Sits in the block's own heading, so the mark and the bars it warns about cannot
 *  be scrolled apart. */
function BlockMark({ sampled }: { sampled: boolean }) {
  if (!sampled) return null;
  return <span className="mw-pill wait sm" title="Invented figures — no table answers this yet.">sample</span>;
}

function ValuesBlock({ values }: { values: NonNullable<DefineProfile["values"]> }) {
  return (
    <Group label="Values">
      <div className="mw-wb-kv" style={{ marginBottom: values.cardinality?.length ? 10 : 0 }}>
        <div className="r"><span>Subjects with a value</span>
          <span className="v tnum">{values.subjects.toLocaleString()}</span></div>
        {values.elements != null && (
          <div className="r"><span>Elements returned</span>
            <span className="v tnum">{values.elements.toLocaleString()}</span></div>
        )}
        <div className="r"><span>Null or empty</span>
          <span className="v tnum">{(values.nullRate * 100).toFixed(1)}%</span></div>
      </div>
      {values.cardinality && values.cardinality.length > 0 && (
        <Histogram label="Elements per subject" bins={values.cardinality} />
      )}
    </Group>
  );
}

/** APP-LIVING-CHARTS — CODE HITS: which of a value set's codes the data actually carries.
 *
 *  The vocabulary is "code hits", not "fired". A code is not an event and it does not fire; it is
 *  matched by the records a retrieve returned, and "hit" is the word every reader of a terminology
 *  report already uses for that. The wire field is still `fired` — renaming it is a contract change,
 *  and the display vocabulary is settled here first.
 *
 *  `declared` is absent until the terminology read path exists, and the headline adapts rather than
 *  printing a denominator nobody has read: "6 code hits" is true with or without it, where "6 / 0"
 *  would be a fabrication. */
function CodeHits({ vs, sampled }: { vs: ProfileValueSet; sampled: boolean }) {
  const unused = vs.declared != null ? vs.declared - vs.fired : null;
  return (
    <Group label={`Code hits · ${vs.declared != null ? `${vs.fired} / ${vs.declared}` : vs.fired}`}
      mark={<BlockMark sampled={sampled} />}
      hint={vs.oid ? `${vs.name} · ${vs.oid}` : vs.name}>
      {unused != null && unused > 0 && (
        <div className="mw-wb-flag warn">{unused} codes in {vs.name} are never hit in this population.</div>
      )}
      {vs.declared == null && (
        <div className="mw-wb-flag">
          How many codes the value set declares needs the terminology tables, which the app cannot read yet.
        </div>
      )}
      <div className="mw-wb-tw">
        <table className="mw-wb-t">
          <thead><tr><th>Code</th><th>Display</th><th className="n">Hits</th></tr></thead>
          <tbody>
            {[...vs.top].sort((a, b) => b.count - a.count).map((c) => (
              <tr key={c.code}>
                <td className="c">{c.code}</td>
                <td title={c.display}>{c.display}</td>
                <td className="n">{c.count.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Group>
  );
}

/** Temporal spread, with the look-back held apart from the period.
 *
 *  Merging them would draw the same bars and lose the only thing the chart is for: a define whose
 *  events all land in the look-back band is reading the wrong window, and that is invisible once the
 *  two are one run of bars. */
export function Temporal({ profile, sampled }: { profile: DefineProfile; sampled: boolean }) {
  const bins = profile.temporal ?? [];
  const max = Math.max(...bins.map((b) => b.count), 1);
  return (
    <Group label="When they fired" mark={<BlockMark sampled={sampled} />}
      hint="Faded bars are before the measurement period opens.">
      <div className="mw-wb-hist">
        {bins.map((b) => (
          <i key={b.label} className={b.inPeriod ? "on" : ""}
            style={{ height: `${Math.max((b.count / max) * 100, 2)}%` }}
            title={`${b.label}: ${b.count.toLocaleString()}`} />
        ))}
      </div>
      <div className="mw-wb-axis">
        <span>{bins[0]?.label ?? ""}</span>
        <span>{bins[bins.length - 1]?.label ?? ""}</span>
      </div>
    </Group>
  );
}

/** APP-PROFILE-STRATA — the ELEMENT-grain cuts only.
 *
 *  The split by grain is unchanged and is the whole point: one patient with nine encounters contributes
 *  nine to an element cut and one to a subject cut, so a single list would invite the reader to compare
 *  a bar in one against a bar in the other — the `SPEC-METRIC` two-grains problem in chart form. What
 *  changed is WHERE the halves live. The subject cuts are what a reader opens this pane for, so they
 *  took a tab of their own (`SubjectCuts`); the element cuts stay under Preview, beside the values they
 *  are cuts OF. Separated by a tab boundary rather than by two headings — a stronger separation than
 *  the one this requirement asks for, not a weaker one. */
function ElementCuts({ strata, sampled }: { strata: ProfileStratum[]; sampled: boolean }) {
  const element = strata.filter((s) => s.grain === "element");
  if (element.length === 0) return null;
  // PER CUT, not per group. Sex and age band are read from the Patient binding while the element-grain
  // cuts have no relation to read, so a heading-level mark would be wrong about one of them whichever
  // way it fell. The chip rides each cut's own summary row, next to the bars it warns about.
  return (
    <Group label={`By ${element[0]?.unit ?? "element"}`}
      hint="Cuts over the events returned — one subject can contribute many.">
      {element.map((s) => <StratumBlock key={s.id} stratum={s} fallback={sampled} />)}
    </Group>
  );
}

function StratumBlock({ stratum, fallback }: {
  stratum: ProfileStratum;
  /** What to assume for a payload predating per-stratum provenance. */
  fallback: boolean;
}) {
  const total = stratum.bins.reduce((n, b) => n + b.count, 0);
  return (
    /* OPEN BY DEFAULT. A cut collapsed to its label and total hides the only part that answers
       anything — a 204/196 sex split and a 400-strong Unknown bucket read identically, and the Unknown
       bucket exists precisely to be SEEN. Terminology's sections are not collapsible, so a collapsed
       Demographics made two tabs behave differently for no reason a reader could infer.
       `open` is written once, never state-driven, so React keeps it across re-renders and a reader who
       collapses a cut keeps it collapsed — a DEFAULT, not a lock. */
    <details className="mw-wb-stratum" open>
      <summary>
        <span className="lb">{stratum.label}</span>
        <BlockMark sampled={stratum.sampled ?? fallback} />
        <span className="ct tnum">{total.toLocaleString()} {stratum.unit}</span>
      </summary>
      {stratum.note && <p className="mw-wb-note">{stratum.note}</p>}
      <Histogram label={null} bins={stratum.bins} />
    </details>
  );
}

/** A labelled distribution. Bars ride `info`, never the accent: this is data, not state, and the accent
 *  is spent on selection (design-system section 2). */
function Histogram({ label, bins }: { label: string | null; bins: ProfileBin[] }) {
  const max = Math.max(...bins.map((b) => b.count), 1);
  // A bar chart under ~7 buckets reads better as labelled rows — the labels ("American Indian or Alaska
  // Native") do not fit under a 40px column, and a rotated axis label in a 300px pane is unreadable.
  const asRows = bins.length <= 7;
  if (asRows) {
    return (
      <div className="mw-wb-rows">
        {label && <div className="mw-wb-rows-lb">{label}</div>}
        {bins.map((b) => (
          <div className="mw-wb-meter" key={b.label}>
            <div className="hd2">
              <span className="lb">{b.label}</span>
              <span className="ct tnum">{b.count.toLocaleString()}</span>
            </div>
            <div className="mw-wb-bar">
              <span style={{ width: `${Math.round((b.count / max) * 100)}%` }} />
            </div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <>
      {label && <div className="mw-wb-rows-lb">{label}</div>}
      <div className="mw-wb-hist">
        {bins.map((b) => (
          <i key={b.label} className="on" style={{ height: `${Math.max((b.count / max) * 100, 2)}%` }}
            title={`${b.label}: ${b.count.toLocaleString()}`} />
        ))}
      </div>
      <div className="mw-wb-axis">
        <span>{bins[0]?.label ?? ""}</span>
        <span>{bins[bins.length - 1]?.label ?? ""}</span>
      </div>
    </>
  );
}

// ── shared small parts ────────────────────────────────────────────────────────────────────────────

/** APP-COHORT-ALGEBRA / APP-DECISIVE-COUNT — who left this population, and which criterion
 *  decided it.
 *
 *  THE RIGHT PANE AND NOT THE CANVAS, and the distinction is `APP-WORKBENCH`'s: this pane holds
 *  assertions ABOUT the document and never a second rendering OF it. A spine re-draws the document's
 *  population structure, which is why it is the nav's. "3,904 left here, and 1,204 of them for Exclusion B
 *  alone" appears nowhere in the document and cannot be read off it — it is the same kind of thing as
 *  the profile below, and it sits in the same place for the same reason.
 *
 *  DECISIVE LEADS, ANY-CAUSE FOLLOWS. Decisive counts a subject for whom flipping THIS criterion alone
 *  would have changed the outcome; any-cause counts everyone who failed it at all. The first is the
 *  only one safe to add up — the second overlaps across criteria and overshoots the cohort, and a
 *  reader WILL sum whatever is put in a column. So the bar is scaled to decisive and the any-cause
 *  figure is stated beside it in words, never as a second bar inviting the comparison.
 *
 *  THE SCOPE OF "DECISIVE" IS STATED, not left to be inferred. It means the subject would pass THIS
 *  gate, and says nothing about whether they would reach the numerator: missing data is correlated
 *  with what comes after it, so a subject with no recorded encounter likely has no recorded reading
 *  either. "Repair the feed and the rate rises" is a counterfactual and a probe. */
export function Departures({ gate }: { gate: FlowGateDto | null }) {
  if (!gate || gate.criteria.length === 0) return null;
  const left = gate.entered - gate.passed;
  if (left <= 0) return null;

  const ranked = rankedCriteria(gate);
  const top = ranked[0];
  const widest = Math.max(...ranked.map((c) => c.decisive), 1);
  const exact = partitions(gate);

  return (
    <Group label={`Departures · ${left.toLocaleString()}`}
      hint={`${gate.entered.toLocaleString()} entered, ${gate.passed.toLocaleString()} passed.`
        + (gate.polarity === "any-of"
          ? " Decisive here means excluded by exactly one reason."
          : " Decisive means that criterion alone held them out — they met the others.")}>
      <div className="mw-wb-drops">
        {ranked.map((c) => (
          <div key={`${c.library}::${c.define}`} className="mw-wb-drop">
            <div className="tp">
              <span className="nm">{c.define}</span>
              <span className="n tnum">{c.decisive.toLocaleString()}</span>
            </div>
            <div className="br"><i style={{ width: `${(c.decisive / widest) * 100}%` }} /></div>
            <div className="sub">
              {c.anyCause.toLocaleString()} failed it at all
              {c.library !== gate.library && <> · {c.library}</>}
            </div>
          </div>
        ))}
        {gate.multiple > 0 && (
          <div className="mw-wb-drop rest">
            <div className="tp">
              <span className="nm">Failed two or more</span>
              <span className="n tnum">{gate.multiple.toLocaleString()}</span>
            </div>
            <div className="br"><i className="mix" style={{ width: `${(gate.multiple / widest) * 100}%` }} /></div>
            <div className="sub">Attributed to no single criterion.</div>
          </div>
        )}
      </div>
      {/* The partition is what makes these safe to sum, and it is CHECKED rather than assumed — a
          column of figures that has quietly stopped adding up is worse than one that says it has. */}
      <p className="mw-wb-note">
        {exact
          ? "These add up: every subject who left is counted once."
          : "These do not currently sum to the departures — read each figure on its own."}
        {" "}Scoped to this gate: it says they would pass here, not that they would reach the numerator.
      </p>
      {gate.reached !== gate.passed && (
        <p className="mw-wb-note">
          {gate.reached.toLocaleString()} are in the population itself, against{" "}
          {gate.passed.toLocaleString()} who met every criterion above — so the population is not
          exactly the conjunction of them.
        </p>
      )}
    </Group>
  );
}

function Group({ label, hint, mark, children }: {
  label: string; hint?: string;
  /** APP-NO-UI-MOCK — the block's provenance chip, beside its own heading. */
  mark?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mw-wb-group">
      <div className="hd">{label}{mark}</div>
      {hint && <p className="hint">{hint}</p>}
      {children}
    </section>
  );
}

const Lead = ({ children }: { children: React.ReactNode }) => <p className="mw-wb-lead">{children}</p>;
const Empty = ({ children }: { children: React.ReactNode }) => <p className="mw-wb-empty">{children}</p>;
const Pending = ({ children }: { children: React.ReactNode }) => (
  <p className="mw-wb-empty"><span className="mw-spin" /> {children}</p>
);
