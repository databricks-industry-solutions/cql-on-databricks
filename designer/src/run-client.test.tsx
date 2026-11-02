// SVC-RESOURCE-SCHEMA — fetchModeledTypes: the editor's type list is the engine's answer, and a failure is an
// error the editor can show. Never an empty list, which would read as "the engine models nothing".
import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchModeledTypes } from "./run-client.ts";

function mockFetch(body: unknown, { ok = true, status = 200 } = {}) {
  global.fetch = vi.fn(async () => ({ ok, status, json: async () => body })) as unknown as typeof fetch;
}

describe("fetchModeledTypes (SVC-RESOURCE-SCHEMA)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns the engine's modeled types", async () => {
    mockFetch({ types: ["Patient", "Encounter"] });
    expect(await fetchModeledTypes()).toEqual(["Patient", "Encounter"]);
  });

  it("rejects with the server's message when the compiler cannot answer", async () => {
    mockFetch({ error: "compiler unreachable listing modeled types" }, { ok: false, status: 502 });
    await expect(fetchModeledTypes()).rejects.toThrow("compiler unreachable listing modeled types");
  });

  it("rejects when the request itself fails, rather than answering with no types", async () => {
    global.fetch = vi.fn(async () => { throw new Error("unreachable"); }) as unknown as typeof fetch;
    await expect(fetchModeledTypes()).rejects.toThrow("unreachable");
  });
});
