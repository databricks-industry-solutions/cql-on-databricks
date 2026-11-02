// APP-SPEC-MODEL — the left pane's OUTLINE tab lists the SAME specification the Specification page
// renders, so a row exists exactly when a section does. These tests pin the LIBRARIES grouping: one
// group per library — the measure's own among them — each holding its populations and ordinary defines
// in one list.
//
// The nav does not split them into `Definitions` for the root and `Included` for everything else. That
// distinction is real (other people's libraries, versioned separately) but not STRUCTURAL, and a split
// would cost the reader depth: the root's defines would sit one level shallower than an include's for no
// difference in how either is reached. What the root keeps is FIRST PLACE, which is the true part.
//
// The tab strip, the filter and the tree belong to the pane shell. These mount the real `NavPaneBody`
// rather than `DefineNav` alone, because the Outline is
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
 *  query as an include, because it is the same shape of thing in the DOM. The root needs no library name of
 *  its own, because it has none to match. */
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
    // Initial Population last, which would make the nav and the page disagree about where the
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
    // The failure this guards against: the nav sorts its own copy — opening on `topo` — while the page renders
    // declaration order, so the nav's Nth row is not the page's Nth section, and no setting can reconcile them
    // because declaration order is not one of the nav's options.
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

  it("scales a population's bar to its share of every evaluated subject", () => {
    // SPEC-POPULATION-CASCADE — a population is counted as written, so its base is
    // the evaluated total: 560 of 1,000 is 56%. A share of the Denominator would mix two definitions'
    // subjects; the cascade belongs to the score (RUN-CLIENT-MEASURE-SCORE).
    const { container } = renderNavPane({ metrics: metricSet() });
    const numerator = [...container.querySelectorAll(".mw-node")]
      .find((r) => r.querySelector(".lab")?.textContent === "Numerator");
    expect(parseFloat((numerator?.querySelector(".mw-node-met > i") as HTMLElement).style.width)).toBeCloseTo(56);
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
    // SPEC-DATA-REQUIREMENTS — Parameters belong in that section on the page, and this pane lists them too: a
    // count computed for the pane and dropped would leave scrolling past every read group as the only route to
    // the subsection. A row, not a group — a parameter table has nothing to open.
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
    // parameters, and the page renders the section for them alone. Gating on the reads would drop the group
    // and take the only route to the parameters with it.
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
