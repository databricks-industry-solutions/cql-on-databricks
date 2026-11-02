// SPEC-NAV-COMPOSITION — the left pane's Composition tab.
//
// THE FAILURE THESE EXIST FOR: a navigator that can only show ONE population's decomposition at a time
// cannot answer "where else is this define used?", which is the question the tree exists for. Composition
// draws EVERY population, so the answer is on screen.
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
    // Every population's subtree is in the DOM together, so a reader asking which populations reach a
    // define sees them side by side rather than visiting each one.
    const { container } = renderNavPane({ tab: "composition" });
    const rendered = labels(container);
    const populations = ["Initial Population", "Denominator", "Numerator"];
    for (const p of populations) expect(rendered).toContain(p);
  });

  it("draws the tree by nesting alone: a population folds, and a define below it is read down", () => {
    // The tree is the navigator, so its only controls are the population rows: `CompositionNav` gives one
    // collapsible level and nothing else. A twisty on a define would turn the tree into a set of controls.
    const { container } = renderNavPane({ tab: "composition" });
    const populations = ["Initial Population", "Denominator", "Numerator"];
    const twisties = [...container.querySelectorAll(".mw-twisty")];
    expect(twisties.length).toBeGreaterThan(0);
    for (const twisty of twisties) {
      expect(populations).toContain(twisty.closest(".mw-node-open")?.querySelector(".lab")?.textContent);
    }
    const nested = [...container.querySelectorAll(".mw-nav-nested")];
    expect(nested.some((branch) => branch.querySelector(".mw-node"))).toBe(true);
    for (const branch of nested) {
      expect(branch.querySelector(".mw-twisty"), "a define under a population has no twisty").toBeNull();
    }
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
