// RUN-CLIENT-RESULT-SETS — synthetic reader responses cannot assemble mixed selections.
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSelectionDerivations, type SelectionDerivationRequest } from "./selection-derivations.ts";

const api = vi.hoisted(() => ({ fetchMeasureMetrics: vi.fn(), fetchReadMetrics: vi.fn(), fetchCohortFlow: vi.fn() }));
vi.mock("../library-client.ts", () => api);

const request: SelectionDerivationRequest = {
  library: "Synthetic", version: "1",
  scope: { dataSourceId: "synthetic", periodStart: "2026-01-01", periodEnd: "2026-12-31", draft: "durable.first" },
  defines: [{ name: "Output", library: "Synthetic", resultType: "Boolean", cohort: { allOf: [], noneOf: [] } }],
  reads: [], gates: [], scoring: null,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
beforeEach(() => { Object.values(api).forEach((fn) => fn.mockReset()); });

describe("complete-selection derivations (RUN-CLIENT-RESULT-SETS)", () => {
  it("never discovers figures without a selected handle", () => {
    const { result } = renderHook(() => useSelectionDerivations(null));
    expect(result.current.metrics).toBeNull();
    expect(api.fetchMeasureMetrics).not.toHaveBeenCalled();
  });

  it("drops old figures immediately and ignores late responses from another selection", async () => {
    const old = deferred<object>();
    api.fetchMeasureMetrics.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ selection: "second" });
    const { result, rerender } = renderHook(({ input }) => useSelectionDerivations(input), {
      initialProps: { input: request },
    });
    rerender({ input: { ...request, scope: { ...request.scope, draft: "durable.second" } } });
    expect(result.current.metrics).toBeNull();
    await waitFor(() => expect(result.current.metrics).toEqual({ selection: "second" }));
    await act(async () => { old.resolve({ selection: "first" }); });
    expect(result.current.metrics).toEqual({ selection: "second" });
    expect(api.fetchMeasureMetrics.mock.calls[1]![3].draft).toBe("durable.second");
  });

  it("publishes counts and metrics together, never a partial measure", async () => {
    const reads = deferred<object>();
    api.fetchMeasureMetrics.mockResolvedValue({ selection: "first" });
    api.fetchReadMetrics.mockReturnValue(reads.promise);
    const input = { ...request, reads: [{ key: "patients", type: "Patient", profiles: [] }] };
    const { result } = renderHook(() => useSelectionDerivations(input));
    await act(async () => {});
    expect(result.current.metrics).toBeNull();
    await act(async () => { reads.resolve({ reads: [] }); });
    expect(result.current.metrics).toEqual({ selection: "first" });
    expect(result.current.reads).toEqual({ reads: [] });
  });

  it("shows no figures and no error when the selection has none, never a partial measure", async () => {
    api.fetchMeasureMetrics.mockResolvedValue(null);
    api.fetchReadMetrics.mockResolvedValue({ reads: [] });
    const input = { ...request, reads: [{ key: "patients", type: "Patient", profiles: [] }] };
    const { result } = renderHook(() => useSelectionDerivations(input));
    await waitFor(() => expect(api.fetchReadMetrics).toHaveBeenCalled());
    await act(async () => {});
    expect(result.current).toEqual({ metrics: null, reads: null, flow: null, error: null });
  });

  it("reports a failed read with the server's error, without retaining a partial measure", async () => {
    api.fetchMeasureMetrics.mockRejectedValue(new Error("warehouse unavailable"));
    const { result } = renderHook(() => useSelectionDerivations(request));
    await waitFor(() => expect(result.current.error).toBe("warehouse unavailable"));
    expect(result.current.metrics).toBeNull();
  });
});
