// SPEC-NAV-COMPOSITION — the left pane's Composition tab.
//
// THE FAILURE THESE EXIST FOR is the one that retired Flow: a navigator that can only show you ONE
// population's decomposition at a time cannot answer "where else is this define used?", which is the
// question the tree exists for. Composition draws EVERY population, so the answer is on screen.
//
// They also pin the invariant the two arrangements share and that `navRows.tsx` exists to protect: the
// rows ARE the Outline's rows, so a define looks and selects the same in both. That held for free while
// both were closures in one component; across two files it lasts until the first divergent edit.

import { fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderNavPane } from "./__fixtures__/navHarness.tsx";

/** The nav's rows, by their visible label, in render order. */
function labels(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".mw-node .lab")].map((el) => el.textContent ?? "");
}

describe("the Composition tab (SPEC-NAV-COMPOSITION)", () => {
  it("offers Outline and Composition, and no Flow", () => {
    const { container } = renderNavPane();
    const tabs = [...container.querySelectorAll('[role="tab"]')].map((t) => t.textContent);
    expect(tabs).toEqual(["Outline", "Composition"]);
  });

  it("draws EVERY population's tree at once, not one rooted branch", () => {
    // The whole reason the tab came back. Under Flow exactly one population's subtree was in the DOM
    // and the rest were behind a spine click; a reader asking which populations reach a define had to
    // visit each one and remember what they saw.
    const { container } = renderNavPane({ tab: "composition" });
    const rendered = labels(container);
    const populations = ["Initial Population", "Denominator", "Numerator"];
    for (const p of populations) expect(rendered).toContain(p);
  });

  it("renders no spine — the tree is the navigator", () => {
    // Geometry in a 256px gutter, competing with the tree it was supposed to steer.
    const { container } = renderNavPane({ tab: "composition" });
    expect(container.querySelector(".mw-spine-wrap")).toBeNull();
  });

  it("selects a define by NAME, the same token the Outline selects", () => {
    // APP-SPEC-MODEL — one row kit, so a row in either arrangement reports the same selection. A
    // Composition row that selected by path would scope the inspector to something the page cannot
    // scroll to.
    const { container, picked } = renderNavPane({ tab: "composition" });
    const row = [...container.querySelectorAll<HTMLElement>(".mw-node")]
      .find((el) => el.querySelector(".lab")?.textContent === "Numerator");
    expect(row).toBeTruthy();
    // A population is BOTH a section of the tree and a define, so it is the collapsible form: the
    // twisty expands and `.sel` selects. Two targets, each meaning fixed by where it lands — the
    // tree-view convention `navRows.tsx` records.
    const sel = row!.querySelector<HTMLButtonElement>("button.sel");
    expect(sel).toBeTruthy();
    fireEvent.click(sel!);
    expect(picked).toContain("Numerator");
  });
});
