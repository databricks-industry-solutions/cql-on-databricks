// DOC-SCOPE-OPEN — the left pane's Scope tab: every library the open scope holds, measures first, so an author
// moves between the libraries of one folder or store without leaving the document.
//
// It lists what the location's rules found (`RUN-ADDRESS-LOCATION`): for a folder, every library anywhere under
// it. It needs no compile — it is the scope's contents, not the open library's structure — so it renders while
// the open library is still compiling or does not compile at all.

import type { Definition } from "../catalog.ts";
import { locationLabel } from "./Navigator.tsx";

/** The open scope, as the left pane lists it. */
export interface NavScope {
  /** `folder:<path>` or `store:<catalog>.<schema>`. */
  at: string;
  libraries: Definition[];
  /** The library open now. */
  openId: string;
  /** Open another library of the scope. The workspace asks first while the open one has an edit not yet saved (`DOC-AUTO-SAVE`). */
  onOpen: (id: string) => void;
}

export function ScopeNav({ scope }: { scope: NavScope }) {
  const measures = scope.libraries.filter((d) => d.cat === "measure");
  const libraries = scope.libraries.filter((d) => d.cat !== "measure");
  const group = (label: string, items: Definition[]) => items.length > 0 && (
    <div className="mw-scope-group">
      <div className="mw-out-head"><span className="t">{label} · {items.length}</span></div>
      {items.map((d) => (
        <button key={d.id} className={"mw-node" + (d.id === scope.openId ? " active" : "")}
          aria-current={d.id === scope.openId ? "page" : undefined} title={`${d.name} ${d.version}`}
          onClick={() => { if (d.id !== scope.openId) scope.onOpen(d.id); }}>
          <span className={"k" + (d.cat === "measure" ? " pop" : "")}>{d.cat === "measure" ? "msr" : "lib"}</span>
          <span className="lab">{d.title || d.name}</span>
          <span className="mw-scope-ver">{d.version}</span>
        </button>
      ))}
    </div>
  );
  return (
    <div className="mw-scope" aria-label="Libraries in this scope">
      <div className="mw-scope-at" title={scope.at.replace(/^(store|folder):/, "")}>{locationLabel(scope.at)}</div>
      {group("Measures", measures)}
      {group("Libraries", libraries)}
    </div>
  );
}
