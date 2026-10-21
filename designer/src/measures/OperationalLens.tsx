// APP-LENSES / APP-MEASURE-DETAIL — the Operational lens: the OPERATE/RUN face of the living
// document, a responsive FLEX tile grid focused on operations — scheduling, data freshness, data
// requirements & gaps (APP-DATA-REQS / APP-DATA-GAPS), run context, and the last result.
// Results-analytics live in the Analytical lens; this lens answers "is it running, on fresh data,
// on schedule?". Tiles compute when the doc is computed (APP-EXEC-MODE).

import { useMemo } from "react";
import { analyzeDataRequirements } from "../authoring/dataRequirements.ts";
import type { RunStage } from "../wire.ts";
import { stageLabel } from "./Inspector.tsx";
import type { Funnel, PatientRow } from "./model.ts";

/** Format a RunStage for inline display, sentence-cased for this lens (SVC-RUN-STAGE).
 *
 *  Delegates to `stageLabel` rather than keeping a second stage vocabulary. The copy that used to live
 *  here still named the retired in-process runner's phases (`binding`, `collecting`, per-statement
 *  `Materializing …`) long after the SDP route stopped sending them — two switches over one union is
 *  how one of them goes stale unnoticed. */
function stageText(s: RunStage | null | undefined): string {
  const label = stageLabel(s ?? null);
  return label.charAt(0).toUpperCase() + label.slice(1);
}

interface Props {
  source: string;
  counts: Record<string, number> | null;
  funnel: Funnel | null;
  live: boolean;
  running: boolean;
  runStage?: RunStage | null;
  error: string | null;
  patient: PatientRow | null;
  onRun: () => void;
  onGoLive: () => void;
}

