// DOC-OPEN-SAVE — New and Save As: name a library and choose where it is created, a workspace folder or a tagged
// store. With a folder open as the scope (DOC-SCOPE-OPEN) the folder starts as that scope, so the library lands in
// its `cql/` and `resources/` beside the scope's others; with none it starts as `<my folder>/<Name>` and follows the
// name until it is edited. Nothing is written over anything: a name already there is refused, and the form says why.

import { useEffect, useState } from "react";
import type { Definition } from "../catalog.ts";
import { createLibrary, fetchLocations, type Locations } from "../library-client.ts";

/** What the form creates: a New measure or library, or a copy of the open one, with its CQL as edited. */
export type NewTarget =
  | { kind: "new"; what: "measure" | "library" }
  | { kind: "saveAs"; definition: Definition; cql: string };

/** The folder a new library is created in by default: the open folder scope itself, else its own folder in mine. */
export function defaultFolder(at: string | null, myFolder: string | null, name: string): string | null {
  if (at?.startsWith("folder:")) return at.slice("folder:".length).replace(/\/+$/, "");
  return myFolder ? `${myFolder.replace(/\/+$/, "")}/${name}` : null;
}

export function NewLibraryForm({ target, at, onCreated, onCancel }: {
  target: NewTarget;
  /** The location the catalog lists (RUN-ADDRESS-LOCATION); a store or a folder here is the default. */
  at: string | null;
  /** Open the created library: its id, and the location it was created at. */
  onCreated: (id: string, location: string) => void;
  onCancel: () => void;
}) {
  const copying = target.kind === "saveAs" ? target.definition : null;
  const [name, setName] = useState(copying ? `${copying.name}Copy` : "");
  const [version, setVersion] = useState("0.1.0");
  const [dest, setDest] = useState<"folder" | "store">(at?.startsWith("store:") ? "store" : "folder");
  const [folder, setFolder] = useState<string | null>(null);   // null follows the name
  const [store, setStore] = useState<string>(at?.startsWith("store:") ? at : "");
  const [locations, setLocations] = useState<Locations | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ message: string; detail?: string[] } | null>(null);

  useEffect(() => {
    let live = true;
    fetchLocations().then((l) => {
      if (!live) return;
      setLocations(l);
      setStore((s) => s || l.stores[0]?.location || "");
    }).catch((e) => { if (live) setProblem({ message: e instanceof Error ? e.message : String(e) }); });
    return () => { live = false; };
  }, []);

  const myFolder = locations?.folders[0]?.location.slice("folder:".length) ?? null;
  const folderPath = folder ?? defaultFolder(at, myFolder, name || "<Name>") ?? "";
  const location = dest === "folder" ? `folder:${folderPath}` : store;
  const ready = name.trim() !== "" && version.trim() !== "" && !busy
    && (dest === "folder" ? folderPath.startsWith("/") && !folderPath.includes("<Name>") : store !== "");

  async function create() {
    setBusy(true);
    setProblem(null);
    try {
      const out = await createLibrary({
        location, name: name.trim(), version: version.trim(),
        ...(copying && target.kind === "saveAs"
          ? { cql: target.cql, copyOf: { location: copying.location, name: copying.name, version: copying.version } }
          : { kind: target.kind === "new" ? target.what : "measure" }),
      });
      if (out.kind === "saved") onCreated(`${name.trim()}@${version.trim()}`, out.location);
      else if (out.kind === "invalid") setProblem({ message: "Not created: the CQL does not compile.", detail: out.diagnostics });
      else setProblem({ message: `Not created: ${out.error}` });
    } catch (e) {
      setProblem({ message: `Not created: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  const title = copying ? `Save ${copying.title ?? copying.name} as a copy`
    : target.kind === "new" && target.what === "library" ? "New library" : "New measure";
  return (
    <form className="mw-save-stale mw-newlib" role="dialog" aria-label={title}
      onSubmit={(e) => { e.preventDefault(); if (ready) void create(); }}>
      <p><b>{title}</b>{copying ? " — the copy is a draft, linked back to this one." : ""}</p>
      <div className="mw-newlib-fields">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} autoFocus
          placeholder="MyMeasure" spellCheck={false} /></label>
        <label>Version<input value={version} onChange={(e) => setVersion(e.target.value)} spellCheck={false} /></label>
        <label>Save in
          <select value={dest} onChange={(e) => setDest(e.target.value as "folder" | "store")}>
            <option value="folder">Workspace folder</option>
            <option value="store" disabled={!locations?.stores.length}>Tagged store</option>
          </select>
        </label>
        {dest === "folder" ? (
          <label className="wide">Folder<input value={folderPath} onChange={(e) => setFolder(e.target.value)}
            spellCheck={false} /></label>
        ) : (
          <label className="wide">Store
            <select value={store} onChange={(e) => setStore(e.target.value)}>
              {locations?.stores.map((s) => (
                <option key={s.location} value={s.location}>{s.catalog}.{s.schema}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {problem && (
        <div className="mw-newlib-problem" role="alert">
          {problem.message}
          {problem.detail && problem.detail.length > 0 && <pre>{problem.detail.join("\n")}</pre>}
        </div>
      )}
      <div className="ac">
        <button type="button" onClick={onCancel}>Cancel</button>
        <button type="submit" className="go" disabled={!ready}>{busy ? "Creating…" : copying ? "Save copy" : "Create"}</button>
      </div>
    </form>
  );
}
