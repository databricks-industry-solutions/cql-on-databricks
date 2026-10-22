// APP-NARRATIVE-OVERLAY — the narration stream's client, and the overlay merge for included libraries.
//
// The stream ends in exactly one terminal event: the composed Specification, a rejection with its
// problems, or an error. Each is pinned here, across chunkings, because an SSE frame can split anywhere.

import { afterEach, describe, expect, it, vi } from "vitest";
import { generateNarration, NarrationRejected, type NarrationProgress, type SpecificationOverlay }
  from "./library-client.ts";
import { withIncluded } from "./measures/specDocument.ts";

const SPEC: SpecificationOverlay = {
  library: "SampleBP", version: "1.0.0", overview: {}, libraryOverviews: {},
  elements: { "SampleBP::Initial Population": { description: { text: "Adults.", provenance: "narrated" } } },
};

/** An SSE body delivered in `pieces` byte-split chunks. */
function stream(frames: unknown[], pieces = 1): Response {
  const bytes = new TextEncoder().encode(frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join(""));
  const size = Math.ceil(bytes.length / pieces);
  let sent = 0;
  return {
    ok: true,
    headers: new Headers({ "content-type": "text/event-stream" }),
    body: {
      getReader: () => ({
        read: async () => {
          if (sent >= bytes.length) return { done: true, value: undefined };
          const value = bytes.slice(sent, sent + size);
          sent += size;
          return { done: false, value };
        },
      }),
    },
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("generateNarration (APP-NARRATIVE-OVERLAY)", () => {
  it.each([1, 3, 200])("reports progress and resolves with the Specification across %i chunks", async (pieces) => {
    const fetcher = vi.fn().mockResolvedValue(stream([
      { type: "stage", stage: "compiling" },
      { type: "stage", stage: "writing", attempt: 1 },
      { type: "problems", attempt: 1, problems: [{ kind: "coverage", message: "defines with no narration" }] },
      { type: "stage", stage: "writing", attempt: 2 },
      { type: "narration", attempts: 2, specification: SPEC },
    ], pieces));
    vi.stubGlobal("fetch", fetcher);
    const seen: NarrationProgress[] = [];
    expect(await generateNarration("SampleBP", "1.0.0", { onProgress: (e) => seen.push(e) })).toEqual(SPEC);
    expect(seen.map((e) => e.type)).toEqual(["stage", "stage", "problems", "stage"]);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/libraries/SampleBP/1.0.0/narration");
    expect(init.method).toBe("POST");
  });

  it("rejects with the last attempt's problems when every attempt failed", async () => {
    const problems = [{ kind: "identifier", message: "<Read profile='x'> is not a profile the compiled ELM reads" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream([{ type: "rejected", attempts: 2, problems }])));
    const failure = await generateNarration("SampleBP", "1.0.0").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(NarrationRejected);
    expect((failure as NarrationRejected).problems).toEqual(problems);
    expect((failure as NarrationRejected).attempts).toBe(2);
  });

  it("raises an error reported inside the stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream([{ type: "error", error: "MODIFY denied" }])));
    await expect(generateNarration("SampleBP", "1.0.0")).rejects.toThrow("MODIFY denied");
  });

  it("raises when the stream ends without a result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream([{ type: "stage", stage: "compiling" }])));
    await expect(generateNarration("SampleBP", "1.0.0")).rejects.toThrow(/ended before it reported a result/);
  });

  it("states a refusal made before the stream opened", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: "CQL_ASSISTANT_ENDPOINT is not set" }), { status: 400 })));
    await expect(generateNarration("SampleBP", "1.0.0")).rejects.toThrow("CQL_ASSISTANT_ENDPOINT is not set");
  });
});

describe("withIncluded (APP-NARRATIVE-OVERLAY)", () => {
  const helper: SpecificationOverlay = {
    library: "SampleCommon", version: "1.0.0", overview: { howToRead: { text: "Not ours.", provenance: "narrated" } },
    elements: { "SampleCommon::Qualifying Encounters": { description: { text: "A finished visit.", provenance: "narrated" } } },
    libraryOverviews: { SampleCommon: { text: "Shared encounter rules.", provenance: "narrated" } },
  };

  it("joins an included library's elements and catalogue overview, never its Overview", () => {
    const merged = withIncluded({ library: "SampleBP", version: "1.0.0" }, SPEC, [helper, null])!;
    expect(Object.keys(merged.elements).sort()).toEqual(["SampleBP::Initial Population", "SampleCommon::Qualifying Encounters"]);
    expect(merged.libraryOverviews.SampleCommon?.text).toBe("Shared encounter rules.");
    expect(merged.overview).toEqual({});
    expect(merged.library).toBe("SampleBP");
  });

  it("stands up the open library's identity when only an include has prose", () => {
    const merged = withIncluded({ library: "SampleBP", version: "1.0.0" }, null, [helper])!;
    expect([merged.library, merged.version]).toEqual(["SampleBP", "1.0.0"]);
    expect(Object.keys(merged.elements)).toEqual(["SampleCommon::Qualifying Encounters"]);
  });

  it("is the open library's own overlay when no include has prose", () => {
    expect(withIncluded({ library: "SampleBP", version: "1.0.0" }, SPEC, [null])).toBe(SPEC);
    expect(withIncluded({ library: "SampleBP", version: "1.0.0" }, null, [])).toBeNull();
  });
});