export function OperationalLens({ source, counts, funnel, live, running, runStage, error, onRun, onGoLive }: Props) {
  const { retrieves, valueSets } = useMemo(() => analyzeDataRequirements(source), [source]);
  // SPEC-SCORING-METHODOLOGY — a rate only where the measure is DECLARED a proportion.
  const readout = funnel?.readout ?? null;
  const rate = readout?.kind === "rate" ? readout.value : null;
  const num = counts?.["Numerator"];
  const den = counts?.["Denominator"] ?? counts?.["Initial Population"];

  return (
    <div className="mw-tiles">
      {/* Last result — the operational headline (are we producing a number?). */}
      <section className="mw-tile span2">
        <div className="mw-tile-head"><span className="mw-ctag pop">Result</span><span className="mw-cname">Last computed rate</span>
          <div className="right">{live ? <RunPill running={running} error={error} onRun={onRun} /> : <span className="mw-pill">static</span>}</div></div>
        <div className="mw-tile-body">
          {!live ? <GoLive onGoLive={onGoLive} what="the current rate" />
            : rate != null ? (
              <div className="mw-score">
                <div className="big tnum">{(rate * 100).toFixed(1)}%</div>
                <div className="txt"><b>{(num ?? 0).toLocaleString()}</b> of <b>{(den ?? 0).toLocaleString()}</b> patients meet the target · measurement period <b>CY 2026</b>.</div>
              </div>
            ) : <p className="mw-desc">{running ? stageText(runStage) : error ? error : "No rate — the run returned no Numerator/Denominator."}</p>}
        </div>
      </section>

      {/* Schedule / next run. */}
      <section className="mw-tile">
        <div className="mw-tile-head"><span className="mw-ctag">Schedule</span><span className="mw-cname">Runs</span></div>
        <div className="mw-tile-body">
          <div className="mw-meta-row"><span>Cadence</span><span className="v">Quarterly</span></div>
          <div className="mw-meta-row"><span>Last run</span><span className="v">{live ? "just now" : "—"}</span></div>
          <div className="mw-meta-row"><span>Next run</span><span className="v">2026-10-01</span></div>
          <div className="mw-meta-row"><span>Owner</span><span className="v">Quality Analytics</span></div>
        </div>
      </section>

      {/* Freshness — last refresh of the measure spec + each source. */}
      <section className="mw-tile">
        <div className="mw-tile-head"><span className="mw-ctag">Freshness</span><span className="mw-cname">Last refresh</span></div>
        <div className="mw-tile-body">
          <div className="mw-meta-row"><span>Specification</span><span className="v">2026-06-14</span></div>
          {retrieves.length === 0 && <div className="mw-meta-row"><span>Sources</span><span className="v">—</span></div>}
          {retrieves.map((r, i) => (
            <div className="mw-meta-row" key={r.type}><span>{r.type}</span><span className="v">{freshDate(i)}</span></div>
          ))}
        </div>
      </section>

      {/* Data requirements & gaps (APP-DATA-REQS / APP-DATA-GAPS). */}
      <section className="mw-tile span2">
        <div className="mw-tile-head"><span className="mw-ctag vs">Data</span><span className="mw-cname">Requirements &amp; gaps</span>
          <div className="right"><span className="mw-out-badge"><b className="tnum">{retrieves.length}</b> resources · <b className="tnum">{valueSets.length}</b> value sets</span></div></div>
        <div className="mw-tile-body">
          <div className="mw-hbars">
            {retrieves.map((r) => {
              const gaps = live ? gapPct(r.type) : null;
              return (
                <div className="mw-hbar" key={r.type}>
                  <span className="lab">{r.type}</span>
                  <span className="track"><i style={{ width: `${live ? 100 - (gaps ?? 0) : 8}%`, background: gaps && gaps > 8 ? "var(--twarn)" : "var(--tgood)" }} /></span>
                  <span className="val tnum">{live ? (gaps === 0 ? "complete" : `${gaps}% gap`) : "—"}</span>
                </div>
              );
            })}
          </div>
          <p className="mw-desc" style={{ marginTop: 10 }}>Coverage of the resources this measure reads. Unmet requirements become <b>Data Gaps</b> (missing/insufficient Case features); full attribute/value-set detail is in <b>Data Requirements</b>.</p>
        </div>
      </section>

      {/* Run context. */}
      <section className="mw-tile">
        <div className="mw-tile-head"><span className="mw-ctag">Run</span><span className="mw-cname">Context</span></div>
        <div className="mw-tile-body">
          <div className="mw-meta-row"><span>Period</span><span className="v">CY 2026</span></div>
          <div className="mw-meta-row"><span>Evaluated</span><span className="v tnum">{live && funnel ? funnel.total.toLocaleString() : "—"}</span></div>
          <div className="mw-meta-row"><span>Engine</span><span className="v">cql() · Spark Connect</span></div>
          <div className="mw-meta-row"><span>Status</span><span className="v">{live ? "computed" : "not run"}</span></div>
        </div>
      </section>
    </div>
  );
}

/** Deterministic synthetic "last refreshed" dates for source resources (concept). */
function freshDate(i: number): string {
  const days = [1, 2, 1, 3, 2, 4][i % 6]!;
  return `2026-07-${String(18 - days).padStart(2, "0")}`;
}
/** Deterministic synthetic data-gap percentage per resource (concept; real gaps are APP-DATA-GAPS). */
function gapPct(type: string): number {
  let h = 0; for (const c of type) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return [0, 0, 4, 11][h % 4]!;
}

function RunPill({ running, error, onRun }: { running: boolean; error: string | null; onRun: () => void }) {
  if (running) return <span className="mw-pill wait"><span className="mw-spin" style={{ width: 11, height: 11 }} /> running</span>;
  if (error) return <span className="mw-pill fail">run failed</span>;
  return <button className="mw-run" onClick={onRun}>↻ Re-run</button>;
}

function GoLive({ onGoLive, what }: { onGoLive: () => void; what: string }) {
  return (
    <div className="mw-static-cta">
      <p className="mw-desc">Static specification. Run <b>synthetic</b> (or go Live) to compute {what}.</p>
      <button className="mw-run" onClick={onGoLive} style={{ marginTop: 8 }}><span className="pulse" aria-hidden="true" />Run synthetic</button>
    </div>
  );
}
