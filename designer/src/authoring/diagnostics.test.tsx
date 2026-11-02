// PHASE-S2 / APP-DIAGNOSTICS (PROC-TDD) — plain-language humanization + the translate transport.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { humanize, humanizeAll, translate } from "./diagnostics.ts";

describe("humanize (APP-DIAGNOSTICS)", () => {
  it("strips a leading location token", () => {
    expect(humanize("[3:5] something went wrong").summary).toBe("Something went wrong");
  });
  it("strips a severity prefix", () => {
    expect(humanize("ERROR: bad thing").summary).toBe("Bad thing");
  });
  it("rewrites an unresolved-identifier message into a clinical hint", () => {
    const d = humanize("Could not resolve identifier Foo in the current library.");
    expect(d.summary).toContain("“Foo” isn’t defined here");
    expect(d.raw).toBe("Could not resolve identifier Foo in the current library.");
  });
  it("keeps the raw message available and never loses it", () => {
    const d = humanize("some totally opaque compiler text");
    expect(d.raw).toBe("some totally opaque compiler text");
    expect(d.summary.length).toBeGreaterThan(0);
  });
  it("humanizeAll preserves order", () => {
    const out = humanizeAll({ elm: null, ok: false, diagnostics: ["ERROR: a", "ERROR: b"] });
    expect(out.map((d) => d.summary)).toEqual(["A", "B"]);
  });
});

describe("translate transport", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { globalThis.fetch = realFetch; });

  it("POSTs to /api/translate and returns the result on 200", async () => {
    const body = { elm: "{}", diagnostics: [], ok: true };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch;
    const r = await translate("define \"X\": 1");
    expect(r.ok).toBe(true);
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(call[0]).toBe("/api/translate");
    // APP-SPEC-MODEL — compiling the include closure is opt-in, so a plain diagnostics translate
    // does not pay for it.
    expect(JSON.parse((call[1] as RequestInit).body as string))
      .toEqual({ cql: "define \"X\": 1", includes: [], include_elm: false, location: null, environment: null });
  });

  it("asks for the compiled include closure when includeElm is set (APP-SPEC-MODEL)", async () => {
    const body = { elm: "{}", diagnostics: [], ok: true, includes: [] };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch;
    await translate("define \"X\": 1", [], { includeElm: true });
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(JSON.parse((call[1] as RequestInit).body as string).include_elm).toBe(true);
  });

  it("throws loudly on a transport/HTTP error (not a compile failure)", async () => {
    globalThis.fetch = vi.fn(async () => new Response("boom", { status: 500 })) as typeof fetch;
    await expect(translate("x")).rejects.toThrow(/validation service error \(500\)/);
  });
});
