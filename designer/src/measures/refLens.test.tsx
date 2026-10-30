// SPEC-REF-LENS — the reference lens's action corner, as a cascade test.
//
// This is the companion to `paneTabs.test.tsx` and exists for the same standing hazard: the global
// `.mw button { color: inherit; background: none; border: 0 }` reset is (0,1,1), so a bare
// `.mw-reflens-acts button` at (0,1,1) loses the ink outright and ties on the fill, winning or losing by
// source order alone. These are ICON-ONLY ghost buttons whose entire state is a fill and an ink, so
// losing either makes the pressed state — the one that reports which read the inspector is scoped to —
// silently invisible. The design system records the reset as a standing caveat (`design-system.md`
// section 6); this file is that caveat made enforceable for the lens.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  const style = document.createElement("style");
  style.textContent = readFileSync("src/measures/measures.css", "utf8");
  document.head.appendChild(style);
});

/** One action button in a lens's header corner, mounted under `.mw` — the workspace scope the reset is
 *  qualified to, and therefore the only scope in which the tie exists. */
function act(pressed: boolean): HTMLButtonElement {
  const mw = document.createElement("div");
  mw.className = "mw";
  const lens = document.createElement("span");
  lens.className = "mw-reflens";
  const head = document.createElement("span");
  head.className = "mw-reflens-head";
  const acts = document.createElement("span");
  acts.className = "mw-reflens-acts";
  const button = document.createElement("button");
  if (pressed) button.setAttribute("aria-pressed", "true");
  acts.appendChild(button);
  head.appendChild(acts);
  lens.appendChild(head);
  mw.appendChild(lens);
  document.body.appendChild(mw);
  return button;
}

describe("reference lens (SPEC-REF-LENS)", () => {
  it("the pressed fill beats the `.mw button` reset", () => {
    // `none` here means the reset won and the pressed state has no mark at all — the `.mw-wb-tab` failure
    // of `paneTabs.test.tsx` in a new control.
    const fill = getComputedStyle(act(true)).background;
    expect(fill).toContain("var(--tacc-w)");
    expect(fill).not.toBe("none");
  });

  it("the resting INK beats the reset's `color: inherit`", () => {
    // This is the assertion that actually bites: `color: inherit` is (0,1,1), so an under-specific rule
    // loses the ink OUTRIGHT rather than losing a tie on source order, and every icon computes to the
    // lens's own text colour — indistinguishable from the pressed one.
    const resting = getComputedStyle(act(false)).color;
    expect(resting).toBe("var(--tink-3)");
    expect(resting).not.toBe("inherit");
  });

  it("stays qualified with `.mw`, so source order is not what is holding it up", () => {
    // The fill rule would TIE with the reset at (0,1,1) unqualified and win only by sitting later in the
    // file — which makes moving a block a silent visual change. Qualified it is (0,2,1) and wins on
    // specificity. Asserted against the source because a passing tie looks identical to a passing win.
    const css = readFileSync("src/measures/measures.css", "utf8");
    expect(css).toContain(".mw .mw-reflens-acts button");
    expect(css).not.toMatch(/^\.mw-reflens-acts button/m);
  });

  it("an unpressed action is a GHOST — no fill until it is the scope or the pointer is on it", () => {
    // The accent is spent on one thing in a popover (design-system.md section 6: the active row), so a
    // resting control must not carry a fill of its own to compete with it.
    expect(getComputedStyle(act(false)).background).not.toContain("var(--tacc-w)");
  });

  it("pressed and unpressed do not resolve to the same ink", () => {
    // Which read the inspector is scoped to is the only thing this toggle reports. If the two states
    // compute alike the control reports nothing, however correct the markup reads.
    expect(getComputedStyle(act(true)).color).not.toBe(getComputedStyle(act(false)).color);
  });

  it("is sized for CHARTS in both dimensions, not for a sentence", () => {
    // It was 190px of 10–12px type — a tooltip's budget. The lens holds the inspector's own three tabs now,
    // and the floors are asserted because they are the reason the layout works: a chart narrower than
    // ~560px of content box cannot carry readable axis labels, and one shorter than ~200px of plot area
    // reports a shape rather than a value. Left to drift these creep back down.
    //
    // WIDTH IS EXACT, HEIGHT IS A FLOOR, and the asymmetry is real rather than an oversight. The box is
    // `position: fixed` and portaled, so it has no parent to derive a width from — without one it would
    // shrink-wrap its content and a tab of short rows would render a narrow box beside a tab of charts.
    const css = readFileSync("src/measures/measures.css", "utf8");
    const rule = /\.mw-reflens \{([^}]*)\}/.exec(css);
    expect(rule).not.toBeNull();
    const width = /[^-]width:\s*(\d+)px/.exec(rule![1]!);
    const minHeight = /min-height:\s*(\d+)px/.exec(rule![1]!);
    expect(Number(width?.[1])).toBeGreaterThanOrEqual(600);
    expect(Number(minHeight?.[1])).toBeGreaterThanOrEqual(340);
  });

  it("grows past its floor, and caps against the VIEWPORT rather than a guess", () => {
    // A FIXED height would have to be re-tuned every time a cut is added, and would clip whatever it was
    // last tuned for. The floor plus a scrolling body is what makes the box stable at its smallest and
    // still correct when it is given more — and the cap has to be the viewport, because three tabs of real
    // distributions can exceed any screen and a popover running off the bottom of the window has a lower
    // half that does not exist.
    const css = readFileSync("src/measures/measures.css", "utf8");
    const rule = /\.mw-reflens \{([^}]*)\}/.exec(css)![1]!;
    expect(rule).not.toMatch(/[^-]height:\s*\d+px/);
    expect(rule).toMatch(/max-height:\s*calc\(100vh/);
    expect(css).toMatch(/\.mw-reflens > \.mw-wb-body \{[^}]*overflow-y:\s*auto/);
  });

  it("is FIXED and portaled, so nothing clips it in either axis", () => {
    // The clamp this replaces (`useRefLens`'s measured negative `left`) existed only because the box lived
    // inside `.mw-doc`, a scrollport that clipped it at the reading column's edge — and it only ever
    // handled the horizontal axis. Holding the inspector's tabs made the vertical case real. `absolute`
    // creeping back here would reinstate the clip silently: the box would look right until a reference near
    // an edge was pressed.
    const css = readFileSync("src/measures/measures.css", "utf8");
    const rule = /\.mw-reflens \{([^}]*)\}/.exec(css)![1]!;
    expect(rule).toMatch(/position:\s*fixed/);
    expect(rule).not.toMatch(/position:\s*absolute/);
    // The host carries `mw`, which is what keeps every `.mw`-qualified rule below reaching inside a box
    // that is no longer a descendant of the workspace.
    expect(css).toMatch(/\.mw-lens-layer \{/);
  });

  it("never reintroduces a hover-opened preview", () => {
    // `pointer-events: none` existed only to let a hover-opened lens be dismissed by moving away. Hover
    // no longer opens it, so the lens is always a real surface — and a stray reset of this property would
    // make every control in it unpressable while looking perfectly correct in the source.
    const css = readFileSync("src/measures/measures.css", "utf8");
    expect(css).not.toMatch(/\.mw-reflens[^-{][^}]*pointer-events:\s*none/);
  });
});
