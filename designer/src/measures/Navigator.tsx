// APP-MEASURE-DOC / APP-LOGIC-VIEW / DESIGNER-TOP-BAR — the artifact (Specification) selector, and the
// app's whole "File" surface (there is no chrome strip).
//
// Two-column popover: LEFT = recently opened artifacts (fast-switch lane), RIGHT = the full catalog
// browser (search + scrollable list), plus an ACTION ROW along the bottom.
//
// THE OPEN ARTIFACT IS LISTED, NOT FILTERED. It renders like every other row — clicking it re-opens
// in place, a harmless no-op — and carries the active accent wash + an `open` tag, the same mark the
// module switcher puts on the module you are in (`APP-STYLE-GUIDE`: the active row is the one place
// a menu spends accent).
//
// THE FILE MENU IS THIS MENU. There is no separate File menu. An "Open from Catalog…" item would be a
// menu entry whose whole function is to open the menu beside it, next to New measure / New library /
// Import, which answer the same question this popover already exists to answer: *which artifact am I
// working on*. They are one decision, so they are one control: browse what exists, or make/ingest a new
// one. The outbound action sits here too because the popover's head names the artifact it would send.
//
// "Publish to Marketplace" is not offered (DESIGNER-ONE-MODULE), so neither is the Data Readiness gate it named.
//
// DOC-OPEN-SAVE — New measure, New library and Save as… work; Import CQL… does not yet. It carries that
// honestly — `disabled`, with the reason in the wrapper's tooltip — but NOT a `soon` tag: see the note on
// the action row below. The module switcher carries no tags, for the same reason, so no menu in this app
// leads with a notice about what does not work yet.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Definition } from "../catalog.ts";
import { fetchFolder, fetchLocations, type FolderListing, type Locations } from "../library-client.ts";
import { ChevronLeft, ChevronDown, List, Plus, Search, Download, FileText, Library, Publish, Save } from "../icons.tsx";

type CatFilter = "all" | "measure" | "lib";

// ── recent history (persisted in localStorage, capped at 8) ──
const RECENT_KEY = "cql-databricks:recent";
const RECENT_MAX = 8;

function loadRecent(): string[] {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; }
}

function pushRecent(id: string) {
  const list = loadRecent().filter((x) => x !== id);
  list.unshift(id);
  if (list.length > RECENT_MAX) list.length = RECENT_MAX;
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* quota */ }
}

/** DOC-OPEN-SAVE — what the action row asks the workspace to do. DOC-INCLUDE-NEW — `include`: create a library and
 *  include it in the open one. DOC-PUBLISH — `publish`: the open folder library into a tagged store. */
export type FileAction = { kind: "new"; what: "measure" | "library" } | { kind: "include" } | { kind: "saveAs" }
  | { kind: "publish" };

/** The artifact-level file actions, in display order. `trailing` pushes an
 *  action to the row's right end — the divide between "make one" and "do something with this one". An action
 *  with no `act` is not built yet. */
const ARTIFACT_ACTIONS: { label: string; Icon: typeof Plus; act?: FileAction; trailing?: boolean }[] = [
  { label: "New measure", Icon: Plus, act: { kind: "new", what: "measure" } },
  { label: "New library", Icon: List, act: { kind: "new", what: "library" } },
  { label: "Include a library…", Icon: Library, act: { kind: "include" } },
  { label: "Import CQL…", Icon: Download },
  { label: "Save as…", Icon: Save, act: { kind: "saveAs" }, trailing: true },
  { label: "Publish…", Icon: Publish, act: { kind: "publish" } },
];

/** RUN-ADDRESS-LOCATION — what the catalog calls a location: a store by its schema, a folder by its last segment. */
export function locationLabel(at: string | null): string {
  if (!at) return "All tagged stores";
  const [kind, ...rest] = at.split(":");
  const path = rest.join(":");
  return kind === "folder" ? path.replace(/\/+$/, "").split("/").pop() || path : path;
}

