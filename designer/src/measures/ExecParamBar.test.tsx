// APP-EXEC-PARAMS / PIPE-MEASUREMENT-PERIOD — the parameter bar renders a LIST, and is written against
// the list rather than against the measurement period: a second parameter must reach the bar without the
// bar learning what it is.
//
// These also pin the stepper's one hard rule — it does not leave the domain. The original toolbar stepper
// did plain `year ± 1` and walked into periods that have no data, which is the defect the disabled arrow
// exists to make impossible.

import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExecParamBar } from "./ExecParamBar.tsx";
import { periodParam, periodYears, type ExecParam } from "./model.ts";

/** A second parameter, standing in for the ones a measure package will declare. */
function extraParam(over: Partial<ExecParam> = {}): ExecParam {
  return {
    id: "ageBand", label: "Age band", value: "adult",
    options: [{ id: "peds", label: "0–17" }, { id: "adult", label: "18–64" }, { id: "senior", label: "65+" }],
    onChange: vi.fn(),
    ...over,
  };
}

const period = () => screen.getByRole("group", { name: "Measurement period" });

describe("ExecParamBar (APP-EXEC-PARAMS)", () => {
  it("prints the current value of every parameter it is given", () => {
    render(<ExecParamBar params={[periodParam(2024, vi.fn()), extraParam()]} />);
    expect(period()).toHaveTextContent("CY 2024");
    expect(screen.getByRole("group", { name: "Age band" })).toHaveTextContent("18–64");
  });

  it("gives each parameter one stepper, whatever the parameter is", () => {
    render(<ExecParamBar params={[periodParam(2024, vi.fn()), extraParam()]} />);
    const groups = screen.getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["Measurement period", "Age band"]);
    expect(within(groups[1]!).getByLabelText("Next age band")).toBeInTheDocument();
  });

  it("names the parameter in the tooltip — the value is on the control, the name is not", () => {
    render(<ExecParamBar params={[periodParam(2024, vi.fn())]} />);
    expect(period()).toHaveAttribute("title", "Measurement period: CY 2024");
  });

  it("steps forward and back along the domain", () => {
    const onChange = vi.fn();
    render(<ExecParamBar params={[periodParam(2024, onChange)]} />);
    fireEvent.click(within(period()).getByLabelText("Next measurement period"));
    expect(onChange).toHaveBeenCalledWith(2025);
    fireEvent.click(within(period()).getByLabelText("Previous measurement period"));
    expect(onChange).toHaveBeenLastCalledWith(2023);
  });

  it("stops at the ends of the domain instead of walking off it", () => {
    const years = periodYears();
    const newest = years[0]!, oldest = years[years.length - 1]!;

    const { unmount } = render(<ExecParamBar params={[periodParam(newest, vi.fn())]} />);
    expect(within(period()).getByLabelText("Next measurement period")).toBeDisabled();
    unmount();

    render(<ExecParamBar params={[periodParam(oldest, vi.fn())]} />);
    expect(within(period()).getByLabelText("Previous measurement period")).toBeDisabled();
  });
});
