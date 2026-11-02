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

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Definition } from "../catalog.ts";
import { fetchDefinitions, fetchLibrarySource, fetchSpecification, fetchDataRequirements, saveLibrary,
  type DataRequirement, type SpecificationOverlay } from "../library-client.ts";
import { useRevision } from "../use-revision.ts";
import { runMeasureStreaming, fetchEnvironments, fetchWarehouses, reopenResults, type RunTrigger } from "../run-client.ts";
import { currentEnvironment, setCurrentEnvironment } from "../environment.ts";
import { translate } from "../authoring/diagnostics.ts";
import { CqlCode } from "../authoring/cqlHighlight.tsx";
import { parseElmDefines, type ElmLibraryInfo } from "../authoring/elmDefines.ts";
import type { EnvironmentRecord, RunResult, RunStage, TranslateResult, WarehouseRecord } from "../wire.ts";
import { MeasureNarrative } from "./MeasureNarrative.tsx";
import { TopBar, type TopBarChrome } from "./TopBar.tsx";
import { Navigator } from "./Navigator.tsx";
import { NewLibraryForm, type NewTarget } from "./NewLibraryForm.tsx";
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
import { Sparkle, EditCql } from "../icons.tsx";
import { useAutoSave, writeLabel, writeTitle } from "./autoSave.ts";
import { withInclude } from "./includeLibrary.ts";
import { PublishForm } from "./PublishForm.tsx";
import { ErrorBanner } from "./ErrorBanner.tsx";
import type { NavScope } from "./ScopeNav.tsx";
import { DefineEditContext, DefineEditor, LazyCqlEditor, type DefineEdit } from "./defineEdit.tsx";
import {
  defaultExampleId, periodYears, periodParam, themeAttr,
  parseSelection, selectionToken, parseNavTab, loadPanes, savePanes, citableParams, storedUpdateMode, runStatusText, runStatusWhen,
  type MeasureView, type ExecMode, type ComputeTarget, type UpdateMode, type DeepLink, type Selection, type RunStatus,
  type PaneSize, type PaneState,
} from "./model.ts";
import { buildMetricCohorts, buildMetricSet, cohortOf, metricFunnel, type MetricSet } from "./specMetrics.ts";
import { buildSpecModel, filterSpecModel, sortSpecModel, type SpecEntry, type SpecModel, type SpecSortKey } from "./specModel.ts";
import { buildSpecDocument, type SpecDocument } from "./specDocument.ts";
import { buildRetrieves, referencedTerminology, type SpecRetrieve } from "./specRetrieves.ts";

const VALID_VIEWS = new Set<MeasureView>(["define", "cql"]);

/** DOC-CQL-EDIT — one compile of one library's CQL. */
type Compiled = { lib: string; source: string; result: TranslateResult };

/** DOC-CQL-EDIT — the define open in place: the draft it was opened on (`base`), the characters of it the
 *  editor replaces (`start`, `end`), what the editor now holds, and the compile the page holds still on. */
type InPlace = { key: string; base: string; start: number; end: number; text: string; firstLine: number;
                 frozen: Compiled };

// Persist the Compute menu selections across page reloads via localStorage.
const STORAGE_KEY = "cql-databricks:compute";
// APP-COMPUTE-TARGET — `warehouseId` is the chosen SQL warehouse; null or absent means the deployment's.
// DOC-ENVIRONMENT-PICKER — `computing` is whether the picked environment computes (None when false); the
// environment itself is kept by `environment.ts`, which every knowledge read uses.
interface StoredCompute { mode?: ExecMode; compute?: ComputeTarget; computing?: boolean; updateMode?: UpdateMode;
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
  // RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — `?at=` is the location the catalog lists: one tagged store or one folder.
  // Absent, it lists all of tagged knowledge, as before locations. Opening a measure from a folder IS setting this
  // to the folder, so it lives in the URL beside `?open=` and a pasted address reopens the same copy.
  const openAt = new URLSearchParams(location.search).get("at");

  // APP-URL-ROUTING — derive lens from URL path; push changes to history.
  const urlView = viewFromPath(location.pathname);
  const view: MeasureView = deepLink?.lens ?? urlView ?? "define";

  const [run, setRun] = useState<RunResult | null>(null);
  const [runStage, setRunStage] = useState<RunStage | null>(null);
  const running = runStage !== null;
  const [runError, setRunError] = useState<string | null>(null);
  // DOC-CQL-EDIT — the latest compile, with the library and the exact source it compiled. The PAGE reads its
  // own pair (`page`, below): the last one that compiled, held still while a define is open in place.
  const [compiled, setCompiled] = useState<Compiled | null>(null);
  const [lastGood, setLastGood] = useState<Compiled | null>(null);
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
  const openArtifact = useCallback((id: string, opts: { replace?: boolean; view?: MeasureView } = {},
                                    at?: string | null) => {
    selfSelected.current = null;
    setParams({ open: id, crit: null, ...(at !== undefined ? { at } : {}) }, opts);
  }, [setParams]);

