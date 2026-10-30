// DESIGNER-TOP-BAR — ONE bar, three zones. These pin the parts of that promise a refactor could quietly
// undo: one bar (no second strip, no wordmark, no module label, no File menu), the artifact selector leads
// on the left, execution context sits in the CENTRE, and tour / appearance are two buttons at the right end
// which SHOW their state (never rows behind an avatar). Settings is removed (APP-SETTINGS SUPERSEDED).

import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Definition } from "../catalog.ts";
import { Navigator } from "./Navigator.tsx";
import { TopBar, type TopBarChrome } from "./TopBar.tsx";

function chrome(over: Partial<TopBarChrome> = {}): TopBarChrome {
  return {
    onToggleTour: vi.fn(),
    tourOpen: false,
    theme: "system",
    onThemeChange: vi.fn(),
    ...over,
  };
}

describe("the module slot (APP-ASSISTANT)", () => {
  it("renders a module's own controls OUTSIDE the app-toggle cluster", () => {
    // The cluster's whole claim is that it holds the controls about the APP and nothing else
    // (design-system section 6). The assistant is about the measure, so putting it inside would make
    // that claim false — and the claim is what earns those two their place at the bar's end.
    renderBar({ moduleTools: <button aria-label="Assistant" /> });
    const assistant = screen.getByLabelText("Assistant");
    expect(assistant).toBeInTheDocument();
    expect(assistant.closest(".mw-bar-tools")).toBeNull();
    expect(screen.getByLabelText("Product tour").closest(".mw-bar-tools")).not.toBeNull();
  });
});

const DEFINITION: Definition = {
  id: "ExampleMeasure@0.0.006", name: "ExampleMeasure", version: "0.0.006", location: "store:k.measures",
  title: "Example Measure",
  cat: "measure", src: "v0.0.006", status: "pub", stat: "Ready to run", statk: "",
  mine: true, meta: "", desc: "",
};

function renderBar(props: Partial<Parameters<typeof TopBar>[0]> = {}, over: Partial<TopBarChrome> = {}) {
  const c = chrome(over);
  const utils = render(<MemoryRouter><TopBar {...c} {...props} /></MemoryRouter>);
  return { ...utils, chrome: c };
}

