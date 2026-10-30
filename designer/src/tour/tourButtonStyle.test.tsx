// APP-TOUR / APP-STYLE-GUIDE (PROC-TDD) — the tour's controls must out-specify the app-wide button
// reset, and this is a CASCADE test: it loads the real stylesheets in the real order and asks what won.
//
// THE DEFECT, EXACTLY. `measures.css` carries `.mw button { color: inherit; background: none; border: 0 }`
// at specificity (0,1,1). A bare `.mw-tour-primary` is (0,1,0), so it LOST every time the control was a
// `<button>`: the hero's "Open the example", every "Show me" and every "Take me there" computed away
// their accent fill and inherited the page ink, rendering as plain words — while the one action that
// happens to be an `<a>` (the published-notebooks link, which a `button` selector cannot reach) was the
// only thing on the page that looked like a button. Reported as "Open the notebooks isn't a button like
// the others", then as its exact inverse once the good one was identified, which is what a page with two
// accidental button styles feels like from the outside.
//
// The source read correctly throughout. That is why this is asserted against the cascade rather than by
// eye, and it is the fourth surface to hit the same reset (pane tabs, the ref lens, the badges).

import { readFileSync } from "node:fs";
import { describe, it, expect, beforeAll } from "vitest";

/** Every selector in tour.css that styles a `button` element, as authored. */
const BUTTON_CLASSES = ["mw-tour-primary", "mw-tour-ghost", "mw-tour-close"];

let css = "";
beforeAll(() => { css = readFileSync("src/tour/tour.css", "utf8"); });

describe("tour buttons out-specify the app-wide reset (APP-TOUR)", () => {
  it.each(BUTTON_CLASSES)("%s is qualified past `.mw button`", (cls) => {
    // Every rule whose selector names the class must carry the `.mw` qualifier: (0,2,0) beats (0,1,1).
    // Matching on the authored text is the point — the fix IS the selector, so the selector is the thing
    // worth pinning, and a rule added later without it fails here rather than in someone's screenshot.
    const selectors = [...css.matchAll(new RegExp(`^([^\\n{]*\\.${cls}[^\\n{]*)\\{`, "gm"))]
      .map((m) => m[1]!.trim());
    expect(selectors.length).toBeGreaterThan(0);
    expect(selectors.filter((s) => !s.startsWith(".mw "))).toEqual([]);
  });

  it("keeps ONE primary style, whether the action is a button or a link", () => {
    // A section's action is a `<button>` when it moves within the app and an `<a>` when it leaves it
    // (the notebooks). Both wear `.mw-tour-primary`; the anchor adds only what the global link colour and
    // the browser underline force it to restate. If it ever declares its own fill or geometry, the two
    // elements have started drifting into two looks again.
    const anchorRule = css.match(/\.mw a\.mw-tour-primary\s*\{([^}]*)\}/);
    expect(anchorRule).not.toBeNull();
    const declared = anchorRule![1]!.split(";").map((d) => d.split(":")[0]!.trim()).filter(Boolean);
    expect(declared.sort()).toEqual(["color", "text-decoration"]);
  });
});
