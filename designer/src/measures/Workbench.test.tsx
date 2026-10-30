// APP-INSPECTOR / APP-PANE-TABS — when the right pane's tab strip is there, and when its tabs
// answer a press.
//
// Both cases below are ones a screenshot passes and a reader does not. A tab disabled only while a fetch
// is in flight LOOKS like a tab with nothing in it, and a strip of three disabled words over "nothing is
// selected" looks like chrome that has stopped working.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Definition } from "../catalog.ts";
import { Workbench } from "./Workbench.tsx";
import { buildSpecModel, filterSpecModel, type SpecLibraryInput, type SpecModel } from "./specModel.ts";
import { parseElmDefines } from "../authoring/elmDefines.ts";
import elmFixtures from "./__fixtures__/spec-patterns-elm.json";
import type { Selection } from "./model.ts";

// The profile fetch NEVER SETTLES here, deliberately: "in flight" is the state the loading case is
// about, and a promise that never resolves holds the pane in it for the whole of a synchronous test.
// Without the stub jsdom also has no base URL, so the real call throws ERR_INVALID_URL into an effect
// and the failure surfaces as an unhandled error rather than as a test result.
vi.mock("../library-client.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../library-client.ts")>()),
  fetchDefineProfile: () => new Promise(() => {}),
}));

const DEFINITION: Definition = {
  id: "ExampleReadingControl@1.0.0", name: "ExampleReadingControl", version: "1.0.0", location: "store:k.measures",
  title: "Example Reading Control",
  cat: "measure", src: "v1.0.0", status: "pub", stat: "Ready to run", statk: "",
  mine: true, meta: "", desc: "",
} as Definition;

function model(): SpecModel {
  const info = parseElmDefines(elmFixtures.root.elm);
  if (!info) throw new Error("spec-patterns fixture did not parse");
  const input: SpecLibraryInput = { info, elm: JSON.parse(elmFixtures.root.elm), source: "" };
  return filterSpecModel(buildSpecModel({ root: input, includes: [] }), false);
}

function renderPane(selected: Selection | null) {
  return render(
    <Workbench
      size="normal" onSize={vi.fn()}
      definition={DEFINITION} spec={model()} doc={null}
      selected={selected} onSelect={vi.fn()} onNavigate={vi.fn()}
      metrics={null} live={false}
      // A scope is what makes the pane fetch a profile; with one set the fetch is in flight for the
      // whole of a synchronous test, which is exactly the state the first case is about.
      scope={{ dataSourceId: "test-deck" }}
      funnel={null} />,
  );
}

const strip = () => screen.queryByRole("tablist", { name: "Inspector" });

describe("the workbench tab strip (APP-INSPECTOR)", () => {
  it("is ABSENT when nothing is selected", () => {
    // Every tab describes a selection, so with none they are all empty — three disabled words over a
    // body already saying "pick something" is chrome explaining its own uselessness.
    renderPane(null);
    expect(strip()).toBeNull();
    expect(screen.getByText(/Nothing is selected/i)).toBeTruthy();
  });

  it("returns whole as soon as something is selected", () => {
    // The other half of the rule: absent for exactly as long as it has nothing to act on, and it comes
    // back complete rather than growing tab by tab (`APP-STABLE-LAYOUT`).
    renderPane({ kind: "define", name: "Initial Population" });
    const tabs = strip();
    expect(tabs).not.toBeNull();
    expect(within(tabs!).getAllByRole("tab")).toHaveLength(3);
  });

  it("leaves every tab PRESSABLE while the profile is still loading", () => {
    // THE BUG THIS EXISTS FOR. Availability is read off the profile payload, so before it lands every
    // tab but Details computed as empty and the strip disabled them: a reader who clicked Terminology
    // during the fetch got no response at all, then watched it become clickable a moment later for no
    // visible reason. A loading tab is not an empty tab — it opens and says it is loading.
    renderPane({ kind: "define", name: "Initial Population" });
    for (const tab of within(strip()!).getAllByRole("tab")) {
      expect(tab.hasAttribute("disabled"), `${tab.textContent} is disabled mid-fetch`).toBe(false);
    }
  });
});

describe("the pane's scope identity (APP-INSPECTOR)", () => {
  it("names the OWNING LIBRARY under the scoped name, as the reference lens does", () => {
    // ONE `InspectorIdentity` serves the pinned pane and the popped lens, so the eyebrow is asserted on
    // BOTH surfaces — the lens's half is in `MeasureNarrative.test.tsx`. Two assertions rather than one
    // because "pinned and popped agree" is the property the shared component exists to guarantee, and a
    // test that only ever looked at one of them would pass through the day they diverge.
    //
    // It is IDENTITY, not a figure: a define's name is unique only within its library (the model keys
    // itself `library + name`), so the scope line printing the name alone asserts something it cannot
    // guarantee — a measure with includes routinely carries two `Denominator`s.
    const { container } = renderPane({ kind: "define", name: "Initial Population" });
    const scope = container.querySelector(".mw-wb-scope")!;
    const lib = scope.querySelector(".mw-ident-lib");
    expect(lib).not.toBeNull();
    expect(lib!.textContent).toBe("ExampleReadingControl");
    // After the name, so it reads as a qualification of it rather than as a second heading.
    expect(scope.querySelector(".nm")!.compareDocumentPosition(lib!))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("invents no library for a READ, which several libraries may ask for", () => {
    // `SPEC-METRIC` — an absence is stated, never filled in. A retrieve is declared by the clauses that
    // ask for it, so an eyebrow here would have to pick one and would be wrong whenever it mattered.
    const { container } = renderPane({ kind: "retrieve", key: "Condition" });
    expect(container.querySelector(".mw-wb-scope .mw-ident-lib")).toBeNull();
  });
});
