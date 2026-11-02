// SVC-RUN-STAGE / RUN-PROGRESS-TASKS (PROC-TDD) — the streaming run's two reads of the SSE frames:
// what the FINAL RESULT is built from, and what the compute gauge measures.
//
// Both regressions these cover have the same cause — the frontend reads a vocabulary only the in-process runner
// sent — and both are invisible to the type system, so the frames below are
// transcribed from what `/api/run-stream` actually emits rather than from the TS interface.
import { webcrypto } from "node:crypto";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { RunStage } from "../wire.ts";
import { designerInstanceId, fetchWarehouses, reopenResults, runMeasure, runMeasureStreaming, runResultFromDone } from "../run-client.ts";
import { stageLabel, stageProgress } from "./Inspector.tsx";

/** A completed `done` frame exactly as `app.py` builds it (note `results`, not `result`). */
const DONE: RunStage = {
  stage: "done",
  status: "completed",
  results: {
    // No year segment: `RUN-SCHEMA-NAME` is specified and unimplemented, so the writer names
    // `{library}_{version}_{eval_hash}`. This fixture claims fidelity to `app.py` above, and a
    // year here would teach an address the backend never produces.
    schema: "acme_results.examplemeasure_1_0_0_081b9dc0",
    defines: [
      { name: "initial_population", define: "Initial Population", subjects: 67 },
      { name: "denominator", define: "Denominator", subjects: 67 },
      { name: "denominator_exclusions", define: "Denominator Exclusions", subjects: 28 },
      { name: "numerator", define: "Numerator", subjects: 2 },
    ],
  },
};

const task = (taskKey: string, status: "pending" | "running" | "completed" | "skipped" | "failed") =>
  ({ taskKey, status });

describe("reopenResults (RUN-CLIENT-REQUEST-REOPEN)", () => {
  const address = { destination: ["synthetic_private", "cql_private_x"] as [string, string], publisher: "synthetic-user",
    session_id: "tab", selection_id: "complete", request_hash: "request", purpose: "private" as const,
    population: "synthetic", targets: ["Output"], computed_since: null };
  afterEach(() => { vi.unstubAllGlobals(); });

  it("opens the caller's own results read-only, with when they were computed", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ found: true, results: DONE.results,
      resultSet: address, identity: { library: "Synthetic", version: "1", dataSource: "synthetic", draft: "durable.x" },
      outcomes: [{ definition: "a", decision: "reused", reason: "", completedAt: "2026-10-09T08:00:00+00:00" },
                 { definition: "b", decision: "reused", reason: "", completedAt: "2026-10-10T09:30:00+00:00" }] })));
    vi.stubGlobal("fetch", fetcher);
    const opened = await reopenResults("synthetic CQL", { environment: "synthetic", periodStart: "2026-01-01",
      periodEnd: "2026-12-31", warehouseId: "wh-chosen" });
    expect(opened.found).toBe(true);
    if (!opened.found) return;
    expect(opened.result.draft).toBe("durable.x");
    expect(opened.result.computedAt).toBe("2026-10-10T09:30:00+00:00");
    expect(opened.result.counts?.Numerator).toBe(2);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/reopen");
    expect(JSON.parse(init.body)).toEqual({ cql: "synthetic CQL", environment: "synthetic",
      periodStart: "2026-01-01", periodEnd: "2026-12-31", warehouseId: "wh-chosen", location: null });
    expect(init.headers).toMatchObject({ "x-designer-instance": designerInstanceId() });
  });

  it("returns a miss as an answer and a failure as an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ found: false, reason: "nothing computed yet" }))));
    expect(await reopenResults("CQL", { environment: "synthetic" })).toEqual({ found: false, reason: "nothing computed yet" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "PERMISSION_DENIED: no CAN_USE" }), { status: 502 })));
    await expect(reopenResults("CQL", { environment: "synthetic", warehouseId: "wh-denied" })).rejects.toThrow("no CAN_USE");
  });
});

