// PHASE-S5 / APP-EVALUATION, SVC-RUN-EVAL — the SPA's client over the run endpoint (via the backend).
//
// RUN-CLIENT-RESULT-SETS — the backend compiles and materializes through the durable client under
// caller SQL credentials. It returns one complete selection and its counts, never partial outputs.

import type { EnvironmentRecord, PreComputedResults, ResultSetAddress, RunOutcome, RunResult,
  RunStage, WarehouseList } from "./wire.ts";
// PERF-CLIENT-REVISION — reads go through the data cache; WRITES go through `mutate`, which reads the
// revision off the response so a successful write invalidates immediately rather than at the next
// refocus. The live reads below (`/describe`, `/resolve`, `/status`, the SSE streams) stay plain
// `fetch`: none is on the server's cacheable allowlist, because each answers about THIS instant.
import { cachedGet, cachedPost, mutate } from "./data-cache.ts";
import { preComputedToRunResult } from "./measures/model.ts";

/** RUN-CLIENT-REFRESH-RESUME — a logical session survives reload in this tab.
 *  It is a label only: retry callers must retain computedSince, not rely on a server-side index. */
const DESIGNER_INSTANCE_ID: string = (() => {
  try {
    const saved = sessionStorage.getItem("cql.calculation-session");
    if (saved) return saved;
    const id = crypto.randomUUID();
    sessionStorage.setItem("cql.calculation-session", id);
    return id;
  } catch {
    /* fall through */
  }
  return `inst-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
})();

export function designerInstanceId(): string {
  return DESIGNER_INSTANCE_ID;
}

/** The CQL context a measure declares (`context Patient` / `context Unfiltered`), so the run evaluates in
 *  the same context it was authored in. Defaults to Patient (the common eCQM case) when undeclared. */
export function contextFromSource(cql: string): string {
  const m = /\bcontext\s+([A-Za-z_]\w*)/.exec(cql);
  return m?.[1] ?? "Patient";
}

export interface RunOptions {
  /** RUN-CLIENT-REFRESH-RESUME — retry callers must pass the same explicit computedSince. */
  recompute?: boolean;
  computedSince?: string;
  /** Restrict to specific defines; omit to run every define in the library. */
  defines?: string[];
  /** DOC-ENVIRONMENT-PICKER — the environment the calculation runs in; omitted means the default. */
  environment?: string;
  /** PIPE-MEASUREMENT-PERIOD: the measurement period start date (YYYY-MM-DD). */
  periodStart?: string;
  /** PIPE-MEASUREMENT-PERIOD: the measurement period end date (YYYY-MM-DD). */
  periodEnd?: string;
  /** APP-COMPUTE-TARGET — the SQL warehouse to run on; omitted means the deployment's. */
  warehouseId?: string;
  /** RUN-ADDRESS-LOCATION — where the library's includes resolve first; omitted means the environment's knowledge. */
  location?: string | null;
}

/** APP-RUN-LOG — the Designer action that started a run: Refresh, or Automatic computing what is missing.
 *  Recorded in the run log; the server requires it and never infers it. */
export type RunTrigger = "refresh" | "automatic";

/** A run's options: the calculation, plus what started it (APP-RUN-LOG). */
export interface RunCallOptions extends RunOptions {
  trigger: RunTrigger;
}

/** RUN-CLIENT-REFRESH-RESUME — retain failed refresh intent in this tab, not a server index.
 *  Store only a request digest and cutoff, not CQL, compiled plans or result data. */
async function refreshIntent(cql: string, opts: RunOptions) {
  const key = "cql.refresh-intent.v1";
  const stored = sessionStorage.getItem(key);
  const pending = stored ? JSON.parse(stored) as { request: string; cutoff: string } : undefined;
  if (stored !== null && (!pending || typeof pending.request !== "string" || typeof pending.cutoff !== "string")) {
    throw new Error("Invalid saved refresh intent; clear this tab's session storage before recomputing.");
  }
  let record: string | undefined;
  let cutoff = opts.computedSince;
  if (pending || opts.recompute || cutoff) {
    const request = JSON.stringify({ cql, defines: opts.defines, environment: opts.environment,
      periodStart: opts.periodStart, periodEnd: opts.periodEnd, location: opts.location ?? null });
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(request));
    const hash = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
    cutoff ??= pending?.request === hash ? pending.cutoff : opts.recompute ? new Date().toISOString() : undefined;
    if (cutoff) {
      record = JSON.stringify({ request: hash, cutoff });
      // A storage failure is an error before submission, not a non-resumable refresh fallback.
      sessionStorage.setItem(key, record);
    }
  }
  return {
    cutoff,
    finish(result: RunResult) {
      if (result.ok && record && sessionStorage.getItem(key) === record) sessionStorage.removeItem(key);
      return result;
    },
  };
}

/** DOC-ENVIRONMENT-PICKER — the environments the compute pill offers: the catalogs tagged
 *  `cql_databricks_environment` the signed-in user can see (/api/environments). Rejects with the server's
 *  message: an empty list would hide why the picker is empty. */
export async function fetchEnvironments(): Promise<EnvironmentRecord[]> {
  return await cachedGet("/api/environments", async (res) => {
    if (!res.ok) throw new Error(await errorText(res, "list environments"));
    return (await res.json()) as EnvironmentRecord[];
  });
}

/** SVC-RESOURCE-SCHEMA — the FHIR resource types the engine actually models, so the Data Sources editor offers
 *  only bindable entities (DATA-SOURCE-PROFILE, APP-NO-UI-MOCK). Degrades to [] on any failure OR a compiler
 *  fallback, so the editor uses its static list rather than an empty picker. */
export async function fetchModeledTypes(): Promise<string[]> {
  try {
    return await cachedGet("/api/modeled-types", async (res) => {
      if (!res.ok) return [];
      const body = (await res.json()) as { types?: string[]; fallback?: boolean };
      return body.fallback ? [] : (body.types ?? []);
    });
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Pre-computed results (PIPE-PRE-COMPUTED)
// ---------------------------------------------------------------------------

/** PIPE-PRE-COMPUTED — fetch pre-computed results for one EVALUATION of a library.
 *
 *  THE MEASUREMENT PERIOD IS PART OF THE ADDRESS, not an optional refinement. A results schema is keyed
 *  by `(library, evaluation)` and the evaluation is the environment plus the
 *  period, so the same measure over two years has two result sets. Sending the library and version alone
 *  named a schema no pipeline had ever written to, and this route returned an empty set every time.
 *
 *  Returns per-define counts at both grains. No funnel — the client derives it from these counts
 *  (`funnelFromCounts()`), which is what keeps the document's reported rate and the Analyze lens from
 *  printing two different scores. If nothing has been computed yet, the result set is empty (no error). */
export async function fetchResults(
  environment: string, library: string, version: string,
  periodStart?: string, periodEnd?: string, draft?: string, resultSet?: ResultSetAddress,
): Promise<PreComputedResults | null> {
  try {
    // RUN-ADDRESS-BY-LIBRARY — a library version's results are its own; a draft's are read by the key its
    // run returned, which is all the Designer knows of where they are.
    return await cachedPost("/api/results", { environment, library, version,
      periodStart, periodEnd, draft, resultSet }, async (res) =>
      res.ok ? ((await res.json()) as PreComputedResults) : null);
  } catch {
    return null;
  }
}

async function errorText(res: Response, verb: string): Promise<string> {
  try {
    const body = (await res.json()) as { error?: string };
    if (body?.error) return body.error;
  } catch {
    /* non-JSON body */
  }
  return `${verb} failed (${res.status})`;
}

/** RUN-CLIENT-REQUEST-REOPEN — the signed-in user's own results for exactly this calculation, if any.
 *
 *  Read-only: the server never computes, never creates the private schema, and never reads a Job's or
 *  another user's results (RUN-CLIENT-PUBLISHED, amended 2026-10-10). A miss resolves `found: false`
 *  with the server's reason; a failure (compile error, no access to the warehouse) rejects with its
 *  message, so the caller shows it rather than reading it as "not computed". */
export type ReopenResult = { found: true; result: RunResult } | { found: false; reason: string };

export async function reopenResults(cql: string, opts: RunOptions): Promise<ReopenResult> {
  const response = await fetch("/api/reopen", {
    method: "POST",
    headers: { "content-type": "application/json", "x-designer-instance": DESIGNER_INSTANCE_ID },
    body: JSON.stringify({ cql, environment: opts.environment, periodStart: opts.periodStart,
      periodEnd: opts.periodEnd, warehouseId: opts.warehouseId, location: opts.location ?? null }),
  });
  if (!response.ok) throw new Error(await errorText(response, "open results"));
  const body = await response.json() as { found: boolean; reason?: string } & RunStage;
  if (!body.found) return { found: false, reason: body.reason ?? "not computed" };
  const result = runResultFromDone({ ...body, stage: "done", status: "completed" });
  if (!result.ok) throw new Error(result.error ?? "The server did not return a complete result selection.");
  return { found: true, result };
}

/** APP-COMPUTE-TARGET — the SQL warehouses the signed-in user can see, and the deployment default.
 *  Rejects with the server's message: an empty list would hide why the Compute section is empty. */
export async function fetchWarehouses(): Promise<WarehouseList> {
  const response = await fetch("/api/warehouses");
  if (!response.ok) throw new Error(await errorText(response, "list SQL warehouses"));
  return await response.json() as WarehouseList;
}

/** APP-NO-UI-MOCK / APP-EXEC-MODE — the Synthetic funnel, computed on the BFF (not the SPA,
 *  which embeds no data). Deterministic + offline (no live-data connection; the BFF needs no compiler/Spark).
 *  Resolves with the RunResult shape the workspace renders; degrades to `ok:false` on any failure so the
 *  offline mode still surfaces a message rather than throwing. */
export async function sampleRun(cql: string): Promise<RunResult> {
  try {
    const res = await fetch("/api/sample-run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ cql }),
    });
    if (!res.ok) return { ok: false, error: `synthetic run failed (${res.status})` };
    return (await res.json()) as RunResult;
  } catch {
    return { ok: false, error: "backend unreachable" };
  }
}

/** Run a measure's CQL against the local target (SVC-RUN-EVAL). Resolves with the RunResult — including
 *  `ok:false` + `error`/`diagnostics` on a failed run — so the UI renders a clinical message rather than
 *  throwing. Rejects only on a transport failure (backend unreachable). */
export async function runMeasure(cql: string, opts: RunCallOptions): Promise<RunResult> {
  const intent = await refreshIntent(cql, opts);
  const overrides: Record<string, string> = {};
  if (opts.periodStart) overrides.periodStart = opts.periodStart;
  if (opts.periodEnd) overrides.periodEnd = opts.periodEnd;

  const body = {
    cql,
    recompute: opts.recompute ?? false,
    computed_since: intent.cutoff,
    session_id: DESIGNER_INSTANCE_ID,
    defines: opts.defines,
    environment: opts.environment,
    overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
    warehouse_id: opts.warehouseId,
    location: opts.location ?? null,
    trigger: opts.trigger,
  };
  const res = await fetch("/api/run", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      // SVC-SESSION-INSTANCE: bind this run to THIS instance's session.
      "x-designer-instance": DESIGNER_INSTANCE_ID,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let parsed: RunResult;
  try {
    parsed = JSON.parse(text) as RunResult;
  } catch {
    return { ok: false, error: `run failed (${res.status}): ${text.slice(0, 300)}` };
  }
  return intent.finish(parsed);
}

/** SVC-RUN-STAGE — run a measure with streaming progress. The backend sends SSE events as the
 *  run progresses (`compiling`, then one `executing` frame for the plan and one per finished definition,
 *  each carrying every definition's state). `onStage` is called for every progress event; the
 *  final `RunResult` is returned on completion.
 *
 *  **THE FINAL RESULT IS BUILT FROM THE `done` FRAME'S OWN FIELDS.** It used to be read from
 *  `event.result`, a key only the retired in-process runner ever sent — so the placeholder below
 *  survived every successful run and the Designer showed "stream ended unexpectedly" on top of
 *  correct numbers, while genuine failures (which do send `stage: "error"`) reported properly. The
 *  inversion is what made it look intermittent. `status` is now read as well, so a `timeout` or a
 *  `failed` Job says which it was instead of borrowing the disconnect message (`RUN-PROGRESS-TASKS`,
 *  `CORE-NO-SILENT-MISLOWER`). */
export async function runMeasureStreaming(
  cql: string,
  opts: RunCallOptions,
  onStage: (stage: RunStage) => void,
): Promise<RunResult> {
  const intent = await refreshIntent(cql, opts);
  const overrides: Record<string, string> = {};
  if (opts.periodStart) overrides.periodStart = opts.periodStart;
  if (opts.periodEnd) overrides.periodEnd = opts.periodEnd;

  const body = {
    cql,
    recompute: opts.recompute ?? false,
    computed_since: intent.cutoff,
    session_id: DESIGNER_INSTANCE_ID,
    defines: opts.defines,
    environment: opts.environment,
    overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
    warehouse_id: opts.warehouseId,
    trigger: opts.trigger,
  };
  const res = await fetch("/api/run-stream", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-designer-instance": DESIGNER_INSTANCE_ID,
    },
    body: JSON.stringify(body),
  });

  // Non-SSE response (error before streaming started) — parse as JSON.
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("text/event-stream")) {
    const text = await res.text();
    try {
      return intent.finish(JSON.parse(text) as RunResult);
    } catch {
      return { ok: false, error: `run failed (${res.status}): ${text.slice(0, 300)}` };
    }
  }

  if (!res.body) return { ok: false, error: "streaming not supported" };

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Reached only when the stream ends with no terminal frame at all — the backend went away
  // mid-run (a reload in dev, a proxy timeout). It is no longer the outcome of an ordinary run.
  let finalResult: RunResult = {
    ok: false,
    error: "The backend closed the run stream before it reported a result.",
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are delimited by double newlines.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6)) as RunStage;
        onStage(event);
        if (event.stage === "done") {
          finalResult = runResultFromDone(event);
        } else if (event.stage === "error") {
          finalResult = { ok: false, error: event.error ?? "run failed" };
        }
      } catch { /* ignore malformed SSE frames */ }
    }
  }

  return intent.finish(finalResult);
}

/** The `done` frame's `status` + `results` as a `RunResult` (`SVC-RUN-STAGE`).
 *
 *  `timeout` and `failed` are named rather than folded into one "run failed", because they send the
 *  reader somewhere different: a timeout means the Job is still going and the poll gave up after 300
 *  seconds, so the results may well arrive on their own; a failure means it stopped.
 *
 *  A completed run with NO `results` is still `ok` — the backend logs and swallows the results read
 *  (`app.py`, "Could not fetch results on fast path"), the funnel is populated independently by
 *  `PIPE-PRE-COMPUTED`, and reporting a run failure for a missing side-read would be the same
 *  inversion this function exists to remove.
 *
 *  The rows go through `preComputedToRunResult` rather than being mapped here, because keying
 *  `counts` is not a loop: the funnel reads canonical population names and a define is
 *  `normalizeResultName`d onto them. Keyed by the raw define name instead, every count would be
 *  present, well-formed, and invisible to `funnelFromCounts()`. One mapping, one place. */
export function runResultFromDone(event: RunStage): RunResult {
  if (event.status === "timeout") {
    return { ok: false, error: "The run is still going — CQL on Databricks stopped watching it after 5 minutes." };
  }
  if (event.status && event.status !== "completed") {
    return { ok: false, error: `The evaluation Job ${event.status}.` };
  }
  if (!event.results) return { ok: false, error: "The run did not return a complete result selection." };
  const result = { ...preComputedToRunResult({ defines: event.results.defines }), resultSet: event.resultSet,
    computedAt: latestCompletion(event.outcomes) };
  // RUN-ADDRESS-BY-LIBRARY — the reads of this run's results name its draft, when it is one.
  return event.identity?.draft ? { ...result, draft: event.identity.draft } : result;
}

/** APP-UPDATE-MODE — the newest successful computation among a selection's outputs, for "(<when>)". */
function latestCompletion(outcomes: RunOutcome[] | undefined): string | undefined {
  const times = (outcomes ?? []).map((o) => o.completedAt).filter(Boolean).sort();
  return times.length ? times[times.length - 1] : undefined;
}
