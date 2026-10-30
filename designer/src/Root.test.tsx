// APP-TOUR / APP-URL-ROUTING (PROC-TDD) — the app frame's navigation invariants: switching module
// moves the app AND takes the first-run tour overlay down with it.
//
// The overlay is `position: fixed; inset: 0` at `z-index: 200`, which makes "did we navigate?" and "can
// the reader see it?" two different questions — and only the first was ever asserted. The tour's own
// "Take me there →" (the features whose destination is a MODULE rather than an example) switched module
// underneath the overlay, so the button read as dead: the URL and the breadcrumb both said Data
// Readiness while the tour was still the only thing on screen. Both halves are pinned here.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Root } from "./Root.tsx";
import { MW_MODULES, TOUR_FEATURES } from "./measures/model.ts";

// The frame fetches the catalog on mount; the navigation under test does not depend on it, and a real
// fetch in jsdom is an unhandled rejection rather than a test.
vi.mock("./library-client.ts", () => ({
  fetchDefinitions: async () => [],
  fetchLibrarySource: async () => "",
}));

// Same reasoning for the tour's workspace read (APP-TOUR): the overlay asks the BFF which workspace
// to link its notebooks into, and the navigation under test does not depend on the answer.
vi.mock("./workspace-client.ts", () => ({
  fetchWorkspaceHost: async () => "https://acme.cloud.databricks.com",
}));

const TOUR_SEEN_KEY = "dha.tourSeen";

// This jsdom setup ships `localStorage` as a bare `{}` with no Storage API (see `model.test.tsx`), so
// every read in `Root` throws and is swallowed — which means the first-run branch can never be exercised
// without a store. A real Map, not a mock: whether the tour is shown IS the thing under test.
let store: Map<string, string>;
beforeEach(() => {
  store = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, v); },
    removeItem: (k: string) => { store.delete(k); },
  });
});
afterEach(() => vi.unstubAllGlobals());

function renderApp(path = "/measures") {
  return render(<MemoryRouter initialEntries={[path]}><Root /></MemoryRouter>);
}

/** Whether the one module's workspace is on screen: its bar, with no module label. */
function onWorkspace(container: HTMLElement): boolean {
  return container.querySelectorAll(".mw-bar").length === 1;
}

describe("Root — one module (DESIGNER-ONE-MODULE)", () => {
  beforeEach(() => { store.set(TOUR_SEEN_KEY, "1"); });

  it("has exactly one module, with no module label, no switcher and no Settings", () => {
    expect(MW_MODULES.map((m) => m.id)).toEqual(["measures"]);
    const { container } = renderApp();
    expect(onWorkspace(container)).toBe(true);
    expect(container.querySelector(".mw-crumb-btn")).toBeNull();
    expect(screen.queryByTitle("Switch module")).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.queryByRole("button", { name: "Settings" })).toBeNull();
  });

  it.each(["/scheduling", "/readiness", "/sharing", "/mapping", "/settings"])(
    "lands a removed screen's old URL %s on the one module", (path) => {
      const { container } = renderApp(path);
      expect(onWorkspace(container)).toBe(true);
    },
  );
});

describe("Root — the tour overlay (APP-TOUR)", () => {
  beforeEach(() => { store.delete(TOUR_SEEN_KEY); });

  it("shows the tour on first run", () => {
    renderApp();
    expect(screen.getByRole("dialog", { name: "Product tour" })).toBeTruthy();
  });

  it("tour features do not point at modules (DESIGNER-ONE-MODULE)", () => {
    expect(TOUR_FEATURES.filter((f) => f.module)).toEqual([]);
  });

  it("dismisses the overlay when a feature opens the example", () => {
    // The path that always worked — pinned so the two ways out of the tour cannot drift apart again.
    renderApp();
    const tour = screen.getByRole("dialog", { name: "Product tour" });
    fireEvent.click(within(tour).getAllByRole("button", { name: /Show me/ })[0]!);
    expect(screen.queryByRole("dialog", { name: "Product tour" })).toBeNull();
  });

  it("marks the tour seen, so a dismissal survives a remount", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Close tour" }));
    expect(store.get(TOUR_SEEN_KEY)).toBe("1");
  });
});
