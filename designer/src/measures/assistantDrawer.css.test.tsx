// APP-ASSISTANT — a CASCADE test over the drawer's load-bearing declarations.
//
// Not a rendering test. jsdom does no layout, so nothing here asks how tall the drawer is; it asks the
// engine what DECLARATIONS won, which is the only thing that can be checked without a browser and is
// exactly what the design system asks for after `.mw-wb-tab` shipped with a resting colour that never
// applied (section 6, "Specificity caveat"; `paneTabs.test.tsx`).
//
// Two properties are worth this. **The overlay contract** (`APP-STABLE-LAYOUT`): the drawer shares the
// reading column's grid cell, so opening it cannot resize the document. Written as a fourth grid track it
// would look identical in the source, pass every rendering test, and reflow the prose under the reader —
// which is the one thing the placement decision in `agent/README.md` turns on. **And the
// hueless provenance mark** (`SVC-ANSWER-PROVENANCE` / `APP-NARRATIVE-PROVENANCE`): whose number a
// figure is, is not a STATE of the measure, so the notice may not borrow good/info/warn/crit.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  const style = document.createElement("style");
  style.textContent = readFileSync("src/measures/measures.css", "utf8");
  document.head.appendChild(style);
});

/** Mount `className` inside the workspace scope the resets are qualified to, and return it. */
function mount(className: string, tag = "div"): HTMLElement {
  const mw = document.createElement("div");
  mw.className = "mw";
  const el = document.createElement(tag);
  el.className = className;
  mw.appendChild(el);
  document.body.appendChild(mw);
  return el;
}

describe("the drawer overlays the reading column (APP-STABLE-LAYOUT)", () => {
  it("is OUT OF FLOW, so it cannot disturb the panes' grid placement", () => {
    // THE BUG THIS EXISTS FOR, and it shipped in the first cut: written as `grid-column: 2`, the
    // drawer became an explicitly-placed grid item, and auto-placement flows AROUND one — so the three
    // panes, none of which declares a column, each shifted one track. The document rendered where the
    // inspector goes and the inspector wrapped to a second row. Every computed-style assertion still
    // passed, because jsdom does no layout; only a screenshot of the real stylesheet caught it.
    const style = getComputedStyle(mount("mw-ask", "section"));
    expect(style.position).toBe("absolute");
    expect(style.gridColumn).not.toBe("2");
  });

  it("is inset by the pane tracks' OWN properties, so it follows every pane state", () => {
    // `--nav-w` / `--wb-w` are the values the grid itself is built from, and the collapsed/expanded
    // overrides rewrite them. Reading them here means there is no second declaration of where the
    // reading column starts — a hardcoded inset would be correct until the first pane was collapsed.
    const style = getComputedStyle(mount("mw-ask", "section"));
    expect(style.left).toContain("--nav-w");
    expect(style.right).toContain("--wb-w");
    expect(style.bottom).toBe("0px");
  });

  it("hangs off a positioned .mw-body rather than the viewport", () => {
    // Without this the inset would resolve against the initial containing block and the drawer would
    // sit over the whole window, inspector included.
    expect(getComputedStyle(mount("mw-body")).position).toBe("relative");
  });

  it("is about a third of the column's height", () => {
    // Section 1.1: "opening at about a third of the height". A percentage rather than a pixel figure,
    // so the proportion holds on a laptop and on a large screen.
    expect(getComputedStyle(mount("mw-ask", "section")).height).toBe("34%");
  });

  it("stacks above the document it covers", () => {
    expect(Number(getComputedStyle(mount("mw-ask", "section")).zIndex)).toBeGreaterThan(0);
  });
});

describe("the transcript scrolls, the input does not", () => {
  it("puts the scroll on the body so the input stays where the reader left it", () => {
    // A form that scrolled away with the transcript would move after every answer, which is the same
    // `APP-STABLE-LAYOUT` failure the pane tab strip hit when it started giving up height.
    expect(getComputedStyle(mount("mw-ask-body")).overflowY).toBe("auto");
    // `flex: none` computes to the `0 0 auto` longhand — grow 0 is the load-bearing half: chrome that
    // can shrink is chrome that vanishes when the body outgrows the pane, which is exactly how the
    // workbench's tab strip collapsed to a hairline.
    expect(getComputedStyle(mount("mw-ask-form", "form")).flex).toBe("0 0 auto");
    expect(getComputedStyle(mount("mw-ask-head", "header")).flex).toBe("0 0 auto");
  });
});

describe("provenance is hueless (SVC-ANSWER-PROVENANCE)", () => {
  it("takes the EXPLAIN wash, not a semantic state colour", () => {
    // `--texpl-w` is the one wash in the system with no hue, and it is the right one for the same
    // reason narration uses it: painting this `warn` would say the MEASURE is in a cautionary state,
    // which is a different and false claim. The figure is legitimate; it is simply not the measure's.
    const background = getComputedStyle(mount("mw-ask-derived", "p")).background;
    expect(background).toContain("--texpl-w");
    for (const state of ["--twarn", "--tfail", "--tgood", "--tinfo", "--tacc"]) {
      expect(background).not.toContain(state);
    }
  });

  it("distinguishes the two source kinds by INK, for the same reason", () => {
    const derived = document.createElement("li");
    derived.className = "mw-ask-src";
    derived.setAttribute("data-provenance", "derived");
    const mark = document.createElement("span");
    mark.className = "pv";
    derived.appendChild(mark);
    const mw = document.createElement("div");
    mw.className = "mw";
    mw.appendChild(derived);
    document.body.appendChild(mw);
    expect(getComputedStyle(mark).color).toContain("--tink-2");
  });
});

describe("the scope chip does not spend the accent (design-system section 2)", () => {
  it("rests on the raised context-control material instead", () => {
    // Accent is reserved for selection and the primary CTA. The scope is a DEFAULT, not a selection —
    // which is the whole distinction `APP-ASSISTANT-CONTEXT` draws — so an accent chip would say the
    // reader had chosen it when the workspace supplied it.
    const style = getComputedStyle(mount("mw-ask-scope", "button"));
    expect(style.background).toContain("--panel-2");
    expect(style.background).not.toContain("--tacc");
  });

  it("is qualified past the .mw button reset so its fill and border actually paint", () => {
    // The standing caveat, and the one that has now bitten four surfaces: `.mw button` carries
    // `background: none; border: 0` at (0,1,1), so a bare single-class button loses both.
    const css = readFileSync("src/measures/measures.css", "utf8");
    expect(css).toContain(".mw .mw-ask-scope");
  });
});
