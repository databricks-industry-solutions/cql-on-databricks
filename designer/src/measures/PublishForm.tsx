// DOC-PUBLISH — publish the open folder library into a tagged knowledge store the author picks, as the signed-in
// user, through the client's publish (CLIENT-PUBLISH). The library's includes in the folder go with it, and only
// what changed is written; the answer lists each library as published, unchanged, refused or not written.

import { useEffect, useState } from "react";
import type { Definition } from "../catalog.ts";
import { fetchLocations, publishLibrary, type Locations, type PublishSummary } from "../library-client.ts";

export function PublishForm({ definition, onClose }: { definition: Definition; onClose: () => void }) {
  const [locations, setLocations] = useState<Locations | null>(null);
  const [store, setStore] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [summary, setSummary] = useState<PublishSummary | null>(null);

  useEffect(() => {
    let live = true;
    fetchLocations().then((l) => {
      if (!live) return;
      setLocations(l);
      setStore((s) => s || (l.stores[0] ? l.stores[0].location.slice("store:".length) : ""));
    }).catch((e) => { if (live) setProblem(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, []);

  async function publish() {
    setBusy(true);
    setProblem(null);
    setSummary(null);
    try {
      const out = await publishLibrary({ location: definition.location, name: definition.name,
                                         version: definition.version, store });
      setSummary(out.summary);
      if (out.kind !== "published") {
        setProblem(out.kind === "refused" ? `Nothing was published: ${out.error}`
          : `Publishing stopped: ${out.error}. The libraries marked published were written.`);
      }
    } catch (e) {
      setProblem(`Not published: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const title = `Publish ${definition.title ?? definition.name}`;
  return (
    <form className="mw-save-stale mw-newlib" role="dialog" aria-label={title}
      onSubmit={(e) => { e.preventDefault(); if (store && !busy) void publish(); }}>
      <p><b>{title}</b> — with the libraries it includes from this folder. Only what changed is written, and a
        published version is replaced.</p>
      <div className="mw-newlib-fields">
        <label className="wide">Tagged store
          <select value={store} onChange={(e) => setStore(e.target.value)} disabled={!locations?.stores.length}>
            {locations?.stores.map((s) => (
              <option key={s.location} value={`${s.catalog}.${s.schema}`}>{s.catalog}.{s.schema}</option>
            ))}
          </select>
        </label>
      </div>
      {locations && !locations.stores.length && (
        <div className="mw-newlib-problem" role="alert">No tagged knowledge store is in scope to publish into.</div>
      )}
      {problem && <div className="mw-newlib-problem" role="alert">{problem}</div>}
      {summary && (
        <ul className="mw-publish-result" aria-label="What was published">
          {summary.libraries.map((lib) => (
            <li key={`${lib.name}@${lib.version}`} className={lib.outcome.replace(" ", "-")}>
              <code>{lib.name} {lib.version}</code> <span>{lib.outcome}</span>
              {lib.outcome === "refused" && lib.detail.length > 0 && <pre>{lib.detail.join("\n")}</pre>}
            </li>
          ))}
        </ul>
      )}
      <div className="ac">
        <button type="button" onClick={onClose}>{summary && !problem ? "Done" : "Cancel"}</button>
        <button type="submit" className="go" disabled={!store || busy}>{busy ? "Publishing…" : "Publish"}</button>
      </div>
    </form>
  );
}
