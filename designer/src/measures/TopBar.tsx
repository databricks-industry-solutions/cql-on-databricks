// DESIGNER-TOP-BAR — ONE bar.
//
// THREE ZONES, one question each: the artifact selector on the left says *what you are reading*,
// the centre says *how it is computed* (Data Source · the compute pill), and the right
// holds the app's own toggles. Execution is the reader's second subject after the measure itself,
// so it belongs in the middle of the bar, not in its margin.
//
// Tour and theme are two buttons (`.mw-bar-tools`) on the right. Settings is removed by DESIGNER-ONE-MODULE.
// No account menu: this app has no identity endpoint. Tour, theme and (formerly) Settings are TOGGLEs
// of the app's own state, and a toggle behind a popover cannot show its state. Buttons in the bar
// say all of them at a glance and answer in one press.
//
// This component owns the parts every module shares; the module-specific zones arrive as slots, so there
// is one bar in the codebase rather than one copy per screen.
//
// Appearance stays THREE-state (System · Light · Dark) — the segmented control became a CYCLE on one
// button. Three, not two, for the reason the segment had three: a two-state toggle cannot express
// "follow the OS", which is the default the workspace ships (`prefers-color-scheme`), so a reader who
// flipped it once could never get back. The glyph is the state the app is IN.

import type { ReactNode } from "react";
import { CircleHelp, Moon, Sun, SunMoon } from "../icons.tsx";
import type { ThemeChoice } from "./model.ts";

/** The appearance button's three faces, and the cycle it walks. One table, so the glyph, the word in the
 *  tooltip and the next stop cannot disagree about what state the app is in. */
const THEME_FACE: Record<ThemeChoice, { Icon: typeof SunMoon; label: string; next: ThemeChoice }> = {
  system: { Icon: SunMoon, label: "System", next: "light" },
  light: { Icon: Sun, label: "Light", next: "dark" },
  dark: { Icon: Moon, label: "Dark", next: "system" },
};

export interface TopBarChrome {
  onToggleTour: () => void;
  /** Whether the tour overlay is up — the tour button is a toggle, so it shows its state. */
  tourOpen: boolean;
  theme: ThemeChoice;
  onThemeChange: (t: ThemeChoice) => void;
}

export function TopBar({ onToggleTour, tourOpen, theme, onThemeChange, crumb, context, moduleTools }: TopBarChrome & {
  /** The left zone — the artifact selector, when the module has something open. */
  crumb?: ReactNode;
  /** The centre zone: execution context (Data Source · the compute pill). */
  context?: ReactNode;
  /** APP-ASSISTANT — the module's own bar controls, BEFORE the app-toggle cluster.
   *
   *  A separate slot rather than more children of `.mw-bar-tools`, because that cluster's whole claim
   *  is that it holds the controls about the APP and nothing else (design-system section 6). The
   *  assistant is about the measure, so it sits outside the cluster and leaves the claim true; the
   *  tighter gap inside `.mw-bar-tools` is what still reads the three as one group. */
  moduleTools?: ReactNode;
}) {
  const face = THEME_FACE[theme];
  return (
    <header className="mw-bar">
      <div className="mw-bz-left">{crumb}</div>

      <div className="mw-bz-center">{context}</div>

      <div className="mw-bz-right">
        {moduleTools}
        {/* Two toggles of the APP's own state — the only things in the bar that are not about the
            measure, which is why they are alone at its end. Grouped at a tighter gap than the zone's own
            so they read as one cluster. */}
        <div className="mw-bar-tools">
          <button className="mw-icon-btn" aria-pressed={tourOpen}
            aria-label="Product tour" title="Product tour" onClick={onToggleTour}>
            <CircleHelp size={16} aria-hidden="true" />
          </button>
          <button className="mw-icon-btn" aria-label={`Appearance: ${face.label}`}
            title={`Appearance: ${face.label} — switch to ${THEME_FACE[face.next].label}`}
            onClick={() => onThemeChange(face.next)}>
            <face.Icon size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </header>
  );
}
