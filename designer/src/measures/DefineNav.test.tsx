// APP-SPEC-MODEL — the left pane's OUTLINE tab lists the SAME specification the Specification page
// renders, so a row exists exactly when a section does. These tests pin the LIBRARIES grouping: one
// group per library — the measure's own among them — each holding its populations and ordinary defines
// in one list.
//
// The nav used to split them: `Definitions` for the root, `Included` for everything else. The
// distinction was real (other people's libraries, versioned separately) but not STRUCTURAL, and the
// reader paid for it in depth — the root's defines sat one level shallower than an include's for no
// difference in how either is reached. What the root keeps is FIRST PLACE, which is the true part.
//
// The tab strip, the filter and the tree now belong to the pane shell and the Flow tab
// (APP-MEASURE-FLOW), so what was the "view tabs" block here lives in `flowNav.test.tsx`. These
// mount the real `NavPaneBody` rather than `DefineNav` alone, because after the split the Outline is
// only correct in combination with the row kit and the filter the shell owns.
//
// Real ELM, from the same Compiler Service fixture `specModel.test.tsx` uses — the nav renders the
// model, so the test builds the model rather than a hand-shaped stand-in.

import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { metricSet, renderNavPane } from "./__fixtures__/navHarness.tsx";

/** The row labels inside ONE library's group, in render order.
 *
 *  One helper for every library, which is the point of the restructure: the root is reached by the same
 *  query as an include, because it is the same shape of thing in the DOM. There were two helpers when
 *  there were two sections, and the root's needed no library name because it had none to match. */
function libraryRows(container: HTMLElement, library: string): string[] {
  const head = [...container.querySelectorAll("button.mw-collapsible")]
    .find((b) => b.textContent?.trim().startsWith(`${library} ·`));
  if (!head) throw new Error(`no ${library} group`);
  const body = head.nextElementSibling;
  if (!body?.classList.contains("mw-nav-nested")) throw new Error(`${library} group is not open`);
  return [...body.querySelectorAll(".mw-node .lab")].map((el) => el.textContent ?? "");
}

