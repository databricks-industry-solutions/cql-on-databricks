// APP-EXEC-PARAMS / PIPE-MEASUREMENT-PERIOD / DESIGNER-TOP-BAR — the execution PARAMETERS, dialled in
// the toolbar: one `‹ value ›` stepper per parameter, beside the compute pill.
//
// Parameters are values you dial while reading — unlike Data Source and the Live switch, which are
// STATES you set once and leave. Every count, rate and funnel on the page is scoped to a parameter,
// so it must be readable and reachable without a click. The pill reports only what it knows:
// whether compute is engaged.
//
// The bar is written against the LIST, not against the measurement period — nothing here knows what a
// measurement period is. Today the list holds one; a measure package that declares more gets more
// steppers, at no change to this file.

import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "../icons.tsx";
import { type ExecParam, paramValueLabel } from "./model.ts";

export function ExecParamBar({ params }: { params: ExecParam[] }) {
  return <>{params.map((p) => <ParamStepper key={p.id} p={p} />)}</>;
}

/** One parameter: its value between a back and a forward arrow.
 *
 *  A parameter is a value on an ORDERED DOMAIN plus a cursor into it, which is why it is stepped rather
 *  than picked from a list — the arrows say which way is "more", and reading it costs no interaction. The
 *  arrows are DISABLED at the ends of the domain rather than wrapping or running on: the toolbar stepper
 *  this replaces did plain `year ± 1` and would walk into periods that have no data. Where the domain
 *  stops, the control stops, and the disabled arrow says so before it is pressed.
 *
 *  The NAME is in the tooltip, not on the control. A parameter's value is what a reader needs at a
 *  glance; its name is what they need once, and a label beside every stepper makes the bar grow by a
 *  word per parameter. */
function ParamStepper({ p }: { p: ExecParam }) {
  const i = p.options.findIndex((o) => o.id === p.value);
  const arrow = (d: -1 | 1, label: string, glyph: ReactNode) => {
    const next = p.options[i + d];
    return (
      <button className="ar" aria-label={`${label} ${p.label.toLowerCase()}`} disabled={!next}
        onClick={() => next && p.onChange(next.id)}>{glyph}</button>
    );
  };
  return (
    <div className="mw-param" role="group" aria-label={p.label} title={`${p.label}: ${paramValueLabel(p)}`}>
      {arrow(-1, "Previous", <ChevronLeft size={14} />)}
      <span className="pv">{p.options[i]?.label ?? p.value}</span>
      {arrow(1, "Next", <ChevronRight size={14} />)}
    </div>
  );
}
