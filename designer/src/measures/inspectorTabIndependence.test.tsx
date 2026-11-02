// SPEC-REF-LENS / APP-INSPECTOR — READING A LENS MUST NOT DISTURB THE PANE. Not its tab, and above all
// not its PIN.
//
// The reported failure: "switching the popup lens's tab also switches the pinned inspector's tab". The
// tabs were never shared — `useInspectorTab` is per-instance, and the first three cases below pin that in
// both directions. The actual defect was worse and is the last case: pressing a tab inside the lens
// CLEARED the pin, so the pane fell back to "Whole document" and looked like it had followed the lens.
//
// WHY IT HAPPENED, because it is a trap any portal in this codebase can fall into: React bubbles events
// through the REACT tree, not the DOM tree. The lens renders into `document.body`, but a click inside it
// still travels up through the reference that opened it, into the define SECTION containing that
// reference, whose `onClick` selects — and selection TOGGLES, so re-firing it on the already-pinned define
// turned the pin off. Every hand-written control in the lens already called `stopPropagation`; the tab
// strip is the PANE's component and correctly does not. The stop belongs at the lens's boundary.
//
// Mounted as the workspace mounts them — the document and the pane as SIBLINGS, with a real toggling
// `onSelect` — because none of this is observable in either surface alone.