export function Navigator({ definition, definitions, onOpenDefinition, dep, onBack, at = null, onOpenAt, onFileAction,
                           includeBlocked = null, publishBlocked = null }: {
  definition: Definition;
  /** The WHOLE catalog, the open artifact included — it renders marked `open`, never filtered out. */
  definitions: Definition[];
  /** Open a different definition from the catalog (switches in-place). */
  onOpenDefinition: (id: string) => void;
  /** APP-DEP-NAV — when an included library is open, the selector shows IT as the current context
   *  (with a Back affordance) instead of repeating the parent's name in a separate breadcrumb. */
  dep?: { name: string } | null;
  onBack?: () => void;
  /** RUN-ADDRESS-LOCATION — the location the catalog lists; null is all of tagged knowledge. */
  at?: string | null;
  /** DOC-OPEN-SAVE — list another location, or open `id` at it. Opening from a folder is setting the location. */
  onOpenAt?: (at: string | null, id?: string) => void;
  /** DOC-OPEN-SAVE — New and Save As. Absent, the actions are not offered. */
  onFileAction?: (action: FileAction) => void;
  /** DOC-INCLUDE-NEW — why "Include a library…" cannot run on the open library now, or null when it can. */
  includeBlocked?: string | null;
  /** DOC-PUBLISH — why "Publish…" cannot run on the open library now, or null when it can. */
  publishBlocked?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [catFilter, setCatFilter] = useState<CatFilter>("all");
  const [yearFilter, setYearFilter] = useState<string>("all");
  // DOC-OPEN-SAVE — the location picker replaces the catalog list while it is open.
  const [picking, setPicking] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Record the current definition in recent history whenever it changes.
  useEffect(() => { if (definition.id) pushRecent(definition.id); }, [definition.id]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [open]);

  const kind = definition.cat === "lib" ? "Library" : "Measure";

  // Distinct years present in the catalog, most recent first.
  const years = useMemo(() => {
    const set = new Set<string>();
    for (const d of definitions) if (d.year) set.add(d.year);
    return [...set].sort().reverse();
  }, [definitions]);

  const catalog = useMemo(() => {
    let list = definitions;
    if (catFilter === "measure") list = list.filter((d) => d.cat !== "lib");
    else if (catFilter === "lib") list = list.filter((d) => d.cat === "lib");
    if (yearFilter !== "all") list = list.filter((d) => d.year === yearFilter);
    const needle = q.trim().toLowerCase();
    if (needle) list = list.filter((d) => `${d.name} ${d.title ?? ""}`.toLowerCase().includes(needle));
    return list;
  }, [definitions, catFilter, yearFilter, q]);

  // Recent: resolve stored IDs against the current definitions. The open artifact leads the lane BY
  // CONSTRUCTION, wearing its `open` mark — it is the most recently opened thing by definition, and
  // reading that fact back from localStorage would race the effect above that records it.
  const recent = useMemo(() => {
    const byId = new Map(definitions.map((d) => [d.id, d]));
    const ids = [definition.id, ...loadRecent().filter((id) => id !== definition.id)];
    return ids.map((id) => byId.get(id)).filter((d): d is Definition => d !== undefined);
  }, [definitions, definition.id]);

  const openDef = useCallback((d: Definition) => {
    pushRecent(d.id);
    setOpen(false);
    onOpenDefinition(d.id);
  }, [onOpenDefinition]);

  return (
    <div className="mw-pop-anchor mw-nav-anchor" ref={ref}>
      {dep && onBack && (
        <button className="mw-nav-back" onClick={onBack} aria-label={`Back to ${definition.name}`} title={`Back to ${definition.name}`}><ChevronLeft size={14} aria-hidden="true" />Back</button>
      )}
      {/* NO KIND CHIP. It sat to the left of the name in accent wash and said MEASURE over a measure's
          own title, on the one control whose entire job is naming what is open — the loudest token in
          the toolbar spent restating the obvious. The distinction it drew survives where it is actually
          needed: an included library reads "included by <parent>" on the line below, which says more
          than the chip did. `kind` is still the popover's own subtitle, where there is room to say it
          once without competing with the name. */}
      <button className="mw-artifact-sel" onClick={() => setOpen((v) => !v)} aria-haspopup="true" aria-expanded={open}>
        <span className="who">
          <span className="nm">{dep ? dep.name : (definition.title ?? definition.name)}</span>
          <span className="ver">{dep ? `included by ${definition.title ?? definition.name}` : definition.src}</span>
        </span>
        <span className="caret" aria-hidden="true"><ChevronDown size={14} /></span>
      </button>
      {open && (
        <div className="mw-pop mw-navpop left">
          <div className="mw-pop-head"><div className="t">{definition.title ?? definition.name}</div><div className="s">{definition.src} · {kind.toLowerCase()}</div></div>

          <div className="mw-navpop-cols">
            {/* Left: recently opened artifacts (fast-switch, excludes current). */}
            <div className="mw-navpop-left">
              <div className="mw-pop-lbl">Recent</div>
              {recent.length === 0 && <div className="mw-pop-foot">No recent artifacts.</div>}
              {recent.map((d) => (
                <button key={d.id} className={"mw-pop-row" + (d.id === definition.id ? " active" : "")}
                  aria-current={d.id === definition.id || undefined} onClick={() => openDef(d)}>
                  <span className="ic" aria-hidden="true">{d.cat === "lib" ? <Library size={15} /> : <FileText size={15} />}</span>
                  <span className="tx"><span className="nm">{d.title ?? d.name}</span></span>
                  {d.id === definition.id && <span className="tag on">open</span>}
                </button>
              ))}
            </div>

            {/* Right: catalog browser — filter chips + search + scrollable list. */}
            <div className="mw-navpop-right">
              <div className="mw-pop-lbl">Catalog</div>
              {/* RUN-ADDRESS-LOCATION — where the catalog below is read from. */}
              {onOpenAt && (
                <button className="mw-navpop-loc" aria-expanded={picking} onClick={() => setPicking((v) => !v)}
                  title={at ?? "Every tagged knowledge store, in scope order"}>
                  <span className="k">Location</span>
                  <span className="v">{locationLabel(at)}</span>
                  <ChevronDown size={12} aria-hidden="true" />
                </button>
              )}
              {picking && onOpenAt ? (
                <LocationPicker at={at}
                  onChoose={(where) => { setPicking(false); onOpenAt(where); }}
                  onOpen={(where, id) => { setPicking(false); setOpen(false); onOpenAt(where, id); }} />
              ) : <>
              <div className="mw-navpop-chips" role="group" aria-label="Filter">
                {([["all", "All"], ["measure", "Measures"], ["lib", "Libraries"]] as const).map(([id, label]) => (
                  <button key={id} className={"mw-navpop-chip" + (catFilter === id ? " on" : "")}
                    aria-pressed={catFilter === id} onClick={() => setCatFilter(id)}>{label}</button>
                ))}
                {years.length > 0 && <>
                  <span className="mw-navpop-chip-sep" aria-hidden="true" />
                  {years.map((y) => (
                    <button key={y} className={"mw-navpop-chip" + (yearFilter === y ? " on" : "")}
                      aria-pressed={yearFilter === y} onClick={() => setYearFilter(yearFilter === y ? "all" : y)}>{y}</button>
                  ))}
                </>}
              </div>
              <div className="mw-pop-search"><Search size={14} aria-hidden="true" /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search measures & libraries…" /></div>
              <div className="mw-navpop-scroll">
                {catalog.length === 0 && <div className="mw-pop-foot">Nothing matches.</div>}
                {catalog.map((d) => (
                  <button key={d.id} className={"mw-pop-row" + (d.id === definition.id ? " active" : "")}
                    aria-current={d.id === definition.id || undefined} onClick={() => openDef(d)}>
                    <span className="ic" aria-hidden="true">{d.cat === "lib" ? <Library size={15} /> : <FileText size={15} />}</span>
                    <span className="tx"><span className="nm">{d.title ?? d.name}</span><span className="desc">{d.src}{d.year ? ` · ${d.year}` : ""}</span></span>
                    {d.id === definition.id && <span className="tag on">open</span>}
                  </button>
                ))}
              </div>
              </>}
            </div>
          </div>

          {/* The action row: the file actions, minus the item that would duplicate this
              popover. CREATE/INGEST on the left (they make a new artifact to open); the open artifact's
              own EXPORT on the right, under the head that names it. */}
          <div className="mw-navpop-actions">
            {/* NO `soon` TAG. Actions carry the row's normal ink and hover, so the menu reads as finished.
                They stay `disabled` so nothing FIRES that cannot happen. The reason is in the tooltip on
                the wrapper, where a disabled control can still explain itself. */}
            {ARTIFACT_ACTIONS.map((a) => {
              const blocked = a.act?.kind === "include" ? includeBlocked
                : a.act?.kind === "publish" ? publishBlocked : null;
              return (
              <span key={a.label} className={"mw-navpop-act-wrap" + (a.trailing ? " trail" : "")}
                title={blocked ?? (a.act && onFileAction ? undefined : `${a.label} — lands in a later phase`)}>
                <button className="mw-navpop-act" disabled={!a.act || !onFileAction || !!blocked}
                  onClick={() => { if (a.act && onFileAction) { setOpen(false); onFileAction(a.act); } }}>
                  <span className="ic" aria-hidden="true"><a.Icon size={14} /></span>
                  {a.label}
                </button>
              </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/** DOC-OPEN-SAVE — choose where the catalog reads from: all of tagged knowledge, one store, or a workspace folder.
 *
 *  A folder is browsed one level at a time (`/api/folders`), as the signed-in user sees it. A folder holding CQL
 *  lists its libraries; opening one sets the location to that folder, which is the whole of "open from a folder". */
function LocationPicker({ at, onChoose, onOpen }: {
  at: string | null;
  onChoose: (at: string | null) => void;
  onOpen: (at: string, id: string) => void;
}) {
  const [locations, setLocations] = useState<Locations | null>(null);
  const [folder, setFolder] = useState<FolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchLocations().then((l) => { if (live) setLocations(l); })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, []);

  const browse = useCallback((path: string) => {
    setError(null);
    fetchFolder(path).then(setFolder).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <div className="mw-pop-foot" role="alert">{error}</div>;
  if (folder) {
    const parent = folder.path.replace(/\/[^/]+\/?$/, "");
    return (
      <div className="mw-navpop-scroll" aria-label={`Folder ${folder.path}`}>
        <div className="mw-navpop-path" title={folder.path}>{folder.path}</div>
        {parent.startsWith("/Workspace") && parent !== folder.path && (
          <button className="mw-pop-row" onClick={() => browse(parent)}>
            <span className="ic" aria-hidden="true"><ChevronLeft size={15} /></span>
            <span className="tx"><span className="nm">Up</span></span>
          </button>
        )}
        {folder.libraries.length > 0 && (
          <button className="mw-pop-row" onClick={() => onChoose(folder.location)}>
            <span className="ic" aria-hidden="true"><List size={15} /></span>
            <span className="tx"><span className="nm">List this folder</span>
              <span className="desc">{folder.libraries.length} librar{folder.libraries.length === 1 ? "y" : "ies"}</span></span>
          </button>
        )}
        {folder.libraries.map((d) => (
          <button key={d.id} className="mw-pop-row" onClick={() => onOpen(folder.location, d.id)}>
            <span className="ic" aria-hidden="true">{d.cat === "lib" ? <Library size={15} /> : <FileText size={15} />}</span>
            <span className="tx"><span className="nm">{d.title ?? d.name}</span><span className="desc">{d.src}</span></span>
          </button>
        ))}
        {folder.folders.map((f) => (
          <button key={f.path} className="mw-pop-row" onClick={() => browse(f.path)}>
            <span className="ic" aria-hidden="true"><ChevronDown size={15} className="mw-rot-right" /></span>
            <span className="tx"><span className="nm">{f.name}</span></span>
          </button>
        ))}
        {folder.libraries.length === 0 && folder.folders.length === 0 &&
          <div className="mw-pop-foot">This folder holds no CQL and no subfolders.</div>}
      </div>
    );
  }
  if (!locations) return <div className="mw-pop-foot"><span className="mw-spin" /> Loading locations…</div>;
  const row = (where: string | null, label: string, desc?: string) => (
    <button key={where ?? "all"} className={"mw-pop-row" + (where === at ? " active" : "")}
      aria-current={where === at || undefined} onClick={() => onChoose(where)}>
      <span className="ic" aria-hidden="true"><Library size={15} /></span>
      <span className="tx"><span className="nm">{label}</span>{desc && <span className="desc">{desc}</span>}</span>
    </button>
  );
  return (
    <div className="mw-navpop-scroll" aria-label="Locations">
      {row(null, "All tagged stores", "Every store in scope, earliest first")}
      {locations.stores.map((s) => row(s.location, `${s.catalog}.${s.schema}`, "Tagged store"))}
      <div className="mw-pop-lbl">Workspace folders</div>
      {locations.folders.map((f) => (
        <button key={f.location} className="mw-pop-row" onClick={() => browse(f.location.slice("folder:".length))}>
          <span className="ic" aria-hidden="true"><ChevronDown size={15} className="mw-rot-right" /></span>
          <span className="tx"><span className="nm">{f.label}</span><span className="desc">{f.location.slice("folder:".length)}</span></span>
        </button>
      ))}
    </div>
  );
}
