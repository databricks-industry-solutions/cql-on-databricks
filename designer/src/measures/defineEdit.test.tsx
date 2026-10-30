// DOC-CQL-EDIT — editing a define where the Specification shows it.
//
// Three things are pinned here. The span: each declaration knows exactly which characters of its library's
// source it covers, so an edit splices back into the real text with the author's comments and formatting
// intact. The document: it offers Edit only where the workspace says so, and draws the workspace's editor in
// place of the code. The editor: Done and a press outside close it, and a press inside does not reach the
// section around it, whose click toggles selection.
//
// The fixture is REAL ELM with the CQL it was compiled from (`scripts/build-ui-fixtures.sh`), so the locators
// are the ones the compiler really emits against that exact text. Monaco does not run in jsdom; the editor is
// a textarea here.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import type { Definition } from "../catalog.ts";
import { MeasureNarrative } from "./MeasureNarrative.tsx";
import { buildSpecModel, locatorSpan, type SpecEntry, type SpecLibraryInput } from "./specModel.ts";
import { buildSpecDocument } from "./specDocument.ts";
import { DefineEditContext, DefineEditor, type DefineEdit } from "./defineEdit.tsx";
import fixtures from "./__fixtures__/spec-patterns-elm.json";

vi.mock("../authoring/CqlEditor.tsx", () => ({
  CqlEditor: ({ value, onChange }: { value: string; onChange: (v: string) => void }) =>
    <textarea aria-label="CQL editor" value={value} onChange={(e) => onChange(e.target.value)} />,
}));

function demo(): SpecLibraryInput {
  const f = fixtures.root;
  return { info: parseElmDefines(f.elm)!, elm: JSON.parse(f.elm), source: f.source };
}

const DEFINITION: Definition = {
  id: "x", name: "ExampleReadingControl", version: "1.0.0", location: "folder:/Users/someone/x", title: "Example", src: "", cat: "measure",
  status: "draft", stat: "", statk: "", mine: true, meta: "", desc: "A fixture measure.",
};

function renderDoc(edit: DefineEdit | null) {
  const lib = demo();
  const model = buildSpecModel({ root: lib, includes: [] });
  const doc = buildSpecDocument(model, new Map([[model.root, lib.elm]]));
  const page = <MeasureNarrative definition={DEFINITION} metrics={null} funnel={null} live={false}
    selected={null} onSelect={() => {}} onNavigate={() => {}} onRunCell={() => {}}
    spec={model} elmInfo={lib.info} doc={doc} namedTerminology={{ valueSets: new Set(), codes: new Set() }} />;
  return { ...render(edit ? <DefineEditContext.Provider value={edit}>{page}</DefineEditContext.Provider> : page) };
}

const section = (container: HTMLElement, name: string) =>
  container.querySelector<HTMLElement>(`[data-crit-name="${name}"]`)!;

function editing(over: Partial<DefineEdit> = {}): DefineEdit {
  return { openKey: null, offers: () => true, blocked: () => null, open: vi.fn(), editor: null, ...over };
}

describe("a declaration's span (DOC-CQL-EDIT)", () => {
  it("covers the whole declaration in its library's source, header included", () => {
    const lib = demo();
    const model = buildSpecModel({ root: lib, includes: [] });
    const denom = model.populations.find((p) => p.name === "Denominator")!;
    const [start, end] = denom.variants[0]!.span!;
    expect(lib.source.slice(start, end)).toBe('define "Denominator":\n  "Initial Population"');
    const ip = model.populations.find((p) => p.name === "Initial Population")!.variants[0]!.span!;
    expect(lib.source.slice(ip[0], ip[1]))
      .toBe('define "Initial Population":\n  "Has Example Condition"\n    and "Has Visit During Period"');
  });

  it("splices an edit back into the source, leaving every other character as it was", () => {
    const lib = demo();
    const model = buildSpecModel({ root: lib, includes: [] });
    const [start, end] = model.populations.find((p) => p.name === "Denominator")!.variants[0]!.span!;
    const edited = lib.source.slice(0, start) + 'define "Denominator":\n  "Initial Population" and true'
      + lib.source.slice(end);
    expect(edited).toBe(lib.source.replace('define "Denominator":\n  "Initial Population"\n',
                                           'define "Denominator":\n  "Initial Population" and true\n'));
  });

  it("is null for a locator the source cannot hold", () => {
    expect(locatorSpan("a\nb", null)).toBeNull();
    expect(locatorSpan("a\nb", "nonsense")).toBeNull();
    expect(locatorSpan("a\nb", "2:1-9:1")).toBeNull();
    expect(locatorSpan("abc\ndef", "2:2-2:3")).toEqual([5, 7]);
  });
});

