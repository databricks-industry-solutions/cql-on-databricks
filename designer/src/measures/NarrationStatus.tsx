// APP-NARRATIVE-OVERLAY — what a narration generation is doing, or why it showed nothing.
//
// A banner above the document, in the run error's place and language: generation takes minutes, and its
// failure is a list the reader can act on (or hand on), not a toast. A rejected narration shows its
// problems and nothing else — it is never shown "with warnings" — so this banner is the only trace of it.

import { useState } from "react";
import { ChevronDown, ChevronUp, X } from "../icons.tsx";
import type { NarrationProblem, NarrationProgress } from "../library-client.ts";

export type NarrationState =
  | { status: "running"; library: string; progress: NarrationProgress | null; retried: NarrationProblem[] | null }
  | { status: "rejected"; library: string; problems: NarrationProblem[]; attempts: number }
  | { status: "error"; library: string; error: string };

/** The running stage, as the reader is told it. */
export function stageText(progress: NarrationProgress | null): string {
  if (!progress || progress.type !== "stage") return "Compiling the library";
  switch (progress.stage) {
    case "compiling": return "Compiling the library";
    case "writing": return (progress.attempt ?? 1) > 1
      ? "Writing it again, with the problems the first draft had" : "Writing the narration";
    case "checking": return "Checking it against the schema and the compiled logic";
    case "saving": return "Saving it";
  }
}

export function NarrationStatus({ state, onDismiss }: { state: NarrationState; onDismiss: () => void }) {
  const [open, setOpen] = useState(false);

  if (state.status === "running") {
    return (
      <div className="mw-narration-status" role="status" aria-live="polite">
        <span className="mw-spin" aria-hidden="true" />
        <span className="mw-error-msg">
          Explaining {state.library} with AI: {stageText(state.progress)}…
          {state.retried && ` The first draft failed ${state.retried.length} check${state.retried.length === 1 ? "" : "s"}.`}
        </span>
      </div>
    );
  }

  const summary = state.status === "rejected"
    ? `No narration for ${state.library}: the draft failed ${state.problems.length} check${state.problems.length === 1 ? "" : "s"} after ${state.attempts} attempts. Nothing was saved.`
    : `No narration for ${state.library}: ${state.error.split("\n")[0]}`;
  const detail = state.status === "rejected"
    ? state.problems.map((p) => `[${p.kind}] ${p.message}`).join("\n")
    : state.error.includes("\n") ? state.error : null;

  return (
    <div className={`mw-error${open ? " expanded" : ""}`} role="alert">
      <div className="mw-error-summary">
        <span className="mw-error-msg">{summary}</span>
        {detail && (
          <button className="mw-error-toggle" onClick={() => setOpen((v) => !v)}>
            {open ? "Hide" : state.status === "rejected" ? "View problems" : "View Details"}{" "}
            {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
        <button onClick={onDismiss} aria-label="Dismiss"><X size={14} /></button>
      </div>
      {open && detail && <pre className="mw-error-detail">{detail}</pre>}
    </div>
  );
}
