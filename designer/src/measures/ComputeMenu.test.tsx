// APP-EXECUTION-PILL / APP-UPDATE-MODE / APP-COMPUTE-TARGET — ONE dropdown of items, in order: Data source
// (a submenu), Compute (a submenu of SQL warehouses), Automatic (one checkable item; unchecked is Manual),
// then the Refresh button. The trigger is the dot and caret alone; its tooltip names the choices.

import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ComputeMenu } from "./ComputeMenu.tsx";
import { runStatusText, runStatusWhen, storedUpdateMode, type RunStatus } from "./model.ts";
import type { DataSourceRecord, WarehouseRecord } from "../wire.ts";

const WAREHOUSES: WarehouseRecord[] = [
  { id: "wh-default", name: "Shared Serverless", state: "RUNNING" },
  { id: "wh-big", name: "Large", state: "STOPPED" },
];

function renderPill(over: Partial<Parameters<typeof ComputeMenu>[0]> = {}) {
  return render(
    <ComputeMenu mode="static" running={false} updateMode="paused" warehouses={WAREHOUSES}
      defaultWarehouseId="wh-default" onModeChange={vi.fn()} onUpdateModeChange={vi.fn()} {...over} />,
  );
}

const trigger = () => screen.getByRole("button", { name: "Execution control" });

describe("ComputeMenu (APP-EXECUTION-PILL)", () => {
  it("prints no value on the trigger and names the choices in its tooltip", () => {
    renderPill({ sources: [source()], selectedSourceId: "sample-data" });
    expect(trigger().textContent).toBe("");
    expect(trigger()).toHaveAttribute("title", "Sample Data · Shared Serverless · Manual");
  });

  it("says None is one inseparable state when nothing is engaged", () => {
    renderPill();
    expect(trigger()).toHaveAttribute("title", "None — no computed data");
  });

  it("is one menu of items with no section headings: Data source, Compute, Automatic, then Refresh", () => {
    renderPill();
    fireEvent.click(trigger());
    expect(document.querySelectorAll(".mw-selpill-group")).toHaveLength(0);
    const items = [...document.querySelectorAll(".mw-selpill-list > *")];
    expect(items.map((i) => (i.textContent ?? "").trim())).toEqual([
      expect.stringMatching(/^Data source/), expect.stringMatching(/^Compute/),
      expect.stringMatching(/^Automatic/), "Refresh"]);
  });

  it("holds the data source behind one item that names it, and closes on a pick", () => {
    const onSourceChange = vi.fn();
    renderPill({ sources: [source()], onSourceChange });
    fireEvent.click(trigger());
    const row = screen.getByRole("button", { name: /^Data source:/ });
    expect(row).toHaveAccessibleName("Data source: None");
    expect(screen.queryByRole("listbox", { name: "Data source" })).toBeNull();
    fireEvent.click(row);
    fireEvent.click(within(screen.getByRole("listbox", { name: "Data source" })).getByRole("option", { name: /Sample Data/ }));
    expect(onSourceChange).toHaveBeenCalledWith("sample-data");
    // Picking a state IS a decision, so it closes the pill.
    expect(screen.queryByRole("button", { name: /^Data source:/ })).toBeNull();
  });

  it("opens one submenu at a time", () => {
    renderPill({ sources: [source()] });
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("button", { name: /^Data source:/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Compute:/ }));
    expect(screen.queryByRole("listbox", { name: "Data source" })).toBeNull();
    expect(screen.getByRole("listbox", { name: "Compute" })).toBeInTheDocument();
  });
});

