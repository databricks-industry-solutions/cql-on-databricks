// DOC-SCOPE-OPEN — the left pane lists the open scope's libraries, and only while a scope is open.
//
// Mounted through the real `NavPane` with no compiled model, because that is the case the Scope tab exists
// for: it lists the scope's contents whether or not the open library has compiled.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Definition } from "../catalog.ts";
import { NavPane } from "./NavPane.tsx";
import type { NavTab } from "./model.ts";
import type { NavScope } from "./ScopeNav.tsx";

const lib = (name: string, cat: "measure" | "lib", title?: string): Definition => ({
  id: `${name}@1.0.0`, name, version: "1.0.0", location: "folder:/Users/someone/authoring", title, src: "", cat,
  status: "draft", stat: "", statk: "", mine: true, meta: "", desc: "",
});
const LIBS = [lib("Screening", "measure", "Example screening"), lib("Common", "lib"), lib("Control", "measure")];

function pane(navTab: NavTab, scope: NavScope | null, onNavTab = vi.fn()) {
  return render(<NavPane dep={null} definition={LIBS[0]!} source="" selected={null}
    onSelectDefine={() => {}} onAnchor={() => {}} specModel={null} visibleSpec={null} specDoc={null}
    retrieves={[]} overviewSections={{} as never} appendixSections={undefined} metrics={null}
    showEliminated={false} onShowEliminatedChange={() => {}} defineSort="declared" onDefineSortChange={() => {}}
    navTab={navTab} onNavTab={onNavTab} size="normal" onSize={() => {}} scope={scope} />);
}

const scopeOf = (onOpen = vi.fn()): NavScope =>
  ({ at: "folder:/Users/someone/authoring", libraries: LIBS, openId: "Screening@1.0.0", onOpen });

describe("the Scope tab (DOC-SCOPE-OPEN)", () => {
  it("is offered only while a scope is open", () => {
    pane("outline", scopeOf());
    expect(screen.getByRole("tab", { name: "Scope" })).toBeTruthy();
  });

  it("is absent with no scope, and a scope link lands on the outline", () => {
    pane("scope", null);
    expect(screen.queryByRole("tab", { name: "Scope" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Outline" }).getAttribute("aria-selected")).toBe("true");
  });

  it("lists the scope's measures, then its libraries, with the open one marked", () => {
    const { container } = pane("scope", scopeOf());
    expect(screen.getByText("authoring")).toBeTruthy();
    const rows = [...container.querySelectorAll(".mw-scope .mw-node .lab")].map((n) => n.textContent);
    expect(rows).toEqual(["Example screening", "Control", "Common"]);
    expect(container.querySelector('.mw-scope [aria-current="page"] .lab')!.textContent).toBe("Example screening");
  });

  it("opens another library of the scope, and not the one already open", () => {
    const onOpen = vi.fn();
    pane("scope", scopeOf(onOpen));
    fireEvent.click(screen.getByText("Common"));
    fireEvent.click(screen.getByText("Example screening"));
    expect(onOpen.mock.calls).toEqual([["Common@1.0.0"]]);
  });
});
