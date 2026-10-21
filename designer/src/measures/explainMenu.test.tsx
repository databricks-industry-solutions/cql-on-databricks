// SPEC-EXPLAIN-VIEW / SPEC-EXPLAIN-AGAIN — the bar's split Explain control.
//
// The behavioural half is here; the CASCADE half — that the caret survives `.mw button`'s reset — is at
// the bottom, for the reason `paneTabs.test.tsx` and `refLens.test.tsx` give: the source of a control
// blanked by that reset reads correctly, and a screenshot of it looks plausible.

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { ExplainMenu } from "./ExplainMenu.tsx";
import type { ExplainTarget } from "./MeasureNarrative.tsx";

const TARGET: ExplainTarget = { label: "Denominator", define: "Denominator" };

function open(props: Partial<Parameters<typeof ExplainMenu>[0]> = {}) {
  const toggled: number[] = [];
  const ran: ExplainTarget[] = [];
  const view = render(
    <div className="mw">
      <ExplainMenu explain onToggle={() => toggled.push(1)} target={null}
        onExplainAgain={(t) => ran.push(t)} {...props} />
    </div>,
  );
  return { ...view, toggled, ran };
}

describe("ExplainMenu — the toggle and its caret are ONE control (SPEC-EXPLAIN-AGAIN)", () => {
  it("keeps the toggle a pure VIEW switch — the caret's menu never rides on its press", () => {
    // The toggle is advertised as safe to flip repeatedly while reading (it filters prose already
    // fetched). A generation behind the same press would make the cheapest control in the bar
    // occasionally expensive, which is how a reader stops trusting it.
    const { toggled } = open();
    fireEvent.click(screen.getByRole("button", { name: /Explain$/ }));
    expect(toggled).toEqual([1]);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens its menu from the caret alone, and reports the state on the caret", () => {
    open();
    const caret = screen.getByRole("button", { name: "Explain options" });
    expect(caret).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(caret);
    expect(caret).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu", { name: "Explain options" })).toBeTruthy();
  });

  it("disables 'Explain again' with no aim, and SAYS WHY in the row rather than in a tooltip", () => {
    // `design-system.md` section 6: a disabled control that cannot say why is indistinguishable from a
    // broken one. The reason lives in the row's own blurb — readable at leisure, reachable on touch —
    // which is the fix that rule prescribes rather than painting the disabled state louder.
    open({ target: null });
    fireEvent.click(screen.getByRole("button", { name: "Explain options" }));
    const row = screen.getByRole("menuitem", { name: /Explain again/ });
    expect(row).toBeDisabled();
    expect(row.textContent).toMatch(/Press the Explain mark on a section/);
  });

  it("fires at the aimed section once the document has aimed it, and closes", () => {
    const { ran } = open({ target: TARGET });
    fireEvent.click(screen.getByRole("button", { name: "Explain options" }));
    const row = screen.getByRole("menuitem", { name: /Explain again/ });
    expect(row).not.toBeDisabled();
    expect(row.textContent).toContain("Denominator");
    fireEvent.click(row);
    expect(ran).toEqual([TARGET]);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("dismisses on Escape and on a press outside — the app's one popover contract", () => {
    open({ target: TARGET });
    fireEvent.click(screen.getByRole("button", { name: "Explain options" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Explain options" }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("ExplainMenu — the caret's rules out-specify the .mw button reset (APP-STYLE-GUIDE)", () => {
  it("qualifies every rule that gives the caret a fill, a border or a resting ink", () => {
    // `.mw button { background: none; border: 0; color: inherit }` is (0,1,1), so a bare
    // `.mw-explain-more` rule at (0,1,0) would silently lose its hairline AND its ink — and the control
    // would render as a bare chevron in inherited ink beside a bordered toggle, which reads as the
    // toggle being broken. Fifth surface to hit this reset; asserted on the authored selectors, because
    // jsdom does no cascade over a stylesheet it was not given.
    const css = readFileSync("src/measures/measures.css", "utf8");
    expect(css).toContain(".mw .mw-explain-more {");
    expect(css).not.toMatch(/^\.mw-explain-more \{/m);
    // The seam: the halves square their facing corners and overlap by a pixel, so one hairline runs
    // between them. A gap here would read as two controls, which is the reading the form exists to
    // prevent.
    expect(css).toContain(".mw .mw-explain-group .mw-explain { border-radius: 9px 0 0 9px; }");
    expect(css).toMatch(/\.mw \.mw-explain-more \{[^}]*margin-left: -1px/);
  });
});
