/** PERF-CLIENT-REVISION — the data cache serves from memory and dies on the server's revision.
 *
 *  The claim under test is two-sided: a read that the revision has not invalidated costs NO request
 *  (which is what makes a remount instant), and a revision that MOVES drops everything (which is what
 *  keeps that from being a second, weaker answer to "is this current").
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  REVISION_HEADER, cachedGet, cachedPost, clear, currentRevision, installRevisionRefresh,
  mutate, noteRevision, onRevisionChange, peek, refreshRevision,
} from "./data-cache.ts";

/** A fetch stub that counts calls and stamps every response with `revision`.
 *
 *  Args are declared so `mock.calls` is typed — the tests assert on the URL and the request init, and
 *  an argument-less stub types those as an empty tuple.
 */
function stubFetch(revision: string, body: unknown = { ok: true }) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), {
    status: 200,
    headers: { [REVISION_HEADER]: revision, "Content-Type": "application/json" },
  }));
  global.fetch = fn as unknown as typeof fetch;
  return fn;
}

const json = async (res: Response) => res.json();

beforeEach(() => clear());
afterEach(() => vi.restoreAllMocks());

describe("serving from memory", () => {
  it("makes ONE request for repeated reads of the same key", async () => {
    const fn = stubFetch("rev-1", { n: 1 });

    const a = await cachedGet("/api/libraries/discover", json);
    const b = await cachedGet("/api/libraries/discover", json);

    expect(a).toEqual({ n: 1 });
    expect(b).toEqual({ n: 1 });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("coalesces concurrent reads into one request", async () => {
    // Two components mounting in the same tick must not both hit the warehouse.
    const fn = stubFetch("rev-1");
    await Promise.all([
      cachedGet("/api/libraries/discover", json),
      cachedGet("/api/libraries/discover", json),
      cachedGet("/api/libraries/discover", json),
    ]);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("keys separate URLs separately", async () => {
    const fn = stubFetch("rev-1");
    await cachedGet("/api/libraries/a/1/source", json);
    await cachedGet("/api/libraries/b/1/source", json);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe("the POST reads, which no HTTP cache can touch", () => {
  it("caches a POST read by its body — the body IS the question", async () => {
    const fn = stubFetch("rev-1", { subjects: 5 });
    const url = "/api/libraries/ExampleMeasure/1.0.0/metrics";

    await cachedPost(url, { defines: ["Numerator"] }, json);
    await cachedPost(url, { defines: ["Numerator"] }, json);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("treats a different body as a different question", async () => {
    const fn = stubFetch("rev-1");
    const url = "/api/libraries/ExampleMeasure/1.0.0/metrics";

    await cachedPost(url, { defines: ["Numerator"] }, json);
    await cachedPost(url, { defines: ["Denominator"] }, json);

    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("sends the same bytes it keys on", async () => {
    const fn = stubFetch("rev-1");
    await cachedPost("/api/x", { gates: [1, 2] }, json);
    const init = fn.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ gates: [1, 2] }));
  });
});

describe("the revision is the only thing that keeps an entry alive", () => {
  it("drops everything when the revision moves", async () => {
    const fn = stubFetch("rev-1");
    await cachedGet("/api/libraries/discover", json);
    expect(fn).toHaveBeenCalledTimes(1);

    // A run completed: the server reports a new revision on its next response.
    noteRevision(new Headers({ [REVISION_HEADER]: "rev-2" }));

    await cachedGet("/api/libraries/discover", json);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("drops entries it never asked about — the revision is GLOBAL", async () => {
    stubFetch("rev-1");
    await cachedGet("/api/libraries/a/1/source", json);
    await cachedPost("/api/libraries/a/1/metrics", { defines: [] }, json);

    noteRevision(new Headers({ [REVISION_HEADER]: "rev-2" }));

    expect(peek("GET /api/libraries/a/1/source")).toBeUndefined();
    expect(peek('POST /api/libraries/a/1/metrics {"defines":[]}')).toBeUndefined();
  });

  it("holds entries while the revision stands", async () => {
    const fn = stubFetch("rev-1");
    await cachedGet("/api/libraries/discover", json);
    noteRevision(new Headers({ [REVISION_HEADER]: "rev-1" }));
    await cachedGet("/api/libraries/discover", json);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("ignores a response with no revision header rather than reading it as a change", async () => {
    // A dev proxy, a non-/api route, or an older server. Absent is NO INFORMATION — treating it as a
    // change would clear the cache on every such response and defeat the whole mechanism.
    const fn = stubFetch("rev-1");
    await cachedGet("/api/libraries/discover", json);

    noteRevision(new Headers({}));

    await cachedGet("/api/libraries/discover", json);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(currentRevision()).toBe("rev-1");
  });

  it("survives a response with no headers object at all", () => {
    // Learning the revision runs on the SUCCESS path of every request, so it must not be able to turn
    // "could not read a header" into "the read failed" — callers that degrade on failure would then
    // report an empty result for a request that actually worked.
    expect(() => noteRevision(undefined)).not.toThrow();
    expect(() => noteRevision(null)).not.toThrow();
    expect(() => noteRevision({} as unknown as Headers)).not.toThrow();
  });

  it("notifies subscribers so a live view can refetch", async () => {
    stubFetch("rev-1");
    await cachedGet("/api/libraries/discover", json);

    const seen = vi.fn();
    const off = onRevisionChange(seen);
    noteRevision(new Headers({ [REVISION_HEADER]: "rev-2" }));
    expect(seen).toHaveBeenCalledTimes(1);

    off();
    noteRevision(new Headers({ [REVISION_HEADER]: "rev-3" }));
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("does not clear on the FIRST revision it ever sees", async () => {
    // `null` is "unknown", not "stale": before any response there is nothing cached to be wrong about.
    const seen = vi.fn();
    onRevisionChange(seen);
    noteRevision(new Headers({ [REVISION_HEADER]: "rev-1" }));
    expect(seen).not.toHaveBeenCalled();
  });
});

describe("a write invalidates immediately", () => {
  it("busts the cache from the mutation's own response", async () => {
    // The regression this exists to prevent: adding a Data Source moved the server's revision, but the
    // cached list was not refetched until a refocus, so the user did not see what they had just added.
    // The mutation response already carries the new revision — reading it is free.
    const read = stubFetch("rev-1", [{ id: "conformance" }]);
    await cachedGet("/api/data-sources", json);
    expect(read).toHaveBeenCalledTimes(1);

    stubFetch("rev-2", { id: "cardiology" });
    await mutate("/api/data-sources", { method: "POST", body: "{}" });

    expect(peek("GET /api/data-sources")).toBeUndefined();
  });

  it("does not cache the write itself", async () => {
    const fn = stubFetch("rev-1");
    await mutate("/api/data-sources/x", { method: "DELETE" });
    await mutate("/api/data-sources/x", { method: "DELETE" });
    // A write is not a question with an answer to remember.
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("leaves the cache alone when the write changed nothing", async () => {
    // A write that the server treats as a no-op does not move the revision, so nothing is dropped —
    // the cache follows the server rather than guessing from the verb.
    const read = stubFetch("rev-1");
    await cachedGet("/api/data-sources", json);
    await mutate("/api/data-sources/x/deploy", { method: "POST" });
    expect(peek("GET /api/data-sources")).toBeDefined();
    expect(read).toHaveBeenCalledTimes(2); // the read, then the write — not a refetch
  });
});

describe("peek — the synchronous read that removes the flash", () => {
  it("distinguishes a cached null from a miss", async () => {
    // A profile the evaluation never materialized is a 409 -> `null`, and that IS the answer. If a miss
    // and a cached null both read as null, that absence is refetched forever.
    stubFetch("rev-1");
    await cachedGet("/api/p", async () => null);

    expect(peek("GET /api/p")).toBeNull();
    expect(peek("GET /api/never-read")).toBeUndefined();
  });
});

describe("failures are not answers", () => {
  it("does not cache a rejected read", async () => {
    let attempt = 0;
    global.fetch = vi.fn(async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("network down");
      return new Response("{}", { status: 200, headers: { [REVISION_HEADER]: "rev-1" } });
    }) as unknown as typeof fetch;

    await expect(cachedGet("/api/libraries/discover", json)).rejects.toThrow("network down");
    // A transient failure must retry on the next mount rather than be remembered as an answer.
    await expect(cachedGet("/api/libraries/discover", json)).resolves.toEqual({});
    expect(attempt).toBe(2);
  });

  it("survives an unreachable revision probe without dropping the cache", async () => {
    const fn = stubFetch("rev-1");
    await cachedGet("/api/libraries/discover", json);

    global.fetch = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    await refreshRevision();

    // Not learning the revision leaves the cache where it was — the same position as before the call.
    global.fetch = fn as unknown as typeof fetch;
    await cachedGet("/api/libraries/discover", json);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe("the refocus probe — the one case the header cannot serve", () => {
  it("re-checks the revision when the tab becomes visible", async () => {
    stubFetch("rev-1");
    await cachedGet("/api/libraries/discover", json);

    const probe = stubFetch("rev-2", { revision: "rev-2" });
    const teardown = installRevisionRefresh();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(probe).toHaveBeenCalled());

    expect(probe.mock.calls[0]?.[0]).toBe("/api/cache/revision");
    await vi.waitFor(() => expect(peek("GET /api/libraries/discover")).toBeUndefined());
    teardown();
  });

  it("does not probe when the tab is being hidden", () => {
    const probe = stubFetch("rev-1");
    const teardown = installRevisionRefresh();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(probe).not.toHaveBeenCalled();
    teardown();
  });

  it("stops probing after teardown", () => {
    const probe = stubFetch("rev-1");
    installRevisionRefresh()();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(probe).not.toHaveBeenCalled();
  });
});
