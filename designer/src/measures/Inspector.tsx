// APP-STEP-INTO-PATIENT / ACC-KA-TRACE — the Patient Lens. The per-member trace lets you step DOWN
// from an aggregate into one member's Case (APP-STEP-INTO-PATIENT); the "Climb to Pathway" block
// traces UP from the criterion that placed them to the guideline recommendation it realizes
// (ACC-KA-TRACE). Live gates computation (APP-LIVING-MEASURE): Static shows the reading prompt.
//
// The surrounding INSPECTOR is gone. APP-WORKBENCH replaced it: the right pane holds assertions
// about the document, not a second rendering of it, and the population summary and Artifact card it
// carried moved into the Workbench's pinned Context block. The Patient Lens itself is only passing
// through — APP-PATIENT-FOCUS makes patient focus a MODE that re-centers the whole document, so a
// member list in a side panel is a contradiction with a scheduled end. It is exported rather than
// deleted so the Workbench can keep it reachable until the left pane grows its Members tab.

import { useMemo, useState } from "react";
import { Check, X, ArrowUp, ArrowRight, Search } from "../icons.tsx";
import type { RunStage } from "../wire.ts";
import type { PatientRow } from "./model.ts";
import { POPULATIONS, isTruthy, landingLabel, landingChip, decidingCriterion, pathwayFor } from "./model.ts";

/** Format a RunStage into a short user-facing label for the badge (SVC-RUN-STAGE).
 *
 *  The compute phase counts the run's definitions (`RUN-PROGRESS-TASKS`), so the badge states how much of the
 *  run is left rather than just that one is happening. `profile` reads as "compiling…" because it is
 *  the orchestration's closing frame, not a phase a reader has a name for.
 *
 *  The switch is exhaustive over `RunStage["stage"]` with no `default`, deliberately: a stage the
 *  backend adds must fail the typecheck here instead of silently rendering "running…" forever. */
export function stageLabel(s: RunStage | null): string {
  if (!s) return "running…";
  switch (s.stage) {
    case "connecting": return "connecting…";
    case "compiling": return "compiling…";
    case "profile": return "compiling…";
    case "executing": {
      const t = taskCounts(s);
      return t ? `computing ${t.done}/${t.total}` : "starting compute…";
    }
    case "done": return "done";
    case "error": return "error";
  }
}

/** RUN-PROGRESS-TASKS — how many of the run's definitions have finished, or `null` before the run
 *  has reported its plan.
 *
 *  Every TERMINAL state counts as done — `completed`, and also `skipped` and `failed`. A failed
 *  definition is not going to progress further; a bar that holds it open can never reach 100% on a run
 *  that has stopped. */
function taskCounts(s: RunStage): { done: number; total: number } | null {
  const tasks = s.tasks ?? [];
  if (tasks.length === 0) return null;
  const terminal = new Set(["completed", "skipped", "failed"]);
  return { done: tasks.filter((t) => terminal.has(t.status)).length, total: tasks.length };
}

/** SVC-RUN-STAGE / RUN-PROGRESS-TASKS — derive a 0–1 progress fraction from the current stage event.
 *
 *  **THE COMPUTE BAND IS REAL PROGRESS, MEASURED IN DEFINITIONS.** `/api/run-stream` ships the run's
 *  whole closure before it runs, then again as each definition is reused or computed — so N-of-M over
 *  that list is the actual unit of work, not an estimate.
 *
 *  What this replaced, and why the shape of the bug matters: the previous bands described the RETIRED
 *  in-process runner's phases (`binding`, `plan`, `collecting` weighted 40–95%), none of which the SDP
 *  route emits. A run therefore read 22% (`compiling`), snapped to **0%** (`profile` matched nothing
 *  and fell through the final `return 0`), then sat at exactly 30% for the entire Job — `executing`
 *  carries no `step`/`total`, so the band's own interpolation term was always zero. Three of the four
 *  frames the backend actually sends were mis-scored, which is why the gauge looked stuck: it was.
 *
 *  Band layout:
 *    connecting 0–5 | compiling 5–15 | computing 15–99 (by task) | done 100 */
export function stageProgress(s: RunStage | null): number {
  if (!s) return 0;
  switch (s.stage) {
    case "connecting": return 0;
    case "compiling": return 0.05;
    // The profile frame arrives once the orchestration is done and the Job is about to be polled, so
    // it is genuinely further along than `compiling` — it must never score lower.
    case "profile": return 0.15;
    case "executing": {
      const t = taskCounts(s);
      // No task list yet: the run has started but has not reported its plan. Hold at the band's
      // floor rather than showing 0 for a run that is demonstrably underway.
      if (!t) return 0.15;
      return 0.15 + 0.84 * (t.done / t.total);
    }
    case "done": return 1;
    case "error": return 1;
  }
}

