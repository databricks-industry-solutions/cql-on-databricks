// APP-PANE-TABS — the two side panes' tab strips are ONE control, so they must resolve to ONE set
// of declarations. This is a CASCADE test, not a rendering test: it loads the real stylesheet and asks
// the engine what actually won, which is the only way to catch the failure it exists for.
//
// THE FAILURE IT EXISTS FOR. `.mw-wb-tab` is a bare single class — specificity (0,1,0) — and the global
// `.mw button { color: inherit }` reset is (0,1,1), so the workbench tab's RESTING colour never applies.
// Measured against the shipped bundle, an inactive tab computes to `var(--tink)`, identical to the
// active one, so the two are distinguishable only by the underline; and `:hover` at (0,2,0) is the one
// colour rule that lands, which makes hovering DIM the tab.
// — the declaration is right there and reads correctly — and a screenshot shows a tab strip that looks
// plausible. The design system records this reset as a standing caveat (section 6, "Specificity
// caveat"); this test is that caveat made enforceable for the control it bit.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";

/** The panes' strip containers. A tab is a plain `button` inside one — there is no per-pane tab class,
 *  which is the point: two classes are two things to keep in step.
 *
 *  STILL TWO however many tabs either strip grows (`SPEC-NAV-COMPOSITION` put Composition back in
 *  `.mw-nav-tabs`; `APP-INSPECTOR` took `.mw-wb-tabs` from two tabs to five, then back to three when
 *  five turned out not to fit the pane). A tab is a `<button>`
 *  inside a strip, never a strip of its own, so every case below covers them unchanged. This list is
 *  the one edit point if a genuine third strip is ever added. */
const STRIPS = ["mw-nav-tabs", "mw-wb-tabs"] as const;

beforeAll(() => {
  const style = document.createElement("style");
  style.textContent = readFileSync("src/measures/measures.css", "utf8");
  document.head.appendChild(style);
});

/** A tab inside `strip`, mounted under `.mw` (the workspace scope the reset is qualified to). */
function tab(strip: string, selected: boolean): HTMLButtonElement {
  const mw = document.createElement("div");
  mw.className = "mw";
  const bar = document.createElement("div");
  bar.className = strip;
  const button = document.createElement("button");
  if (selected) button.setAttribute("aria-selected", "true");
  bar.appendChild(button);
  mw.appendChild(bar);
  document.body.appendChild(mw);
  return button;
}