describe("the Outline Libraries grouping (APP-SPEC-MODEL)", () => {
  it("has no Population criteria sub-group", () => {
    // A population is a define: same declaring library, same kind of section on the page, and the
    // `pop` chip already marks the rows. Bracketing them off cost a level of nesting and a second
    // collapse to reach the library's own declarations, to restate what the chip states in place.
    renderNavPane();
    expect(screen.queryByText(/Population criteria/)).toBeNull();
  });

  it("lists populations and ordinary defines in ONE list under the declaring library", () => {
    const { container } = renderNavPane();
    const rows = libraryRows(container, "Demo");
    // Populations LEAD in eCQM reporting order — not the Sort facet's order. Topological sort put
    // Initial Population last, which is how the nav and the page came to disagree about where the
    // measure starts.
    expect(rows.slice(0, 3)).toEqual(["Initial Population", "Denominator", "Numerator"]);
    // …and the library's own non-population defines are in the SAME list, not a sibling group.
    expect(rows).toContain("Has Screening");
  });

  it("lists every declaration of the root library, populations included", () => {
    const { container } = renderNavPane();
    // 3 populations + `Has Screening` + `Orphan Helper`.
    expect(libraryRows(container, "Demo")).toEqual([
      "Initial Population", "Denominator", "Numerator", "Has Screening", "Orphan Helper",
    ]);
  });

  it("lists the order the MODEL is in, and never re-sorts it (SPEC-SHARED-ORDER)", () => {
    // The bug: the nav sorted its own copy — opening on `topo` — while the page rendered declaration
    // order, so the nav's Nth row was not the page's Nth section, and no setting could reconcile them
    // because declaration order was not one of the nav's options.
    //
    // The `sort` prop is the CONTROL's current value, for the popover's checkmark. It must not be a
    // second place the sorting happens: here the nav is told "az" but handed a model in declaration
    // order, and it must list declaration order.
    const declared = renderNavPane({ sort: "declared" });
    expect(libraryRows(declared.container, "Helpers")).toEqual(["ToString", "ToQuantity"]);

    const sorted = renderNavPane({ sort: "az", modelSort: "az" });
    expect(libraryRows(sorted.container, "Helpers")).toEqual(["ToQuantity", "ToString"]);

    const lied = renderNavPane({ sort: "az", modelSort: "declared" });
    expect(libraryRows(lied.container, "Helpers")).toEqual(["ToString", "ToQuantity"]);
  });

  it("carries no figure at all when there is no data source", () => {
    // APP-NAV-METRIC / SPEC-SAMPLE-METRIC — one statement of absence, not one per row. Forty rows
    // each saying "not computed" is what made the document unreviewable; the compute pill says it once.
    const { container } = renderNavPane();
    expect(container.querySelectorAll(".mw-node-met")).toHaveLength(0);
  });

  it("shows the same figure on the row that the section shows", () => {
    // The nav and the page resolve through ONE resolver (`resolveMetric`). Two surfaces each doing their
    // own arithmetic is how a row comes to read 8,204 beside a heading reading 8,207.
    const { container } = renderNavPane({ metrics: metricSet() });
    const rows = [...container.querySelectorAll(".mw-node")]
      .map((r) => [r.querySelector(".lab")?.textContent, r.querySelector(".mw-node-met")?.textContent]);
    expect(rows).toContainEqual(["Initial Population", "800"]);
    expect(rows).toContainEqual(["Numerator", "560"]);
  });

  it("scales each row's bar to its share of its own base", () => {
    // SPEC-ATTRITION — the Numerator is 560 of a denominator of 800, so 70%. Against the evaluated
    // total it would be 56%, which is the wrong denominator and the reason the base is never guessed.
    const { container } = renderNavPane({ metrics: metricSet() });
    const numerator = [...container.querySelectorAll(".mw-node")]
      .find((r) => r.querySelector(".lab")?.textContent === "Numerator");
    expect((numerator?.querySelector(".mw-node-met > i") as HTMLElement).style.width).toBe("70%");
  });

  it("gives the root library a group header like any other library's", () => {
    // The root is a library group like the rest, holding its own count. What marks it out
    // is its POSITION — first — rather than the absence of a header. This gives alignment:
    // all library defines sit a level deeper under a name, including the root.
    const { container } = renderNavPane();
    expect(screen.getByText(/^Demo · \d+$/)).toBeInTheDocument();
    const groups = [...container.querySelectorAll("button.mw-dag-group.mw-sub")]
      .map((b) => b.textContent?.trim().split(" ·")[0]);
    expect(groups).toEqual(["Demo", "Helpers"]);
  });

  it("reaches BOTH halves of Data requirements — the reads, and the parameters", () => {
    // SPEC-DATA-REQUIREMENTS — Parameters moved into that section on the page, and nothing here listed
    // it: the count was computed for this pane and dropped, so the only route to the subsection was
    // scrolling past every read group. A row, not a group — a parameter table has nothing to open.
    const { container, anchored } = renderNavPane({ appendix: { parameters: 1 } });
    const group = [...container.querySelectorAll("button.mw-collapsible")]
      .find((b) => b.textContent?.trim().startsWith("Data requirements"));
    const rows = [...group!.nextElementSibling!.querySelectorAll(".mw-node .lab")]
      .map((el) => el.textContent ?? "");
    expect(rows.at(-1)).toBe("Parameters · 1");
    fireEvent.click(group!.nextElementSibling!.querySelector("button.mw-node")!);
    expect(anchored).toContain("mw-app-params");
  });

  it("keeps the Data requirements group when the parameters are all it has left", () => {
    // The group's condition is the SECTION's: a measure whose reads were all eliminated still declares
    // parameters, and the page renders the section for them alone. Gating on the reads dropped the group
    // and took the only route to the parameters with it.
    const { container } = renderNavPane({ appendix: { dataRequirements: [], parameters: 2 } });
    const group = [...container.querySelectorAll("button.mw-collapsible")]
      .find((b) => b.textContent?.trim().startsWith("Data requirements"));
    expect(group).toBeDefined();
    expect(group!.nextElementSibling!.textContent).toContain("Parameters · 2");
  });

  it("counts LIBRARIES on the section heading, matching the groups under it", () => {
    // A heading may never carry a number its own rows do not add up to. This section's rows ARE the
    // library groups, so the count is of libraries — the per-library define counts live on the groups,
    // beside the entries they count.
    const { container } = renderNavPane();
    const head = [...container.querySelectorAll("button.mw-collapsible")]
      .find((b) => b.textContent?.trim().startsWith("Libraries"));
    expect(head?.textContent?.trim()).toBe("Libraries · 2");
  });
});