  // SPEC-NAV-COMPOSITION — which arrangement the left pane is in. It lives in the URL for the same
  // reason `?crit=` does: a pasted address has to reproduce what the sender was looking at.
  //
  // `?pop=` is GONE with the Flow tab that owned it. Composition draws every population at once, so
  // there is no root to address; an old link carrying one is simply ignored.
  const navTab = useMemo(
    () => parseNavTab(new URLSearchParams(location.search).get("nav")), [location.search]);
  // APP-EXEC-MODE — the OFFLINE state: `static` reads the spec. The real targets are the environments
  // (`runEnvironment`) below.
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
  // DOC-ENVIRONMENT-PICKER — the environments the picker offers and the one the Designer runs in: the picked one,
  // else the default once the list is read. `runEnvironment` is it while it computes, and null for None.
  // API-driven (APP-NO-UI-MOCK).
  const [environments, setEnvironments] = useState<EnvironmentRecord[]>([]);
  const [environmentError, setEnvironmentError] = useState<string | null>(null);
  const [environment, setEnvironment] = useState<string | null>(currentEnvironment());
  const [computing, setComputing] = useState<boolean>(stored.current.computing ?? false);
  const runEnvironment = computing ? environment : null;
  const computed = runEnvironment !== null;
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
  // RUN-CLIENT-RESULT-SETS — `draft` labels the last run's complete selection; every figure on the page reads that
  // selection's results.
  const draft = run?.draft || undefined;
  const profileScope = useMemo<ProfileScope | null>(() => (
    runEnvironment && draft && run?.resultSet
      ? { environment: runEnvironment, periodStart, periodEnd, draft, resultSet: run.resultSet,
          warehouseId: chosenWarehouse } : null
  ), [runEnvironment, periodStart, periodEnd, draft, run?.resultSet, chosenWarehouse]);
  // APP-EXEC-PARAMS — the execution parameters this run is compiled with. The measurement period is
  // the only one the app has a source for today; the rest belong to the measure package the compiler
  // emits (a measure declares its own parameters), so they join this list when that data is wired — the
  // parameter bar needs no change to show them.
  const execParams = useMemo(() => [periodParam(periodYear, setPeriodYear)], [periodYear]);
  const token = useRef(0);
  const busy = useRef(false);
  useEffect(() => {
    fetchEnvironments().then((list) => {
      setEnvironments(list); setEnvironmentError(null);
      // DOC-ENVIRONMENT-PICKER — the default is preselected; a picked environment the user no longer sees is not kept.
      setEnvironment((picked) => (list.some((e) => e.name === picked) ? picked
        : list.find((e) => e.default)?.name ?? null));
    }).catch((e) => setEnvironmentError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => { setCurrentEnvironment(environment); }, [environment]);
  useEffect(() => {
    fetchWarehouses().then((list) => {
      setWarehouses(list.warehouses); setDefaultWarehouseId(list.default); setWarehouseError(null);
    }).catch((e) => setWarehouseError(e instanceof Error ? e.message : String(e)));
  }, []);
  // Persist every change so the next page load restores the last selection.
  useEffect(() => { saveCompute({ mode, compute, computing, updateMode, periodYear, warehouseId }); },
    [mode, compute, computing, updateMode, periodYear, warehouseId]);

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
  // DOC-OPEN-SAVE — bumped when New or Save As creates a library, so the catalog lists it.
  const [catalogGen, setCatalogGen] = useState(0);
  useEffect(() => {
    let live = true;
    let retries = 0;
    function load() {
      fetchDefinitions(openAt).then((d) => {
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
  }, [openArtifact, openAt, catalogGen]); // reloads when the location changes or a library is created

  const definition = useMemo(
    () => definitions.find((d) => d.id === selectedId) ?? definitions[0] ?? null,
    [definitions, selectedId],
  );
  // RUN-ADDRESS-LOCATION — where the open library is read, and so where its includes resolve first. Every read,
  // compile and run below carries it, so all of them describe the same copy.
  const libLocation = definition?.location ?? null;
  // DOC-CQL-EDIT — which library a compile belongs to, so one library's last good compile never renders another's.
  const libKey = `${libLocation ?? ""}\0${definition?.id ?? ""}`;
  // DOC-EDITABLE-IN-SCOPE — a library from a tagged store is published and read-only here; it is edited as a copy in
  // a folder (Save As). Nothing is asked first: a folder write is the user's own, and Databricks refuses one they
  // cannot make.
  const readOnly = libLocation?.startsWith("store:")
    ? "Published in a tagged store, which the Designer does not edit. Save a copy to a workspace folder to change it."
    : null;

  // PERF-DISCOVER-LIGHTWEIGHT — the discover endpoint omits CQL source; lazy-load it
  // from /api/libraries/{name}/{version}/source when a definition is selected. While the
  // fetch is in-flight, `source` is empty and the UI shows a loading state. If the fetch
  // fails, the error is surfaced — never silently fall back to a generated stub.
  const [loadedSource, setLoadedSource] = useState<string | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  // DOC-AUTO-SAVE — how many times the open library was read again, after a write was refused because its copy
  // changed elsewhere. Each reading is a new start for the writes (`Opened.key`).
  const [reads, setReads] = useState(0);
  useEffect(() => { setReads(0); }, [definition?.id, libLocation]);
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
    const { name, version } = definition;
    if (!name || !version) { setSourceLoading(false); return; }
    let live = true;
    fetchLibrarySource(name, version, definition.location)
      .then((s) => { if (live) { setLoadedSource(s); setSourceError(null); } })
      .catch((e) => { if (live) setSourceError(`Failed to load source: ${e instanceof Error ? e.message : e}`); })
      .finally(() => { if (live) setSourceLoading(false); });
    return () => { live = false; };
  }, [definition?.id, libLocation]); // eslint-disable-line react-hooks/exhaustive-deps
  // DOC-CQL-EDIT — `edited` is the CQL as edited, or null while it is as opened. Everything below reads the DEBOUNCED edit, so the
  // document recompiles a beat after typing stops rather than on every key.
  const [edited, setEdited] = useState<string | null>(null);
  const [editedSource, setEditedSource] = useState<string | null>(null);
  useEffect(() => {
    if (edited === null) { setEditedSource(null); return; }
    const t = setTimeout(() => setEditedSource(edited), 700);
    return () => clearTimeout(t);
  }, [edited]);
  useEffect(() => { setEdited(null); }, [loadedSource]);
  // Source is empty while loading or on error — never use a generated stub.
  const source = editedSource ?? loadedSource ?? "";

  // DOC-ENVIRONMENT-PICKER / SVC-RUN-EVAL — run in the picked environment, by its name; the backend binds its
  // published views.
  // RUN-CLIENT-REQUEST-REOPEN — the calculation whose results were last looked up or shown, so a finished
  // run is not followed by a redundant reopen of what it just returned.
  const scopeKey = `${libLocation ?? ""}\0${definition?.id ?? ""}\0${source}\0${runEnvironment ?? ""}\0${periodStart}\0${periodEnd}\0${warehouseId ?? ""}\0${updateMode}`;
  const currentKey = useRef(scopeKey);
  currentKey.current = scopeKey;
  const lookedUp = useRef<string | null>(null);
  // APP-UPDATE-MODE — "Calculation changed since last run": the last results shown were for this measure,
  // environment and period, but other CQL. `shownNow` is this render's calculation, read through a ref so
  // the memoized callbacks below never record a stale one.
  type Shown = { id: string; environment: string; period: string; source: string };
  const shownNow = useRef<Shown | null>(null);
  shownNow.current = definition && runEnvironment
    ? { id: definition.id, environment: runEnvironment, period: `${periodStart}/${periodEnd}`, source } : null;
  const shownFor = useRef<Shown | null>(null);
  // APP-RUN-LOG — each caller names its action; Refresh recomputes, Automatic computes only what is missing.
  const doRunSource = useCallback((src: string, inEnvironment: string, trigger: RunTrigger) => {
    const recompute = trigger === "refresh";
    if (!src.trim() || busy.current) return;
    busy.current = true;
    const my = ++token.current;
    setRunStage({ stage: "connecting", message: "Starting…" });
    setRunError(null);
    runMeasureStreaming(src, { environment: inEnvironment, periodStart, periodEnd, recompute, trigger,
                               warehouseId: chosenWarehouse, location: libLocation }, (stage) => {
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
  }, [periodStart, periodEnd, chosenWarehouse, libLocation]); // eslint-disable-line react-hooks/exhaustive-deps

  // RUN-CLIENT-RESULT-SETS — another scope must not display this selection's figures. A different
  // warehouse re-reads them, so a warehouse the user cannot use fails visibly (APP-COMPUTE-TARGET).
  useEffect(() => {
    ++token.current;
    setRun(null);
    setRunError(null);
  }, [source, runEnvironment, periodStart, periodEnd, definition?.id, warehouseId, libLocation]);

  const [lookup, setLookup] = useState<"checking" | "not-computed" | "changed" | null>(null);

  // RUN-CLIENT-REQUEST-REOPEN — opening a measure, or changing its CQL, data source, period or warehouse,
  // opens the user's own results for exactly that calculation, read-only. It never computes. Results
  // are always private: there is no published-results lookup (RUN-CLIENT-PUBLISHED, amended 2026-10-10).
  // A miss is "Not computed"; in Automatic it then runs Compute missing, in Manual it does not.
  useEffect(() => {
    if (!definition || !runEnvironment || !source) { setLookup(null); lookedUp.current = null; return; }
    if (running || busy.current || lookedUp.current === scopeKey) return;
    lookedUp.current = scopeKey;
    const my = ++token.current;
    setLookup("checking");
    reopenResults(source, { environment: runEnvironment, periodStart, periodEnd, warehouseId: chosenWarehouse,
                            location: libLocation })
      .then((r) => {
        if (my !== token.current) return;
        if (r.found) {
          setRun(r.result); setRunError(null); setLookup(null); shownFor.current = shownNow.current;
          return;
        }
        const prev = shownFor.current;
        setLookup(prev && prev.id === definition.id && prev.environment === runEnvironment
          && prev.period === `${periodStart}/${periodEnd}` && prev.source !== source ? "changed" : "not-computed");
        if (updateMode === "live") doRunSource(source, runEnvironment, "automatic");
      })
      .catch((e) => {
        if (my !== token.current) return;
        setLookup(null);
        setRunError(e instanceof Error ? e.message : String(e));
      });
  }, [scopeKey, running]); // eslint-disable-line react-hooks/exhaustive-deps

  // APP-UPDATE-MODE — why the page shows what it shows; never a bare "Idle".
  const tasks = runStage?.tasks ?? [];
  const runStatus: RunStatus = !runEnvironment ? { kind: "no-environment", error: environmentError ?? undefined }
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
    const settle = (result: TranslateResult) => {
      if (!current) return;
      const c = { lib: libKey, source, result };
      setCompiled(c);
      if (result.ok) setLastGood(c);
    };
    translate(source, [], { includeElm: true, location: libLocation }).then(settle).catch((e) => {
      if (!current) return;
      console.error("translate failed:", e);
      settle({ elm: "", diagnostics: [`Compile failed: ${e instanceof Error ? e.message : e}`], ok: false });
    }).finally(() => { if (current) setCompiling(false); });
    return () => { current = false; };
  }, [source, libLocation]); // eslint-disable-line react-hooks/exhaustive-deps

  // DOC-CQL-EDIT — what the page renders: while a define is open in place, the compile it opened on, so the
  // block being edited stays where it is even when its define is renamed; otherwise the last compile that
  // succeeded, so a draft that does not compile leaves the document readable (its problems are a banner).
  // `latest` is the compile of the draft itself, which the editors report.
  const [inPlace, setInPlace] = useState<InPlace | null>(null);
  const latest = compiled?.lib === libKey ? compiled : null;
  const page = inPlace?.frozen ?? (lastGood?.lib === libKey ? lastGood : null);
  const trans = page?.result ?? null;
  const pageSource = page?.source ?? "";

  // SPEC-MEASURE-METADATA — the prose the measure's FHIR resource states, quoted and attributed: the
  // Overview's description, intent, rationale, recommendation and guidance, and the measure's scoring.
  // Prose only; the document's STRUCTURE is derived from the ELM below and this cannot move it. Read
  // again when the server's revision moves (`PERF-CLIENT-REVISION`).
  const revision = useRevision();
  const [overlay, setOverlay] = useState<SpecificationOverlay | null>(null);
  useEffect(() => {
    if (!definition) { setOverlay(null); return; }
    const { name, version } = definition;
    if (!name || !version) return;
    let live = true;
    fetchSpecification(name, version, definition.location).then((o) => { if (live) setOverlay(o); })
      .catch(() => { if (live) setOverlay(null); });
    return () => { live = false; };
  }, [definition?.id, libLocation, revision]); // eslint-disable-line react-hooks/exhaustive-deps


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
  }, [source, runEnvironment]);


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
      return [name, await fetchDataRequirements(name, version, libLocation).catch(() => [])] as const;
    })).then((pairs) => { if (live) setDataReqs(new Map(pairs)); });
    return () => { live = false; };
  }, [elmInfo, compiledIncludes, libLocation]); // eslint-disable-line react-hooks/exhaustive-deps

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
  // Per library, not per edit: a re-sort on every keystroke would move the define being edited.
  useEffect(() => { setShowEliminated(false); setDefineSort("declared"); }, [libKey]);
  const specModel = useMemo<SpecModel | null>(() => {
    if (!elmInfo || !trans?.elm) return null;
    return buildSpecModel({
      root: { info: elmInfo, elm: JSON.parse(trans.elm), source: pageSource },
      includes: compiledIncludes.ok,
      unresolved: compiledIncludes.failed,
    });
  }, [elmInfo, trans, pageSource, compiledIncludes]);
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
  // APP-ASSISTANT — the drawer's open state. NOT in the URL and NOT persisted: a conversation is a
  // session, not a place. `APP-PANE-STATE` persists the panes because a pane is a layout preference the
  // reader wants back tomorrow; an open drawer restored on load would be a transcript with no
  // conversation in it.
  const [askOpen, setAskOpen] = useState(false);

  /* APP-ASSISTANT — Control+` opens and closes the drawer. The binding and its reasoning live with
   *  the drawer (`useAssistantShortcut`); what belongs here is the STATE it flips.
   *
   *  THE SAME ACT AS THE BAR BUTTON — one toggle, so the button's `aria-pressed` reports the keyboard's
   *  result and the two cannot disagree about whether the drawer is open. The drawer follows the
   *  selection (`askSelection`). */
  const toggleAssistant = useCallback(() => setAskOpen((open) => !open), []);
  useAssistantShortcut(Boolean(definition), toggleAssistant);

  // APP-ASSISTANT-CONTEXT — the scope, resolved ONCE here and handed down, exactly as the workbench's
  // is (`profileScope`). It PREFILLS tool arguments and is never read as ambient state: the drawer sends
  // it explicitly, the endpoint puts it in the prompt as values, and every tool still takes its own
  // arguments — which is what keeps the same tools usable by an agent that has no UI (`SVC-MCP-TOOLS`).
  const askSelection = useMemo<AssistantSelection>(() => ({
    resultSet: run?.resultSet,
    library: definition?.name,
    version: definition?.version,
    location: libLocation ?? undefined,
    // Only a DEFINE is a node the tools can address — a retrieve scopes the inspector but is not a
    // `NodeAddress`, so sending one would prefill an argument no tool could use.
    define: selected?.kind === "define" ? selected.name : undefined,
    periodStart, periodEnd,
    // The LABELS — what the screen calls those identifiers. Not tool arguments; the assistant writes
    // with them so it does not say `HypertensionBPControl` at a clinician who calls it something else. Resolved here because this is where both are already
    // known — the alternative is the assistant spending a tool round-trip on what is on the screen.
    measureTitle: definition?.title ?? undefined,
    environmentName: environment ?? undefined,
  }), [definition, selected, environment, periodStart, periodEnd, run?.resultSet]);

  const specDoc = useMemo(
    () => (specModel ? buildSpecDocument(specModel, elmByLibrary, overlay) : null),
    [specModel, elmByLibrary, overlay],
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

  // SPEC-POPULATION-CASCADE — the cohort each define is counted among, from placement and the declared
  // scoring. One map for the request and the metric set, so a figure is named by the cohort it was
  // counted in.
  const metricCohorts = useMemo(
    () => buildMetricCohorts(specDoc, overlay?.scoring ?? null), [specDoc, overlay]);
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
      .map((e) => {
        const { allOf, noneOf } = cohortOf(metricCohorts, e.library, e.name);
        return { name: e.name, library: e.library, resultType: e.variants[0]?.returns ?? null,
                 cohort: { allOf, noneOf } };
      });
  }, [specModel, metricCohorts]);
  const readRequest = useMemo(
    () => retrieves.map((r) => ({ key: r.key, type: r.type, profiles: r.profiles })), [retrieves]);
  const flowGates = useMemo(
    () => buildFlowRequest(specDoc, overlay?.scoring ?? null), [specDoc, overlay]);
  // RUN-CLIENT-RESULT-SETS — every figure on the document arrives together from these exact pins.
  const resultLibrary = definition?.name, resultVersion = definition?.version;
  const derivations = useSelectionDerivations(
    resultLibrary && resultVersion && runEnvironment && draft && run?.resultSet ? {
      library: resultLibrary, version: resultVersion,
      scope: { environment: runEnvironment, periodStart, periodEnd, draft, resultSet: run.resultSet,
               warehouseId: chosenWarehouse },
      defines: metricDefines, reads: readRequest, gates: flowGates, scoring: overlay?.scoring ?? null,
    } : null);
  // APP-ERROR-DISMISS — the failed read the reader closed. The hook returns one object per settled read,
  // so comparing identity hides exactly that failure.
  const [dismissedDerivations, setDismissedDerivations] = useState<typeof derivations | null>(null);
  const metrics = useMemo(
    () => buildMetricSet(derivations.metrics, overlay?.scoring ?? null, metricCohorts),
    [derivations.metrics, overlay, metricCohorts]);
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

  // The nav's section INVENTORIES, derived here rather than inside the pane so the pane takes two
  // computed objects instead of the whole overlay. Both are contracts with the page: a nav row must
  // never scroll nowhere, and must never carry a different number from the heading it scrolls to.
  const overviewSections = useMemo(() => ({
    intent: Boolean(overlay?.overview?.intent?.statement),
    rationale: Boolean(overlay?.overview?.intent?.clinicalRationale),
    recommendation: Boolean(overlay?.overview?.intent?.recommendation),
    scope: Boolean(overlay?.overview?.scope?.inScope || overlay?.overview?.scope?.nonGoals?.length),
    guidance: Boolean(overlay?.overview?.guidance),
  }), [overlay]);

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
        ...(overlay?.citations?.length ? [{ id: "mw-app-refs", label: "References" }] : []),
        ...(overlay?.copyright ? [{ id: "mw-app-copyright", label: "Copyright" }] : []),
        ...(overlay?.disclaimer ? [{ id: "mw-app-disclaimer", label: "Disclaimer" }] : []),
      ],
    };
  }, [specDoc, retrieves, namedTerminology, overlay]);

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

  // DOC-AUTO-SAVE — every edit is written where the library was opened, as the signed-in user, a beat after typing
  // stops, compiled or not. A read-only library is never written.
  const opened = useMemo(() => definition && loadedSource !== null ? {
    key: `${libKey}\0${reads}`, location: definition.location, name: definition.name, version: definition.version,
    cql: loadedSource,
  } : null, [definition, libKey, reads, loadedSource]);
  const autoSave = useAutoSave({ opened, edited, enabled: !readOnly, save: saveLibrary });
  const writeState = autoSave.state;
  const unwritten = autoSave.unwritten;
  const [dismissedWrite, setDismissedWrite] = useState<typeof writeState | null>(null);
  // Read the library again after its copy changed elsewhere: what is there now, with the edit that was refused gone.
  const reread = useCallback(() => {
    if (!definition) return;
    const { name, version, location } = definition;
    fetchLibrarySource(name, version, location)
      .then((s) => { setEdited(null); setLoadedSource(s); setReads((n) => n + 1); setSourceError(null); })
      .catch((e) => setSourceError(`Failed to load source: ${e instanceof Error ? e.message : e}`));
  }, [definition]);
  // Cmd/Ctrl+S writes now, rather than when typing stops.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); autoSave.flush(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [autoSave.flush]); // eslint-disable-line react-hooks/exhaustive-deps
  // Leaving the page while the latest edit is not written asks first; so does opening another library (`leaveEdit`).
  useEffect(() => {
    if (!unwritten) return;
    const onLeave = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, [unwritten]);
  const leaveEdit = useCallback(
    () => !unwritten || window.confirm("This library's latest edit is not saved. Leave it and lose the edit?"),
    [unwritten]);
  // DOC-SCOPE-OPEN — the open scope is the location the catalog lists (`?at=`); its libraries are the catalog.
  const navScope = useMemo<NavScope | null>(() => (openAt && definition ? {
    at: openAt, libraries: definitions, openId: definition.id,
    onOpen: (id) => { if (leaveEdit()) openArtifact(id, { view: "define" }); },
  } : null), [openAt, definition, definitions, leaveEdit, openArtifact]);
  // DOC-OPEN-SAVE — New and Save As, from the Navigator's action row. New leaves the open library, so it asks
  // first, as opening another does; Save As takes the CQL as edited into the copy.
  const [fileForm, setFileForm] = useState<NewTarget | null>(null);
  useEffect(() => { setFileForm(null); }, [definition?.id, libLocation]);
  // DOC-INCLUDE-NEW — "Include a library…" puts the new library's `include` where the compiler placed the open
  // library's last include or using, so it runs only on an editable library whose CQL as edited is compiled as it
  // stands, with no define open in place (that editor would write its own text back over the include).
  const includeElm = latest?.result.ok && latest.result.elm && latest.source === (edited ?? loadedSource)
    ? latest.result.elm : null;
  const includeBlocked = readOnly
    ?? (inPlace ? "Close the define you are editing first."
        : includeElm ? null : "The CQL as edited must compile first: the include goes after its last include.");
  // DOC-PUBLISH — Publish reads the library from its folder, so it waits until the latest edit is written there.
  const publishBlocked = readOnly
    ?? (unwritten ? "Wait until the latest edit is saved: Publish reads the library from its folder." : null);
  const [publishing, setPublishing] = useState(false);
  useEffect(() => { setPublishing(false); }, [definition?.id, libLocation]);
  const [includeProblem, setIncludeProblem] = useState<string | null>(null);
  useEffect(() => { setIncludeProblem(null); }, [definition?.id, libLocation]);
  // A new library opens in the CQL view, to be written; a copy opens where its original was read; a library made to
  // be included stays unopened, and the open library gains its `include`, written as any edit is (`DOC-AUTO-SAVE`).
  const created = useCallback((id: string, at: string) => {
    const form = fileForm;
    setFileForm(null);
    setCatalogGen((g) => g + 1);
    if (form?.kind === "include") {
      const [name = "", version = ""] = id.split("@");
      const current = edited ?? loadedSource;
      const out = includeElm && current !== null ? withInclude(current, includeElm, name, version)
        : { refused: "The CQL changed while the library was being created." };
      if ("cql" in out) setEdited(out.cql);
      else setIncludeProblem(`${name} ${version} was created in ${at.replace(/^folder:/, "")} but not included: `
                             + `${out.refused} Add the include by hand.`);
      return;
    }
    setEdited(null);
    openArtifact(id, { view: form?.kind === "new" ? "cql" : "define" }, at);
  }, [fileForm, openArtifact, edited, loadedSource, includeElm]);
  const editorTheme: "light" | "dark" = themeAttr(chrome.theme)
    ?? (typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

  // DOC-CQL-EDIT — a define edited in place. Its text replaces `[start, end)` of the draft it opened on, so
  // the edit splices into the ONE text the whole-library editor also edits and every write sends (`DOC-AUTO-SAVE`).
  const draftCql = edited ?? loadedSource ?? "";
  // The draft has not compiled yet: typing has not settled, or the compile of what settled is in flight.
  const pending = compiling || edited !== null && edited !== editedSource || latest?.source !== source;
  const broken = !pending && latest && !latest.result.ok ? latest : null;
  const [dismissedCompile, setDismissedCompile] = useState<Compiled | null>(null);
  // The define last closed, so it can reopen on its own text while the draft is still exactly what it left —
  // the one way back into a define closed while it did not compile, when no span on the page is current.
  const [lastInPlace, setLastInPlace] = useState<InPlace | null>(null);
  // Another library, view or dependency closes it, and so does reading the library again: its text is no longer the
  // one the define was opened on. A write does not: it writes exactly the text this edit made.
  useEffect(() => { setInPlace(null); }, [libKey, view, dep, reads]);
  useEffect(() => { setLastInPlace(null); }, [libKey, reads]);
  const inPlaceRef = useRef(inPlace);
  inPlaceRef.current = inPlace;
  const closeInPlace = useCallback(() => {
    if (inPlaceRef.current) setLastInPlace(inPlaceRef.current);
    setInPlace(null);
  }, []);
  const resumes = (entry: SpecEntry) => !!lastInPlace && lastInPlace.key === entry.key
    && draftCql === lastInPlace.base.slice(0, lastInPlace.start) + lastInPlace.text + lastInPlace.base.slice(lastInPlace.end);
  const changeInPlace = useCallback((text: string) => {
    const p = inPlaceRef.current;
    if (!p) return;
    setInPlace({ ...p, text });
    setEdited(p.base.slice(0, p.start) + text + p.base.slice(p.end));
  }, []);
  const defineEdit = useMemo<DefineEdit>(() => ({
    openKey: inPlace?.key ?? null,
    // The library being read; an include is a reference, edited where it lives.
    // DOC-EDITABLE-IN-SCOPE — and only in a folder: a published library from a store offers no Edit.
    offers: (entry) => !dep && !readOnly && entry.library === specModel?.root,
    blocked: (entry) => {
      if (entry.variants.length !== 1) return "A function with overloads is edited in the whole-library editor.";
      if (!entry.variants[0]!.span) return "The compiler gave no source position for this define.";
      if (resumes(entry)) return null;
      if (pending) return "Compiling the CQL as edited…";
      if (!page || page.source !== draftCql) return "The CQL as edited does not compile. Fix it in the define you last edited, or in the whole-library editor.";
      return null;
    },
    open: (entry) => {
      if (resumes(entry)) { setInPlace(lastInPlace); return; }
      const span = entry.variants[0]?.span;
      if (!span || !page || page.source !== draftCql) return;
      setInPlace({ key: entry.key, base: draftCql, start: span[0], end: span[1], text: draftCql.slice(span[0], span[1]),
                   firstLine: draftCql.slice(0, span[0]).split("\n").length, frozen: page });
    },
    editor: inPlace && (
      <DefineEditor value={inPlace.text} firstLine={inPlace.firstLine} onChange={changeInPlace}
        onClose={closeInPlace} theme={editorTheme}
        status={pending ? "Compiling…" : broken ? `${broken.result.diagnostics.length} problem${broken.result.diagnostics.length === 1 ? "" : "s"}` : "Compiles"}
        problems={broken?.result.diagnostics ?? []} />
    ),
  }), [inPlace, lastInPlace, dep, readOnly, specModel, pending, page, draftCql, broken, changeInPlace,
      closeInPlace, editorTheme]);

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
        {openAt?.startsWith("folder:")
          ? <p>This folder holds no CQL.</p>
          : <p>No measures found. No schema you can read is tagged <code>cql_databricks_knowledge</code>.</p>}
        {/* DOC-OPEN-SAVE — with nothing to open there is no Open menu, so New is offered here. */}
        <p className="mw-empty-acts">
          <button onClick={() => setFileForm({ kind: "new", what: "measure" })}>New measure</button>
          <button onClick={() => setFileForm({ kind: "new", what: "library" })}>New library</button>
        </p>
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
            Execution sits in the middle of the bar; the compute pill is on the right beside controls
            touched once a session. */}
        {/* APP-ASSISTANT — `moduleTools` is the drawer's door. A toggle, so it shows its state
            (`aria-pressed`), which is the same argument that took tour/Settings/appearance out of the
            account menu. `Sparkle` because that is the assistant mark the retired inspector tab already
            used, and a reader should meet one mark for one thing. Rendered only with a measure open:
            the assistant's scope is the measure, and a door onto an empty room is not a door. */}
        <TopBar {...chrome}
          moduleTools={definition && (<>
            {/* DOC-CQL-EDIT — the library's CQL, editable; the Specification recompiles from the edit. */}
            <button className="mw-icon-btn" aria-pressed={view === "cql" && !dep}
              aria-label="Edit CQL" title={view === "cql" ? "Back to the Specification" : "Edit the CQL"}
              onClick={() => { setDep(null); setView(view === "cql" ? "define" : "cql"); }}>
              <EditCql size={16} aria-hidden="true" />
            </button>
            <button className="mw-icon-btn" aria-pressed={askOpen}
              aria-label="Assistant" title="Ask about this measure"
              onClick={toggleAssistant}>
              <Sparkle size={16} aria-hidden="true" />
            </button>
          </>)}
          crumb={definition && (<>
            <Navigator definition={definition} definitions={definitions}
              onOpenDefinition={(id) => { if (leaveEdit()) openArtifact(id, { view: "define" }); }}
              dep={dep} onBack={() => { setDep(null); setView("define"); }}
              /* DOC-OPEN-SAVE — a location lists its libraries; opening one from a folder sets the location. */
              at={openAt}
              onOpenAt={(at, id) => {
                if (!leaveEdit()) return;
                if (id) openArtifact(id, { view: "define" }, at);
                else setParams({ at, open: null, crit: null });
              }}
              includeBlocked={includeBlocked}
              publishBlocked={publishBlocked}
              onFileAction={(action) => {
                if (action.kind === "new") { if (leaveEdit()) setFileForm(action); }
                else if (action.kind === "include") { if (!includeBlocked) setFileForm(action); }
                else if (action.kind === "publish") { if (!publishBlocked) setPublishing(true); }
                else if (loadedSource !== null) setFileForm({ kind: "saveAs", definition, cql: edited ?? loadedSource });
              }} />
            {/* DOC-AUTO-SAVE — whether the latest edit is written where the library was opened, or why not. */}
            <span className={"mw-save-state " + writeState.kind} role="status"
              title={readOnly ?? writeTitle(writeState, definition.location)}>
              {readOnly ? "Read-only" : writeLabel(writeState)}
            </span>
          </>)}
          context={definition && <>
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
              environments={environments} environment={runEnvironment}
              warehouses={warehouses} defaultWarehouseId={defaultWarehouseId} warehouseId={warehouseId}
              warehouseError={warehouseError}
              updateMode={updateMode}
              onModeChange={() => { setComputing(false); setMode("static"); }}
              onEnvironmentChange={(name) => { setEnvironment(name); setComputing(true); }}
              onWarehouseChange={(id) => setWarehouseId(id === defaultWarehouseId ? null : id)}
              onUpdateModeChange={setUpdateMode}
              runDisabled={running || compiling || !source || !runEnvironment}
              onRefresh={() => { if (runEnvironment) doRunSource(source, runEnvironment, "refresh"); }} />
          </>} />

        {/* DOC-OPEN-SAVE — New from the empty state; with a library open, the form renders with the banners below. */}
        {fileForm && !definition && (
          <NewLibraryForm key={fileForm.kind === "new" ? fileForm.what : fileForm.kind} target={fileForm} at={openAt}
            onCancel={() => setFileForm(null)} onCreated={created} />
        )}
        {/* Loading / empty body — shown while definitions load or when none exist. */}
        {bodyContent}

        {/* definition-dependent content: error banner, body, inspector */}
        {definition ? <>
          {/* APP-ERROR-DISMISS — closing hides this failed read; a new one is a new object and shows. */}
          {derivations.error && derivations !== dismissedDerivations && (
            <ErrorBanner message={derivations.error} onDismiss={() => setDismissedDerivations(derivations)} />
          )}
          {/* DOC-CQL-EDIT — the draft does not compile, so the page is the last version that did (or none). The
              whole-library editor and an open define list the problems themselves. */}
          {broken && !inPlace && view === "define" && !dep && broken !== dismissedCompile && (
            <ErrorBanner message={page ? "The CQL as edited does not compile. The page shows the last version that did."
                                       : "The CQL does not compile."}
              detail={broken.result.diagnostics.join("\n")} collapsible onDismiss={() => setDismissedCompile(broken)} />
          )}
          {/* DOC-AUTO-SAVE — why the latest edit was not written. A changed copy stops the writes until it is read
              again; any other refusal is retried on the next edit. */}
          {writeState.kind === "conflict" && (
            <div className="mw-save-stale" role="alertdialog" aria-label="Not saved: the library changed elsewhere">
              <p>Not saved: {writeState.error}. Saving has stopped for this library. Reload it to see what is there
                now; your edit since the last save is lost.</p>
              <div className="ac"><button className="go" onClick={reread}>Reload</button></div>
            </div>
          )}
          {writeState.kind === "refused" && writeState !== dismissedWrite && (
            <ErrorBanner message={`Not saved: ${writeState.error}`} onDismiss={() => setDismissedWrite(writeState)} />
          )}
          {/* DOC-INCLUDE-NEW — the library was made but its include could not be placed. */}
          {includeProblem && <ErrorBanner message={includeProblem} onDismiss={() => setIncludeProblem(null)} />}
          {publishing && <PublishForm definition={definition} environmentName={environment}
                                         onClose={() => setPublishing(false)} />}
          {fileForm && (
            <NewLibraryForm key={fileForm.kind === "new" ? fileForm.what : fileForm.kind} target={fileForm} at={openAt}
              onCancel={() => setFileForm(null)} onCreated={created} />
          )}
          {writeState.kind === "stale" && (
            <div className="mw-save-stale" role="alertdialog" aria-label="Files this save would leave out of date">
              <p>Saving would leave these copies out of date. They can't be regenerated here, so nothing is saved
                until you agree to delete them.</p>
              <ul>{writeState.paths.map((p) => <li key={p}><code>{p}</code></li>)}</ul>
              <div className="ac">
                <button className="go" onClick={autoSave.agreeStale}>Delete them and save</button>
              </div>
            </div>
          )}
          {/* Run error banner — subtle, dismissible, collapsible. Keyed by the error, so a new failure opens
              collapsed. */}
          {runError && (
            <ErrorBanner key={runError} message={runError.split("\n")[0]!}
              detail={runError.includes("\n") ? runError : null} collapsible onDismiss={() => setRunError(null)} />
          )}

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
              scope={navScope}
            />

            <main className="mw-doc">
              <div className={"mw-doc-inner" + (!dep && view === "define" ? " read" : "")}>
                {/* DOC-EDITABLE-IN-SCOPE — a published library opens read-only, says why, and offers the copy to edit. */}
                {readOnly && !dep && (
                  <div className="mw-dep-ribbon mw-readonly"><span className="ro">read-only</span><span>{readOnly}</span>
                    {loadedSource !== null && (
                      <button className="mw-readonly-copy"
                        onClick={() => setFileForm({ kind: "saveAs", definition, cql: edited ?? loadedSource })}>
                        Save a copy…
                      </button>
                    )}
                  </div>
                )}
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
                    <DefineEditContext.Provider value={defineEdit}>
                    <MeasureNarrative definition={definition} metrics={dispMetrics} live={computed}
                      readCounts={computed ? readCounts : null}
                      funnel={dispFunnel}
                      selected={selected} onSelect={selectInSpec} onNavigate={selectFromNav}
                      onRunCell={runCell}
                      results={computed ? (run?.results ?? null) : null}
                      spec={visibleSpec} elmInfo={elmInfo}
                      overlay={overlay} doc={specDoc}
                      retrieves={retrieves} namedTerminology={namedTerminology}
                      parameters={citableParams(execParams)}
                      /* SPEC-REF-LENS — the same scope the inspector pane resolves, so a define popped from
                         a sentence and the same define pinned in the pane profile one evaluation. */
                      profileScope={profileScope} />
                    </DefineEditContext.Provider>
                  ) : <div className="mw-empty">{compiling ? <><span className="mw-spin" /> Compiling…</> : broken ? "The CQL does not compile." : "Waiting for source…"}</div>
                ) : view === "cql" ? (
                  /* DOC-CQL-EDIT — the CQL, editable. Diagnostics are the compile of the debounced edit. */
                  sourceLoading ? <div className="mw-empty"><span className="mw-spin" /> Loading source…</div>
                  : sourceError ? <div className="mw-empty"><span className="err">{sourceError}</span></div>
                  : (
                    <div className="mw-cqledit">
                      <div className="mw-cqledit-bar">
                        <span className="fn">{definition.name}-{definition.version}.cql</span>
                        <span className="at" title={definition.location}>{definition.location.replace(/^(store|folder):/, "")}</span>
                        <span className="st">{compiling ? "Compiling…" : latest?.result.ok ? "Compiles" : latest ? `${latest.result.diagnostics.length} problem${latest.result.diagnostics.length === 1 ? "" : "s"}` : ""}</span>
                      </div>
                      <div className="mw-cqledit-body">
                        <React.Suspense fallback={<div className="mw-empty"><span className="mw-spin" /> Loading the editor…</div>}>
                          <LazyCqlEditor value={edited ?? loadedSource ?? ""} onChange={setEdited} theme={editorTheme}
                            readOnly={!!readOnly} />
                        </React.Suspense>
                      </div>
                      {latest && !latest.result.ok && latest.result.diagnostics.length > 0 && (
                        <ul className="mw-cqledit-diags" aria-label="Problems">
                          {latest.result.diagnostics.map((d, i) => <li key={i}>{d}</li>)}
                        </ul>
                      )}
                    </div>
                  )
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
              selection={askSelection}
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
