// APP-PILL-MENU / APP-STABLE-LAYOUT / APP-EXECUTION-PILL — the reusable SELECTOR PILL: a
// compact toolbar control for the execution states (Data Source · Compute · Updates).
// Its trigger is a status DOT and a caret, and it opens a popover. Open-state is a straight accent bar,
// never a ring (cql-databricks-no-curved-accent-rings).
//
// THE TRIGGER CARRIES NO VALUE (APP-EXECUTION-PILL). A `● <data source> · <warehouse> ▾` label was tried
// and dropped: it was long and reflowed the toolbar. The values are in the tooltip and in the popover's rows.
//
// THE POPOVER IS ONE SHORT MENU OF ITEMS, no section headings. An item is one of:
//   • SUBMENU — one row: its label as an eyebrow above the current value, then `›`; its options open
//               beside the popover. For a choice set once (data source, warehouse).
//   • CHECK   — one checkable item (`Automatic`) in the shared row language: checked takes the accent
//               wash and the tick every selected row takes; unchecked is a plain row. No form controls
//               (no box, no dot), and no second item for the off state (Manual).
//   • ACTIONS — verbs that act on the items above them (Refresh), after a separator. Drawn as BUTTONS,
//               not rows: a verb that looks like a choice was read as one. The blurb is the
//               button's tooltip, so the popover stays narrow.
// Picking closes the pill: everything in here is a decision.
//
// DRAWN AS BUTTONS, NOT ROWS (items are: submenu — one row with its label as eyebrow, options open
// beside; check — one checkable item in the shared row language; actions — verbs that act on the items
// above them after a separator, drawn as buttons not rows because a verb that looks like a choice reads
// as one).

import type React from "react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Check } from "../icons.tsx";

/** Semantic status of a pill option / the trigger dot, driving its color (and the running pulse). */
export type PillStatus = "none" | "info" | "live";

export interface PillOption {
  id: string;
  label: string;
  blurb?: string;
  status?: PillStatus;
}

/** SUBMENU — one row naming `label` and the selected option; the options open beside the popover. */
export interface PillSubmenuSection {
  kind: "submenu";
  /** The item's own label, shown inside the row (it replaces a section heading). */
  label: string;
  value: string;
  options: PillOption[];
  onChange: (id: string) => void;
  /** Shown in place of the options when there are none to offer (for example, a failed list). */
  empty?: string;
}

/** CHECK — one checkable item; unchecked is the other state. */
export interface PillCheckSection {
  kind: "check";
  label: string;
  blurb?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

/** One verb in an ACTIONS group. */
export interface PillAction {
  id: string;
  label: string;
  blurb?: string;
  disabled?: boolean;
  onPick: () => void;
}

/** ACTIONS — verbs that act on the items above them. `label` names the group for assistive tech. */
export interface PillActionSection {
  kind: "actions";
  label: string;
  actions: PillAction[];
}

export type PillSection = PillSubmenuSection | PillCheckSection | PillActionSection;

export function PillMenu({
  status, running = false, progress = null, ariaLabel, title, sections, stageBadge,
}: {
  /** The trigger dot's status color. */
  status: PillStatus;
  /** True while a live/async selection is computing. Exposed as `data-running` on the trigger — state
   *  on the DOM, not motion: the dot no longer pulses (a permanent animation in the toolbar corner of
   *  a reading surface), and progress is reported by the stage badge at the head of the popover. */
  running?: boolean;
  /** APP-COMPUTE-ACTIVITY — how far the running work has got, 0–1, or null when there is no figure.
   *
   *  Drawn as a HAIRLINE along the collapsed trigger's bottom edge, so the pill reports progress without
   *  being opened. The stage badge is inside the popover, which means the one control that says a run is
   *  happening could not say how far along it was until you clicked it — and the answer to "is it worth
   *  waiting?" should not itself cost an interaction. Same material as the badge's bar (`--fun-3` over a
   *  hairline track) so the collapsed and expanded readings are one idiom at two sizes, and a fill rather
   *  than an animation for the same reason the dot stopped pulsing. */
  progress?: number | null;
  ariaLabel: string;
  title?: string;
  sections: PillSection[];
  /** Stage badge, at the head of the popover: the run status (APP-UPDATE-MODE). LEFT-ALIGNED: the badge
   *  is the head, and the head of a popover starts where every row below it starts — a chip alone against the
   *  right edge reads as an afterthought pinned to the corner rather than the first thing stated. */
  stageBadge?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // One submenu open at a time; closing the pill closes it.
  const [openSub, setOpenSub] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!open) setOpenSub(null); }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc); document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [open]);

  return (
    <div className={"mw-selpill" + (open ? " open" : "")} ref={ref}>
      {/* `aria-haspopup="true"`, not "listbox": the popover holds submenus, a checkable item and actions,
          so it is a menu of controls rather than one list of options. */}
      {/* The hairline is painted from `--run-pct`, set only while running and only when there is a
          figure to state — an unset property leaves the track empty rather than claiming 0% progress,
          which is a different assertion (`SPEC-METRIC`: absence is stated, never rendered as a zero). */}
      <button className="mw-selpill-trigger" data-status={status} data-running={running || undefined}
        style={running && progress != null
          ? { "--run-pct": `${Math.round(progress * 100)}%` } as React.CSSProperties : undefined}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true" aria-expanded={open} aria-label={ariaLabel} title={title ?? ariaLabel}>
        <Dot status={status} />
        <span className="caret" aria-hidden="true"><ChevronDown size={14} /></span>
      </button>
      {open && (
        <div className="mw-selpill-list" aria-label={ariaLabel}>
          {stageBadge && <div className="mw-selpill-info" role="status">{stageBadge}</div>}
          {sections.map((s) => s.kind === "actions" ? (
            <div key={s.label} className="mw-selpill-sec mw-selpill-actions" role="group" aria-label={s.label}>
              {s.actions.map((a) => (
                <button key={a.id} className="mw-selpill-action" title={a.blurb}
                  disabled={a.disabled} onClick={() => { setOpen(false); a.onPick(); }}>
                  {a.label}
                </button>
              ))}
            </div>
          ) : s.kind === "check" ? (
            <button key={s.label} className={"mw-selpill-row mw-selpill-check" + (s.checked ? " on" : "")}
              role="menuitemcheckbox" aria-checked={s.checked}
              onClick={() => { s.onChange(!s.checked); setOpen(false); }}>
              <span className="tx"><span className="nm">{s.label}</span>{s.blurb && <span className="bl">{s.blurb}</span>}</span>
              {s.checked && <span className="tick" aria-hidden="true"><Check size={13} /></span>}
            </button>
          ) : (
            <PillSubmenu key={s.label} s={s} open={openSub === s.label}
              onToggle={() => setOpenSub((v) => (v === s.label ? null : s.label))}
              onPicked={() => setOpen(false)} />
          ))}
        </div>
      )}
    </div>
  );
}