describe("fetchWarehouses (APP-COMPUTE-TARGET)", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("returns the caller's warehouses and the deployment default, and rejects with the server's reason", async () => {
    const list = { default: "wh-default", warehouses: [{ id: "wh-default", name: "Shared", state: "RUNNING" }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(list))));
    expect(await fetchWarehouses()).toEqual(list);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "signed-in caller required" }), { status: 502 })));
    await expect(fetchWarehouses()).rejects.toThrow("signed-in caller required");
  });
});

describe("runResultFromDone (SVC-RUN-STAGE)", () => {
  it("reports a completed run as ok — the regression that showed an error over correct numbers", () => {
    const r = runResultFromDone(DONE);
    expect(r.ok).toBe(true);
    expect(r.error).toBeUndefined();
  });

  it("keys counts by CANONICAL POPULATION so the funnel can read them", () => {
    // Keyed by the raw define name instead, every count is present and the funnel renders nothing.
    expect(runResultFromDone(DONE).counts).toEqual({
      "Initial Population": 67, "Denominator": 67, "Denominator Exclusions": 28, "Numerator": 2,
    });
  });

  it("refuses an incomplete done frame (RUN-CLIENT-RESULT-SETS)", () => {
    const r = runResultFromDone({ stage: "done", status: "completed" });
    expect(r.ok).toBe(false);
    expect(r.error).toContain("complete result selection");
  });

  it("names a timeout as a timeout, not as a lost stream", () => {
    const r = runResultFromDone({ stage: "done", status: "timeout" });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/still going/);
  });

  it("names a failed Job", () => {
    const r = runResultFromDone({ stage: "done", status: "failed" });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/failed/);
  });
});