describe("the document's Edit (DOC-CQL-EDIT)", () => {
  it("offers nothing without a workspace to edit in", () => {
    const { container } = renderDoc(null);
    expect(section(container, "Denominator").querySelector(".mw-cql-edit")).toBeNull();
  });

  it("opens the define it is pressed on", () => {
    const edit = editing();
    const { container } = renderDoc(edit);
    fireEvent.click(section(container, "Denominator").querySelector<HTMLButtonElement>(".mw-cql-edit")!);
    expect((edit.open as ReturnType<typeof vi.fn>).mock.calls[0]![0]).toMatchObject({ name: "Denominator" });
  });

  it("opens on a double-click of the code", () => {
    const edit = editing();
    const { container } = renderDoc(edit);
    fireEvent.doubleClick(section(container, "Denominator").querySelector("pre.mw-cql")!);
    expect((edit.open as ReturnType<typeof vi.fn>).mock.calls[0]![0]).toMatchObject({ name: "Denominator" });
  });

  it("is disabled, with the reason, when the define cannot open now", () => {
    const edit = editing({ blocked: () => "The CQL as edited does not compile." });
    const { container } = renderDoc(edit);
    const button = section(container, "Denominator").querySelector<HTMLButtonElement>(".mw-cql-edit")!;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe("The CQL as edited does not compile.");
    fireEvent.doubleClick(section(container, "Denominator").querySelector("pre.mw-cql")!);
    expect(edit.open).not.toHaveBeenCalled();
  });

  it("is not offered where the workspace says no", () => {
    const { container } = renderDoc(editing({ offers: (e: SpecEntry) => e.name !== "Denominator" }));
    expect(section(container, "Denominator").querySelector(".mw-cql-edit")).toBeNull();
    expect(section(container, "Initial Population").querySelector(".mw-cql-edit")).not.toBeNull();
  });

  it("draws the editor in place of the open define's code, and only there", () => {
    const { container } = renderDoc(editing({ openKey: "ExampleReadingControl::Denominator", editor: <div data-testid="the-editor" /> }));
    const denom = section(container, "Denominator");
    expect(denom.querySelector('[data-testid="the-editor"]')).not.toBeNull();
    expect(denom.querySelector("pre.mw-cql")).toBeNull();
    expect(screen.getAllByTestId("the-editor")).toHaveLength(1);
  });
});

describe("the in-place editor (DOC-CQL-EDIT)", () => {
  const props = { value: 'define "X":\n  true', firstLine: 7, onChange: () => {}, status: "Compiles", problems: [],
                  theme: "light" as const };

  it("closes on Done", () => {
    const onClose = vi.fn();
    render(<DefineEditor {...props} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on a press outside it, not inside it", async () => {
    const onClose = vi.fn();
    render(<div><p>outside</p><DefineEditor {...props} onClose={onClose} /></div>);
    await screen.findByLabelText("CQL editor");
    fireEvent.mouseDown(screen.getByLabelText("CQL editor"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps its clicks from the section around it, whose click toggles selection", async () => {
    const select = vi.fn();
    render(<section onClick={select}><DefineEditor {...props} onClose={() => {}} /></section>);
    fireEvent.click(await screen.findByLabelText("CQL editor"));
    expect(select).not.toHaveBeenCalled();
  });

  it("reports each change and lists the draft's problems", async () => {
    const onChange = vi.fn();
    render(<DefineEditor {...props} onChange={onChange} onClose={() => {}} status="1 problem"
      problems={["line 8: Could not resolve identifier"]} />);
    fireEvent.change(await screen.findByLabelText("CQL editor"), { target: { value: 'define "X":\n  false' } });
    expect(onChange).toHaveBeenCalledWith('define "X":\n  false');
    expect(screen.getByText("line 8: Could not resolve identifier")).toBeTruthy();
    expect(screen.getByText("1 problem")).toBeTruthy();
  });
});