import { useState } from "react";
import { render, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import type { Definition } from "../catalog.ts";
import { MeasureNarrative } from "./MeasureNarrative.tsx";
import { Workbench } from "./Workbench.tsx";
import { buildSpecModel, filterSpecModel, type SpecLibraryInput } from "./specModel.ts";
import { buildSpecDocument } from "./specDocument.ts";
import { buildRetrieves, type ReferenceSet } from "./specRetrieves.ts";
import { selectionToken, type Selection } from "./model.ts";
import fixtures from "./__fixtures__/spec-model-elm.json";
import refsets from "./__fixtures__/spec-model-refset.json";

const REFSETS = Object.values(refsets) as unknown as ReferenceSet[];

const DEFINITION: Definition = {
  id: "doc@1", name: "Doc", version: "1", location: "store:k.measures", title: "Doc", src: "eCQM · Doc", cat: "measure", status: "draft",
  stat: "", statk: "", mine: true, meta: "", desc: "A fixture measure.",
};

/** The document and the pane, side by side, sharing ONE selection that TOGGLES — which is the workspace's
 *  own `selectInSpec` and the reason the defect was reachable at all. A harness with an inert `onSelect`
 *  cannot see it: the click still bubbles, it just has nothing to undo. */
function Workspace({ pinned }: { pinned: Selection }) {
  const f = fixtures.demo;
  const info = parseElmDefines(f.elm)!;
  const input: SpecLibraryInput = { info, elm: JSON.parse(f.elm), source: f.source };
  const model = filterSpecModel(buildSpecModel({ root: input, includes: [] }), false);
  const doc = buildSpecDocument(model, new Map<string, unknown>([[model.root, input.elm]]));
  const retrieves = buildRetrieves(model, REFSETS, new Map());

  const [selected, setSelected] = useState<Selection | null>(pinned);
  // `MeasureWorkspace.selectInSpec`, copied deliberately rather than imported: selecting the thing that is
  // already selected CLEARS it, and that toggle is half the mechanism under test.
  const select = (sel: Selection) =>
    setSelected((cur) => (cur && selectionToken(sel) === selectionToken(cur) ? null : sel));

  return (
    <div className="mw">
      <main className="mw-doc">
        <MeasureNarrative definition={DEFINITION} metrics={null} funnel={null} live={false}
          selected={selected} onSelect={select} onNavigate={select} onRunCell={() => {}}
          spec={model} elmInfo={info} doc={doc} retrieves={retrieves}
          namedTerminology={{ valueSets: new Set(), codes: new Set() }} />
      </main>
      <Workbench definition={DEFINITION} spec={model} doc={doc}
        selected={selected} onSelect={setSelected} onNavigate={select} retrieves={retrieves}
        metrics={null} live={false} scope={null} funnel={null}
        size="normal" onSize={() => {}} />
    </div>
  );
}

function renderBoth() {
  const f = fixtures.demo;
  const info = parseElmDefines(f.elm)!;
  const input: SpecLibraryInput = { info, elm: JSON.parse(f.elm), source: f.source };
  const model = filterSpecModel(buildSpecModel({ root: input, includes: [] }), false);
  const pinned: Selection = { kind: "define", name: model.populations[0]!.name };
  return { ...render(<Workspace pinned={pinned} />), pinned };
}

/** The PANE's strip — the one inside the workbench aside, never the lens's. */
function paneTabs(container: HTMLElement) {
  return container.querySelector(".mw-workbench .mw-wb-tabs")!;
}

/** The LENS's strip, in the portal host outside the rendered tree. */
function lensTabs() {
  return document.querySelector(".mw-lens-layer .mw-reflens .mw-wb-tabs")!;
}

const openTab = (strip: Element) =>
  strip.querySelector("[role=tab][aria-selected=true]")!.textContent;

const pick = (strip: Element, label: string) =>
  fireEvent.click([...strip.querySelectorAll<HTMLButtonElement>("[role=tab]")]
    .find((t) => t.textContent === label)!);

/** The marks a reference carries inside a criterion — a bound name in the prose, or a token in the CQL.
 *  `.vs` is excluded: a value set is not something the inspector can be scoped to, so its lens has no
 *  tab strip and these cases are about the strip. */
const REF = ".mw-defref, button.cql-link:not(.vs)";

/** A criterion that actually carries a reference — picked off the render rather than named, so these
 *  survive the fixture growing or being re-narrated.
 *
 *  The closed CQL blocks are OPENED first (`SPEC-CQL-VIEW` closes them under prose), because a criterion's
 *  tokens are where the references live. There is no population "Builds on" row outside the code
 *  (`SPEC-NO-UI-PROSE`). */
function refSection(container: HTMLElement, exclude?: string) {
  for (const t of container.querySelectorAll<HTMLButtonElement>('.mw-cqlblock-toggle[aria-expanded="false"]')) {
    fireEvent.click(t);
  }
  const section = [...container.querySelectorAll("[data-crit-name]")]
    .find((el) => !el.classList.contains("mw-dreq-row")
      && el.getAttribute("data-crit-name") !== exclude && el.querySelector(REF))!;
  expect(section, "no criterion in the fixture carries a reference").toBeTruthy();
  return section;
}

/** Open the lens on a reference INSIDE a criterion. There is no population "Builds on" row to press
 *  (`SPEC-NO-UI-PROSE`). Pressing a reference inside a section is the stronger gesture: it is the one
 *  the reported defect below was filed against, because the click bubbles through the criterion's own
 *  handler on its way up. */
function openLensOnReference(container: HTMLElement) {
  const section = refSection(container);
  fireEvent.click(section.querySelector<HTMLButtonElement>(REF)!);
  return section;
}

describe("the lens's tab and the pane's tab are independent (SPEC-REF-LENS)", () => {
  it("switching the lens's tab leaves the pinned pane's tab alone", () => {
    const { container } = renderBoth();
    expect(openTab(paneTabs(container))).toBe("Details");

    // Open a lens on a reference inside a criterion.
    openLensOnReference(container);
    expect(openTab(lensTabs())).toBe("Details");

    pick(lensTabs(), "Terminology");
    expect(openTab(lensTabs())).toBe("Terminology");
    // THE ASSERTION. The pane is a different reading of a different (or the same) define, and the reader
    // did not touch it.
    expect(openTab(paneTabs(container))).toBe("Details");
  });

  it("switching the PANE's tab leaves an open lens alone", () => {
    // The same independence in the other direction, which a single shared state would also break.
    const { container } = renderBoth();
    openLensOnReference(container);

    pick(paneTabs(container), "Demographics");
    expect(openTab(paneTabs(container))).toBe("Demographics");
    expect(openTab(lensTabs())).toBe("Details");
  });

  it("pressing a tab inside the lens does not CLEAR the pin (the reported defect)", () => {
    // THE EXACT SEQUENCE REPORTED: pin a population, open the lens on a reference INSIDE that section,
    // press Demographics — and the pane fell back to "Whole document".
    //
    // The reference has to come from inside the pinned SECTION. Pressed anywhere a criterion's own
    // `onClick` does not contain, the click bubbles into nothing and every assertion passes while the
    // defect is live. A portal's events travel up the REACT tree, so which element the box renders INTO
    // tells you nothing about where its clicks go.
    const { container, pinned } = renderBoth();
    // NOT the criterion the harness starts pinned to: selection TOGGLES, so clicking that one would
    // clear the pin instead of setting it, and the case would test the opposite of what it names.
    const section = refSection(container, selectionToken(pinned));
    const name = section.getAttribute("data-crit-name")!;

    // Pin it the way a reader does: click the criterion.
    fireEvent.click(section);
    const scope = () => container.querySelector(".mw-workbench .mw-wb-scope")!.textContent;
    expect(scope()).toContain(name);

    // Open the lens on a reference inside it, and confirm THAT did not disturb the pin either.
    fireEvent.click(section.querySelector<HTMLButtonElement>(REF)!);
    expect(lensTabs()).not.toBeNull();
    expect(scope()).toContain(name);

    pick(lensTabs(), "Demographics");
    expect(openTab(lensTabs())).toBe("Demographics");

    // THE ASSERTION. Reading a lens is not an act on the document: the pin survives it, and the pane has
    // not fallen back to its unscoped state.
    expect(scope()).toContain(name);
    expect(container.querySelector(".mw-workbench .mw-wb-scope .all")).toBeNull();
  });

  it("a freshly opened lens starts on Details however the pane is set", () => {
    // The lens's tab is a fact about THIS reading, not about the document — so it does not inherit the
    // pane's, and it does not persist from the previous reference either.
    const { container } = renderBoth();
    pick(paneTabs(container), "Terminology");

    openLensOnReference(container);
    expect(openTab(lensTabs())).toBe("Details");
    expect(openTab(paneTabs(container))).toBe("Terminology");
  });
});
