// SPEC-ATTRITION / SPEC-POPULATION-CASCADE / APP-DESIGN-SYSTEM (PROC-TDD) — the funnel's rendering
// contract: every population is its own bar, counted as written, and the Numerator's figure is the
// client's score with its base named (RUN-CLIENT-MEASURE-SCORE).
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
}, 100, { kind: "rate", value: 38 / 71, numerator: 38, base: 71 })!;

/** The stage row whose name is `name`. Geometry is the thing under test here, so these reach for the
 *  element rather than the accessible text — a bar's width is not readable any other way. */
const row = (container: HTMLElement, name: string) =>
  [...container.querySelectorAll<HTMLElement>(".mw-fstage")]
    .find((el) => el.querySelector(".nm")?.textContent === name)!;

describe("FunnelView (SPEC-ATTRITION)", () => {
  it("gives a bar to every population, removals included", () => {
    const { container } = render(<FunnelView funnel={FUNNEL} />);
    const names = [...container.querySelectorAll(".mw-fstage .nm")].map((el) => el.textContent);
    expect(names).toEqual(["All patients", "Initial Population", "Denominator", "Denominator Exclusions",
      "Denominator Exceptions", "Numerator"]);
  });

  it("draws each bar at its own share of the cohort, with no segments", () => {
    const { container } = render(<FunnelView funnel={FUNNEL} />);
    expect(row(container, "Denominator").querySelector<HTMLElement>(".mw-fbar")!.style.width).toBe("80%");
    expect(row(container, "Denominator Exclusions").querySelector<HTMLElement>(".mw-fbar")!.style.width)
      .toBe("6%");
    expect(container.querySelectorAll(".mw-fseg, .mw-fparts")).toHaveLength(0);
  });

  it("names the base the Numerator's score is taken against, in visible text", () => {
    // The score's base (71) is the client's cascaded count, a different base from the bar the figure
    // sits on, so it is named beside it.
    render(<FunnelView funnel={FUNNEL} />);
    expect(screen.getByText(/of 71/)).toBeTruthy();
    expect(screen.getByText(/53\.5%/)).toBeTruthy();
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
    expect(row(container, "Denominator").querySelector<HTMLElement>(".mw-fmark")!.style.left)
      .toBe("80%");
  });
});
