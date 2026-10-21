// APP-STEP-INTO-PATIENT / SPEC-ATTRITION / APP-DESIGN-SYSTEM — the population funnel, built from a
// Live run's counts (buildFunnel). Semantic colors per design-system.md: population/denominator on the
// info→navy scale, the Numerator on --good, exclusions on --tfail and exceptions on --twarn (the roles
// table names those two removals separately, and drawing both in crit said they were the same kind of
// thing). When a patient is selected in the Patient Lens, a lava pin marks each stage they reach.
//
// A REMOVAL IS A SEGMENT OF ITS BASE'S BAR, NOT A ROW. An exclusion is a SUBSET of the population it
// comes out of; a stacked segment says that, and a separate row says the opposite — that something
// happened after the denominator, to some other cohort. So the Denominator's bar is one bar of its own
// full width, whose solid head is what survived and whose hatched tail is what did not. Every row is
// anchored to the funnel's left edge; nothing floats.
//
// THE SOLID SEGMENT'S RIGHT EDGE IS THE RATE'S BASE, which is the reason this shape earns its keep:
// the quantity the Numerator is divided by becomes visible without a row of its own. A derived
// `Performance denominator` bar was built for that job and cut — see `FunnelRemoval`.
//
// WIDTHS ARE ALL COHORT SHARES, segment widths included, so there is ONE axis and one base for every
// number on the chart. Nothing is floored in JS: `.mw-fbar` / `.mw-fseg` carry a 3px `min-width`, so a
// near-zero removal stays visible without its neighbours being pushed out of alignment.

import { ALL_PATIENTS, type Funnel, type FunnelRemoval } from "./model.ts";

const FUN = ["var(--fun-1)", "var(--fun-2)", "var(--fun-3)", "var(--fun-4)"];

/** Numerator is the "met" cohort → semantic green; all other stages walk the info→navy scale. */
function stageColor(name: string, step: number): string {
  if (name.includes("Numerator")) return "var(--tgood)";
  return FUN[Math.min(step, FUN.length - 1)]!;
}

/** Which of the two removal hues a segment wears — crit for exclusions, warn for exceptions, as the
 *  design-system roles table assigns them. PRESENTATIONAL ONLY, which is why a name test is fine here
 *  and is not fine for deciding whether a population is a removal at all (that is `removesFrom`, and it
 *  is scoring's answer): the worst a wrong guess does is paint a segment the neighbouring hue. */
function removalKind(name: string): string {
  return name.includes("Exceptions") ? "exception" : "exclusion";
}

/** A removal named RELATIVE TO the bar it sits in — "Denominator Exclusions" inside the Denominator's
 *  own bar is just "exclusions". The base's name is already the row's heading, and repeating it in the
 *  caption spends the reader's attention on the word they are looking at. */
function removalTerm(name: string, base: string): string {
  const own = name.startsWith(`${base} `) ? name.slice(base.length + 1) : name;
  return own.toLowerCase();
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
        const removals: FunnelRemoval[] = s.removals ?? [];
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
                    width: `${s.remainingPct ?? s.pct}%`,
                    background: stageColor(s.name, colorStep++),
                  }}
                />
                {removals.map((r, i) => (
                  <div
                    key={r.name}
                    className="mw-fseg"
                    data-removal={removalKind(r.name)}
                    // The LAST segment carries the bar's right rounding, and it is marked here rather
                    // than found with a CSS `:last-child` — the pin is a sibling inside the same track,
                    // so a positional selector picks the wrong element exactly when a member is selected.
                    data-last={i === removals.length - 1 ? "" : undefined}
                    style={{ left: `${r.offsetPct}%`, width: `${r.pct}%` }}
                  />
                ))}
                {pinned && (
                  <div className="mw-fmark" style={{ left: `${s.pct}%` }}>
                    <div className="tag">{patientLabel}</div><div className="pin" />
                  </div>
                )}
              </div>
              <span className="mw-fcount tnum">{s.count.toLocaleString()}</span>
            </div>
            {/* EACH LABEL SITS UNDER THE SEGMENT IT NAMES, indented to that segment's own offset, so the
                association is positional and needs no legend to decode. `padding-left` puts the label in
                NORMAL FLOW at the offset — an absolutely positioned one would contribute no height and
                the rows would collapse onto each other.

                ONE LABEL PER LINE. Sharing a line is only safe while every label is narrower than the
                segment it starts under, which a 3-patient exception never is; stacking them is the rule
                that holds at every cohort size instead of the common one.

                THE REMAINDER GETS NO LABEL. It is the solid head, and its figure is already printed one
                row down as the base of the Numerator's rate ("56.7% of 9,114") — naming it here as well
                puts the same number on the page twice, two lines apart, which invites the reader to
                check whether they are the same number rather than to read one. */}
            {removals.length > 0 && (
              // The wrapper is what makes the percentage MEAN the right thing: percentage padding
              // resolves against the containing block, so the labels have to sit in a box that is the
              // TRACK's width, not the row's — the row carries the count column too.
              <div className="mw-fparts">
                {removals.map((r) => (
                  <div
                    key={r.name}
                    className="mw-fpart"
                    data-removal={removalKind(r.name)}
                    style={{ paddingLeft: `${r.offsetPct}%` }}
                  >
                    −{r.count.toLocaleString()} {removalTerm(r.name, s.name)}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
