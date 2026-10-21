// SPEC-ATTRITION / APP-DESIGN-SYSTEM (PROC-TDD) — the funnel's rendering contract, and specifically
// the part that was wrong: a removal has to be drawn TO SCALE inside the bar it comes out of, not as a
// line of text between two bars that never narrow.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FunnelView } from "./Funnel.tsx";
import { funnelFromCounts } from "./model.ts";

const FUNNEL = funnelFromCounts({
  "Initial Population": 100,
  "Denominator": 80,
  "Denominator Exclusions": 6,
  "Denominator Exceptions": 3,
  "Numerator": 40,
}, 100, "proportion")!;

/** The stage row whose name is `name`. Geometry is the thing under test here, so these reach for the
 *  element rather than the accessible text — a segment's offset and width are not readable any other
 *  way. */
const row = (container: HTMLElement, name: string) =>
  [...container.querySelectorAll<HTMLElement>(".mw-fstage")]
    .find((el) => el.querySelector(".nm")?.textContent === name)!;

describe("FunnelView (SPEC-ATTRITION)", () => {
  it("gives a bar to each declared population and none to a removal", () => {
    const { container } = render(<FunnelView funnel={FUNNEL} />);
    const names = [...container.querySelectorAll(".mw-fstage .nm")].map((el) => el.textContent);
    expect(names).toEqual(["All patients", "Initial Population", "Denominator", "Numerator"]);
  });

  it("stacks the removals across the tail of their base's bar, on the cohort axis", () => {
    const { container } = render(<FunnelView funnel={FUNNEL} />);
    const den = row(container, "Denominator");
    // The solid head is what survived; the hatched tail is what did not, and the boundary between them
    // is the rate's own base.
    expect(den.querySelector<HTMLElement>(".mw-fbar")!.style.width).toBe("71%");
    const segs = [...den.querySelectorAll<HTMLElement>(".mw-fseg")];
    expect(segs.map((el) => [el.dataset.removal, el.style.left, el.style.width])).toEqual([
      ["exclusion", "71%", "6%"],
      ["exception", "77%", "3%"],
    ]);
    // Only the last segment rounds — the solid head's right edge is an interior boundary now.
    expect(segs.map((el) => el.dataset.last)).toEqual([undefined, ""]);
  });

  it("leaves an undivided population as one full-width bar", () => {
    const { container } = render(<FunnelView funnel={FUNNEL} />);
    const num = row(container, "Numerator");
    expect(num.querySelector<HTMLElement>(".mw-fbar")!.style.width).toBe("40%");
    expect(num.querySelectorAll(".mw-fseg")).toHaveLength(0);
    expect(num.querySelector(".mw-fparts")).toBeNull();
  });

  it("puts each removal's label under the segment it names", () => {
    const { container } = render(<FunnelView funnel={FUNNEL} />);
    const parts = [...row(container, "Denominator").querySelectorAll<HTMLElement>(".mw-fpart")];
    // Indented to the segment's own offset, one per line — and named RELATIVE to the bar, whose heading
    // already says "Denominator".
    expect(parts.map((el) => [el.textContent, el.style.paddingLeft])).toEqual([
      ["−6 exclusions", "71%"],
      ["−3 exceptions", "77%"],
    ]);
    // The remainder gets no label: it is printed one row down as the Numerator's own base.
    expect(container.textContent).not.toContain("scored");
  });

  it("names the base the Numerator's figure is taken against, in visible text", () => {
    // It was a `title` attribute. A Numerator labelled 56.3% beside a bar drawn at 40% of the cohort is
    // two percentages against two bases, and the one that explains the gap has to be on the page.
    render(<FunnelView funnel={FUNNEL} />);
    expect(screen.getByText(/of 71/)).toBeTruthy();
    expect(screen.getByText(/56\.3%/)).toBeTruthy();
  });

  it("pins a selected member at the full extent of every stage they reached", () => {
    const { container } = render(
      <FunnelView funnel={FUNNEL} patientReached={["Initial Population", "Denominator"]}
        patientLabel="p1" />,
    );
    const pinned = [...container.querySelectorAll<HTMLElement>(".mw-fstage")]
      .filter((el) => el.querySelector(".mw-fmark"))
      .map((el) => el.querySelector(".nm")!.textContent);
    expect(pinned).toEqual(["All patients", "Initial Population", "Denominator"]);
    // At the bar's own extent, not the solid head's — reaching the Denominator is reaching all of it.
    expect(row(container, "Denominator").querySelector<HTMLElement>(".mw-fmark")!.style.left)
      .toBe("80%");
  });
});