describe("the Compute submenu (APP-COMPUTE-TARGET)", () => {
  const computeRow = () => screen.getByRole("button", { name: /^Compute:/ });

  it("is one row naming the warehouse, the default when nothing is chosen", () => {
    renderPill();
    fireEvent.click(trigger());
    expect(computeRow()).toHaveAccessibleName("Compute: Shared Serverless");
    expect(screen.queryByRole("listbox", { name: "Compute" })).toBeNull();
    fireEvent.click(computeRow());
    expect(within(screen.getByRole("listbox", { name: "Compute" })).getByRole("option", { name: /Shared Serverless/ }))
      .toHaveTextContent("Default");
  });

  it("opens the warehouse list beside it, and reports a pick", () => {
    const onWarehouseChange = vi.fn();
    renderPill({ warehouseId: "wh-big", onWarehouseChange, sources: [source()], selectedSourceId: "sample-data" });
    expect(trigger()).toHaveAttribute("title", "Sample Data · Large · Manual");
    fireEvent.click(trigger());
    fireEvent.click(computeRow());
    const compute = screen.getByRole("listbox", { name: "Compute" });
    expect(within(compute).getByRole("option", { name: /Large/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(compute).getByRole("option", { name: /Shared Serverless/ }));
    expect(onWarehouseChange).toHaveBeenCalledWith("wh-default");
    expect(screen.queryByRole("listbox", { name: "Compute" })).toBeNull();
  });

  it("says why the list is empty instead of showing nothing", () => {
    renderPill({ warehouses: [], warehouseError: "signed-in caller required" });
    fireEvent.click(trigger());
    fireEvent.click(computeRow());
    expect(screen.getByText("SQL warehouses unavailable: signed-in caller required")).toBeInTheDocument();
  });
});

// APP-UPDATE-MODE — the one act sits at the foot of the menu whose choices it acts on.
describe("the Refresh button (APP-UPDATE-MODE)", () => {
  const refresh = () => screen.getByRole("button", { name: "Refresh" });

  it("is the one act: a button, not a row, and there is no Run", () => {
    const onRefresh = vi.fn();
    renderPill({ onRefresh });
    fireEvent.click(trigger());
    expect(refresh()).not.toHaveClass("mw-selpill-row");
    expect(refresh()).toHaveAttribute("title", "Recompute every define from the current data.");
    expect(screen.queryByRole("button", { name: /^Run/ })).toBeNull();
    expect(screen.queryByText(/Recompute everything/)).toBeNull();
    fireEvent.click(refresh());
    expect(onRefresh).toHaveBeenCalledTimes(1);
    // Acting closes the pill.
    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  });

  it("is disabled when nothing can run", () => {
    renderPill({ runDisabled: true });
    fireEvent.click(trigger());
    expect(refresh()).toBeDisabled();
  });
});

// APP-UPDATE-MODE — the status lives at the head of the dropdown, not beside it in the toolbar.
describe("the run status (APP-UPDATE-MODE)", () => {
  it("is announced from the head of the open popover", () => {
    renderPill({ stageBadge: <span className="badge">Not computed. Press Refresh</span> });
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(trigger());
    expect(screen.getByRole("status")).toHaveTextContent("Not computed. Press Refresh");
  });
});

describe("runStatusText (APP-UPDATE-MODE)", () => {
  it("says why nothing is shown, and never says Idle", () => {
    const texts = ([
      { kind: "not-computed" }, { kind: "changed" }, { kind: "running", done: 2, total: 5 },
      { kind: "running" }, { kind: "failed" }, { kind: "shown" }, { kind: "checking" }, { kind: "no-source" },
    ] as RunStatus[]).map(runStatusText);
    expect(texts.slice(0, 6)).toEqual(["Not computed. Press Refresh", "Calculation changed since last run",
      "Running 2/5", "Running…", "Failed. Retry", "Last Data Refresh"]);
    expect(texts.join(" ")).not.toMatch(/idle/i);
  });

  it("states when the shown results were computed, as a second line", () => {
    const shown: RunStatus = { kind: "shown", computedAt: "2026-10-10T14:56:00" };
    expect(runStatusText(shown)).toBe("Last Data Refresh");
    expect(runStatusWhen(shown)).toMatch(/Oct 10.*2:56/);
    expect(runStatusWhen({ kind: "shown" })).toBe("Time not recorded");
    expect(runStatusWhen({ kind: "changed" })).toBeUndefined();
  });
});

// APP-UPDATE-MODE — two options, exactly one selected, so the state is never ambiguous.
// APP-UPDATE-MODE — one checkable item. Unchecked is Manual; there is no Manual item to pick.
describe("the Automatic item (APP-UPDATE-MODE)", () => {
  const automatic = () => screen.getByRole("menuitemcheckbox", { name: /Automatic/ });

  it("is unchecked by default, a plain row with no tick and no form control, and offers no Manual item", () => {
    renderPill({ updateMode: storedUpdateMode(undefined) });
    fireEvent.click(trigger());
    expect(automatic()).toHaveAttribute("aria-checked", "false");
    expect(automatic()).not.toHaveClass("on");
    expect(automatic().querySelector(".tick, .box, .dot, input")).toBeNull();
    expect(screen.queryByText("Manual")).toBeNull();
    expect(trigger()).toHaveAttribute("title", "None — no computed data");
  });

  it("is checked, with the accent wash and the tick, in Automatic", () => {
    renderPill({ updateMode: "live", sources: [source()], selectedSourceId: "sample-data" });
    expect(trigger()).toHaveAttribute("title", "Sample Data · Shared Serverless · Automatic");
    fireEvent.click(trigger());
    expect(automatic()).toHaveAttribute("aria-checked", "true");
    expect(automatic()).toHaveClass("on");
    expect(automatic().querySelector(".tick")).not.toBeNull();
  });

  it("toggles between Automatic and Manual", () => {
    const onUpdateModeChange = vi.fn();
    const { unmount } = renderPill({ onUpdateModeChange });
    fireEvent.click(trigger());
    fireEvent.click(automatic());
    expect(onUpdateModeChange).toHaveBeenCalledWith("live");
    unmount();
    renderPill({ updateMode: "live", onUpdateModeChange });
    fireEvent.click(trigger());
    fireEvent.click(automatic());
    expect(onUpdateModeChange).toHaveBeenLastCalledWith("paused");
  });

  it("migrates stored state: only an explicit `live` stays Automatic", () => {
    expect(storedUpdateMode("live")).toBe("live");
    expect(storedUpdateMode("paused")).toBe("paused");
    expect(storedUpdateMode(undefined)).toBe("paused");
    expect(storedUpdateMode("something-else")).toBe("paused");
  });
});

// DATA-SOURCE-DESCRIPTION — a source row's subtitle is the schema's own comment about the DATA. These pin
// that nothing else is printed there: the record's metadata (the catalog.schema, the size of the binding
// map) describes the record, and a binding count in the position of a figure reads as a population size.
// The record is the tagged-schema shape (DATA-SOURCE-UC-TAG): no class, status or libraries.
function source(over: Partial<DataSourceRecord> = {}): DataSourceRecord {
  return {
    id: "sample-data", name: "Sample Data", catalog: "acme", schema: "demo_population",
    dataBindings: { Patient: { function: "acme.demo_population.retrieve_Patient" },
                    Condition: { function: "acme.demo_population.retrieve_Condition" } },
    ...over,
  };
}

describe("source subtitles (DATA-SOURCE-DESCRIPTION)", () => {
  it("prints the description under the source's name", () => {
    renderPill({ sources: [source({ description: "Synthetic patients with an example condition." })] });
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("button", { name: /^Data source:/ }));
    const row = screen.getByRole("option", { name: /Sample Data/ });
    expect(row).toHaveTextContent("Synthetic patients with an example condition.");
  });

  it("says nothing rather than describing the registry when no description was written", () => {
    renderPill({ sources: [source()] });
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("button", { name: /^Data source:/ }));
    const row = screen.getByRole("option", { name: /Sample Data/ });
    expect(row.querySelector(".bl")).toBeNull();
    expect(row).not.toHaveTextContent("acme.demo_population");
    expect(row).not.toHaveTextContent("bindings");
  });
});

// APP-COMPUTE-ACTIVITY — the collapsed trigger reports how far a run has got, so the reader does not
// have to open the pill to find out whether waiting is worth it. The hairline is painted from
// `--run-pct`, so these assert the property rather than a rendered pixel.
describe("run hairline on the collapsed trigger (APP-COMPUTE-ACTIVITY)", () => {
  const pct = () => trigger().style.getPropertyValue("--run-pct");

  it("states the progress it was given while running", () => {
    renderPill({ running: true, progress: 0.42 });
    expect(pct()).toBe("42%");
  });

  it("paints nothing when nothing is running", () => {
    renderPill({ running: false, progress: 0.42 });
    expect(pct()).toBe("");
  });

  it("states ABSENCE rather than zero when a run carries no figure", () => {
    // SPEC-METRIC — an unset property leaves the track empty; `0%` would be the claim "no progress yet",
    // which is a different assertion from "this stage has no count to report".
    renderPill({ running: true, progress: null });
    expect(pct()).toBe("");
  });
});
