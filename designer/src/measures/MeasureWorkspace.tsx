// APP-MEASURE-DOC / APP-LIVING-MEASURE — the Designer workspace: one focused screen for
// READING and COMPUTING a living clinical specification. ONE top bar (DESIGNER-TOP-BAR) + the
// structure nav on the left + the document + the data inspector on the right.
//
// ONE SURFACE (APP-ONE-SURFACE, 2026-08-06). There were three lenses — Define, Validate, Analyze —
// and a shared spine they were arranged around. Both are withdrawn. Validate's content moved to a Data
// Readiness module, which was itself removed (DESIGNER-ONE-MODULE, 2026-10-03). Analyze is dropped — its funnel is the document's own `ReportedResult` and its trend
// needed run history nothing accumulates. With one destination the lens bar has nothing to switch, and
// the spine had nothing left to span.
//
// The spec reads STATIC by default; selecting a Data Source (APP-LIVING-MEASURE) gates the run
// (SVC-RUN-EVAL) that streams the funnel and counts into the document. The registry provides the
// artifact and its source; /api/translate drives the CQL compile status (part of reading the doc, so it
// runs regardless of Live). Narrative / CQL / Data Requirements reuse the wired authoring components.

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Definition } from "../catalog.ts";
import { fetchDefinitions, fetchLibrarySource, fetchSpecification, fetchDataRequirements, type DataRequirement,
  type SpecificationOverlay, generateNarration, NarrationRejected } from "../library-client.ts";
import { useRevision } from "../use-revision.ts";
import { NarrationStatus, type NarrationState } from "./NarrationStatus.tsx";
import { runMeasureStreaming, fetchDataSources, fetchWarehouses, reopenResults, type RunTrigger } from "../run-client.ts";
import { translate } from "../authoring/diagnostics.ts";
import { CqlCode } from "../authoring/cqlHighlight.tsx";
import { parseElmDefines, type ElmLibraryInfo } from "../authoring/elmDefines.ts";
import type { DataSourceRecord, RunResult, RunStage, TranslateResult, WarehouseRecord } from "../wire.ts";
import { MeasureNarrative, type ExplainTarget } from "./MeasureNarrative.tsx";
import { ExplainMenu } from "./ExplainMenu.tsx";
import { TopBar, type TopBarChrome } from "./TopBar.tsx";
import { Navigator } from "./Navigator.tsx";
import { stageLabel, stageProgress } from "./Inspector.tsx";
import { Workbench, type ProfileScope } from "./Workbench.tsx";
import { ComputeMenu } from "./ComputeMenu.tsx";
import { ExecParamBar } from "./ExecParamBar.tsx";
import { NavPane } from "./NavPane.tsx";
import { AssistantDrawer, useAssistantShortcut } from "./AssistantDrawer.tsx";
import { useSpecLens } from "./specLens.ts";
import type { AssistantSelection } from "../assistant-client.ts";
import { buildFlow, buildFlowRequest } from "./specFlow.ts";
import { useSelectionDerivations } from "./selection-derivations.ts";
// `Explain` / `ExplainOff` left with the toggle: the bar's Explain control is `ExplainMenu` now, and it
// draws its own glyphs. Importing them here for a control this file no longer renders is how a second
// copy of that toggle gets written.
import { X, ChevronDown, ChevronUp, Sparkle } from "../icons.tsx";
import {
  defaultExampleId, periodYears, periodParam, themeAttr,
  parseSelection, selectionToken, parseNavTab, loadPanes, savePanes, citableParams, storedUpdateMode, runStatusText, runStatusWhen,
  type MeasureView, type ExecMode, type ComputeTarget, type UpdateMode, type DeepLink, type Selection, type RunStatus,
  type PaneSize, type PaneState,
} from "./model.ts";
import { buildMetricSet, metricFunnel, type MetricSet } from "./specMetrics.ts";
import { buildSpecModel, filterSpecModel, sortSpecModel, type SpecModel, type SpecSortKey } from "./specModel.ts";
import { buildSpecDocument, withIncluded, withoutNarrated, type SpecDocument } from "./specDocument.ts";
import { buildRetrieves, referencedTerminology, type SpecRetrieve } from "./specRetrieves.ts";

const VALID_VIEWS = new Set<MeasureView>(["define", "cql"]);

// Persist the Compute menu selections across page reloads via localStorage.
const STORAGE_KEY = "cql-databricks:compute";
// APP-COMPUTE-TARGET — `warehouseId` is the chosen SQL warehouse; null or absent means the deployment's.
interface StoredCompute { mode?: ExecMode; compute?: ComputeTarget; sourceId?: string | null; updateMode?: UpdateMode;
  periodYear?: number; warehouseId?: string | null; }
function loadCompute(): StoredCompute {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch { return {}; }
}
function saveCompute(s: StoredCompute) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); } catch { /* quota / private mode */ }
}

/** Derive the active lens from the second URL segment (e.g. /measures/validate → "validate"). */
function viewFromPath(pathname: string): MeasureView | null {
  const seg = pathname.split("/").filter(Boolean)[1] ?? "";
  if (VALID_VIEWS.has(seg as MeasureView)) return seg as MeasureView;
  return null; // no explicit view in URL — use default
}

/** Scroll the Specification lens to a criterion selected in the left-pane nav (APP-NAV-FILTER).
 *  Selection is by NAME across the lenses, while spec anchors are library-qualified (APP-SPEC-MODEL),
 *  so resolve through the cell's `data-crit-name`. */