describe("runMeasureStreaming (SVC-RUN-STAGE)", () => {
  beforeEach(() => {
    sessionStorage.removeItem("cql.refresh-intent.v1");
    vi.stubGlobal("crypto", webcrypto);
  });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  /** Serve `frames` as an SSE body from a stubbed `fetch`. */
  function stubStream(frames: object[]) {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        const enc = new TextEncoder();
        for (const f of frames) c.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
        c.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(body, { headers: { "content-type": "text/event-stream" } })));
  }

  it("resolves ok for the frame sequence a real run sends", async () => {
    // THE REPORTED BUG: this sequence resolved to "stream ended unexpectedly" on every success.
    stubStream([
      { stage: "compiling", message: "Creating per-library pipeline" },
      { stage: "profile", steps: [{ name: "compile_pipeline", elapsed_ms: 1200 }], total_ms: 1400 },
      { stage: "executing", status: "running", tasks: [task("a", "completed"), task("b", "running")] },
      DONE,
    ]);
    const seen: RunStage[] = [];
    const r = await runMeasureStreaming("library X version '1'", { environment: "deck", trigger: "automatic" },
      (s) => seen.push(s));
    expect(r.ok).toBe(true);
    expect(r.counts?.["Numerator"]).toBe(2);
    expect(seen.map((s) => s.stage)).toEqual(["compiling", "profile", "executing", "done"]);
  });

  it("reports a lost stream ONLY when no terminal frame arrived", async () => {
    stubStream([{ stage: "executing", status: "running", tasks: [task("a", "running")] }]);
    const r = await runMeasureStreaming("library X version '1'", { environment: "deck", trigger: "automatic" }, () => {});
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/closed the run stream/);
  });

  it("surfaces an explicit error frame's own message", async () => {
    stubStream([{ stage: "error", error: "RuntimeError: no SQL warehouse configured" }]);
    const r = await runMeasureStreaming("library X version '1'", { environment: "deck", trigger: "automatic" }, () => {});
    expect(r).toEqual({ ok: false, error: "RuntimeError: no SQL warehouse configured" });
  });

  it("retains the logical session and explicit refresh intent across matching retries (RUN-CLIENT-REFRESH-RESUME)", async () => {
    for (let retry = 0; retry < 2; retry++) {
      stubStream([{ stage: "error", error: "synthetic final publication failure" }]);
      await runMeasureStreaming("library Synthetic version '1'", {
        environment: "synthetic", recompute: true, computedSince: "2026-01-01T00:00:00Z", trigger: "refresh",
      }, () => {});
      const request = JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string);
      expect(request.recompute).toBe(true);
      expect(request.computed_since).toBe("2026-01-01T00:00:00Z");
      expect(request.session_id).toBe(designerInstanceId());
      expect(vi.mocked(fetch).mock.calls[0]![1]!.headers).toMatchObject({
        "x-designer-instance": designerInstanceId(),
      });
    }
  });

  it("retains an automatic cutoff after failure and module reload, then clears it only on success (RUN-CLIENT-REFRESH-RESUME)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    const cql = "library Synthetic version '1'";
    const opts = { environment: "synthetic", recompute: true, trigger: "refresh" as const };
    stubStream([{ stage: "error", error: "synthetic write failure" }]);
    await runMeasureStreaming(cql, opts, () => {});
    const saved = sessionStorage.getItem("cql.refresh-intent.v1")!;
    expect(saved).not.toContain(cql);
    expect(JSON.parse(saved).cutoff).toBe("2026-01-01T00:00:00.000Z");
    vi.setSystemTime(new Date("2026-01-02T00:00:00Z"));
    vi.resetModules();
    const fresh = await import("../run-client.ts");
    stubStream([DONE]);
    // A plain compute retry cannot silently abandon an unfinished refresh.
    await fresh.runMeasureStreaming(cql, { environment: "synthetic", trigger: "automatic" }, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).computed_since)
      .toBe("2026-01-01T00:00:00.000Z");
    expect(sessionStorage.getItem("cql.refresh-intent.v1")).toBeNull();
    stubStream([DONE]);
    await runMeasureStreaming(cql, opts, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).computed_since)
      .toBe("2026-01-02T00:00:00.000Z");
  });

  it("does not apply another period's pending cutoff (RUN-CLIENT-REFRESH-RESUME)", async () => {
    stubStream([{ stage: "error", error: "synthetic failure" }]);
    await runMeasureStreaming("synthetic CQL", { environment: "synthetic", periodStart: "2026-01-01",
      recompute: true, computedSince: "2026-01-01T00:00:00Z", trigger: "refresh" }, () => {});
    stubStream([DONE]);
    await runMeasureStreaming("synthetic CQL", { environment: "synthetic", periodStart: "2025-01-01", trigger: "automatic" }, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).computed_since).toBeUndefined();
  });

  it("uses the same saved intent for the non-streaming entry point (RUN-CLIENT-REFRESH-RESUME)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ ok: false, error: "synthetic failure" }))));
    await runMeasure("synthetic CQL", { recompute: true, computedSince: "2026-01-01T00:00:00Z", trigger: "refresh" });
    vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify({ ok: true })));
    await runMeasure("synthetic CQL", { trigger: "automatic" });
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1]![1]!.body as string).computed_since)
      .toBe("2026-01-01T00:00:00Z");
    expect(sessionStorage.getItem("cql.refresh-intent.v1")).toBeNull();
  });

  it("refuses submission if refresh intent cannot be saved (RUN-CLIENT-REFRESH-RESUME)", async () => {
    vi.stubGlobal("fetch", vi.fn());
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => { throw new Error("storage unavailable"); },
    });
    await expect(runMeasureStreaming("synthetic CQL", { recompute: true, trigger: "refresh" }, () => {}))
      .rejects.toThrow("storage unavailable");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("does not silently lose malformed saved intent (RUN-CLIENT-REFRESH-RESUME)", async () => {
    sessionStorage.setItem("cql.refresh-intent.v1", "null");
    vi.stubGlobal("fetch", vi.fn());
    await expect(runMeasureStreaming("synthetic CQL", { recompute: true, trigger: "refresh" }, () => {}))
      .rejects.toThrow("Invalid saved refresh intent");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("preserves the complete-selection handle for every later reader (RUN-CLIENT-RESULT-SETS)", async () => {
    const handle = "durable.synthetic-selection";
    const resultSet = { destination: ["synthetic", "results"] as [string, string], publisher: "synthetic-owner",
      session_id: "synthetic-session", selection_id: "synthetic-selection", request_hash: "request",
      purpose: "private" as const, population: "synthetic", targets: ["Output"], computed_since: null };
    stubStream([{ ...DONE, resultSet,
      identity: { library: "Synthetic", version: "1", dataSource: "synthetic", draft: handle } }]);
    const result = await runMeasureStreaming("library Synthetic version '1'", { environment: "synthetic", trigger: "automatic" }, () => {});
    expect(result.draft).toBe(handle);
    expect(result.resultSet).toEqual(resultSet);
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).recompute).toBe(false);
  });

  it("sends what started the run (APP-RUN-LOG)", async () => {
    stubStream([DONE]);
    await runMeasureStreaming("synthetic CQL", { environment: "synthetic", trigger: "automatic" }, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).trigger).toBe("automatic");
    stubStream([DONE]);
    await runMeasureStreaming("synthetic CQL", { environment: "synthetic", recompute: true, trigger: "refresh" }, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).trigger).toBe("refresh");
  });

  it("sends the chosen warehouse, and none when the deployment default applies (APP-COMPUTE-TARGET)", async () => {
    stubStream([DONE]);
    await runMeasureStreaming("synthetic CQL", { environment: "synthetic", warehouseId: "wh-chosen", trigger: "automatic" }, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).warehouse_id).toBe("wh-chosen");
    stubStream([DONE]);
    await runMeasureStreaming("synthetic CQL", { environment: "synthetic", trigger: "automatic" }, () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).warehouse_id).toBeUndefined();
  });

});

