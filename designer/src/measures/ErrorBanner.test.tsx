// APP-ERROR-DISMISS — every error banner can be closed, and its detail shows as the caller asked.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorBanner } from "./ErrorBanner.tsx";

describe("ErrorBanner (APP-ERROR-DISMISS)", () => {
  it("reports the close press", () => {
    const onDismiss = vi.fn();
    render(<ErrorBanner message="warehouse unavailable" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("shows the detail outright unless it is collapsible", () => {
    render(<ErrorBanner message="Not saved" detail="line 3: bad" onDismiss={() => {}} />);
    expect(screen.getByText("line 3: bad")).toBeTruthy();
    expect(screen.queryByText("View Details")).toBeNull();
  });

  it("hides a collapsible detail behind View Details", () => {
    render(<ErrorBanner message="run failed" detail={"run failed\ntrace"} collapsible onDismiss={() => {}} />);
    expect(screen.queryByText(/trace/)).toBeNull();
    fireEvent.click(screen.getByText(/View Details/));
    expect(screen.getByText(/trace/)).toBeTruthy();
  });
});
