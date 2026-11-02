// APP-ASSISTANT — the SPA's client for the assistant drawer (`agent/README.md`).
//
// One call, `POST /api/assistant/ask`, and the shapes below hand-mirror the backend's `ask.py`
// dataclasses the way `wire.ts` mirrors the Pydantic models.
//
// NOT cached (unlike `library-client.ts`'s reads): a question is not a resource, two identical
// questions are two conversations, and the data underneath moves. `data-cache` exists to stop the
// catalogue being fetched five times, which is a different problem.
//
// THE CONVERSATION IS THE CLIENT'S STATE. `messages` comes back on every answer and goes back up on
// the next turn — the server holds no session, for the same reason the tools carry no run id
// (`SVC-TOOL-ADDRESS`): an answer reproducible only from inside the conversation that produced it is
// not reproducible. The drawer keeps them opaque and never reads into them.

import { currentEnvironment } from "./environment.ts";
import type { ResultSetAddress } from "./wire.ts";

/** APP-ASSISTANT-CONTEXT — what the reader is looking at, as the removable chip states it. */
export interface AssistantSelection {
  resultSet?: ResultSetAddress;
  library?: string;
  version?: string;
  /** RUN-ADDRESS-LOCATION — where the open library is read, so a tool reads the same copy. */
  location?: string;
  define?: string;
  periodStart?: string;
  periodEnd?: string;
  /** What the identifiers above are CALLED on screen. Sent separately and never passed to a tool: an
   *  identifier is not a name a clinician recognises, and without these the assistant either says
   *  `ExampleMeasure` at someone who calls it *Example Measure*, or
   *  spends a tool round-trip resolving what the screen already had on it. */
  measureTitle?: string;
  /** The picked environment's name, so the answer can say whose data it read. */
  environmentName?: string;
}

/** SVC-ANSWER-PROVENANCE — whose number a tool call produced.
 *
 *  `engine` is what the compiler and executor produced — the same figure the document shows.
 *  `derived` is the assistant's own derivation over the lakehouse. `context` produced no figure.
 *  The drawer renders from THIS, not from the prose: a model can forget to say it derived something,
 *  and a derived figure that looks like a measure result is the failure the requirement exists for. */
export type Provenance = "engine" | "derived" | "context";

/** One tool call the assistant made, as the answer reports it. */
export interface AssistantInvocation {
  tool: string;
  arguments: Record<string, unknown>;
  provenance: Provenance;
  /** The relation the call addressed (`SVC-TOOL-ADDRESS`) — the answer's reproducible citation. */
  handle?: string;
  error?: string;
}

export interface AssistantAnswer {
  text: string;
  invocations: AssistantInvocation[];
  messages: unknown[];
  /** True when any figure in the answer is the assistant's own derivation. */
  hasDerivedFigures: boolean;
}

/** Ask one question, reporting each tool call as it lands.
 *
 *  SSE, because a real answer is a SEQUENCE of calls and not one round — `onInvocation` is what lets
 *  the drawer show the investigation instead of a spinner (`THEME-GLASSBOX`). Rejects with the
 *  server's message so the drawer can state it (`KNOWLEDGE-FAIL-LOUD`).
 *
 *  Frame handling mirrors `run-client.ts`: SSE frames are `\n\n`-delimited and a chunk can split one,
 *  so the tail is carried over rather than parsed. */
export async function askAssistant(
  question: string,
  opts: {
    selection?: AssistantSelection;
    messages?: unknown[];
    signal?: AbortSignal;
    onInvocation?: (inv: AssistantInvocation) => void;
  } = {},
): Promise<AssistantAnswer> {
  const res = await fetch("/api/assistant/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      question,
      selection: opts.selection ?? null,
      messages: opts.messages ?? null,
      // AGENT-DEFAULT-ENVIRONMENT, DOC-ENVIRONMENT-PICKER — every tool runs in the picked environment.
      environment: currentEnvironment(),
    }),
    signal: opts.signal,
  });
  // A failure BEFORE the stream opens still answers JSON (a bad request, an auth gate).
  if (!res.ok) throw new Error(await errorText(res));
  if (!res.body) throw new Error("The assistant stream is not supported by this browser.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer: AssistantAnswer | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data: ")) continue;
      const event = JSON.parse(line.slice(6)) as
        | { type: "tool"; invocation: AssistantInvocation }
        | ({ type: "answer" } & AssistantAnswer)
        | { type: "error"; error: string };
      if (event.type === "tool") opts.onInvocation?.(event.invocation);
      else if (event.type === "answer") answer = event;
      // An error arrives IN the stream, because the status line went out with the first byte — so it
      // has to be raised here rather than read off `res.ok`.
      else throw new Error(event.error);
    }
  }

  if (!answer) {
    // The stream ended with no terminal frame: the backend went away mid-question (a reload in dev,
    // a proxy timeout). Saying so beats rendering an empty answer as though it were one.
    throw new Error("The assistant stream ended before it reported an answer.");
  }
  return answer;
}

/** The server's own message where there is one — it says what went wrong; a status code does not. */
async function errorText(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: string };
    if (body.error) return body.error;
  } catch {
    // Not JSON. The status line below is then the only thing there is to say.
  }
  return `The assistant could not answer (${res.status}).`;
}
