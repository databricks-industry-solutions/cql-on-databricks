// APP-ASSISTANT-REFS / APP-STYLE-GUIDE — a chart the assistant draws from figures it derived.
//
// WHAT IT IS FOR. `query_data` answers questions the measure did not compute — a breakdown by age
// band, a month-over-month movement — and a column of numbers in a chat drawer is the shape those
// answers are hardest to read in. This is the one artifact whose data the model WRITES, which is also
// why it is the one artifact that always says so.
//
// EVERY FIGURE HERE IS THE ASSISTANT'S (`SVC-ANSWER-PROVENANCE`), unconditionally and by construction:
// the numbers were transcribed into the fenced block by the model rather than resolved by the client
// out of the metric set, which is exactly the difference between this and a `cql-databricks:figure` reference.
// So the chart wears a derived mark whatever the conversation did — a chart drawn from a `get_output`
// figure is still the model's transcription of one, and "the model copied it correctly" is not a claim
// the client can make. The mark is HUELESS for the reason `APP-NARRATIVE-PROVENANCE` gives: whose
// number a figure is, is not a STATE of the measure.
//
// ONE SERIES, DELIBERATELY. A second series needs a legend, a fixed hue order and a colorblind
// separation check on every adjacent pair — and it needs them inside a panel that is a third of a
// column, where two series and a legend leave no room for the plot. A question that wants two series
// wants two charts, which the model can write as two blocks. The single hue is the app's own chart
// ramp at step 2 (`--fun-2`), because a magnitude chart here already looks like the funnel and
// inventing a second chart palette beside it is what the style guide exists to prevent — step 2 and not
// step 1 because `--fun-1` fails the ramp's 2:1 light-end floor against the panel.
//
// BARS ARE HTML, LINES ARE SVG, and that is not an inconsistency: a horizontal bar row IS the funnel's
// construction (a label, a proportional fill, a figure), so it reuses it; a line has no HTML form.

import { useMemo } from "react";

/** What a ```cql-databricks-chart fence carries. Deliberately flat — a model writes this by hand, and every
 *  nested object is a shape it can get subtly wrong in a way that renders as an empty chart. */
export interface ChartSpec {
  type: "bar" | "line";
  title?: string;
  /** What the categories ARE (`Age band`), for the axis label a bar chart otherwise has to imply. */
  x?: string;
  /** What the values ARE (`Patients`). Named beside the figures rather than on an axis. */
  y?: string;
  /** `[label, value]` per row, in the order they should read. */
  rows: [string, number][];
}

/** Parse a fence body into a spec, or null when it is not one.
 *
 *  NULL RATHER THAN A PARTIAL CHART. A malformed block falls back to rendering as an ordinary code
 *  block, so the reader sees the model's own JSON — which is honest, and is also the only thing that
 *  makes the failure diagnosable. A chart drawn from half-understood rows is a wrong picture of real
 *  data, which is worse than no picture.
 */
export function parseChart(body: string): ChartSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const spec = raw as Partial<ChartSpec>;
  const type = spec.type === "line" ? "line" : spec.type === "bar" ? "bar" : null;
  if (!type || !Array.isArray(spec.rows)) return null;
  const rows: [string, number][] = [];
  for (const row of spec.rows) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const [label, value] = row;
    // A non-finite value is DROPPED rather than plotted as zero (`SPEC-METRIC`: absent is not zero).
    // A bar of length zero asserts a count of none, which is a different and often false claim.
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    rows.push([String(label), value]);
  }
  if (rows.length === 0) return null;
  return { type, rows, title: str(spec.title), x: str(spec.x), y: str(spec.y) };
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

