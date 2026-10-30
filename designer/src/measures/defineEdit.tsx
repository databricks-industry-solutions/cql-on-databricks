// DOC-CQL-EDIT — editing a define where the Specification shows it.
//
// The workspace owns the draft, the compile and the open editor; the document only asks, per entry, whether
// it may offer Edit and what to draw while it is open. That question travels by context rather than props
// because the CQL is drawn three components deep in two different places (a section's `CqlPane` and a
// catalogue row), and threading four props through every renderer between them is how one gets missed.
// No provider (a test, the read-only dependency view) means nothing is editable.

import { createContext, lazy, Suspense, useContext, useEffect, useRef, type ReactNode } from "react";
import type { SpecEntry } from "./specModel.ts";
import { EditCql } from "../icons.tsx";

/** Monaco, loaded on first use: the whole-library editor and the in-place one share this one import. */
export const LazyCqlEditor = lazy(() => import("../authoring/CqlEditor.tsx").then((m) => ({ default: m.CqlEditor })));

export interface DefineEdit {
  /** The key of the entry being edited, or null. */
  openKey: string | null;
  /** Whether the document offers Edit on this entry at all. A reference (an included library) never does. */
  offers: (entry: SpecEntry) => boolean;
  /** Why an offered Edit cannot open right now, or null when it can. */
  blocked: (entry: SpecEntry) => string | null;
  open: (entry: SpecEntry) => void;
  /** The open editor, drawn where the entry's CQL was. */
  editor: ReactNode;
}

export const DefineEditContext = createContext<DefineEdit | null>(null);

/** What the document needs for one entry: null when Edit is not offered on it. */
export function useDefineEdit(entry: SpecEntry): { editing: boolean; reason: string | null; open: () => void;
                                                   editor: ReactNode } | null {
  const ctx = useContext(DefineEditContext);
  if (!ctx || !ctx.offers(entry)) return null;
  return { editing: ctx.openKey === entry.key, reason: ctx.blocked(entry), open: () => ctx.open(entry),
           editor: ctx.editor };
}

/** The hover control that opens an entry's CQL for editing. Disabled, with the reason as its title,
 *  when the entry cannot open now. */
export function EditCqlButton({ edit }: { edit: NonNullable<ReturnType<typeof useDefineEdit>> }) {
  if (edit.editing) return null;
  return (
    <button className="mw-cql-edit" disabled={edit.reason !== null}
      title={edit.reason ?? "Edit this define's CQL (or double-click it)"}
      onClick={(e) => { e.stopPropagation(); edit.open(); }}>
      <EditCql size={13} aria-hidden="true" /> Edit
    </button>
  );
}

/** One define's CQL, open in Monaco in place. Escape, Done or a press outside closes it.
 *
 *  EVENTS STOP AT THIS BOUNDARY. React bubbles through the React tree, and the section around this
 *  editor selects on click, and selection toggles — the trap `inspectorTabIndependence.test.tsx`
 *  records. A click to place the cursor must not unpin the define being edited. */
export function DefineEditor({ value, firstLine, onChange, onClose, status, problems, theme }: {
  value: string;
  /** The library line the text starts on, so the editor's numbers match the compiler's diagnostics. */
  firstLine: number;
  onChange: (next: string) => void;
  onClose: () => void;
  status: string;
  /** The draft's compile problems, whole-library, as the compiler states them. */
  problems: string[];
  theme: "light" | "dark";
}) {
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const down = (e: MouseEvent) => {
      // Monaco draws its context menu outside the editor (`.context-view`); a press there is inside.
      const inside = e.composedPath().some((n) =>
        n === box.current || (n instanceof Element && n.classList.contains("context-view")));
      if (!inside) close.current();
    };
    document.addEventListener("mousedown", down, true);
    return () => document.removeEventListener("mousedown", down, true);
  }, []);
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();
  return (
    // Keys are NOT stopped: Cmd/Ctrl+S is the window's, and nothing around the block handles keys.
    <div className="mw-defedit" ref={box} onClick={stop} onDoubleClick={stop}>
      <div className="mw-defedit-bar">
        <span className="st">{status}</span>
        <button onClick={onClose}>Done</button>
      </div>
      <Suspense fallback={<div className="mw-empty"><span className="mw-spin" /> Loading the editor…</div>}>
        <LazyCqlEditor value={value} onChange={onChange} theme={theme} inline={{ firstLine }} onEscape={onClose} />
      </Suspense>
      {problems.length > 0 && (
        <ul className="mw-cqledit-diags" aria-label="Problems">
          {problems.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
      )}
    </div>
  );
}
