// APP-NAV-DEPTH / SPEC-NAV-COMPOSITION — one indent step per level, and the disclosure slot is never
// paid for out of it. This is a CASCADE test, not a rendering test: it loads the real stylesheet and asks
// the engine which declaration won, because the defect it exists for is invisible in a source read and
// looks plausible in a screenshot (the same reasoning as `paneTabs.test.tsx`).
//
// THE FAILURE IT EXISTS FOR. In the Composition tree a population is a `.mw-node` and so are the defines
// beneath it, so the indent is the ONLY thing that can separate them. The collapsible row spent that
// indent on its own twisty, which is an unsized `<button>` and so takes the UA's own padding (24px wide).
// Measured in Chrome against the shipped stylesheet: the population's label column lands at x=47 and its
// own children's at x=24 — 23px to its LEFT. The hierarchy does not read as flat, it reads INVERTED, which
// is the exact failure `APP-NAV-DEPTH` names. jsdom does not resolve `var()`/`calc()` into pixels, so
// the arithmetic is not what is asserted here; what is asserted is that the slot is DECLARED rather than
// inherited from the UA, and that a collapsible row's children clear it.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";

const CSS = readFileSync("src/measures/measures.css", "utf8");

beforeAll(() => {
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
});

/** The nav's two shapes of level: one hanging under a collapsible ROW, one under a section heading. */
function nav() {
  document.body.innerHTML = `
    <div class="mw"><div class="mw-dnav"><div class="mw-nav-sections">
      <div class="mw-node mw-node-open" id="parent">
        <button class="mw-twisty" id="twisty"></button><button class="sel"></button>
      </div>
      <div class="mw-nav-nested" id="under-row">
        <div class="mw-node" id="child"></div>
        <div class="mw-nav-nested" id="under-child"><div class="mw-node"></div></div>
      </div>
      <button class="mw-dag-group mw-collapsible"></button>
      <div class="mw-nav-nested" id="under-heading"><div class="mw-node" id="plain"></div></div>
    </div></div></div>`;
  return (id: string) => getComputedStyle(document.getElementById(id)!);
}

const step = (s: CSSStyleDeclaration) => s.getPropertyValue("--nav-step");

describe("nav depth (APP-NAV-DEPTH)", () => {
  it("gives the twisty a DECLARED slot, not the UA button's padding", () => {
    // `auto` / "" here means the rule went away and the slot is whatever the UA makes of a `<button>`
    // wrapping a 12px glyph — which is where the 13px inversion comes from.
    const s = nav()("twisty");
    expect(s.width).toBe("var(--nav-twisty)");
    expect(s.padding).toBe("0px");
  });

  it("a collapsible row's children clear the slot AS WELL AS the step", () => {
    // The regression, stated as a comparison: children of a row must be a step deeper than children of a
    // heading at the same level, because the row has a slot in front of its label and a heading does not.
    const g = nav();
    expect(step(g("under-row"))).not.toBe(step(g("under-heading")));
    expect(step(g("under-row"))).toBe("calc(var(--nav-indent) + var(--nav-twisty))");
  });

  it("leaves ordinary depth alone — a level under a heading, or under a leaf row, is one step", () => {
    // The Outline's levels are the majority of the pane and are correct; the slot rule must not
    // reach them. `under-child` also covers the tree below a population, where no row carries a twisty.
    const g = nav();
    expect(step(g("under-heading"))).toBe("var(--nav-indent)");
    expect(step(g("under-child"))).toBe("var(--nav-indent)");
  });

  it("hangs the guide rail off the level's own step, so it cannot be left behind", () => {
    // The rail is a hardcoded 11px — right for a 16px step and silently wrong for any other, which is
    // how a deeper level would get a rail 21px adrift of the rows it brackets.
    // Read from the source, not from the engine: jsdom does not compute pseudo-elements at all.
    const rail = /\.mw-nav-nested::before\s*\{([^}]*)\}/.exec(CSS);
    expect(rail).not.toBeNull();
    expect(rail![1]).toContain("left: calc(var(--nav-step) - 5px)");
  });

  it("shares a left edge between a collapsible row and a plain one", () => {
    // Two kinds of row at one level are still one level: the slot sits INSIDE the row's padding, so the
    // rows' left edges agree and only the label column differs.
    const g = nav();
    expect(g("parent").paddingLeft).toBe(g("plain").paddingLeft);
    // …and the slot alone separates twisty from label, so the row's own gap may not add to it.
    expect(g("parent").gap).toBe("0px");
  });
});

describe("pane size (APP-PANE-STATE)", () => {
  it("declares the size overrides AFTER the responsive queries", () => {
    // SOURCE ORDER IS THE WHOLE MECHANISM. `[data-nav="collapsed"]` and the media queries' `.mw-body`
    // are both (0,2,0), so nothing but order decides which wins — and an explicit choice by the reader
    // has to beat the viewport default. Read from the source because jsdom does not evaluate media
    // queries at all, which would make a computed-style assertion here quietly meaningless.
    const lastQuery = CSS.lastIndexOf("@media (max-width: 1500px) { .mw-body");
    const firstOverride = CSS.indexOf('.mw-body[data-nav="collapsed"]');
    expect(lastQuery).toBeGreaterThan(-1);
    expect(firstOverride).toBeGreaterThan(lastQuery);
  });

  it("leaves `normal` writing no rule, so it falls back to the viewport", () => {
    // A `[data-nav="normal"]` rule would pin one width at every breakpoint and quietly retire the
    // responsive clamp the pane has had all along.
    expect(CSS).not.toContain('[data-nav="normal"]');
    expect(CSS).not.toContain('[data-wb="normal"]');
  });
});