export function AnswerChart({ spec }: { spec: ChartSpec }) {
  const max = useMemo(() => Math.max(...spec.rows.map(([, v]) => v), 0), [spec.rows]);
  return (
    <figure className="mw-ask-chart" aria-label={chartLabel(spec)}>
      {spec.title && <figcaption className="ttl">{spec.title}</figcaption>}
      {spec.type === "bar" ? <Bars spec={spec} max={max} /> : <Line spec={spec} max={max} />}
      {/* The standing statement, once per chart. It is not the answer-level notice repeated: that one
          fires when a TOOL derived a figure, and this one is about the transcription. */}
      <figcaption className="pv">Charted from figures the assistant derived, not computed by the
        measure.</figcaption>
    </figure>
  );
}

/** Horizontal bars — the funnel's construction, so a magnitude chart in an answer and the population
 *  funnel above it read as one idiom. Every bar carries its own figure as TEXT, which is what lets the
 *  chart drop a value axis entirely and what keeps the numbers readable to a screen reader. */
function Bars({ spec, max }: { spec: ChartSpec; max: number }) {
  return (
    <div className="rows">
      {spec.rows.map(([label, value], i) => (
        <div className="row" key={i}>
          <span className="lb" title={label}>{label}</span>
          <span className="tr">
            {/* WIDTH IS THE SHARE OF THE LARGEST BAR, so there is one axis and one base for every
                number on the chart. `min-width` in the CSS keeps a near-zero row visible without
                pushing its neighbours out of alignment. */}
            <span className="bar" style={{ width: `${max > 0 ? (value / max) * 100 : 0}%` }} />
          </span>
          <span className="vl">{value.toLocaleString()}</span>
        </div>
      ))}
      {(spec.x || spec.y) && (
        <p className="ax">{[spec.x, spec.y].filter(Boolean).join(" · ")}</p>
      )}
    </div>
  );
}

/** A line, for movement over an ordered dimension. Endpoints only are labelled and marked — a number
 *  on every point is the noise the reader has to read past to see the shape, which is the one thing a
 *  line is for. Every point carries a `<title>`, so the values are one hover (and one AT step) away. */
function Line({ spec, max }: { spec: ChartSpec; max: number }) {
  const W = 320;
  const H = 96;
  const PAD = 6;
  const rows = spec.rows;
  const min = Math.min(...rows.map(([, v]) => v), 0);
  const span = max - min || 1;
  const at = (i: number, v: number) => ({
    x: rows.length === 1 ? W / 2 : PAD + (i * (W - PAD * 2)) / (rows.length - 1),
    y: PAD + (1 - (v - min) / span) * (H - PAD * 2),
  });
  const points = rows.map(([, v], i) => at(i, v));
  return (
    <div className="plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={chartLabel(spec)} preserveAspectRatio="none">
        {/* The baseline is RECESSIVE — a hairline in the document's own rule ink, not a drawn axis. */}
        <line className="ax" x1={0} y1={H - PAD / 2} x2={W} y2={H - PAD / 2} />
        <polyline className="ln" points={points.map((p) => `${p.x},${p.y}`).join(" ")} />
        {points.map((p, i) => (
          <circle className="pt" key={i} cx={p.x} cy={p.y} r={i === 0 || i === points.length - 1 ? 4 : 0}>
            <title>{`${rows[i]![0]}: ${rows[i]![1].toLocaleString()}`}</title>
          </circle>
        ))}
      </svg>
      <p className="ends">
        <span>{rows[0]![0]} · {rows[0]![1].toLocaleString()}</span>
        {rows.length > 1 && (
          <span>{rows[rows.length - 1]![0]} · {rows[rows.length - 1]![1].toLocaleString()}</span>
        )}
      </p>
    </div>
  );
}

/** The chart's accessible name: what it plots, over what, in how many steps. */
function chartLabel(spec: ChartSpec): string {
  const what = spec.title ?? [spec.y, spec.x].filter(Boolean).join(" by ") ?? "Chart";
  return `${what} — ${spec.type} chart, ${spec.rows.length} value${spec.rows.length === 1 ? "" : "s"}`;
}
