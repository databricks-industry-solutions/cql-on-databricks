// APP-STEP-INTO-PATIENT / SPEC-ATTRITION / APP-DESIGN-SYSTEM — the population funnel, built from the
// measure's metric set (`metricFunnel`) or a Live run's counts (`buildFunnel`). Semantic colors per
// design-system.md: population/denominator on the info→navy scale, the Numerator on --good, exclusions on
// --tfail and exceptions on --twarn. When a patient is selected in the Patient Lens, a lava pin marks each
// stage they reach.
//
// EVERY POPULATION IS ITS OWN BAR, COUNTED AS WRITTEN (`SPEC-POPULATION-CASCADE`, amended 2026-10-29).
// Raw Denominator Exclusions can hold patients who were never in the Denominator, so drawing them as a
// segment inside the Denominator's bar would subtract two unrelated sets. The cascade belongs to the
// score, which the client computes (`RUN-CLIENT-MEASURE-SCORE`); the Numerator's figure is that score,
// with its own base named beside it.
//
// WIDTHS ARE ALL COHORT SHARES, so there is ONE axis and one base for every bar. Nothing is floored in
// JS: `.mw-fbar` carries a 3px `min-width`, so a near-zero population stays visible.

import { ALL_PATIENTS, type Funnel } from "./model.ts";

const FUN = ["var(--fun-1)", "var(--fun-2)", "var(--fun-3)", "var(--fun-4)"];

/** Numerator is the "met" cohort → semantic green; exclusions crit and exceptions warn, as the
 *  design-system roles table assigns them; every other stage walks the info→navy scale. PRESENTATIONAL
 *  ONLY: the worst a wrong name test does is paint a bar the neighbouring hue. */
function stageColor(name: string, step: number): string {
  if (name.includes("Numerator") && !name.includes("Exclusions")) return "var(--tgood)";
  if (name.includes("Exceptions")) return "var(--twarn)";
  if (name.includes("Exclusions")) return "var(--tfail)";
  return FUN[Math.min(step, FUN.length - 1)]!;
}

export function FunnelView({ funnel, patientReached, patientLabel }: {
  funnel: Funnel;
  patientReached?: string[] | null;
  patientLabel?: string | null;
}) {
  let colorStep = 0;
  return (
    <div className="mw-funnel">
      {funnel.stages.map((s) => {
        // WIDTH IS ALWAYS THE SHARE OF THE COHORT, so the bars narrow down the page and the funnel
        // reads as one. The FIGURE beside the name is the stage's own — for a proportion's Numerator
        // that is the measure's score (`SPEC-REPORTED-RESULT`), which is a different base from the
        // bar it sits on. Named in visible text beside it, because two percentages in one column
        // measured against two bases is exactly the thing a reader is entitled to have named.
        const figure = s.rate != null ? Math.round(s.rate * 1000) / 10 : s.pct;
        const pinned = patientReached != null && patientLabel != null &&
          (s.name === ALL_PATIENTS || patientReached.includes(s.name));
        return (
          <div className="mw-fstage" key={s.name}>
            <div className="meta">
              <span className="nm">{s.name}</span>
              <span className="pct tnum">
                {figure}%
                {s.rateBase != null && <span className="of"> of {s.rateBase.toLocaleString()}</span>}
              </span>
            </div>
            <div className="mw-fbar-wrap">
              <div className="mw-fbar-track">
                <div
                  className="mw-fbar"
                  style={{
                    width: `${s.pct}%`,
                    background: stageColor(s.name, colorStep++),
                  }}
                />
                {pinned && (
                  <div className="mw-fmark" style={{ left: `${s.pct}%` }}>
                    <div className="tag">{patientLabel}</div><div className="pin" />
                  </div>
                )}
              </div>
              <span className="mw-fcount tnum">{s.count.toLocaleString()}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
