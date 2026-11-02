// SVC-RESOURCE-SCHEMA (PROC-TDD) — fetchModeledTypes fallback contract: the editor's
// type dropdown must get the engine's modeled types when available, and an EMPTY list (never a throw) when
// the compiler is unreachable/degraded, so it falls back to its static list rather than an empty picker.
import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchModeledTypes } from "./run-client.ts";

function mockFetch(body: unknown, ok = true) {
  global.fetch = vi.fn(async () => ({ ok, json: async () => body })) as unknown as typeof fetch;
}

describe("fetchModeledTypes", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns the engine's modeled types when not a fallback", async () => {
    mockFetch({ types: ["Patient", "Encounter"], fallback: false });
    expect(await fetchModeledTypes()).toEqual(["Patient", "Encounter"]);
  });

  it("returns [] on the compiler fallback so the editor uses its static list", async () => {
    mockFetch({ types: [], fallback: true });
    expect(await fetchModeledTypes()).toEqual([]);
  });

  it("returns [] on an HTTP or transport failure (never throws)", async () => {
    mockFetch({}, false);
    expect(await fetchModeledTypes()).toEqual([]);
    global.fetch = vi.fn(async () => { throw new Error("unreachable"); }) as unknown as typeof fetch;
    expect(await fetchModeledTypes()).toEqual([]);
  });
});
