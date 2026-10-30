// DESIGNER-ONE-MODULE — the top-level app frame. The Measures workspace is the one module
// (DESIGNER-ONE-MODULE). Settings is removed by DESIGNER-ONE-MODULE.
// APP-URL-ROUTING — every path renders the one module; the workspace owns its own query state.
//
// DESIGNER-TOP-BAR — this frame owns the chrome every screen shares: the theme and the tour.
// Each screen renders ONE `TopBar` from the `chrome` bundle it is handed, so the bar
// exists once in the codebase. This avoids duplicating theme state across multiple screens that
// would need resetting whenever the reader switched module.

import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MeasureWorkspace } from "./measures/MeasureWorkspace.tsx";
import type { TopBarChrome } from "./measures/TopBar.tsx";
import { ProductTour } from "./tour/ProductTour.tsx";
import {
  loadTheme, pathForModule, saveTheme,
  type DeepLink, type ExampleTarget, type ThemeChoice, type TopModuleId,
} from "./measures/model.ts";

const TOUR_SEEN_KEY = "dha.tourSeen";

export function Root() {
  const navigate = useNavigate();

  // APP-TOUR — a "Show me" deep-links into the workspace; a normal module switch clears it.
  const [deepLink, setDeepLink] = useState<DeepLink | null>(null);
  // Tour overlay — shown on first run, re-openable from the bar's tour button (APP-TOUR).
  const [tourOverlay, setTourOverlay] = useState(() => {
    try { return !localStorage.getItem(TOUR_SEEN_KEY); } catch { return false; }
  });
  // DESIGNER-TOP-BAR — appearance has ONE owner and one store, and survives a module switch and a reload.
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme);
  const changeTheme = useCallback((t: ThemeChoice) => { setTheme(t); saveTheme(t); }, []);

  const dismissOverlay = useCallback(() => {
    setTourOverlay(false);
    try { localStorage.setItem(TOUR_SEEN_KEY, "1"); } catch { /* ignore */ }
  }, []);

  // APP-TOUR — A MODULE SWITCH TAKES THE OVERLAY DOWN WITH IT. The tour is `position: fixed; inset: 0`
  // at `z-index: 200`, so a navigation that leaves it standing lands the reader on a screen they cannot
  // see: any tour action that switches module underneath the overlay would read as a dead button.
  // This rule keeps the overlay state in sync with the module.
  //
  // Dismissing here rather than in `ProductTour` keeps ONE rule with one owner: this frame owns the
  // overlay, so every way out of it ends in the same call.
  const switchModule = useCallback((id: TopModuleId) => {
    setDeepLink(null);
    dismissOverlay();
    navigate(pathForModule(id));
  }, [navigate, dismissOverlay]);

  const openExample = useCallback((t: ExampleTarget) => {
    setDeepLink({ lens: t.lens, mode: t.mode });
    navigate("/measures");
    dismissOverlay();
  }, [navigate, dismissOverlay]);

  const toggleTour = useCallback(() => {
    setTourOverlay((v) => !v);
  }, []);

  /** The chrome bundle for a screen — the bar's invariants. */
  const chrome: TopBarChrome = {
    onToggleTour: toggleTour,
    tourOpen: tourOverlay,
    theme,
    onThemeChange: changeTheme,
  };

  // DESIGNER-ONE-MODULE — one module; every URL lands on it.
  const baseScreen = <MeasureWorkspace chrome={chrome} deepLink={deepLink ?? undefined} />;

  return (
    <>
      {baseScreen}
      {tourOverlay && (
        <ProductTour onSwitchModule={switchModule} onOpenExample={openExample} onClose={dismissOverlay} />
      )}
    </>
  );
}