describe("stageProgress (RUN-PROGRESS-TASKS)", () => {
  it("never goes BACKWARDS across the sequence a real run sends", () => {
    // The bug: `profile` matched no branch and scored 0, so the bar snapped back to empty mid-run.
    const frames: RunStage[] = [
      { stage: "connecting" },
      { stage: "compiling" },
      { stage: "profile", total_ms: 1400 },
      { stage: "executing", tasks: [task("a", "running"), task("b", "pending")] },
      { stage: "executing", tasks: [task("a", "completed"), task("b", "running")] },
      { stage: "executing", tasks: [task("a", "completed"), task("b", "completed")] },
      DONE,
    ];
    const pcts = frames.map(stageProgress);
    expect(pcts).toEqual([...pcts].sort((x, y) => x - y));
    expect(pcts.at(-1)).toBe(1);
  });

  it("advances WITHIN the compute phase, instead of pinning at one value", () => {
    // The failure this guards against: `executing` carries no step/total, so the band would be
    // 0.30 + 0.10*0 for the whole Job.
    const at = (done: number, total: number) => stageProgress({
      stage: "executing",
      tasks: Array.from({ length: total }, (_, i) => task(`t${i}`, i < done ? "completed" : "pending")),
    });
    expect(at(0, 10)).toBeLessThan(at(5, 10));
    expect(at(5, 10)).toBeLessThan(at(9, 10));
    expect(at(10, 10)).toBeCloseTo(0.99, 5);
  });

  it("counts every terminal task state as progress, so a stopped run can reach the end", () => {
    const tasks = [task("a", "completed"), task("b", "skipped"), task("c", "failed")];
    expect(stageProgress({ stage: "executing", tasks })).toBeCloseTo(0.99, 5);
  });

  it("holds at the compute floor before the Job has reported its task graph", () => {
    // Not 0: the run is demonstrably underway, and an empty list is "not reported yet".
    expect(stageProgress({ stage: "executing", tasks: [] })).toBe(0.15);
    expect(stageProgress({ stage: "executing" })).toBe(0.15);
  });
});

describe("stageLabel (RUN-PROGRESS-TASKS)", () => {
  it("states how many barriers of how many have finished", () => {
    expect(stageLabel({
      stage: "executing",
      tasks: [task("a", "completed"), task("b", "completed"), task("c", "running")],
    })).toBe("computing 2/3");
  });

  it("says the run is starting when the task graph has not arrived", () => {
    expect(stageLabel({ stage: "executing" })).toBe("starting compute…");
  });
});
