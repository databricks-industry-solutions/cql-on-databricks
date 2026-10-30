// APP-STYLE-GUIDE / SPEC-DEFINE-SECTIONS — a CASCADE test over the document's heading rules.
//
// Not a rendering test: jsdom does no layout, so nothing here asks where a chip sits. It asks the engine
// which DECLARATIONS won, which is the check the design system asks for after `.mw-wb-tab` shipped with a
// resting colour that never applied (design-system.md section 6, "Specificity caveat"), and it is the
// only check available without a browser.
//
// WHAT IT GUARDS. `.mw-spec h2` / `.mw-spec h3` style the document's own headings, and they are a class
// plus a type (0,1,1) — so they outrank any component rule written as a bare class (0,1,0). A component
// whose heading happens to be an `h2` or `h3` therefore loses its own declarations silently: nothing
// fails, and the dead declaration still reads as if it were in force. That is how every `DEF` / `FN` kind
// chip came to float ~6px above the name it labels — `.mw-spec h3`'s `margin: 16px 0 4px` beat
// `.mw-define-title { margin: 0 }`, the 24px title got a 44px outer box, and the chip, which is centred
// on the flex LINE, centred on the phantom.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  const style = document.createElement("style");
  style.textContent = readFileSync("src/measures/measures.css", "utf8");
  document.head.appendChild(style);
});

/** Mount an element inside the document scope the heading rules are qualified to. */
function inSpec(tag: string, className?: string): HTMLElement {
  const mw = document.createElement("div");
  mw.className = "mw";
  const spec = document.createElement("article");
  spec.className = "mw-spec";
  const el = document.createElement(tag);
  if (className) el.className = className;
  spec.appendChild(el);
  mw.appendChild(spec);
  document.body.appendChild(mw);
  return el;
}