describe("pane tab strip (APP-PANE-TABS)", () => {
  it.each(STRIPS)("%s: the resting colour beats the `.mw button` reset", (strip) => {
    // `inherit` here means the reset won and the strip's own colour was never applied — the exact
    // shape of the bug, in whichever pane reintroduces it.
    const resting = getComputedStyle(tab(strip, false)).color;
    expect(resting).toBe("var(--tink-2)");
    expect(resting).not.toBe("inherit");
  });

  it.each(STRIPS)("%s: an inactive tab is quieter than the active one", (strip) => {
    // They computed to the SAME ink before this, which is what left the underline carrying the whole
    // state on its own.
    expect(getComputedStyle(tab(strip, false)).color)
      .not.toBe(getComputedStyle(tab(strip, true)).color);
  });

  it.each(STRIPS)("%s: selecting never changes the weight (APP-STABLE-LAYOUT)", (strip) => {
    expect(getComputedStyle(tab(strip, true)).fontWeight)
      .toBe(getComputedStyle(tab(strip, false)).fontWeight);
  });

  it.each(STRIPS)("%s: a pane-state control in the strip is NOT styled as a tab", (strip) => {
    // APP-PANE-STATE — expand and collapse ride at the end of the tab strip, so they are `button`s
    // inside it and `.mw {strip} button` at (0,2,1) will claim them: tab ink, tab padding, tab
    // letter-spacing. This is the `.mw-wb-tab` failure of this file's header in a new place, and it is
    // invisible in the source for the same reason — the control's own rule reads correctly.
    const mw = document.createElement("div");
    mw.className = "mw";
    const bar = document.createElement("div");
    bar.className = strip;
    const ctl = document.createElement("button");
    ctl.className = "mw-pane-ctl";
    bar.appendChild(ctl);
    mw.appendChild(bar);
    document.body.appendChild(mw);

    const control = getComputedStyle(ctl);
    const asTab = getComputedStyle(tab(strip, false));
    expect(control.padding).not.toBe(asTab.padding);
    expect(control.color).toBe("var(--tink-3)");
    expect(control.letterSpacing).toBe("normal");
  });

  it("the workbench's control still resolves in the SCOPE ROW it moved to", () => {
    // APP-PANE-STATE — the control sits in `.mw-wb-scope`, not `.mw-wb-tabs`. Its rules are qualified
    // per-container, so a move takes it out
    // of every selector that styled it: `.mw button { color: inherit; background: none; border: 0 }` at
    // (0,1,1) is then the only thing left, and the control renders as an unsized, uncoloured glyph. That
    // is the failure this file exists for, one container along, and it is invisible in the source.
    const mw = document.createElement("div");
    mw.className = "mw";
    const scope = document.createElement("div");
    scope.className = "mw-wb-scope";
    const ctl = document.createElement("button");
    ctl.className = "mw-pane-ctl";
    scope.appendChild(ctl);
    mw.appendChild(scope);
    document.body.appendChild(mw);

    const s = getComputedStyle(ctl);
    expect(s.color).toBe("var(--tink-3)");
    expect(s.color).not.toBe("inherit");
    expect(s.width).toBe("24px");
  });

  it.each(STRIPS)("%s: the strip never gives up its own height (APP-STABLE-LAYOUT)", (strip) => {
    // THE FAILURE THIS EXISTS FOR. `overflow-x: auto` makes the strip a scroll container, which resolves
    // its flex-item `min-height: auto` to ZERO — so in `.mw-workbench`'s column flex box the whole of a
    // long body's overflow was absorbed by the tab strip, which collapses to its own hairline. The tabs
    // were present, selectable by keyboard and 0px tall, and it happened only when the selection's
    // content overran the window, so it read as tabs that "sometimes disappear". jsdom does no layout —
    // this asserts the declaration that makes the shrink impossible.
    const bar = tab(strip, false).parentElement!;
    expect(getComputedStyle(bar).flexShrink).toBe("0");
  });

  it("a pane-state control never grows the tabs' accent underline", () => {
    const css = readFileSync("src/measures/measures.css", "utf8");
    expect(css).toMatch(/\.mw \.mw-nav-tabs \.mw-pane-ctl::after[^}]*content:\s*none/);
  });

  it("is ONE control with three states, not two buttons", () => {
    // APP-PANE-STATE — two glyphs that both mean "resize this pane" make a reader work out which is
    // which, every time, for a control they use without looking. One target that cycles is the idiom.
    const css = readFileSync("src/measures/measures.css", "utf8");
    for (const state of ["expanded", "collapsed"]) {
      expect(css).toContain(`.mw-pane-ctl[data-size="${state}"]`);
    }
    // The glyph rotates rather than changing, so the control stays one object across the cycle.
    expect(css).toMatch(/\.mw-pane-ctl\[data-size="expanded"\] svg \{[^}]*rotate\(180deg\)/);
  });

  it("both panes resolve to the same declarations — one control, one rule set", () => {
    // The regression this guards is the one that prompted it: the two strips had drifted to different
    // weights, resting inks, gaps, paddings and underline radii for a control doing one job.
    const read = (strip: string, selected: boolean) => {
      const s = getComputedStyle(tab(strip, selected));
      return ["color", "fontWeight", "fontSize", "gap", "padding", "letterSpacing"]
        .map((p) => `${p}:${s[p as keyof CSSStyleDeclaration] as string}`).join(" ");
    };
    for (const selected of [false, true]) {
      expect(read("mw-wb-tabs", selected)).toBe(read("mw-nav-tabs", selected));
    }
  });
});

// APP-STABLE-LAYOUT, generalised beyond the tab strip. The rule this file already pins for tabs
// ("selecting never changes the weight") is being broken one control over: `.mw-wb-meter.open > .row
// .lb` sets `font-weight: 600`, so expanding a Preview record makes its label wider and shifts the label
// and everything trailing it — under the pointer, at the moment of the click. Weight is the one property
// that cannot carry a state in this app, because it is the one that reflows; colour and glyphs are paint.
//
// A SOURCE ASSERTION rather than a computed-style one, deliberately: this has to catch the NEXT rule
// someone writes, in a container this file has never heard of, which a per-selector check cannot do.
describe("no expand-state rule changes weight (APP-STABLE-LAYOUT)", () => {
  it("never ties font-weight to an open/expanded state", () => {
    const css = readFileSync("src/measures/measures.css", "utf8");
    const offenders = css
      .split("\n")
      .filter((line) => /\.open|\[open\]|aria-expanded="true"/.test(line))
      .filter((line) => /font-weight/.test(line));
    expect(offenders, `weight must not change on expand:\n${offenders.join("\n")}`).toEqual([]);
  });
});