describe("TopBar (DESIGNER-TOP-BAR)", () => {
  it("is one bar: the artifact selector and the bar tools, with no chrome strip", () => {
    const { container } = renderBar();
    expect(container.querySelectorAll(".mw-bar")).toHaveLength(1);
    // The retired strip and its contents.
    expect(container.querySelector(".mw-chrome")).toBeNull();
    expect(container.querySelector(".mw-brand")).toBeNull();
    expect(screen.queryByText("File")).toBeNull();
    // The wordmark is gone.
    expect(screen.queryByText("CqlDatabricks")).toBeNull();
    // ...and so is the account menu: no avatar, no profile name, no popover to open for a toggle.
    expect(container.querySelector(".mw-avatar")).toBeNull();
    expect(screen.queryByRole("button", { name: /account and preferences/i })).toBeNull();
  });

  it("carries no module label: the left zone is the artifact selector alone", () => {
    // DESIGNER-ONE-MODULE — with one module, a module mark names nothing.
    const { container } = renderBar();
    expect(container.querySelector(".mw-crumb-btn")).toBeNull();
    expect(screen.queryByText("Designer")).toBeNull();
    expect(screen.queryByTitle("Switch module")).toBeNull();
  });

  it("puts the tour and appearance in the bar, one press each, and no Settings", () => {
    const { container, chrome: c } = renderBar();
    expect(container.querySelectorAll(".mw-bar-tools .mw-icon-btn")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Settings" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Product tour" }));
    expect(c.onToggleTour).toHaveBeenCalled();
  });

  it("shows the tour toggle's state, which is what a menu could not do", () => {
    renderBar({}, { tourOpen: true });
    expect(screen.getByRole("button", { name: "Product tour" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("cycles appearance System → Light → Dark → System, so 'follow the OS' stays reachable", () => {
    // Three states, not two: a light/dark toggle cannot express "follow the OS", which is the default the
    // workspace ships — a reader who flipped it once could never get back.
    const cases: [TopBarChrome["theme"], string, string][] = [
      ["system", "Appearance: System", "light"],
      ["light", "Appearance: Light", "dark"],
      ["dark", "Appearance: Dark", "system"],
    ];
    for (const [theme, label, next] of cases) {
      const { chrome: c, unmount } = renderBar({}, { theme });
      fireEvent.click(screen.getByRole("button", { name: label }));
      expect(c.onThemeChange).toHaveBeenCalledWith(next);
      unmount();
    }
  });

  it("renders the artifact selector first in the left zone, with no separator before it", () => {
    const { container } = renderBar({
      crumb: <Navigator definition={DEFINITION} definitions={[]} onOpenDefinition={() => {}} />,
    });
    expect(container.querySelector(".mw-crumb-sep")).toBeNull();
    expect(screen.getByText("Example Measure")).toBeTruthy();
  });

  it("puts execution context in the CENTRE zone and the app's toggles alone on the right", () => {
    // The zones answer one question each: what you are reading · how it is computed · the app itself.
    // Execution context sits in the centre where it belongs, not competing for right-edge space with
    // single-touch controls.
    const { container } = renderBar({ context: <button>Explain</button> });
    expect(container.querySelector(".mw-bz-center")?.textContent).toBe("Explain");
    expect(container.querySelector(".mw-bz-right")?.textContent).toBe("");
    expect(container.querySelector(".mw-bz-right .mw-bar-tools")).toBeTruthy();
  });
});

describe("Navigator — the Open menu is the File surface (DESIGNER-TOP-BAR)", () => {
  it("carries the File actions and no longer offers 'Open from Catalog'", () => {
    render(<Navigator definition={DEFINITION} definitions={[]} onOpenDefinition={() => {}} />);
    fireEvent.click(screen.getByText("Example Measure"));

    // The item whose only function was to open this very menu is gone.
    expect(screen.queryByText(/Open from Catalog/)).toBeNull();
    for (const label of ["New measure", "New library", "Import CQL…"]) {
      expect(screen.getByRole("button", { name: new RegExp(label.replace("…", "")) })).toBeTruthy();
    }
  });

  it("no longer offers Publish to Marketplace (DESIGNER-ONE-MODULE)", () => {
    render(<Navigator definition={DEFINITION} definitions={[]} onOpenDefinition={() => {}} />);
    fireEvent.click(screen.getByText("Example Measure"));
    expect(screen.queryByRole("button", { name: /Publish to Marketplace/ })).toBeNull();
  });

  it("lists the OPEN artifact, marked, and lets it be opened again", () => {
    // The open artifact renders like any other row, wearing the module switcher's active mark — wash +
    // an `open` tag — and clicking it is a harmless re-open, not a dead spot.
    const other: Definition = { ...DEFINITION, id: "Other@1.0.0", name: "Other", title: "Other Measure" };
    const onOpen = vi.fn();
    const { container } = render(
      <Navigator definition={DEFINITION} definitions={[DEFINITION, other]} onOpenDefinition={onOpen} />);
    fireEvent.click(screen.getByText("Example Measure"));

    const rows = Array.from(container.querySelectorAll<HTMLButtonElement>(".mw-navpop-right .mw-pop-row"));
    const current = rows.find((r) => r.textContent?.includes("Example Measure"));
    expect(current).toBeTruthy();
    expect(current!.classList.contains("active")).toBe(true);
    expect(current!.querySelector(".tag.on")?.textContent).toBe("open");
    // The OTHER rows carry no mark — the accent is spent on exactly one row.
    expect(container.querySelectorAll(".mw-pop-row.active")).toHaveLength(
      container.querySelectorAll(".tag.on").length);

    fireEvent.click(current!);
    expect(onOpen).toHaveBeenCalledWith(DEFINITION.id);
  });

  it("leads the Recent lane with the open artifact, by construction", () => {
    // Recency is read from localStorage, which the recording effect writes AFTER the first render — so
    // the lane derives its head from the definition itself rather than racing that write.
    const other: Definition = { ...DEFINITION, id: "Other@1.0.0", name: "Other", title: "Other Measure" };
    const { container } = render(
      <Navigator definition={DEFINITION} definitions={[DEFINITION, other]} onOpenDefinition={() => {}} />);
    fireEvent.click(screen.getByText("Example Measure"));

    const first = container.querySelector(".mw-navpop-left .mw-pop-row");
    expect(first?.textContent).toContain("Example Measure");
    expect(first?.querySelector(".tag.on")?.textContent).toBe("open");
  });

  it("guards the unimplemented actions without advertising it", () => {
    // An action not built is `disabled`, so nothing fires that cannot happen. The reason still has to be
    // reachable, which is why the tooltip is on the wrapper (a disabled button shows none).
    const onFileAction = vi.fn();
    const { container } = render(<Navigator definition={DEFINITION} definitions={[]} onOpenDefinition={() => {}}
      onFileAction={onFileAction} />);
    fireEvent.click(screen.getByText("Example Measure"));
    const actions = Array.from(container.querySelectorAll<HTMLButtonElement>(".mw-navpop-act"));
    expect(actions.map((b) => [b.textContent, b.disabled])).toEqual([
      ["New measure", false], ["New library", false], ["Include a library…", false], ["Import CQL…", true],
      ["Save as…", false], ["Publish…", false]]);
    expect(container.querySelector(".soon")).toBeNull();
    expect(actions[3]!.closest(".mw-navpop-act-wrap")?.getAttribute("title")).toMatch(/later phase/);
    // DOC-OPEN-SAVE — New and Save As go to the workspace, which owns the form.
    fireEvent.click(actions[0]!);
    expect(onFileAction).toHaveBeenCalledWith({ kind: "new", what: "measure" });
  });

  it("says why a library cannot be included in the open one now", () => {
    // DOC-INCLUDE-NEW — the workspace gives the reason; the action is disabled and the reason is its tooltip.
    const onFileAction = vi.fn();
    const { container } = render(<Navigator definition={DEFINITION} definitions={[]} onOpenDefinition={() => {}}
      onFileAction={onFileAction} includeBlocked="The CQL as edited must compile first." />);
    fireEvent.click(screen.getByText("Example Measure"));
    const include = Array.from(container.querySelectorAll<HTMLButtonElement>(".mw-navpop-act"))
      .find((b) => b.textContent === "Include a library…")!;
    expect(include.disabled).toBe(true);
    expect(include.closest(".mw-navpop-act-wrap")?.getAttribute("title")).toBe("The CQL as edited must compile first.");
  });

  it("offers no file action where nothing can take it", () => {
    const { container } = render(<Navigator definition={DEFINITION} definitions={[]} onOpenDefinition={() => {}} />);
    fireEvent.click(screen.getByText("Example Measure"));
    expect(Array.from(container.querySelectorAll<HTMLButtonElement>(".mw-navpop-act")).every((b) => b.disabled))
      .toBe(true);
  });
});