function scrollToCrit(name: string) {
  const el = document.querySelector<HTMLElement>(`[data-crit-name="${CSS.escape(name)}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

const LAST_MEASURE_KEY = "cql-databricks:lastOpenMeasure";

function pickDefault(defs: Definition[]): string {
  // 1. URL ?open= param (explicit navigation).
  const url = new URL(window.location.href).searchParams.get("open");
  if (url && defs.some((d) => d.id === url)) return url;
  // 2. Last-used measure persisted in localStorage.
  const last = localStorage.getItem(LAST_MEASURE_KEY);
  if (last && defs.some((d) => d.id === last)) return last;
  // 3. First registry-backed measure (has real CQL source — not a mock stub).
  const real = defs.find((d) => d.cat !== "lib" && d.source !== undefined);
  if (real) return real.id;
  // 4. Fallback to the bundled default example.
  return defaultExampleId(defs);
}

export function MeasureWorkspace({ chrome, deepLink }: {
  /** DESIGNER-TOP-BAR — the shared bar's invariants (module · theme · tour · palette), owned by Root. */
  chrome: TopBarChrome;
  /** APP-TOUR — a Product Tour "Show me" opens the workspace on an example at a given lens/mode. */
  deepLink?: DeepLink;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [definitions, setDefinitions] = useState<Definition[]>([]);
  const [loaded, setLoaded] = useState(false);

  // APP-URL-ROUTING — `?open=` IS the open artifact. There is no second copy: `selectedId` used to
  // be a useState synced with the URL in BOTH directions — a raw `replaceState` wrote one way, an
  // effect wrote the other — and the raw write is what react-router never sees. Any navigation built
  // after it from the router's stale search (as `setView` was) resurrected the previous `?open=`, and
  // the sync effect then dutifully adopted it: open a measure, watch the URL flicker, land back on
  // the artifact you just left. State with two writers and a reconciler is three chances to disagree;
  // a derivation is none.
  const urlOpen = new URLSearchParams(location.search).get("open");
  const selectedId = urlOpen ?? "";

  // APP-URL-ROUTING — derive lens from URL path; push changes to history.
  const urlView = viewFromPath(location.pathname);
  const view: MeasureView = deepLink?.lens ?? urlView ?? "define";

  const [run, setRun] = useState<RunResult | null>(null);
  const [runStage, setRunStage] = useState<RunStage | null>(null);
  const running = runStage !== null;
  const [runError, setRunError] = useState<string | null>(null);
  const [errorExpanded, setErrorExpanded] = useState(false);
  const [trans, setTrans] = useState<TranslateResult | null>(null);
  const [compiling, setCompiling] = useState(false);

  const [dep, setDep] = useState<{ name: string; source: string | null } | null>(null);

  // Whatever `?open=` says — however it got there: the Open menu, a deep link, a pasted address,
  // Back — is what is open, so the per-artifact side state follows it from ONE place. The dependency
  // view is scoped to the artifact that included it, and the last-opened record is how the next
  // session finds its way back (`pickDefault`).
  useEffect(() => {
    setDep(null);
    if (selectedId) localStorage.setItem(LAST_MEASURE_KEY, selectedId);
  }, [selectedId]);

  // APP-LOGIC-VIEW / APP-URL-ROUTING — the criterion selected across the nav ↔ Define lens.
  //
  // The URL IS the state (`?crit=`), not a copy of it. A reader who has scrolled to `Denominator
  // Exclusions` and pastes the address bar to a colleague has to land them there, and Back has to undo
  // the last jump — both fall out for free when selection lives in history rather than beside it.
  // Selecting therefore PUSHES an entry; only the initial sync-from-URL replaces.
  const selectedToken = new URLSearchParams(location.search).get("crit");
  // ONE conversion point (`model.ts`): the URL and the DOM carry a token because they are string-shaped;
  // every consumer below is handed the union.
  const selected = useMemo(() => parseSelection(selectedToken), [selectedToken]);
  // What THIS component last selected. Anything in the URL that does not match it arrived from
  // outside — a pasted address, Back, Forward — and has to move the page, not merely tint a row.
  const selfSelected = useRef<string | null>(selectedToken);
  // APP-URL-ROUTING — the ONE writer for the workspace's ENTIRE URL: the query state (`open`,
  // `crit`, `nav`) and, when the gesture changes it, the lens path. It takes a PATCH rather than a
  // single param because a gesture that changes several has to land as ONE history entry — two
  // `navigate` calls in one tick both build from the search as it stood before either committed, so
  // the second silently drops the first's write (and Back would undo half a move even if it didn't).
  // That drop is exactly how the Open menu came to bounce back to the previous measure while a raw
  // `replaceState` was a second writer; there is no second writer now, and nothing here may call
  // `navigate` on its own.
  //
  // Built from `window.location`, not the router's copy: a push updates `window.location`
  // synchronously while `useLocation` waits for the router's commit, so this is the one read that is
  // correct even between a write and its render.
  const setParams = useCallback((patch: Record<string, string | null>,
    opts: { replace?: boolean; view?: MeasureView } = {}) => {
    const params = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) { if (v) params.set(k, v); else params.delete(k); }
    const qs = params.toString();
    const path = opts.view ? `/measures/${opts.view}` : window.location.pathname;
    navigate(`${path}${qs ? `?${qs}` : ""}`, { replace: opts.replace ?? false });
  }, [navigate]);
  const setView = useCallback((v: MeasureView) => setParams({}, { view: v }), [setParams]);
  const setSelected = useCallback((sel: Selection | null) => {
    const name = sel ? selectionToken(sel) : null;
    selfSelected.current = name;
    setParams({ crit: name });
  }, [setParams]);
  // DESIGNER-TOP-BAR / APP-URL-ROUTING — open an artifact: ONE history write carrying the id, the
  // cleared criterion (the old selection names a define the new artifact does not have — and clearing
  // it AT THE WRITE is what lets Back keep the criterion of the entry it returns to), and, when the
  // gesture implies one, the lens. Everything per-artifact — the workspace state, the last-opened
  // record, the dependency reset — follows from the URL this writes.
  const openArtifact = useCallback((id: string, opts: { replace?: boolean; view?: MeasureView } = {}) => {
    selfSelected.current = null;
    setParams({ open: id, crit: null }, opts);
  }, [setParams]);

  // SPEC-NAV-COMPOSITION — which arrangement the left pane is in. It lives in the URL for the same
  // reason `?crit=` does: a pasted address has to reproduce what the sender was looking at.
  //
  // `?pop=` is GONE with the Flow tab that owned it. Composition draws every population at once, so
  // there is no root to address; an old link carrying one is simply ignored.
  const navTab = useMemo(
    () => parseNavTab(new URLSearchParams(location.search).get("nav")), [location.search]);
  // APP-EXEC-MODE — the two OFFLINE states: `static` reads the spec, `deck` computes the synthetic Test
  // Deck client-side. Real-backend targets come from the Data Source registry (`selectedSourceId`) below.
  // Restore from localStorage so the last selection survives a page reload.
  const stored = useRef(loadCompute());
  const [mode, setMode] = useState<ExecMode>(deepLink?.mode ?? stored.current.mode ?? "static");
  // APP-COMPUTE-TARGET — the stored literal of the retired runtime axis; the warehouse is the choice now.
  const compute: ComputeTarget = "serverless";
  // APP-COMPUTE-TARGET — the SQL warehouse a run and every result read use, remembered per user. null
  // is the deployment's WAREHOUSE_ID. A chosen warehouse the user cannot use fails; nothing falls back.
  const [warehouseId, setWarehouseId] = useState<string | null>(stored.current.warehouseId ?? null);
  const [warehouses, setWarehouses] = useState<WarehouseRecord[]>([]);
  const [defaultWarehouseId, setDefaultWarehouseId] = useState<string | null>(null);
  const [warehouseError, setWarehouseError] = useState<string | null>(null);
  const chosenWarehouse = warehouseId ?? undefined;
  // APP-UPDATE-MODE — Manual is the default. Automatic computes missing results after CQL, source,
  // period or warehouse changes that find no results; it does not detect source-data changes, which
  // need Recompute everything. A stored `paused` or a missing value is Manual (`storedUpdateMode`).
  const [updateMode, setUpdateMode] = useState<UpdateMode>(storedUpdateMode(stored.current.updateMode));
  // DATA-SOURCE-REGISTRY — the data sources the picker offers (Live + Conformance) and the selected one. A
  // selected registry source overrides the ExecMode data source and runs via `data_source_id`; the backend
  // resolves the bind. API-driven
  // (APP-NO-UI-MOCK); a source selection still "computes" the document.
  const [sources, setSources] = useState<DataSourceRecord[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(stored.current.sourceId ?? null);
  const computed = selectedSourceId !== null;
  // PIPE-MEASUREMENT-PERIOD — the selected measurement period year, dialled in the toolbar's parameter
  // bar (DESIGNER-TOP-BAR); the selected year is passed to compile_pipeline as "Measurement Period" so the
  // SQL is baked with the correct period literals. Sticky with the rest of the compute selection:
  // a stored year the menu doesn't offer reverts to the current one.
  const [periodYear, setPeriodYear] = useState(() => {
    const offered = periodYears();
    const saved = stored.current.periodYear;
    return saved !== undefined && offered.includes(saved) ? saved : offered[0]!;
  });
  const periodStart = `${periodYear}-01-01`;
  const periodEnd = `${periodYear}-12-31`;
  // APP-DATA-PROFILE — the evaluation a define's profile is a fact ABOUT, resolved ONCE. Both surfaces
  // that render a profile read this same object: the inspector pane, and the reference lens inside the
  // Specification, which renders the inspector's own tabs (`SPEC-REF-LENS`). Memoised rather than written
  // as a literal at each of the two call sites — an object rebuilt every render is a new identity every
  // render, which would re-run the lens context's memo continuously, and two literals are two places for
  // the period to be forgotten from.
  // RUN-ADDRESS-BY-LIBRARY — `draft` is the key the last run's CQL was given when it is not the published
  // version; every figure on the page then reads that draft's results, never the version's.
  const draft = run?.draft || undefined;
  const profileScope = useMemo<ProfileScope | null>(() => (
    selectedSourceId && draft && run?.resultSet
      ? { dataSourceId: selectedSourceId, periodStart, periodEnd, draft, resultSet: run.resultSet,
          warehouseId: chosenWarehouse } : null
  ), [selectedSourceId, periodStart, periodEnd, draft, run?.resultSet, chosenWarehouse]);
  // APP-EXEC-PARAMS — the execution parameters this run is compiled with. The measurement period is
  // the only one the app has a source for today; the rest belong to the measure package the compiler
  // emits (a measure declares its own parameters), so they join this list when that data is wired — the
  // parameter bar needs no change to show them.
  const execParams = useMemo(() => [periodParam(periodYear, setPeriodYear)], [periodYear]);
  const token = useRef(0);
  const busy = useRef(false);
  useEffect(() => { fetchDataSources().then(setSources).catch(() => {}); }, []);
  useEffect(() => {
    fetchWarehouses().then((list) => {
      setWarehouses(list.warehouses); setDefaultWarehouseId(list.default); setWarehouseError(null);
    }).catch((e) => setWarehouseError(e instanceof Error ? e.message : String(e)));
  }, []);
  // Persist every change so the next page load restores the last selection.
  useEffect(() => { saveCompute({ mode, compute, sourceId: selectedSourceId, updateMode, periodYear, warehouseId }); },
    [mode, compute, selectedSourceId, updateMode, periodYear, warehouseId]);

  // APP-TOUR — apply a deep-link when it changes (a "Show me" while the workspace is already
  // mounted). The artifact and the lens land as ONE write — two would race (`setParams`).
  useEffect(() => {
    if (!deepLink) return;
    if (deepLink.openId) openArtifact(deepLink.openId, { replace: true, view: deepLink.lens });
    else if (deepLink.lens) setView(deepLink.lens);
    if (deepLink.mode) setMode(deepLink.mode);
  }, [deepLink, setView, openArtifact]);

  // Registry-backed definitions — the registry is the single source of truth (no mock fallback).
  // Only override the selected id when there is no current selection or it is absent from the new
  // definitions (stale/removed); an explicit user or deep-link selection always wins.
  //
  // KNOWLEDGE-FAIL-LOUD — a failed fetch is NOT an empty catalogue. The backend used to answer 200
  // with [] when Lakebase was unreachable and this pane called that "No measures found", sending
  // the reader to Settings to fix a configuration that was never wrong. The error is now surfaced
  // on the FIRST failure (retries continue underneath) so a real outage reads as an outage.
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let retries = 0;
    function load() {
      fetchDefinitions().then((d) => {
        if (!live) return;
        setLoaded(true);
        setLoadError(null);
        setDefinitions(d);
        // Adopt the URL's artifact when it names a real one; otherwise land on the default — as a
        // REPLACE, since nobody chose the stale/absent id this corrects.
        const open = new URLSearchParams(window.location.search).get("open");
        if (!open || !d.some((x) => x.id === open)) openArtifact(pickDefault(d), { replace: true });
      }).catch((e) => {
        if (!live) return;
        setLoadError(e instanceof Error ? e.message : String(e));
        // Backend may not be ready yet (proxy ETIMEDOUT on cold start) — retry a few times.
        if (retries < 5) { retries++; setTimeout(load, 1500); }
        else setLoaded(true);
      });
    }
    load();
    return () => { live = false; };
  }, [openArtifact]); // stable — the effect still runs once

  const definition = useMemo(
    () => definitions.find((d) => d.id === selectedId) ?? definitions[0] ?? null,
    [definitions, selectedId],
  );

  // PERF-DISCOVER-LIGHTWEIGHT — the discover endpoint omits CQL source; lazy-load it
  // from /api/libraries/{name}/{version}/source when a definition is selected. While the
  // fetch is in-flight, `source` is empty and the UI shows a loading state. If the fetch
  // fails, the error is surfaced — never silently fall back to a generated stub.
  const [loadedSource, setLoadedSource] = useState<string | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  useEffect(() => {
    if (!definition) { setLoadedSource(null); setSourceError(null); return; }
    if (definition.source !== undefined && definition.source.trim() !== "") {
      setLoadedSource(definition.source);
      setSourceError(null);
      return;
    }
    setLoadedSource(null);
    setSourceError(null);
    setSourceLoading(true);
    const [name, version] = definition.id.split("@");
    if (!name || !version) { setSourceLoading(false); return; }
    let live = true;
    fetchLibrarySource(name, version)
      .then((s) => { if (live) { setLoadedSource(s); setSourceError(null); } })
      .catch((e) => { if (live) setSourceError(`Failed to load source: ${e instanceof Error ? e.message : e}`); })
      .finally(() => { if (live) setSourceLoading(false); });
    return () => { live = false; };
  }, [definition?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  // Source is empty while loading or on error — never use a generated stub.
  const source = loadedSource ?? "";

  // DATA-SOURCE-BIND / SVC-RUN-EVAL — run against a selected registry data source by its id; the backend
  // resolves the bind origin (a Live source binds its retrieve UDTFs). One path for both.
  // RUN-CLIENT-REQUEST-REOPEN — the calculation whose results were last looked up or shown, so a finished
  // run is not followed by a redundant reopen of what it just returned.
  const scopeKey = `${definition?.id ?? ""}\0${source}\0${selectedSourceId ?? ""}\0${periodStart}\0${periodEnd}\0${warehouseId ?? ""}\0${updateMode}`;
  const currentKey = useRef(scopeKey);
  currentKey.current = scopeKey;
  const lookedUp = useRef<string | null>(null);
  // APP-UPDATE-MODE — "Calculation changed since last run": the last results shown were for this measure,
  // data source and period, but other CQL. `shownNow` is this render's calculation, read through a ref so
  // the memoized callbacks below never record a stale one.
  type Shown = { id: string; sourceId: string; period: string; source: string };
  const shownNow = useRef<Shown | null>(null);
  shownNow.current = definition && selectedSourceId
    ? { id: definition.id, sourceId: selectedSourceId, period: `${periodStart}/${periodEnd}`, source } : null;
  const shownFor = useRef<Shown | null>(null);
  // APP-RUN-LOG — each caller names its action; Refresh recomputes, Automatic computes only what is missing.
  const doRunSource = useCallback((src: string, sourceId: string, trigger: RunTrigger) => {
    const recompute = trigger === "refresh";
    if (!src.trim() || busy.current) return;
    busy.current = true;
    const my = ++token.current;
    setRunStage({ stage: "connecting", message: "Starting…" });
    setRunError(null); setErrorExpanded(false);
    runMeasureStreaming(src, { dataSourceId: sourceId, periodStart, periodEnd, recompute, trigger, warehouseId: chosenWarehouse }, (stage) => {
      if (my !== token.current) return;
      setRunStage(stage);
    }).then((r) => {
      if (my !== token.current) return;
      if (r.ok) { setRun(r); setRunError(null); lookedUp.current = currentKey.current; shownFor.current = shownNow.current; }
      else { setRunError(r.error || (r.diagnostics ?? []).join("; ") || "run failed"); }
    }).catch((e) => {
      if (my !== token.current) return;
      setRunError(e instanceof Error ? e.message : "backend unreachable");
    }).finally(() => {
      // RUN-CLIENT-REFRESH — changing the displayed scope does not cancel the writer. Wait for it
      // before starting another operation in this tab. Cross-tab/process serialization is the owner's.
      busy.current = false;
      setRunStage(null);
    });
  }, [periodStart, periodEnd, chosenWarehouse]); // eslint-disable-line react-hooks/exhaustive-deps

  // RUN-CLIENT-RESULT-SETS — another scope must not display this selection's figures. A different
  // warehouse re-reads them, so a warehouse the user cannot use fails visibly (APP-COMPUTE-TARGET).
  useEffect(() => {
    ++token.current;
    setRun(null);
    setRunError(null);
  }, [source, selectedSourceId, periodStart, periodEnd, definition?.id, warehouseId]);

  const [lookup, setLookup] = useState<"checking" | "not-computed" | "changed" | null>(null);

  // RUN-CLIENT-REQUEST-REOPEN — opening a measure, or changing its CQL, data source, period or warehouse,
  // opens the user's own results for exactly that calculation, read-only. It never computes. Results
  // are always private: there is no published-results lookup (RUN-CLIENT-PUBLISHED, amended 2026-10-10).
  // A miss is "Not computed"; in Automatic it then runs Compute missing, in Manual it does not.
  useEffect(() => {
    if (!definition || !selectedSourceId || !source) { setLookup(null); lookedUp.current = null; return; }
    if (running || busy.current || lookedUp.current === scopeKey) return;
    lookedUp.current = scopeKey;
    const my = ++token.current;
    setLookup("checking");
    reopenResults(source, { dataSourceId: selectedSourceId, periodStart, periodEnd, warehouseId: chosenWarehouse })
      .then((r) => {
        if (my !== token.current) return;
        if (r.found) {
          setRun(r.result); setRunError(null); setLookup(null); shownFor.current = shownNow.current;
          return;
        }
        const prev = shownFor.current;
        setLookup(prev && prev.id === definition.id && prev.sourceId === selectedSourceId
          && prev.period === `${periodStart}/${periodEnd}` && prev.source !== source ? "changed" : "not-computed");
        if (updateMode === "live") doRunSource(source, selectedSourceId, "automatic");
      })
      .catch((e) => {
        if (my !== token.current) return;
        setLookup(null);
        setRunError(e instanceof Error ? e.message : String(e));
      });
  }, [scopeKey, running]); // eslint-disable-line react-hooks/exhaustive-deps

  // APP-UPDATE-MODE — why the page shows what it shows; never a bare "Idle".
  const tasks = runStage?.tasks ?? [];
  const runStatus: RunStatus = !selectedSourceId ? { kind: "no-source" }
    : running ? (tasks.length
      ? { kind: "running", total: tasks.length,
          done: tasks.filter((t) => ["completed", "skipped", "failed"].includes(t.status)).length }
      : { kind: "running" })
    : runError ? { kind: "failed" }
    : run ? { kind: "shown", computedAt: run.computedAt }
    // Until this calculation has been looked up (source still loading, or the lookup not yet started),
    // nothing is known: "Not computed" here would be a claim made before asking.
    : lookup === "checking" || (!source && !sourceError) || lookedUp.current !== scopeKey ? { kind: "checking" }
    : lookup === "changed" ? { kind: "changed" }
    : { kind: "not-computed" };

  // Compile status is part of READING the doc (APP-MEASURE-DOC) — refresh it on every source change,
  // regardless of Live; clear any prior run so a new artifact never shows the previous one's numbers.
  // Criterion is cleared where the artifact SWITCHES — `openArtifact` patches `crit: null` into the
  // same history write as `open`.
  useEffect(() => {
    if (!source) return;
    setRun(null);
    setCompiling(true);
    let current = true;
    // APP-SPEC-MODEL — compile the include closure too: the Define lens derives its whole model
    // from ELM, so the includes must arrive compiled, not merely resolved.
    translate(source, [], { includeElm: true }).then((result) => { if (current) setTrans(result); }).catch((e) => {
      if (!current) return;
      console.error("translate failed:", e);
      setTrans({ elm: "", diagnostics: [`Compile failed: ${e instanceof Error ? e.message : e}`], ok: false });
    }).finally(() => { if (current) setCompiling(false); });
    return () => { current = false; };
  }, [source]);

  // APP-NARRATIVE-OVERLAY — the Specification narration overlay for the current definition: intent,
  // scope, and the what/why/how-to-read triple per define. Prose only; the document's STRUCTURE is
  // derived from the ELM below and the overlay cannot move it. Read again when the server's revision
  // moves (`PERF-CLIENT-REVISION`): a narration generated elsewhere changes this body.
  const revision = useRevision();
  const [ownOverlay, setOwnOverlay] = useState<SpecificationOverlay | null>(null);
  useEffect(() => {
    if (!definition) { setOwnOverlay(null); return; }
    const [name, version] = definition.id.split("@");
    if (!name || !version) return;
    let live = true;
    fetchSpecification(name, version).then((o) => { if (live) setOwnOverlay(o); })
      .catch(() => { if (live) setOwnOverlay(null); });
    return () => { live = false; };
  }, [definition?.id, revision]); // eslint-disable-line react-hooks/exhaustive-deps


  // RUN-CLIENT-RESULT-SETS — figures arrive from the run's complete selection. Ordinary runs reuse
  // matching completed results across sessions; no independent latest-result discovery is needed.

  // APP-CQL-AUTHORITATIVE — the authoritative define catalog from the compiler's ELM output.
  // This replaces regex-based CQL parsing as the source of truth for what defines exist; consumers
  // (MeasureNarrative, DefineNav) use this when available, falling back to CQL scan only offline.
  const elmInfo = useMemo(() => (trans?.ok && trans.elm ? parseElmDefines(trans.elm) : null), [trans]);

  // APP-LOGIC-VIEW — selecting in the NAV scrolls the Define lens to the criterion and keeps it
  // selected; selecting IN the spec just toggles selection (no scroll — you're already there).
  // Selecting from the NAV — and, since SPEC-REF-LENS, following a reference inside the document —
  // scrolls to the section that renders the define. Highlighting it in the nav and leaving the reader
  // where they were is not "following a link".
  //
  // APP-MEASURE-FLOW — EVERY nav selection scrolls through the landing effect below, not from
  // here. Selection lives in the URL (`setSelected` → `navigate`), and a router update does not
  // commit in the click's own flush — so a scroll launched from this handler, on any frame timer,
  // RACES the commit that renders the selection and the reveals that expand what holds it
  // (`MeasureNarrative`): it measured the pre-expansion page, and the expansion then moved its
  // destination out from under the smooth scroll. Clearing the stamp `setSelected` just wrote makes
  // the landing effect treat the token's arrival as something that must MOVE the page — and that
  // effect runs after the commit it is keyed on, so it always measures the expanded page. This is
  // also what makes the pane lens-invariant for free: a row clicked under the CQL lens lands the
  // same way, once the Specification has mounted — one scroll path, not two.
  //
  // RE-SELECTING the selected criterion is the one case with no commit to wait for: the URL is the
  // state and it is already there, so nothing downstream renders, reveals or lands. Scroll directly
  // over the page exactly as it stands.
  const selectFromNav = useCallback((sel: Selection) => {
    if (dep) setDep(null);
    const token = selectionToken(sel);
    if (token === selectedToken && view === "define") {
      requestAnimationFrame(() => scrollToCrit(token));
      return;
    }
    selfSelected.current = null;
    // The criterion and, from another lens, the lens itself: ONE write (`setParams`) — as two they
    // would race and Back would undo half the move.
    setParams({ crit: token }, { view: view !== "define" ? "define" : undefined });
  }, [dep, selectedToken, view, setParams]);

  // APP-URL-ROUTING — land the reader on a document HEADING from the nav, from any lens.
  //
  // Replaces five ad-hoc `getElementById(...).scrollIntoView(...)` call sites in `DefineNav` (Overview
  // anchors, Terminology's two rows, the notices rows and a read group's resource) plus the
  // `onScrollToSection` prop that was plumbed three levels and never called. All five broke the same
  // way once the pane outlived the Define lens: the heading only exists while the Specification is
  // mounted, so the scroll has to wait for it.
  const pendingAnchor = useRef<string | null>(null);
  const landOnAnchor = useCallback((domId: string) => {
    if (dep) setDep(null);
    if (view === "define") { document.getElementById(domId)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    pendingAnchor.current = domId;
    setView("define");
  }, [dep, view, setView]);
  const selectInSpec = useCallback((sel: Selection) => (
    setSelected(selectionToken(sel) === selectedToken ? null : sel)
  ), [setSelected, selectedToken]);
  // APP-SPEC-CELLS — a cell "Run": select it and ensure the doc is computed so its inline figure
  // appears. Per-define isolated runs are future work (needs a define-scoped run).
  const runCell = useCallback((name: string) => {
    setSelected({ kind: "define", name });
    scrollToCrit(name);
  }, [setSelected]);


  // Clear stale run results when the definition or data source changes. In Live mode, the
  // auto-kick-off effect will immediately start a fresh run after this clears.
  useEffect(() => {
    setRun(null); setRunError(null);
  }, [source, selectedSourceId]);


  // APP-SPEC-MODEL — the include closure arrives COMPILED on the translate result, so there are no
  // per-include source fetches and no source scan: each include is a parsed ELM catalog plus its CQL
  // (needed only to resolve statement locators to text).
  const compiledIncludes = useMemo(() => {
    const out: { info: ElmLibraryInfo; elm: unknown; source: string }[] = [];
    const failed: { name: string }[] = [];
    for (const inc of trans?.includes ?? []) {
      const info = inc.elm ? parseElmDefines(inc.elm) : null;
      if (!info || !inc.elm) { failed.push({ name: inc.name }); continue; }
      out.push({ info, elm: JSON.parse(inc.elm), source: inc.source });
    }
    return { ok: out, failed };
  }, [trans]);
  // SPEC-DATA-REQUIREMENTS — `Library.dataRequirement` for the WHOLE CLOSURE, keyed by library.
  //
  // ENRICHMENT ONLY. What is read, and by which define, is derived from the ELM (`specRetrieves.ts`);
  // the published resource supplies `mustSupport`, the element paths the logic touches, which nothing
  // else knows. Per library because the resource is per library — and the closure, not just the root,
  // because the root's copy stops at its own defines: a root that reads an encounter only through a
  // helper library carries no row for it, though the measure plainly reads it.
  //
  // A library whose ingested resource has none still contributes its reads; only the mustSupport
  // column is empty. Some ingested copies genuinely lack it (one version of a helper library may ship
  // the resource while another does not), so this must degrade rather than fail.
  const [dataReqs, setDataReqs] = useState<Map<string, DataRequirement[]>>(new Map());
  useEffect(() => {
    const libraries = elmInfo ? [elmInfo.name, ...compiledIncludes.ok.map((i) => i.info.name)] : [];
    if (libraries.length === 0) { setDataReqs(new Map()); return; }
    let live = true;
    const versionOf = (name: string) =>
      name === elmInfo?.name ? elmInfo.version
        : compiledIncludes.ok.find((i) => i.info.name === name)?.info.version ?? "";
    void Promise.all(libraries.map(async (name) => {
      const version = versionOf(name);
      if (!version) return [name, [] as DataRequirement[]] as const;
      return [name, await fetchDataRequirements(name, version).catch(() => [])] as const;
    })).then((pairs) => { if (live) setDataReqs(new Map(pairs)); });
    return () => { live = false; };
  }, [elmInfo, compiledIncludes]);

  // APP-NARRATIVE-OVERLAY — each included library's own narration, joined onto the open library's. A
  // narration is cached per library, so a helper narrated once reads the same in every measure using it.
  const [includedOverlays, setIncludedOverlays] = useState<(SpecificationOverlay | null)[]>([]);
  useEffect(() => {
    const includes = compiledIncludes.ok.map((i) => i.info);
    if (includes.length === 0) { setIncludedOverlays([]); return; }
    let live = true;
    void Promise.all(includes.map((i) => fetchSpecification(i.name, i.version).catch(() => null)))
      .then((found) => { if (live) setIncludedOverlays(found); });
    return () => { live = false; };
  }, [compiledIncludes, revision]);
  const overlay = useMemo(() => {
    const [library = "", version = ""] = (definition?.id ?? "").split("@");
    return withIncluded({ library, version }, ownOverlay, includedOverlays);
  }, [definition?.id, ownOverlay, includedOverlays]);

  // The read-only dependency view still wants name + source.
  const resolvedIncludes = useMemo(
    () => (trans?.includes ?? []).map((i) => ({ name: i.name, source: i.source })),
    [trans],
  );

  // The ONE model of what this specification contains. Built once here and handed to BOTH Define-lens
  // surfaces, so the left nav and the Specification page can never disagree about which entries exist,
  // how overloads collapse, what is eliminated, or WHAT ORDER THEY COME IN. "Show eliminated defines"
  // and the catalogue's order are properties of the document (not of one pane), so their state lives
  // here and each is applied once (SPEC-SHARED-ORDER).
  const [showEliminated, setShowEliminated] = useState(false);
  // SPEC-SHARED-ORDER — `declared` is the CQL's own statement order: opening on anything else would
  // reorder the published document before the reader asked for it.
  const [defineSort, setDefineSort] = useState<SpecSortKey>("declared");
  useEffect(() => { setShowEliminated(false); setDefineSort("declared"); }, [source]);
  const specModel = useMemo<SpecModel | null>(() => {
    if (!elmInfo || !trans?.elm) return null;
    return buildSpecModel({
      root: { info: elmInfo, elm: JSON.parse(trans.elm), source },
      includes: compiledIncludes.ok,
      unresolved: compiledIncludes.failed,
    });
  }, [elmInfo, trans, source, compiledIncludes]);
  // Filtered, then ordered — ONE model, so everything derived below it (the page, the nav, the data
  // requirements) reads the same catalogue in the same order.
  const visibleSpec = useMemo(
    () => (specModel ? sortSpecModel(filterSpecModel(specModel, showEliminated), defineSort) : null),
    [specModel, showEliminated, defineSort],
  );

  // APP-PANE-STATE — normal / expanded / collapsed, per side pane. A preference, so localStorage
  // rather than the URL (`model.ts` records why).
  const [panes, setPanes] = useState<PaneState>(loadPanes);
  useEffect(() => { savePanes(panes); }, [panes]);
  const setPaneSize = useCallback((pane: keyof PaneState, size: PaneSize) => {
    setPanes((p) => ({ ...p, [pane]: size }));
  }, []);

  // SPEC-DOCUMENT — the derived Specification: populations decomposed into Concepts and Rules, with
  // everything shared hoisted into COMMON. Derived from the SAME model both surfaces render, so the
  // document and the nav still cannot disagree (APP-SPEC-MODEL). Built off the UNFILTERED model:
  // "show eliminated" governs the Definitions catalogue, and an eliminated define is by definition
  // unreachable from a population, so it can never appear in a derived section anyway.
  // SPEC-EXPLAIN-INPUTS — the server's resolved reference sets for this closure, as returned with the
  // ELM they were derived from. Empty until the compile lands, which is the same state `elmByLibrary` is
  // in, so nothing downstream needs a second "not ready yet".
  const referenceSets = useMemo(() => trans?.referenceSets ?? [], [trans]);
  const elmByLibrary = useMemo(() => {
    const out = new Map<string, unknown>();
    if (elmInfo && trans?.elm) out.set(elmInfo.name, JSON.parse(trans.elm));
    for (const inc of compiledIncludes.ok) out.set(inc.info.name, inc.elm);
    return out;
  }, [elmInfo, trans, compiledIncludes]);
  // SPEC-EXPLAIN-VIEW — the Explain toggle is a PROVENANCE FILTER over the prose, not a second
  // document. Off, the page carries only what can be stated without interpretation: the ELM's
  // structure, metrics and code, plus prose the measure's own author published. On, the interpreted
  // layer is added back. Filtering here, once, means every consumer below — the page, the nav, the
  // catalogue one-liners — shows the same view and cannot disagree about it (APP-SPEC-MODEL).
  const [explain, setExplain] = useState(true);
  const shownOverlay = useMemo(
    () => (explain ? overlay : withoutNarrated(overlay)), [explain, overlay],
  );

  // SPEC-EXPLAIN-AGAIN — which section the bar's "Explain again" would be about.
  //
  // AIMED BY THE DOCUMENT, HELD BY THE BAR. Re-explaining is an act on ONE criterion and the toolbar
  // cannot know which, so the aim comes from the section's own Explain mark (`ExplainBadge`) and lands
  // here, where the control that fires it lives. It is NOT the selection: selection is the document's
  // you-are-here mark and is set by half a dozen gestures that say nothing about re-explaining, so
  // reading it here would arm a generation off a click on a nav row.
  //
  // NOT PERSISTED and cleared with the measure — an aim taken at a criterion in one document means
  // nothing in the next, and a stale one would fire the act at a name this measure does not declare.
  const [explainTarget, setExplainTarget] = useState<ExplainTarget | null>(null);
  useEffect(() => { setExplainTarget(null); }, [definition?.id]);

  // APP-NARRATIVE-OVERLAY — generate the open library's narration. The server compiles it, writes it with
  // the model, checks it (one visible retry) and caches it only when it passes; the composed Specification
  // it now serves comes back on the stream and replaces the open library's overlay directly. Leaving the
  // library closes the stream, and the server stops with it: nothing is saved for an abandoned generation.
  const openLibrary = definition?.id.split("@")[0] ?? "";
  const [narration, setNarration] = useState<NarrationState | null>(null);
  const narrationAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    narrationAbort.current?.abort();
    narrationAbort.current = null;
    setNarration(null);
  }, [definition?.id]);
  const generate = useCallback(() => {
    if (!definition) return;
    const [name, version] = definition.id.split("@");
    if (!name || !version) return;
    narrationAbort.current?.abort();
    const ctl = new AbortController();
    narrationAbort.current = ctl;
    setNarration({ status: "running", library: name, progress: null, retried: null });
    generateNarration(name, version, {
      signal: ctl.signal,
      onProgress: (progress) => {
        if (ctl.signal.aborted) return;
        setNarration((prev) => prev?.status === "running"
          ? progress.type === "problems"
            ? { ...prev, retried: progress.problems }
            : { ...prev, progress }
          : prev);
      },
    }).then((specification) => {
      if (ctl.signal.aborted) return;
      setOwnOverlay(specification);
      setNarration(null);
      // The reader asked to see it: a narration generated behind Explain-off would look like nothing happened.
      setExplain(true);
    }).catch((e: unknown) => {
      if (ctl.signal.aborted) return;
      setNarration(e instanceof NarrationRejected
        ? { status: "rejected", library: name, problems: e.problems, attempts: e.attempts }
        : { status: "error", library: name, error: e instanceof Error ? e.message : String(e) });
    });
  }, [definition]);

  // APP-ASSISTANT — the drawer's open state, and the question an inline entry point handed it.
  // NOT in the URL and NOT persisted: a conversation is a session, not a place. `APP-PANE-STATE`
  // persists the panes because a pane is a layout preference the reader wants back tomorrow; an open
  // drawer restored on load would be a transcript with no conversation in it.
  const [askOpen, setAskOpen] = useState(false);
  const [askQuestion, setAskQuestion] = useState<string | undefined>(undefined);
  // APP-ASSISTANT-CONTEXT — the define the drawer is scoped to WHEN IT IS NOT THE SELECTION.
  // A reference lens opens on a name in a sentence that is usually NOT what the document is selected
  // on — that is the whole reason the lens exists (`SPEC-REF-LENS`: reading it must not move the
  // reader) — so asking from one has to scope to the reference pressed rather than to the selection
  // left behind. `null` means the ordinary case: follow the selection.
  const [askDefine, setAskDefine] = useState<string | null>(null);
  const openAssistant = useCallback((opts: { define?: string; question?: string } = {}) => {
    setAskDefine(opts.define ?? null);
    setAskQuestion(opts.question);
    setAskOpen(true);
  }, []);

  // SPEC-EXPLAIN-AGAIN — re-explain a section: hand it to the assistant, scoped.
  //
  // WHAT THIS DOES AND DOES NOT DO, stated once because the difference is the whole design. It asks for
  // a FRESH READING in the drawer; it does not touch the paragraph on the page. The document's prose is
  // an authored artifact — a Specification instance, written against `clinical-explain` and reviewed —
  // and replacing one from a conversation would let a chat rewrite a clinical document behind its
  // reader. GENERATION is the assistant's, and the contract it writes to is the one this page's prose
  // was written to (`clinical-explain`, published over MCP by `get_skill`), so the two cannot come out
  // in different voices. PERSISTENCE is DESIGNER'S OWN ACT and has no path from here at all — which is
  // what the tool surface being read-only enforces rather than merely documents.
  //
  // The question is written for the reader's benefit as much as the model's: it lands in the transcript
  // verbatim, so a reader who scrolls back sees what was actually asked on their behalf.
  const explainAgain = useCallback((target: ExplainTarget) => {
    openAssistant({
      define: target.define,
      question: target.define
        ? `Explain the criterion "${target.label}" again, in plain language for a clinician: what it `
          + `includes, what it excludes, and why it is there. Load the clinical-explain skill first and `
          + `write to that contract, and cite the figures you use rather than stating them.`
        : `Explain the "${target.label}" section of this measure again, in plain language for a `
          + `clinician. Load the clinical-explain skill first and write to that contract.`,
    });
  }, [openAssistant]);

  /* APP-ASSISTANT — Control+` opens and closes the drawer. The binding and its reasoning live with
   *  the drawer (`useAssistantShortcut`); what belongs here is the STATE it flips.
   *
   *  THE SAME ACT AS THE BAR BUTTON — one toggle, so the button's `aria-pressed` reports the keyboard's
   *  result and the two cannot disagree about whether the drawer is open. Opening from the keyboard is
   *  the plain case: no explicit scope, so the drawer follows the selection (`askSelection`). A stale
   *  `askDefine` left behind by a re-explain would otherwise silently re-scope a conversation the reader
   *  started from somewhere else. */
  const toggleAssistant = useCallback(() => {
    setAskOpen((open) => {
      if (!open) { setAskDefine(null); setAskQuestion(undefined); }
      return !open;
    });
  }, []);
  useAssistantShortcut(Boolean(definition), toggleAssistant);

  // APP-ASSISTANT-CONTEXT — the scope, resolved ONCE here and handed down, exactly as the workbench's
  // is (`profileScope`). It PREFILLS tool arguments and is never read as ambient state: the drawer sends
  // it explicitly, the endpoint puts it in the prompt as values, and every tool still takes its own
  // arguments — which is what keeps the same tools usable by an agent that has no UI (`SVC-MCP-TOOLS`).
  const askSelection = useMemo<AssistantSelection>(() => ({
    resultSet: run?.resultSet,
    library: definition?.name,
    version: definition?.id.split("@")[1],
    // An explicit scope (a reference lens's Ask) wins over the selection; otherwise the selection is
    // the default. Only a DEFINE is a node the tools can address — a retrieve scopes the inspector but
    // is not a `NodeAddress`, so sending one would prefill an argument no tool could use.
    define: askDefine ?? (selected?.kind === "define" ? selected.name : undefined),
    dataSourceId: selectedSourceId ?? undefined,
    periodStart, periodEnd,
    // The LABELS — what the screen calls those identifiers. Not tool arguments; the assistant writes
    // with them so it does not say `HypertensionBPControl` and `synthetic-population-small` at a
    // clinician who calls them something else. Resolved here because this is where both are already
    // known — the alternative is the assistant spending a tool round-trip on what is on the screen.
    measureTitle: definition?.title ?? undefined,
    dataSourceName: sources.find((s) => s.id === selectedSourceId)?.name ?? undefined,
  }), [definition, askDefine, selected, selectedSourceId, periodStart, periodEnd, sources, run?.resultSet]);

  /* SPEC-EXPLAIN-VIEW — FLIPPING THE LAYER MUST NOT MOVE THE READER. Turning Explain off deletes prose
     from ABOVE the viewport as well as inside it, so the document shortens under a fixed `scrollTop` and
     the reader is thrown somewhere further down — worst exactly where the control is most useful, deep in
     a criterion they are checking against the CQL. The toggle is advertised as safe to flip repeatedly
     while reading, and losing your place is the one thing that makes it unsafe.
     So the scrollport is RE-ANCHORED, not restored: the topmost structural element still in view is
     remembered with its offset from the port's top edge, and after the re-render the port is scrolled so
     that same element sits at that same offset. The anchors are section headings and criteria
     (`h2[id]`, `[data-crit-name]`) because those are DERIVED from the ELM and survive the filter —
     anchoring to a narrated block would anchor to something the toggle is about to delete. */
  const docRef = useRef<HTMLElement | null>(null);
  const anchor = useRef<{ key: string; offset: number } | null>(null);
  const toggleExplain = useCallback(() => {
    const port = docRef.current;
    if (port) {
      const top = port.getBoundingClientRect().top;
      for (const el of port.querySelectorAll<HTMLElement>("[data-crit-name], h2[id]")) {
        const box = el.getBoundingClientRect();
        // The first element whose bottom is still below the port's top edge: what the reader is reading.
        if (box.bottom <= top + 1) continue;
        const key = el.getAttribute("data-crit-name") ?? el.id;
        if (key) anchor.current = { key, offset: box.top - top };
        break;
      }
    }
    setExplain((v) => !v);
  }, []);
  // Before paint, so the correction is never a visible jump. A remembered anchor that no longer exists
  // (the section itself was filtered out) leaves the scroll alone — there is nothing truer to restore to.
  useLayoutEffect(() => {
    const port = docRef.current, want = anchor.current;
    anchor.current = null;
    if (!port || !want) return;
    const el = port.querySelector<HTMLElement>(
      `[data-crit-name="${CSS.escape(want.key)}"], h2[id="${CSS.escape(want.key)}"]`,
    );
    if (!el) return;
    port.scrollTop += (el.getBoundingClientRect().top - port.getBoundingClientRect().top) - want.offset;
  }, [explain]);
  const specDoc = useMemo(
    () => (specModel ? buildSpecDocument(specModel, elmByLibrary, shownOverlay) : null),
    [specModel, elmByLibrary, shownOverlay],
  );

  // SPEC-DATA-REQUIREMENTS — every read the VISIBLE logic performs. Built from `visibleSpec`, so
  // ELIMINATION EXTENDS TO READS by construction: a define nothing reaches contributes no data
  // requirement, which is what keeps a helper library's hundreds of published rows out of a short table.
  //
  // THE READS ARE THE SERVER'S ANSWER now (`SPEC-EXPLAIN-INPUTS`): `referenceSets` rides back on the
  // same `/api/translate` response as the ELM, so the model this renders and the reads resolved into it
  // describe one compilation. What happens here is the INVERSION into per-read-with-call-sites plus the
  // `mustSupport` join, neither of which is a fact about the CQL.
  const retrieves = useMemo(
    () => (visibleSpec ? buildRetrieves(visibleSpec, referenceSets, dataReqs) : []),
    [visibleSpec, referenceSets, dataReqs],
  );

  const metricDefines = useMemo(() => {
    if (!specModel) return [];
    // Every entry the document can render a figure for — populations and library entries alike, since
    // the nav shows the same figure as the section it points at (`APP-NAV-METRIC`).
    //
    // FUNCTIONS ARE EXCLUDED HERE, once, rather than filtered at each surface. A function has no figure
    // and never will: it is evaluated per call site with arguments, not once per subject, so there is no
    // population it counts (`SPEC-METRIC`). The page already knew this and rendered no metric for one;
    // the nav did not, so 52 `fn` rows carried subject counts for something that has no subjects. Not
    // asking for the figure is the fix — a filter on each surface is two chances to disagree.
    return [...specModel.populations, ...specModel.libraries.flatMap((l) => l.entries)]
      .filter((e) => e.kind !== "function")
      .map((e) => ({ name: e.name, library: e.library, resultType: e.variants[0]?.returns ?? null }));
  }, [specModel]);
  const readRequest = useMemo(
    () => retrieves.map((r) => ({ key: r.key, type: r.type, profiles: r.profiles })), [retrieves]);
  const flowGates = useMemo(
    () => buildFlowRequest(specDoc, shownOverlay?.scoring ?? null), [specDoc, shownOverlay]);
  // RUN-CLIENT-RESULT-SETS — every figure on the document arrives together from these exact pins.
  const [resultLibrary, resultVersion] = definition?.id.split("@") ?? [];
  const derivations = useSelectionDerivations(
    resultLibrary && resultVersion && selectedSourceId && draft && run?.resultSet ? {
      library: resultLibrary, version: resultVersion,
      scope: { dataSourceId: selectedSourceId, periodStart, periodEnd, draft, resultSet: run.resultSet,
               warehouseId: chosenWarehouse },
      defines: metricDefines, reads: readRequest, gates: flowGates,
    } : null);
  const metrics = useMemo(
    () => buildMetricSet(derivations.metrics, shownOverlay?.scoring ?? null), [derivations.metrics, shownOverlay]);
  const readCounts = useMemo(() => derivations.reads
    ? new Map(derivations.reads.reads.map((r) => [r.key, r.subjects])) : null, [derivations.reads]);
  const flow = useMemo(() => buildFlow(computed ? derivations.flow : null), [derivations.flow, computed]);

  // The mode gates what the document DISPLAYS: with no data source the published specification reads on
  // its own, and every inline figure is ABSENT rather than each printing "not computed" — one statement
  // of absence (the compute pill's own gray dot) instead of one per element, forty to a page.
  const dispMetrics = computed ? metrics : null;
  const dispFunnel = useMemo(() => metricFunnel(dispMetrics), [dispMetrics]);

  // SPEC-DERIVED-APPENDIX — the terminology the VISIBLE logic names. Built from `visibleSpec`, so
  // elimination reaches terminology by the same rule it reaches defines and reads.
  const namedTerminology = useMemo(() => {
    // SHOW ELIMINATED reveals everything the document holds back — and terminology is held back for
    // TWO different reasons, only one of which is elimination. A value set referenced only by an
    // unreachable define is hidden by reachability. One a helper library DECLARES and nothing references
    // — its define reads a Patient element, not the value set — was never hidden by a reachability rule.
    //
    // Filtering on reference alone made the toggle reveal the first kind and not the second, which
    // reads as the toggle being broken: the reader asked to see what was hidden and something stayed
    // hidden. On, the filter is every DECLARATION; off, it is what live logic names.
    if (showEliminated && specDoc) {
      return {
        valueSets: new Set(specDoc.appendix.valuesets.map((v) => `${v.library}::${v.name}`)),
        codes: new Set(specDoc.appendix.codes.map((c) => `${c.library}::${c.name}`)),
      };
    }
    return visibleSpec ? referencedTerminology(visibleSpec, referenceSets)
      : { valueSets: new Set<string>(), codes: new Set<string>() };
  }, [showEliminated, specDoc, visibleSpec, referenceSets]);

  // APP-ASSISTANT-REFS — the reference lens, built HERE because two surfaces render references now:
  // the document, and the assistant's answers in the drawer under it. It is the same hook the document
  // calls with the same inputs (`specLens.ts`), so a define named in an answer resolves to the entry a
  // define named in a sentence resolves to — one lens, one gesture, one set of verbs.
  //
  // Called UNCONDITIONALLY, before there is a model to resolve against, because that is what a hook
  // requires. Nothing is lost by it: an absent model resolves nothing, and a reference that resolves to
  // nothing renders as the words the assistant wrote, which is the degradation every binding in the
  // document already follows.
  const assistantLens = useSpecLens({
    definitionId: definition?.id ?? "", spec: visibleSpec, doc: specDoc, metrics: dispMetrics,
    live: computed, funnel: dispFunnel, profileScope, retrieves, namedTerminology,
    readCounts: computed ? readCounts : null, parameters: citableParams(execParams),
    selected, onSelect: selectInSpec, onNavigate: selectFromNav,
  });

  // SPEC-LIBRARY-DENSITY — which libraries have been explained at all, from the UNFILTERED overlay.
  // It decides whether a library section opens Verbose or Compact, and that is a property of the
  // narration corpus, not of the reader's Explain preference: collapsing a narrated library to rows because
  // someone turned Explain off would change the shape of the document to answer a different question.
  const narratedLibraries = useMemo(() => {
    const out = new Set<string>();
    for (const key of Object.keys(overlay?.elements ?? {})) {
      const at = key.indexOf("::");
      if (at > 0) out.add(key.slice(0, at));
    }
    return out;
  }, [overlay]);

  // The nav's section INVENTORIES, derived here rather than inside the pane so the pane takes two
  // computed objects instead of the whole overlay. Both are contracts with the page: a nav row must
  // never scroll nowhere, and must never carry a different number from the heading it scrolls to.
  const overviewSections = useMemo(() => ({
    intent: Boolean(shownOverlay?.overview?.intent?.statement),
    rationale: Boolean(shownOverlay?.overview?.intent?.clinicalRationale),
    recommendation: Boolean(shownOverlay?.overview?.intent?.recommendation),
    scope: Boolean(shownOverlay?.overview?.scope?.inScope || shownOverlay?.overview?.scope?.nonGoals?.length),
    guidance: Boolean(shownOverlay?.overview?.guidance),
  }), [shownOverlay]);

  // SPEC-DERIVED-APPENDIX — the nav counts the DERIVED rows, the same ones the page renders.
  const appendixSections = useMemo(() => {
    if (!specDoc) return undefined;
    return {
      // Per RESOURCE, matching how the section is grouped — the nav points INTO the section, not
      // merely at it, so each row has to name a heading that exists.
      dataRequirements: [...retrieves.reduce((m, r) => m.set(r.type, (m.get(r.type) ?? 0) + 1),
        new Map<string, number>())].map(([type, count]) => ({ type, count })),
      // The COMPARED-AGAINST subset only: terminology that filters a read is stated with that read
      // under Data requirements, so counting it here too would double it in the nav. The full
      // registry, minus only what elimination removed.
      valuesets: specDoc.appendix.valuesets.filter(
        (v) => namedTerminology.valueSets.has(`${v.library}::${v.name}`)).length,
      codes: specDoc.appendix.codes.filter(
        (c) => namedTerminology.codes.has(`${c.library}::${c.name}`)).length,
      parameters: specDoc.appendix.parameters.length,
      // A row exists exactly when the licence section carries the heading it scrolls to.
      notices: [
        ...(shownOverlay?.citations?.length ? [{ id: "mw-app-refs", label: "References" }] : []),
        ...(shownOverlay?.copyright ? [{ id: "mw-app-copyright", label: "Copyright" }] : []),
        ...(shownOverlay?.disclaimer ? [{ id: "mw-app-disclaimer", label: "Disclaimer" }] : []),
      ],
    };
  }, [specDoc, retrieves, namedTerminology, shownOverlay]);

  // APP-URL-ROUTING / APP-MEASURE-FLOW — land the reader on a criterion: one arriving in the
  // URL from outside (a pasted address, Back), or one the nav selected (`selectFromNav` clears the
  // stamp so this fires). Placed after `specDoc` because it is the document's existence that decides
  // whether the section is in the DOM yet: a pasted address resolves only once the compile and the
  // overlay have landed, so the effect re-runs on `specDoc` rather than firing once into an empty
  // article. Running AFTER the commit it is keyed on is the point: the reveals that expand a collapsed
  // card, group or section for this selection are layout effects of that same commit, so the scroll a
  // frame later always measures the expanded page. Selection made IN the spec is skipped —
  // `selfSelected` matches then, and the reader is already there.
  useEffect(() => {
    if (view !== "define" || !specDoc) return;
    if (!selectedToken || selectedToken === selfSelected.current) return;
    selfSelected.current = selectedToken;
    const frame = requestAnimationFrame(() => scrollToCrit(selectedToken));
    return () => cancelAnimationFrame(frame);
  }, [selectedToken, view, specDoc]);

  // The same gate for a HEADING queued by `landOnAnchor` from another lens — one frame after the
  // Specification mounts, or the query runs against an article that is not there yet.
  useEffect(() => {
    if (view !== "define" || !specDoc || !pendingAnchor.current) return;
    const id = pendingAnchor.current;
    pendingAnchor.current = null;
    const frame = requestAnimationFrame(
      () => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, [view, specDoc]);

  function navigateDep(name: string) {
    setDep({ name, source: resolvedIncludes.find((i) => i.name === name)?.source ?? null });
    setView("cql");
  }

  // Content to render in the body — the chrome and toolbar render unconditionally.
  // KNOWLEDGE-FAIL-LOUD — an outage and an empty catalogue are different states and say so. Only
  // a catalogue that really came back empty says which tag declares a knowledge source.
  const bodyContent = loadError && !definitions.length ? (
    <div style={{ flex: 1, display: "grid", placeItems: "center" }}>
      <div className="mw-empty" style={{ maxWidth: "52ch" }}>
        <p>We're having trouble connecting. Refreshing your browser, or try again later.</p>
        <p><span className="err">{loadError}</span></p>
        {!loaded ? <p><span className="mw-spin" /> Retrying…</p> : null}
      </div>
    </div>
  ) : !loaded ? (
    <div style={{ flex: 1, display: "grid", placeItems: "center" }}><div className="mw-empty">Loading measures…</div></div>
  ) : !definitions.length ? (
    <div style={{ flex: 1, display: "grid", placeItems: "center" }}>
      <div className="mw-empty" style={{ maxWidth: "44ch" }}>
        <p>No measures found. No schema you can read is tagged <code>cql_databricks_knowledge</code>.</p>
      </div>
    </div>
  ) : null;

  return (
    <div className="mw" data-mw-theme={themeAttr(chrome.theme)}>
      <div className="mw-app">

        {/* DESIGNER-TOP-BAR — ONE bar, three zones. The breadcrumb's tail is the artifact selector (which
            is also the File surface, Navigator); the CENTRE carries the execution context passed below;
            the right end holds the app's own toggles, which this screen does not supply.

            APP-ONE-SURFACE — the lens bar that used to fill the centre is GONE rather than emptied.
            With Validate in its own module and Analyze withdrawn there is one destination, and a switcher
            between one place and itself is chrome a reader pays a glance for and gets nothing back from.
            Execution sits in the middle of the bar; Explain and the compute pill are on the right
            beside controls touched once a session. */}
        {/* APP-ASSISTANT — `moduleTools` is the drawer's door. A toggle, so it shows its state
            (`aria-pressed`), which is the same argument that took tour/Settings/appearance out of the
            account menu. `Sparkle` because that is the assistant mark the retired inspector tab already
            used, and a reader should meet one mark for one thing. Rendered only with a measure open:
            the assistant's scope is the measure, and a door onto an empty room is not a door. */}
        <TopBar {...chrome}
          moduleTools={definition && (
            <button className="mw-icon-btn" aria-pressed={askOpen}
              aria-label="Assistant" title="Ask about this measure"
              onClick={toggleAssistant}>
              <Sparkle size={16} aria-hidden="true" />
            </button>
          )}
          crumb={definition && (
            <Navigator definition={definition} definitions={definitions}
              onOpenDefinition={(id) => openArtifact(id, { view: "define" })}
              dep={dep} onBack={() => { setDep(null); setView("define"); }} />
          )}
          context={definition && <>
            {/* SPEC-EXPLAIN-VIEW / SPEC-EXPLAIN-AGAIN — the interpreted layer, on or off, plus the one
                act that belongs to the same subject behind its caret (`ExplainMenu`, which carries why
                the two share a control). Absent in the dependency view, which is another library's raw
                source and carries no narration to filter — and therefore nothing to re-explain. */}
            {view === "define" && (
              <ExplainMenu explain={explain} onToggle={toggleExplain}
                target={explainTarget} onExplainAgain={explainAgain}
                narration={{ library: openLibrary, exists: narratedLibraries.has(openLibrary),
                             running: narration?.status === "running" }}
                onGenerate={generate} />
            )}
            {/* DESIGNER-SPACE-CONTEXT — show the active Data Source name when the measure belongs to one. */}
            {/* APP-EXEC-PARAMS — the execution parameters, dialled in the bar. They scope every count,
                rate and funnel on the page, and a value a reader changes while reading is right here. */}
            <ExecParamBar params={execParams} />
            {/* APP-EXECUTION-PILL — the execution states: Data Source, Compute (the SQL warehouse) and
                Updates (Manual or Automatic, APP-UPDATE-MODE). */}
            {/* ONE progress figure, read once and given to both surfaces — the collapsed trigger's
                hairline and the popover's badge bar. Derived twice, the pill could report a different
                fraction from the badge it opens onto, which is the two-surfaces-disagreeing failure in
                miniature. */}
            <ComputeMenu mode={mode} running={running} compiling={compiling}
              progress={running && runStage ? stageProgress(runStage) : null}
              stageBadge={
                <span className={"badge" + (computed ? "" : " static") + (running ? " running" : "")
                  + (runStatus.kind === "failed" ? " failed" : "")}
                  style={running && runStage ? { "--run-pct": `${stageProgress(runStage) * 100}%` } as React.CSSProperties : undefined}>
                  <span className="d" />
                  {running ? stageLabel(runStage ?? null) : runStatusWhen(runStatus) ? (
                    <span className="tx"><span>{runStatusText(runStatus)}</span>
                      <span className="when">{runStatusWhen(runStatus)}</span></span>
                  ) : runStatusText(runStatus)}
                </span>
              }
              sources={sources} selectedSourceId={selectedSourceId}
              warehouses={warehouses} defaultWarehouseId={defaultWarehouseId} warehouseId={warehouseId}
              warehouseError={warehouseError}
              updateMode={updateMode}
              onModeChange={() => { setSelectedSourceId(null); setMode("static"); }}
              onSourceChange={setSelectedSourceId}
              onWarehouseChange={(id) => setWarehouseId(id === defaultWarehouseId ? null : id)}
              onUpdateModeChange={setUpdateMode}
              runDisabled={running || compiling || !source || !selectedSourceId}
              onRefresh={() => { if (selectedSourceId) doRunSource(source, selectedSourceId, "refresh"); }} />
          </>} />

        {/* Loading / empty body — shown while definitions load or when none exist. */}
        {bodyContent}

        {/* definition-dependent content: error banner, body, inspector */}
        {definition ? <>
          {derivations.error && <div className="mw-error" role="alert">{derivations.error}</div>}
          {/* APP-NARRATIVE-OVERLAY — a generation in progress, or why it showed nothing. */}
          {narration && <NarrationStatus state={narration} onDismiss={() => setNarration(null)} />}
          {/* Run error banner — subtle, dismissible, collapsible. */}
          {runError && <div className={`mw-error${errorExpanded ? " expanded" : ""}`} role="alert">
            <div className="mw-error-summary">
              <span className="mw-error-msg">{runError.split("\n")[0]}</span>
              {runError.includes("\n") && (
                <button className="mw-error-toggle" onClick={() => setErrorExpanded(e => !e)}>
                  {errorExpanded ? "Hide" : "View Details"}{" "}
                  {errorExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </button>
              )}
              <button onClick={() => { setRunError(null); setErrorExpanded(false); }} aria-label="Dismiss"><X size={14} /></button>
            </div>
            {errorExpanded && <pre className="mw-error-detail">{runError}</pre>}
          </div>}

          {/* body */}
          <div className="mw-body" data-nav={panes.nav} data-wb={panes.workbench}>
            <NavPane
              dep={dep} definition={definition} source={source}
              selected={selected} onSelectDefine={selectFromNav} onAnchor={landOnAnchor}
              specModel={specModel} visibleSpec={visibleSpec} specDoc={specDoc} retrieves={retrieves}
              overviewSections={overviewSections} appendixSections={appendixSections}
              metrics={dispMetrics}
              showEliminated={showEliminated} onShowEliminatedChange={setShowEliminated}
              defineSort={defineSort} onDefineSortChange={setDefineSort}
              navTab={navTab} onNavTab={(t) => setParams({ nav: t === "outline" ? null : t }, { replace: true })}
              size={panes.nav} onSize={(s) => setPaneSize("nav", s)}
            />

            {/* SPEC-EXPLAIN-VIEW — the scrollport the Explain toggle re-anchors (`toggleExplain`). */}
            <main className="mw-doc" ref={docRef}>
              <div className={"mw-doc-inner" + (!dep && view === "define" ? " read" : "")}>
                {dep ? (
                  <>
                    <div className="mw-dep-ribbon"><span className="ro">read-only dependency</span>
                      <span>Viewing <b>{dep.name}</b>, included by {definition.title ?? definition.name}. Edits happen in its own library.</span></div>
                    <div className="mw-embed">
                      <div className="mw-embed-bar"><span className="fn">{dep.name}.cql</span><span className="st">included library</span></div>
                      {dep.source
                        ? <CqlCode className="mw-spec-logic mw-embed-src" source={dep.source} />
                        : <div className="mw-empty">Source for <b>{dep.name}</b> isn’t in the registry yet.</div>}
                    </div>
                  </>
                ) : view === "define" ? (
                  sourceLoading ? (
                    <div className="mw-empty"><span className="mw-spin" /> Loading source…</div>
                  ) : sourceError ? (
                    <div className="mw-empty"><span className="err">{sourceError}</span></div>
                  ) : elmInfo && visibleSpec && specDoc ? (
                    <MeasureNarrative definition={definition} metrics={dispMetrics} live={computed}
                      readCounts={computed ? readCounts : null}
                      funnel={dispFunnel}
                      selected={selected} onSelect={selectInSpec} onNavigate={selectFromNav}
                      /* SPEC-EXPLAIN-AGAIN — the document AIMS the bar's re-explain (a section's Explain
                         mark) and can also FIRE it (the disclaimer popover's own verb). Two call sites,
                         one act: the same target shape and the same callback, so a re-explanation asked
                         for from the page and one asked for from the toolbar cannot differ. */
                      onExplainScope={setExplainTarget}
                      onExplainAgain={explainAgain}
                      onRunCell={runCell}
                      results={computed ? (run?.results ?? null) : null}
                      spec={visibleSpec} elmInfo={elmInfo}
                      overlay={shownOverlay} doc={specDoc} narratedLibraries={narratedLibraries}
                      /* SPEC-EXPLAIN-VIEW — the STATE as well as the filtered overlay. The filter says
                         which prose survives; this says what a section is once its prose is gone, which
                         no amount of filtering can express: off, the section is its CQL. */
                      explain={explain}
                      retrieves={retrieves} namedTerminology={namedTerminology}
                      parameters={citableParams(execParams)}
                      /* SPEC-REF-LENS — the same scope the inspector pane resolves, so a define popped from
                         a sentence and the same define pinned in the pane profile one evaluation. */
                      profileScope={profileScope} />
                  ) : <div className="mw-empty">{compiling ? <><span className="mw-spin" /> Compiling…</> : "Waiting for source…"}</div>
                ) : null}
              </div>
            </main>

            {/* APP-WORKBENCH — the right pane. Selection SCOPES it; it does not mirror the
                Specification, which already renders everything an inspector would have repeated. */}
            <Workbench definition={definition} spec={specModel} doc={specDoc}
              selected={selected} onSelect={setSelected} onNavigate={selectFromNav}
              retrieves={retrieves}
              metrics={dispMetrics} live={computed}
              scope={profileScope}
              funnel={dispFunnel}
              size={panes.workbench} onSize={(s) => setPaneSize("workbench", s)}
              referenceSets={referenceSets} />

            {/* APP-ASSISTANT — the conversation. A SIBLING of the three panes, sharing the reading
                column's grid cell so it overlays the document rather than resizing it
                (`APP-STABLE-LAYOUT`); the CSS carries why that beats a fourth track. */}
            <AssistantDrawer open={askOpen} onClose={() => setAskOpen(false)}
              selection={askSelection} initialQuestion={askQuestion}
              /* APP-ASSISTANT-REFS — what an answer's inline references resolve against, and the
                 funnel a `cql-databricks:funnel` artifact renders. The SAME bundle the document resolves
                 through, so the two surfaces cannot disagree about what a reference means. */
              lens={assistantLens} funnel={dispFunnel} />

          </div>
        </> : null}
      </div>
    </div>
  );
}