/** The searchable member list plus the stepped-into trace (APP-STEP-INTO-PATIENT).
 *
 *  CURRENTLY UNMOUNTED. The right pane is scoped to data about the SELECTION (`APP-INSPECTOR`),
 *  and a member list is not that. `APP-PATIENT-FOCUS` makes stepping into a member a MODE
 *  that re-centres the whole document, which a side panel cannot do.
 *
 *  It is KEPT, not deleted. This is the only implementation of `APP-STEP-INTO-PATIENT`,
 *  a specified and reviewed feature. Deleting a working surface loses the work rather than re-siting it.
 *  What it needs is the mode `APP-PATIENT-FOCUS` describes. Nothing here should be EXTENDED in the
 *  meantime — an unmounted component is not a foundation to build on — and if that mode is abandoned,
 *  this goes with it. */
export function PatientLens({ patients, selectedId, onSelect, computed, running, runStage, error }: {
  patients: PatientRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  computed: boolean;
  running: boolean;
  runStage?: RunStage | null;
  error: string | null;
}) {
  const [q, setQ] = useState("");
  const selected = patients.find((p) => p.id === selectedId) ?? null;

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle ? patients.filter((p) => p.label.toLowerCase().includes(needle) || p.id.toLowerCase().includes(needle)) : patients;
    return list.slice(0, 80);
  }, [patients, q]);

  return (
    <>
      <div className="mw-lens-card">
        <div className="mw-lens-search">
          <Search size={14} aria-hidden="true" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Trace a patient by id…" aria-label="Search patients" />
        </div>
        <div className="mw-plist">
          {shown.length === 0 && <div className="mw-lens-hint">{!computed ? "Select a data source from the Compute menu to run the measure." : running ? stageLabel(runStage ?? null) : error ? `Run failed: ${error}` : "No members in the computed population."}</div>}
          {shown.map((p) => (
            <button key={p.id} className={"mw-prow" + (p.id === selectedId ? " sel" : "")} onClick={() => onSelect(p.id)}>
              {/* Leading abbreviated landing chip (nav/Define type-chip language) + name — parallel to a
                  nav row; the redundant prose descriptor + avatar are dropped (APP-STYLE-GUIDE). */}
              <span className={"k mw-land-" + p.landing} title={landingLabel(p.landing)}>{landingChip(p.landing)}</span>
              <span className="pn">{p.label}</span>
            </button>
          ))}
        </div>
      </div>

      {selected ? (
        <PatientTrace patient={selected} />
      ) : (
        <div className="mw-lens-hint">Pick a patient to <b>step into them</b> — every population define shows that member’s value, a pin marks where they land in the funnel, and you can climb from the deciding criterion up to the guideline it implements.</div>
      )}
    </>
  );
}

/** The per-member trace (APP-STEP-INTO-PATIENT) plus the up-climb to the Pathway recommendation the
 *  deciding criterion realizes (ACC-KA-TRACE). The deciding step is flagged; its recommendation, when
 *  authored, is shown as pluggable, attributed guideline content (APP-CLINICAL-BOUNDARY). */
function PatientTrace({ patient }: { patient: PatientRow }) {
  const deciding = decidingCriterion(patient);
  const rec = pathwayFor(deciding.define);
  return (
    <div className="mw-trace">
      <div className="th"><span className="nm">{patient.label}</span><span className="ag">{landingLabel(patient.landing)}</span></div>
      {POPULATIONS.filter((n) => n in patient.values).map((n) => {
        const yes = isTruthy(patient.values[n]);
        const decided = n === deciding.define;
        return (
          <div className={"mw-tstep " + (yes ? "y" : "n") + (decided ? " decided" : "")} key={n}>
            <span className="ic">{yes ? <Check size={11} /> : <X size={11} />}</span>
            <span className="sl">{n}{decided && <span className="decides">placed here</span>}</span>
            <span className="sv">{fmt(patient.values[n])}</span>
          </div>
        );
      })}

      {/* Raw values — every key the backend returned for this subject, regardless of POPULATIONS matching. */}
      <details className="mw-raw-section">
        <summary className="mw-raw-toggle">Raw values ({Object.keys(patient.values).length} keys)</summary>
        <pre className="mw-raw-json">{JSON.stringify(patient.values, null, 2)}</pre>
      </details>

      {/* ACC-KA-TRACE (up) — climb from the deciding define to the guideline recommendation it realizes. */}
      <div className="mw-pathway">
        <div className="mw-pathway-head"><span className="up" aria-hidden="true"><ArrowUp size={12} /></span>Climb to Pathway</div>
        {rec ? (
          <>
            <div className="mw-pathway-chain">
              <span className="node def">{deciding.define}</span>
              <span className="arm" aria-hidden="true"><ArrowRight size={12} /></span>
              <span className="node rec">Recommendation {rec.rec}</span>
            </div>
            <p className="mw-pathway-stmt">“{rec.statement}”</p>
            <div className="mw-pathway-src">{rec.guideline} · authored guideline content</div>
          </>
        ) : (
          <p className="mw-pathway-none">No Pathway recommendation is linked to <b>{deciding.define}</b> yet.</p>
        )}
      </div>
    </div>
  );
}

function fmt(v: unknown): string {
  if (v === true) return "true";
  if (v === false) return "false";
  if (v == null) return "—";
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) return `${v.length} item${v.length === 1 ? "" : "s"}`;
  if (typeof v === "object") return "…";
  return String(v).slice(0, 24);
}
