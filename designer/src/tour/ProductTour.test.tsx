// APP-TOUR / SVC-EXAMPLE-NOTEBOOKS (PROC-TDD) — the tour's one action that leaves the app: the
// published example notebooks live in the DATABRICKS WORKSPACE, not on a screen, and the workspace is a
// different origin from the App, so the link can only be composed once the BFF has said which host.
//
// Both halves are pinned, because they fail in opposite directions. Before the host arrives — and in a
// deployment that has none to give — the card must still say WHERE the notebooks are, or the reader is
// told they exist and given no way to find them. Once it arrives, the href must be the published path
// under the workspace host, not a link back into the App that serves this page.

import { readFileSync } from "node:fs";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ProductTour } from "./ProductTour.tsx";
import { EXAMPLES_PATH, TOUR_FEATURES } from "../measures/model.ts";

const host = vi.hoisted(() => ({ resolve: async () => "https://acme.cloud.databricks.com" }));
vi.mock("../workspace-client.ts", () => ({ fetchWorkspaceHost: () => host.resolve() }));

function renderTour() {
  return render(<ProductTour onSwitchModule={() => {}} onOpenExample={() => {}} onClose={() => {}} />);
}

/** The section whose destination is the workspace, found by the address it falls back to stating. */
function notebooksCard(): HTMLElement {
  return screen.getByText(EXAMPLES_PATH).closest(".mw-tour-feature") as HTMLElement;
}

beforeEach(() => { host.resolve = async () => "https://acme.cloud.databricks.com"; });
afterEach(() => vi.restoreAllMocks());

describe("ProductTour — every pillar draws its own mark (APP-TOUR)", () => {
  it("has a `g-<id>` rule in tour.css for every section in the registry", () => {
    // The art panel drew the SAME diamond seven times while the component built a per-feature class
    // that styled nothing. Now the class is load-bearing, which moves the failure: a section added
    // without its rule renders an empty stage. Asserted against the real stylesheet rather than the
    // rendered output, because jsdom computes no `::before`/`::after` geometry to look at.
    const css = readFileSync("src/tour/tour.css", "utf8");
    const missing = TOUR_FEATURES.filter((f) => !css.includes(`.g-${f.id} span`)).map((f) => f.id);
    expect(missing).toEqual([]);
  });
});

describe("ProductTour — one section per pillar (APP-TOUR)", () => {
  it("gives every section its own id", () => {
    // Two sections once shared `analytics`: a duplicate React key, and the same mark drawn twice.
    const ids = TOUR_FEATURES.map((f) => f.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("lists a pillar's applications under its dek", () => {
    const { container } = renderTour();
    for (const f of TOUR_FEATURES.filter((t) => t.applications)) {
      const card = screen.getByText(f.title).closest(".mw-tour-feature") as HTMLElement;
      expect(Array.from(card.querySelectorAll(".mw-tour-apps li b")).map((b) => b.textContent))
        .toEqual(f.applications!.map((a) => a.title));
    }
    expect(container.querySelectorAll(".mw-tour-apps li").length).toBeGreaterThan(0);
  });
});

describe("ProductTour — a section with nowhere to go carries no action (APP-TOUR)", () => {
  it("renders an action only for the sections that have a destination", async () => {
    // Three of the seven pillars are the product's direction rather than a screen. They must close on
    // their chips: a disabled or dead "coming soon" button is the defect this requirement was already
    // fixed for once. Asserted as an identity against the registry so adding a section cannot drift.
    const { container } = renderTour();
    await screen.findByRole("link", { name: /Open the notebooks/ });
    const withDestination = TOUR_FEATURES.filter((f) => f.example || f.module || f.workspacePath);
    expect(container.querySelectorAll(".mw-tour-feature .mw-tour-actions")).toHaveLength(withDestination.length);
    expect(withDestination.length).toBeLessThan(TOUR_FEATURES.length);
  });
});

describe("ProductTour — the published notebooks (SVC-EXAMPLE-NOTEBOOKS)", () => {
  it("links to the notebooks under the workspace host the BFF reports", async () => {
    renderTour();
    const link = await screen.findByRole("link", { name: /Open the notebooks/ });
    expect(link.getAttribute("href"))
      .toBe(`https://acme.cloud.databricks.com/#workspace${EXAMPLES_PATH}`);
    // It leaves the app, so it must not replace the workspace the reader is in.
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("offers ONE affordance — the button, wearing the same class as every other action", async () => {
    // The section used to print the path as a grey mono line ABOVE the button, so it alone showed two
    // things where every other section shows one, and the muted line read as the weaker of the two.
    // The address rides the button now; the standalone line is the no-host fallback only.
    renderTour();
    const link = await screen.findByRole("link", { name: /Open the notebooks/ });
    expect(link.className).toBe("mw-tour-primary");
    expect(link.getAttribute("title")).toBe(EXAMPLES_PATH);
    expect(screen.queryByText(EXAMPLES_PATH)).toBeNull();
  });

  it("keeps the address and drops the link when no workspace host resolves", async () => {
    // Local dev with no Databricks profile: the BFF answers 503 rather than guessing a host. The card
    // must degrade to the address it already states, never to a broken or invented link.
    vi.spyOn(console, "warn").mockImplementation(() => {});
    host.resolve = async () => { throw new Error("workspace host failed: 503"); };
    renderTour();
    await waitFor(() => expect(console.warn).toHaveBeenCalled());
    expect(notebooksCard()).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Open the notebooks/ })).toBeNull();
  });
});
