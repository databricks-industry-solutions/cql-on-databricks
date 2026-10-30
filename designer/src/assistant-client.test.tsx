// APP-ASSISTANT — the drawer's SSE client.
//
// This exists for the frame handling, which is the part that looks right and is not: a chunk from the
// network can split an SSE frame anywhere, including mid-JSON, so a reader that parses whatever
// arrived works against a fast local backend and drops events against a real one. The cases below
// feed the same stream in three chunkings and assert the same result.
//
// It also pins the two failures that CANNOT be read off `res.ok`: an error raised after the stream
// opened (the status line went out with the first byte), and a stream that ends with no answer at all.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { askAssistant } from "./assistant-client.ts";

const ANSWER = {
  type: "answer",
  text: "The denominator holds 11,154 patients.",
  invocations: [{ tool: "get_output", arguments: {}, provenance: "engine", handle: "c.s.denominator" }],
  messages: [{ role: "assistant", content: "…" }],
  hasDerivedFigures: false,
};

/** An SSE body delivered in `chunks` pieces, byte-split wherever they fall. */
function stream(frames: unknown[], pieces = 1): Response {
  const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
  const bytes = new TextEncoder().encode(body);
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

beforeEach(() => { vi.stubGlobal("fetch", vi.fn()); });

describe("askAssistant (APP-ASSISTANT)", () => {
  it.each([1, 3, 200])("reassembles frames split across %i network chunks", async (pieces) => {
    // 200 pieces splits mid-JSON on nearly every frame, which is the case a naive reader fails.
    vi.mocked(fetch).mockResolvedValue(stream([
      { type: "tool", invocation: { tool: "get_specification", arguments: {}, provenance: "context" } },
      ANSWER,
    ], pieces));
    const seen: string[] = [];
    const answer = await askAssistant("q", { onInvocation: (i) => seen.push(i.tool) });
    expect(seen).toEqual(["get_specification"]);
    expect(answer.text).toBe("The denominator holds 11,154 patients.");
  });

  it("reports every tool in the order it landed", async () => {
    vi.mocked(fetch).mockResolvedValue(stream([
      { type: "tool", invocation: { tool: "get_specification", arguments: {}, provenance: "context" } },
      { type: "tool", invocation: { tool: "get_output", arguments: {}, provenance: "engine" } },
      { type: "tool", invocation: { tool: "query_data", arguments: {}, provenance: "derived" } },
      ANSWER,
    ], 5));
    const seen: string[] = [];
    await askAssistant("q", { onInvocation: (i) => seen.push(i.tool) });
    expect(seen).toEqual(["get_specification", "get_output", "query_data"]);
  });

  it("raises an error that arrived INSIDE the stream", async () => {
    // The status line went out with the first byte, so this cannot be read off `res.ok` — a client
    // that only checked the status would render a failure as an empty answer.
    vi.mocked(fetch).mockResolvedValue(stream([{ type: "error", error: "called tools 24 times" }]));
    await expect(askAssistant("q")).rejects.toThrow("called tools 24 times");
  });

  it("refuses to invent an answer when the stream ends without one", async () => {
    // The backend went away mid-question (a reload in dev, a proxy timeout). Saying so beats
    // rendering an empty answer as though the assistant had nothing to say (`KNOWLEDGE-FAIL-LOUD`).
    vi.mocked(fetch).mockResolvedValue(stream([
      { type: "tool", invocation: { tool: "get_output", arguments: {}, provenance: "engine" } },
    ]));
    await expect(askAssistant("q")).rejects.toThrow(/ended before it reported an answer/);
  });

  it("surfaces a failure that happened BEFORE the stream opened", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false, status: 400,
      json: async () => ({ error: "question is required" }),
    } as unknown as Response);
    await expect(askAssistant("")).rejects.toThrow("question is required");
  });

  it("sends the scope and the prior turns", async () => {
    const resultSet = { destination: ["synthetic", "results"] as [string, string], publisher: "synthetic-owner",
      session_id: "session", selection_id: "selection", request_hash: "request", purpose: "private" as const,
      population: "synthetic", targets: ["Output"], computed_since: null };
    vi.mocked(fetch).mockResolvedValue(stream([ANSWER]));
    await askAssistant("q", {
      selection: { library: "ExampleMeasure", define: "Denominator", resultSet },
      messages: [{ role: "assistant", content: "…" }],
    });
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string);
    expect(body.selection).toEqual({ library: "ExampleMeasure", define: "Denominator", resultSet });
    expect(body.messages).toEqual([{ role: "assistant", content: "…" }]);
  });
});