/** A submenu's options: one listbox. */
function PillOptions({ s, onPicked }: { s: PillSubmenuSection; onPicked: () => void }) {
  if (s.options.length === 0 && s.empty) return <div className="mw-selpill-empty" role="status">{s.empty}</div>;
  return (
    <div role="listbox" aria-label={s.label}>
      {s.options.map((o) => (
        <PillRow key={o.id} o={o} active={o.id === s.value} onPick={() => { s.onChange(o.id); onPicked(); }} />
      ))}
    </div>
  );
}

/** SUBMENU: one row naming its label and the selected option; the options open beside the popover.
 *  Click-only: opening on hover as well made the click that followed the pointer close it again. */
function PillSubmenu({ s, open, onToggle, onPicked }: {
  s: PillSubmenuSection; open: boolean; onToggle: () => void; onPicked: () => void;
}) {
  const current = s.options.find((o) => o.id === s.value);
  const value = current?.label ?? (s.value || "None available");
  return (
    <div className="mw-selpill-subwrap">
      <button className={"mw-selpill-row mw-selpill-subrow" + (open ? " open" : "")} aria-haspopup="listbox"
        aria-expanded={open} aria-label={`${s.label}: ${value}`} onClick={onToggle}>
        <span className="tx"><span className="hd">{s.label}</span><span className="val">{value}</span></span>
        <span className="more" aria-hidden="true"><ChevronRight size={13} /></span>
      </button>
      {open && <div className="mw-selpill-sub"><PillOptions s={s} onPicked={onPicked} /></div>}
    </div>
  );
}

function PillRow({ o, active, onPick }: { o: PillOption; active: boolean; onPick: () => void }) {
  return (
    <button className={"mw-selpill-row" + (active ? " on" : "")} data-status={o.status ?? "none"} role="option"
      aria-selected={active} onClick={onPick}>
      {/* A dot only where there is a STATE to report. An option that is a plain value gets none — a gray
          dot claiming otherwise is colour as decoration (design-system section 6). */}
      {o.status && <Dot status={o.status} />}
      <span className="tx"><span className="nm">{o.label}</span>{o.blurb && <span className="bl">{o.blurb}</span>}</span>
      {active && <span className="tick" aria-hidden="true"><Check size={13} /></span>}
    </button>
  );
}

function Dot({ status }: { status: PillStatus }): ReactNode {
  return <span className={"dot" + (status === "none" ? " none" : "")} data-status={status} aria-hidden="true" />;
}
