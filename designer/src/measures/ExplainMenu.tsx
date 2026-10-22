// SPEC-EXPLAIN-VIEW / SPEC-EXPLAIN-AGAIN — the bar's Explain control: a two-state VIEW toggle and, on a
// caret beside it, the one ACT that belongs to the same subject.
//
// WHY A SPLIT CONTROL AND NOT TWO. Explain governs whether the interpreted layer is showing; "Explain
// again" asks for a fresh reading of one section of it. They are the same subject at two altitudes — a
// preference and an act — and a reader looking for the second goes to the first. Two independent
// controls in a 52px bar would be two objects to learn where the second is used once in a session; a
// caret is the standing sign that there is more behind a control, which is what the compute pill and
// every breadcrumb segment in this bar already teach.
//
// THE ACT DOES NOT LIVE ON THE TOGGLE'S PRESS, and that is the whole reason for the caret. The toggle is
// advertised as safe to flip repeatedly while reading (`SPEC-EXPLAIN-VIEW`: it filters prose already
// fetched, generates nothing, costs nothing). Putting a generation behind the same press would make the
// cheapest control in the bar occasionally expensive, which is how a reader stops trusting it.
//
// THE MENU'S ROW IS AIMED BY THE DOCUMENT, not by this control. "Explain again" acts on ONE section, and
// the bar cannot know which — so the target is whatever section's inline Explain mark was last pressed
// (`ExplainBadge`), and with none pressed the row is disabled and SAYS SO in its own blurb rather than in
// a tooltip. A disabled control that cannot say why is indistinguishable from a broken one
// (`design-system.md` section 6, the pane-tab rule); this one says why in the row's own body, which is
// the same fix that rule prescribes.
//
// APP-NARRATIVE-OVERLAY — the menu's second act generates the open library's narration. It is a library-
// level act, so it is aimed by the open artifact rather than by a section mark. Included libraries are
// narrated from their own page: a narration belongs to the library that declares the define.

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Explain, ExplainOff } from "../icons.tsx";
import type { ExplainTarget } from "./MeasureNarrative.tsx";

export function ExplainMenu({ explain, onToggle, target, onExplainAgain, narration, onGenerate }: {
  /** SPEC-EXPLAIN-VIEW — whether the interpreted layer is showing. */
  explain: boolean;
  onToggle: () => void;
  /** SPEC-EXPLAIN-AGAIN — the section the document last aimed at, or null if none has been. */
  target: ExplainTarget | null;
  onExplainAgain: (target: ExplainTarget) => void;
  /** APP-NARRATIVE-OVERLAY — the open library, whether it has a narration yet, and whether one is being
   *  written. */
  narration: { library: string; exists: boolean; running: boolean };
  onGenerate: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  // The app's one popover dismissal contract: a press outside, or Escape (`design-system.md` section 6).
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <span className="mw-pop-anchor mw-explain-group" ref={ref}>
      {/* SPEC-EXPLAIN-VIEW — unchanged in every respect but its right-hand radius. A VIEW, not an
          action: it filters prose already fetched, so it is safe to flip repeatedly while reading, and
          the workspace's `toggleExplain` keeps the reader's place across the flip so that claim holds.

          THE GLYPH CARRIES THE STATE, not only the fill behind it. A speech bubble is commentary; the
          dashed bubble is that same commentary withdrawn, so the control says what it is showing at a
          glance rather than making `aria-pressed`'s wash the only sign. */}
      <button className="mw-explain" aria-pressed={explain} onClick={onToggle}
        title={explain
          ? "Explain is on — our reading of the logic is shown, each block signed with how it was produced. Turn it off to see only what is derived from the CQL and stated by the measure's author."
          : "Explain is off — only derived structure and the author's own words. Turn it on to add the interpreted prose back."}>
        {explain
          ? <Explain size={13} aria-hidden="true" />
          : <ExplainOff size={13} aria-hidden="true" />}
        Explain
      </button>
      {/* The caret is NAMED, because it is icon-only and a glyph nobody can read is not a control. Its
          resting ink is `--tink-3` — the step this bar gives a glyph that sits BESIDE a label, since the
          word next door does the naming (`design-system.md` section 5). */}
      <button className="mw-explain-more" aria-haspopup="menu" aria-expanded={open}
        aria-label="Explain options" title="Explain options"
        onClick={() => setOpen((v) => !v)}>
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {open && (
        <div className="mw-pop mw-explain-pop right" role="menu" aria-label="Explain options">
          <div className="mw-pop-lbl">Explain</div>
          {/* The shared popover row language — name over blurb (`design-system.md` section 6). No dot:
              a dot in this language carries STATUS, and a verb has none. */}
          <button className="mw-selpill-row" role="menuitem" disabled={!target}
            onClick={() => { if (target) { setOpen(false); onExplainAgain(target); } }}>
            <span className="tx">
              <span className="nm">Explain again</span>
              <span className="bl">
                {target
                  ? `Ask the assistant for a fresh reading of ${target.label}. It writes to the same
                     contract this document's prose was written to; nothing here is overwritten.`
                  : `Press the Explain mark on a section to choose what to re-explain — a re-explanation
                     is about one criterion, and no section has been picked yet.`}
              </span>
            </span>
          </button>
          <button className="mw-selpill-row" role="menuitem" disabled={narration.running}
            onClick={() => { setOpen(false); onGenerate(); }}>
            <span className="tx">
              <span className="nm">{narration.exists ? "Regenerate narration" : "Generate narration"}</span>
              <span className="bl">
                {narration.running
                  ? `A narration of ${narration.library} is being written. Its progress shows above the document.`
                  : `${narration.exists ? "Write a new" : "Write a"} narration of ${narration.library} with AI. It is
                     checked against the compiled logic before it is shown, and saved for you only. Takes a few
                     minutes.`}
              </span>
            </span>
          </button>
        </div>
      )}
    </span>
  );
}