describe("a component's heading beats the document's heading rule (APP-STYLE-GUIDE)", () => {
  it("gives a define's title NO margin, so its row is as tall as the title", () => {
    // The row is `display: flex; align-items: baseline` and the kind chip is `align-self: center`, so
    // the chip is placed against the flex LINE. A margin on the title inflates that line without
    // drawing anything, and the chip goes with it — the pill floats above the name it labels.
    const style = getComputedStyle(inSpec("h3", "mw-define-title"));
    expect(style.marginTop).toBe("0px");
    expect(style.marginBottom).toBe("0px");
  });

  it("gives every NAME in the document one and the same type (APP-STYLE-GUIDE)", () => {
    // The rule the 2026-08-10 change exists to hold: everything that NAMES something is one style —
    // serif `title`, 21px, 600 — and depth is carried by containment and by the eyebrow instead. These
    // three used to render at three different sizes, which made a reader rank them by comparing them, and
    // they did not agree: a define's name matched the `Condition` heading that is not its parent.
    const specimens = {
      half: getComputedStyle(inSpec("h2")),                       // Description / Value sets
      group: getComputedStyle(inSpec("h3")),                      // Condition / Encounter
      entry: getComputedStyle(inSpec("h3", "mw-define-title")),    // a define's name
    };
    for (const [what, style] of Object.entries(specimens)) {
      expect(style.fontFamily, what).toBe("var(--f-serif-display)");
      // 19px is a step ADDED for this rule (see design-system.md, type scale). Pinned because a name is
      // the most repeated type on the page and an off-scale drift here is invisible one specimen at a time.
      expect(style.fontSize, what).toBe("19px");
      expect(style.fontWeight, what).toBe("600");
      expect(style.letterSpacing, what).toBe("-0.01em");
    }
    // A define declares NO type of its own — it takes the document's. Its one override is the margin
    // (asserted above), which is the only thing the shared rule gets wrong for a flex row.
    expect(specimens.entry.marginTop).toBe("0px");
  });

  it("keeps the CARD TITLE out of that set, in a register of its own (APP-STYLE-GUIDE)", () => {
    // It was in the set, and could not stay once a resource type became a heading: the card title
    // "Data requirements" and the "Condition" inside it rendered identically — this file's own collision,
    // one level up. Sans + uppercase ranks it by REGISTER without competing on size.
    const title = getComputedStyle(inSpec("span", "mw-spec-sec-title"));
    expect(title.fontFamily).toBe("var(--f-ui)");
    expect(title.fontSize).toBe("13px");
    expect(title.fontWeight).toBe("600");
    expect(title.textTransform).toBe("uppercase");
    expect(title.letterSpacing).toBe("0.1em");
    // PRIMARY ink, not `--tink-2`. At caption contrast the card title was quieter than the lava eyebrow
    // inside it, so a subdivision out-ranked its own card — and an uppercase 13px label has little mass to
    // spend, so ink is most of what it has. It is also the section's collapse control.
    expect(title.color).toBe("var(--tink)");
    // Uppercase without tracking is the one thing section 1 forbids outright, so the two are asserted
    // together: whichever is dropped, this fails.
    expect(title.fontFamily).not.toBe(getComputedStyle(inSpec("h2")).fontFamily);
  });

  it("gives EVERY kind chip one weight, whether or not it is inside a heading (APP-STYLE-GUIDE)", () => {
    // A population's chip renders INSIDE its `h2`; a define's is a SIBLING of its `h3`. With the chip's
    // weight unpinned, that structural accident made `pop` 600 and `def` 400 — and made the document's
    // `pop` disagree with the nav's, which shares the mapping on purpose. It was a synthesized bold too:
    // DM Mono has no face above 500.
    const spec = document.createElement("article");
    spec.className = "mw-spec";
    const h2 = document.createElement("h2");
    const inside = document.createElement("span");
    inside.className = "mw-spec-kind pop";
    h2.appendChild(inside);
    spec.appendChild(h2);
    const beside = document.createElement("span");
    beside.className = "mw-spec-kind def";
    spec.appendChild(beside);
    const mw = document.createElement("div");
    mw.className = "mw";
    mw.appendChild(spec);
    document.body.appendChild(mw);
    expect(getComputedStyle(inside).fontWeight).toBe("400");
    expect(getComputedStyle(beside).fontWeight).toBe("400");
    // And the nav's chip, which the mapping is deliberately shared with. jsdom does not resolve
    // INHERITED values, so an undeclared weight reports `normal` rather than the 400 it renders at —
    // the assertion available is that the nav has not pinned itself to something heavier, which is the
    // half that could actually regress.
    const node = document.createElement("div");
    node.className = "mw-node";
    const navChip = document.createElement("span");
    navChip.className = "k pop";
    node.appendChild(navChip);
    mw.appendChild(node);
    expect(["normal", "400"]).toContain(getComputedStyle(navChip).fontWeight);
  });

  it("keeps the chip centred on that line, which is the other half of the pair", () => {
    // Stated here because the two rules are one behaviour: centring is only correct while the line is
    // the title's own height. If the chip ever goes back to `baseline` the margin above stops mattering
    // and this test should be the thing that says so.
    const mw = document.createElement("div");
    mw.className = "mw";
    const chip = document.createElement("span");
    chip.className = "mw-spec-kind def";
    mw.appendChild(chip);
    document.body.appendChild(mw);
    expect(getComputedStyle(chip).alignSelf).toBe("center");
  });

  it("ranks a SECTION by containment, since its type no longer distinguishes it (APP-STYLE-GUIDE)", () => {
    // With one heading style, the card is the ONLY thing left saying that a section is a section — so
    // these declarations are load-bearing. The card is on `.mw-spec-sec-body`, with the section's name
    // on the ground above it. The section element carries only the token and spacing — asserted here,
    // because a stray fill would put paper behind the ground eyebrow.
    // jsdom does not substitute custom properties, so the assertions read the token.
    const body = getComputedStyle(inSpec("div", "mw-spec-sec-body"));
    expect(body.backgroundColor).toBe("var(--panel)");
    expect(body.borderRadius).toBe("10px");
    // An unset background computes transparent, which is the assertion available in jsdom.
    const section = getComputedStyle(inSpec("section", "mw-spec-sec"));
    expect(section.backgroundColor).toBe("rgba(0, 0, 0, 0)");
    expect(section.boxShadow).toBe("");
    // And the ground eyebrow carries no rule of its own: the card edge 8px below it is the boundary, and
    // two boundaries for one division is what the bled header used to be.
    // `0px` rather than unset: `.mw button` resets borders, so the assertion available is that nothing has
    // put one back. That reset is also why the rule has to be REMOVED rather than merely not declared —
    // the bled `border-bottom` this head used to carry was its own declaration.
    const head = getComputedStyle(inSpec("button", "mw-spec-sec-head"));
    expect(head.borderBottomWidth).toBe("0px");
    expect(head.paddingLeft).toContain("--spec-pad-x");
  });

  it("gives a library card's title the CARD-TITLE role in the mono register (APP-STYLE-GUIDE)", () => {
    // A library card's title names a card, exactly as a section's ground eyebrow does, so it takes the same
    // step and the same primary ink. Two departures, both forced by what the string IS — and both are the
    // kind of thing that gets "tidied" into consistency by someone who has not read why.
    const lib = getComputedStyle(inSpec("span", "mw-defs-lib-title"));
    const sec = getComputedStyle(inSpec("span", "mw-spec-sec-title"));
    expect(lib.fontSize).toBe(sec.fontSize);
    expect(lib.color).toBe("var(--tink)");
    // 1. MONO, SENTENCE CASE. A library name is an identifier; uppercasing
    //    `BloodPressureControlScreeningDemo` gives a 33-character all-caps smear. Section names are words
    //    and can be uppercased; this cannot.
    expect(lib.fontFamily).toBe("var(--f-mono)");
    expect(lib.textTransform).not.toBe("uppercase");
    expect(sec.textTransform).toBe("uppercase");
    // 2. WEIGHT 500, the DM Mono ceiling — 600 would be a synthesized bold at the card-title step.
    expect(lib.fontWeight).toBe("500");
  });

  it("keeps the in-card EYEBROW subordinate, and it alone carries the bar (APP-STYLE-GUIDE)", () => {
    // `.mw-spec-mark` and the library title shared a declaration for one afternoon, on the argument that a
    // library and `Retrieves` are the same thing. Making a library a CARD retired that: a library is a
    // section with a header, while `Retrieves` divides the card around it and has no edge of its own.
    const mark = getComputedStyle(inSpec("div", "mw-spec-mark"));
    expect(mark.color).toBe("var(--tink-2)");
    expect(mark.fontSize).toBe("11px");
    expect(mark.marginRight).toContain("--spec-pad-x");
    // Not a heading — that is the point.
    expect(mark.fontSize).not.toBe(getComputedStyle(inSpec("h2")).fontSize);
  });

  it("draws the subdivision with a TRAILING RULE to the card's edge (APP-STYLE-GUIDE)", () => {
    // The label NAMES the division; the hairline DRAWS it. Both halves are load-bearing and neither is
    // visible in a class name, so they are pinned here. The negative margin is what puts the rule's end on
    // the card's edge — it cancels `--spec-pad-x` BY NAME, so a re-padded card cannot leave it short.
    const style = getComputedStyle(inSpec("div", "mw-spec-mark"));
    expect(style.marginRight).toContain("--spec-pad-x");
    // A graphic centres on the label's box; `baseline` would hang it under the text.
    expect(style.alignItems).toBe("center");
    // NO FILL, on either. A fill out-ranks the card that contains it.
    expect(getComputedStyle(inSpec("div", "mw-spec-mark")).backgroundColor).toBe("rgba(0, 0, 0, 0)");
    // 3px, not a hairline: at 1px the rule was the same object as an ordinary border and read as one. It
    // matches the change bar in the gutter, the document's other 3px mark. jsdom cannot compute a
    // pseudo-element, so the declaration is read from the sheet — the only check available here.
    const sheet = readFileSync("src/measures/measures.css", "utf8");
    const trailing = /\.mw-spec-mark::after \{[^}]*\}/.exec(sheet)?.[0] ?? "";
    expect(trailing).toContain("height: 3px");
    // `--tline`, the RULE weight: with the label's accent withdrawn the bar is the whole of what makes a
    // subdivision findable, so it may not sit at inner-divider weight.
    expect(trailing).toContain("var(--tline)");
    expect(trailing).toContain("flex: 1 1 auto");
  });

  it("keeps the library title OUT of the heading ladder, and off the accent (APP-STYLE-GUIDE)", () => {
    // It renders as a `span` rather than an `h2` — asserted in `MeasureNarrative.test.tsx`, since the
    // element is the component's decision — so a library is not a rung a reader has to rank against the
    // criterion names below it.
    // NOT THE ACCENT. What makes a library findable is that it IS a card; the title only has to name it.
    const label = getComputedStyle(inSpec("span", "mw-defs-lib-title"));
    expect(label.color).not.toBe("var(--tacc)");
    expect(label.fontSize).not.toBe(getComputedStyle(inSpec("h2")).fontSize);
  });

  it("leaves the DOCUMENT unpapered, so the card is the outermost mark", () => {
    // `.mw-spec` was the single sheet every section sat on. With sections as cards it is a page: the
    // measure title, its eyebrow and the meta row sit on the ground above the stack, because the title is
    // the page rather than a section of it.
    // An unset background computes transparent, which is the assertion available in jsdom.
    const style = getComputedStyle(inSpec("article", "mw-spec"));
    expect(style.backgroundColor).toBe("rgba(0, 0, 0, 0)");
    expect(style.boxShadow).toBe("");
  });

  it("does NOT let section spacing reach a define row", () => {
    // THE 26px BUG, and it is the third instance of one trap. Every define is a `<section>`, so
    // `.mw-spec section { margin-bottom: 26px }` (0,1,1) beat `.mw-define`'s own margin (0,1,0) and gave
    // every row in the document a 26px bottom margin: measured, a shut define cost 67px — a 41px row plus
    // a margin nobody had written for it. Section spacing belongs to the card, which is the thing that
    // actually is a section.
    const row = document.createElement("section");
    row.className = "mw-define collapsed";
    inSpec("div", "mw-defs-lib").appendChild(row);
    expect(getComputedStyle(row).marginBottom).toBe("0px");
  });

  it("keeps the library head's target from shrinking with its type", () => {
    const mw = document.createElement("div");
    mw.className = "mw";
    const head = document.createElement("div");
    head.className = "mw-defs-lib-head";
    mw.appendChild(head);
    document.body.appendChild(mw);
    expect(getComputedStyle(head).minHeight).toBe("24px");
    // NOT sticky, and no fill: both were tried and withdrawn. A filled full-bleed band made the boundary
    // unmissable and made L2 out-rank L1; sticky is the one thing a fill buys that a label cannot.
    expect(getComputedStyle(head).position).toBe("relative");
    expect(getComputedStyle(head).backgroundColor).toBe("rgba(0, 0, 0, 0)");
  });

  it("keeps every collapse control's text on the content edge", () => {
    // TWO rules make this true and both are easy to lose. The caret is OUT OF FLOW, so it cannot push the
    // text off the edge (in flow it cost its own width plus the row gap, and the pull that compensated was
    // one pixel out for years) — and `padding: 0`, because `.mw button` resets colour, background and
    // border but NOT the UA's `padding: 1px 6px`, which held every one of these labels 6px inside its own
    // box. That 6px is invisible until something has to line up with it.
    expect(getComputedStyle(inSpec("svg", "mw-spec-caret")).position).toBe("absolute");
    // The offset is `calc(--spec-pad-x - 16px)`: out to the card's edge, then back into the gutter.
    expect(getComputedStyle(inSpec("svg", "mw-spec-caret")).left).toContain("16px");
    // The section head is exempt from the `padding: 0` rule BECAUSE it declares its own (the card header's
    // `13px var(--spec-pad-x)`), which overrides the UA value the same way. What must not happen is a bare
    // button inheriting `1px 6px`, so the two that carry no padding of their own are the ones pinned.
    expect(getComputedStyle(inSpec("button", "mw-defs-lib-toggle")).paddingLeft).toBe("0px");
    expect(getComputedStyle(inSpec("button", "mw-define-toggle")).position).toBe("absolute");
  });

  it("puts the kind chip on the right rail, in both kinds of row", () => {
    // SPEC-DEFINE-SECTIONS — leading, the chip sat between the caret and the name, so the name started
    // 45px inside the content edge AND stepped 6px sideways by kind (`pop`/`def` chips are wider than
    // `fn`/`rd`). Populations render through their own `h2`, so the rule has to reach both or one list
    // ends up with two chip columns.
    const inRow = (parent: string, tag: string, cls: string) => {
      const mw = document.createElement("div");
      mw.className = "mw";
      const spec = document.createElement("article");
      spec.className = "mw-spec";
      const p = document.createElement("div");
      p.className = parent;
      const el = document.createElement(tag);
      el.className = cls;
      p.appendChild(el); spec.appendChild(p); mw.appendChild(spec); document.body.appendChild(mw);
      return el;
    };
    const chip = getComputedStyle(inRow("mw-define-head", "span", "mw-spec-kind def"));
    expect(chip.marginLeft).toBe("auto");
    expect(chip.order).toBe("4");
    const popHead = document.createElement("h2");
    const popChip = document.createElement("span");
    popChip.className = "mw-spec-kind pop";
    popHead.appendChild(popChip);
    inRow("mw-spec-cell mw-spec-pop", "div", "holder").parentElement!.appendChild(popHead);
    expect(getComputedStyle(popChip).marginLeft).toBe("auto");
  });

  it("gives a collapsed define row ONE height, open or shut", () => {
    // A collapsed row is a list row: it was carrying 14px/10px section padding for 24px of content. The
    // collapsed-only override is gone with the tightening — a row that changed height on toggle moved the
    // heading a few pixels under the pointer that had just pressed it.
    const open = getComputedStyle(inSpec("section", "mw-define"));
    const shut = getComputedStyle(inSpec("section", "mw-define collapsed"));
    expect(open.paddingTop).toBe("9px");
    expect(open.paddingBottom).toBe("7px");
    expect(shut.paddingBottom).toBe(open.paddingBottom);
  });

  it("still gives the DOCUMENT's own h3 the section rhythm", () => {
    // The generic rule is not the defect — it is what spaces a resource heading under Data requirements
    // (`SPEC-DATA-REQUIREMENTS`). The fix is that a component may override it, not that it goes away.
    const style = getComputedStyle(inSpec("h3"));
    expect(style.marginTop).toBe("16px");
    expect(style.marginBottom).toBe("4px");
  });
});
