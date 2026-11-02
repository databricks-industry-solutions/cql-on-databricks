// APP-ASSISTANT — the conversation drawer (`agent/README.md`).
//
// What these pin is the part a refactor could quietly undo, and it is mostly NOT the chat mechanics:
// that the scope travels as a removable default rather than as ambient state
// (`APP-ASSISTANT-CONTEXT`), that an agent-derived figure cannot be mistaken for a measure result
// (`SVC-ANSWER-PROVENANCE`), that the answer carries the address that makes it reproducible
// (`SVC-TOOL-ADDRESS`), and that the read-only boundary is stated before a reader is refused
// (`KNOWLEDGE-FAIL-LOUD`).

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useCallback, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantDrawer, useAssistantShortcut } from "./AssistantDrawer.tsx";
import type { AssistantAnswer } from "../assistant-client.ts";

const askAssistant = vi.hoisted(() => vi.fn());
vi.mock("../assistant-client.ts", () => ({ askAssistant }));

const SELECTION = {
  library: "ExampleMeasure",
  version: "1.0.0",
  define: "Denominator",
  periodStart: "2026-01-01",
  periodEnd: "2026-12-31",
};

function answer(over: Partial<AssistantAnswer> = {}): AssistantAnswer {
  return {
    text: "The denominator holds 11,154 patients.",
    invocations: [],
    messages: [{ role: "assistant", content: "…" }],
    hasDerivedFigures: false,
    ...over,
  };
}

function drawer(props: Partial<Parameters<typeof AssistantDrawer>[0]> = {}) {
  const onClose = vi.fn();
  const utils = render(
    <AssistantDrawer open onClose={onClose} selection={SELECTION} {...props} />,
  );
  return { ...utils, onClose };
}

async function ask(question: string) {
  fireEvent.change(screen.getByLabelText("Ask a question"), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

beforeEach(() => {
  askAssistant.mockReset();
  askAssistant.mockResolvedValue(answer());
});

describe("AssistantDrawer (APP-ASSISTANT)", () => {
  it("renders nothing until it is opened", () => {
    render(<AssistantDrawer open={false} onClose={vi.fn()} selection={SELECTION} />);
    expect(screen.queryByRole("region", { name: "Assistant" })).toBeNull();
  });

  it("states the read-only boundary before a reader is refused", () => {
    // KNOWLEDGE-FAIL-LOUD applied to a CAPABILITY. Edit and Operate are the `propose_*` family and do
    // not exist; a reader who learns that by asking and being turned down has been let down for nothing.
    drawer();
    expect(screen.getByText(/cannot change the measure/i)).toBeInTheDocument();
  });

  it("dismisses on Escape, like every other overlay", () => {
    const { onClose } = drawer();
    fireEvent.keyDown(screen.getByRole("region", { name: "Assistant" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("the answer is markdown (APP-ASSISTANT-MARKDOWN)", () => {
  it("renders the model's structure as structure, not as punctuation", async () => {
    // The failure this replaces: the answer was preformatted text, so a bulleted answer arrived as a
    // column of literal hyphens and a table as a wall of pipes — at a clinician, in the surface whose
    // whole claim is that it explains a measure in their terms.
    askAssistant.mockResolvedValue(answer({
      text: [
        "The denominator holds **11,154** patients.",
        "",
        "| Population | Patients |",
        "| --- | --- |",
        "| Denominator | 11,154 |",
        "",
        "- patients 20 to 90",
        "- with an example condition diagnosis",
      ].join("\n"),
    }));
    drawer();
    await ask("What is in the denominator?");

    expect((await screen.findByText("11,154", { selector: "b" })).tagName).toBe("B");
    expect(screen.getByRole("columnheader", { name: "Population" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["patients 20 to 90", "with an example condition diagnosis"]);
  });
});

describe("references and artifacts in an answer (APP-ASSISTANT-REFS)", () => {
  /** A lens that resolves one define and nothing else — enough to prove the answer goes through the
   *  DOCUMENT's renderer, without compiling a measure to get a real spec model. */
  const lens = {
    inspector: { metrics: null },
    reads: {
      defineEntry: (name: string) =>
        (name === "Denominator" ? { name: "Denominator", library: "ExampleMeasure" } : null),
      defineSelected: () => false,
      onSelect: vi.fn(),
      onNavigate: vi.fn(),
      selectedKey: null,
      count: () => null,
      params: {},
      resolve: () => null,
      terminology: () => null,
      inspector: { metrics: null },
    },
  } as unknown as Parameters<typeof AssistantDrawer>[0]["lens"];

  const FUNNEL = {
    total: 100,
    stages: [{ name: "Initial Population", count: 100, pct: 100 },
             { name: "Denominator", count: 60, pct: 60 }],
    readout: { kind: "unsupported" },
  } as unknown as Parameters<typeof AssistantDrawer>[0]["funnel"];

  it("renders a define reference as the document's own control, keeping the reader's words", async () => {
    // The point of the whole feature: a define named in an answer is the SAME object as the define
    // named in a sentence above it, so it gets the same lens and the same verbs — and the phrase stays
    // the words the sentence needed, never the declared name in title case.
    askAssistant.mockResolvedValue(answer({
      text: "It counts [patients with an example condition](cql-databricks:define/ExampleMeasure/Denominator).",
    }));
    drawer({ lens });
    await ask("What does it count?");
    const ref = await screen.findByRole("button", { name: "patients with an example condition" });
    expect(ref).toHaveClass("mw-defref");
  });

  it("degrades an unresolvable reference to the words the model wrote", async () => {
    // The rule every binding in the document follows. A reference to something this measure does not
    // render must never become a dead link, and must never resolve to some other define.
    askAssistant.mockResolvedValue(answer({
      text: "It counts [patients with an example condition](cql-databricks:define/ExampleMeasure/Moved%20Away).",
    }));
    drawer({ lens });
    await ask("What does it count?");
    expect(await screen.findByText(/patients with an example condition/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "patients with an example condition" })).toBeNull();
  });

  it("draws a chart from a fenced block, and says the figures are the assistant's", async () => {
    // SVC-ANSWER-PROVENANCE — a chart's numbers were TRANSCRIBED by the model rather than resolved by
    // the client, so the statement is unconditional rather than a function of which tools ran.
    askAssistant.mockResolvedValue(answer({
      text: '```cql-databricks-chart\n{"type":"bar","title":"By band","rows":[["18-44",812]]}\n```',
    }));
    drawer({ lens });
    await ask("How does it break down?");
    expect(await screen.findByText("By band")).toBeInTheDocument();
    expect(screen.getByText("812")).toBeInTheDocument();
    expect(screen.getByText(/figures the assistant derived/i)).toBeInTheDocument();
  });

  it("falls back to a code block when the chart JSON is malformed", async () => {
    // The reader sees the model's own block, which is honest and is the only thing that makes the
    // failure diagnosable. A chart drawn from half-understood rows would be a wrong picture.
    askAssistant.mockResolvedValue(answer({ text: "```cql-databricks-chart\n{oops\n```" }));
    drawer({ lens });
    await ask("How does it break down?");
    expect(await screen.findByText("{oops")).toBeInTheDocument();
    expect(screen.queryByText(/figures the assistant derived/i)).toBeNull();
  });

  it("renders the DOCUMENT's funnel rather than rebuilding one from the answer", async () => {
    askAssistant.mockResolvedValue(answer({ text: "Here is where they drop:\n\n```cql-databricks-funnel\n```" }));
    drawer({ lens, funnel: FUNNEL });
    await ask("Where do patients drop?");
    expect(await screen.findByText("Initial Population")).toBeInTheDocument();
  });

  it("leaves an ordinary code fence alone", async () => {
    askAssistant.mockResolvedValue(answer({ text: "```sql\nSELECT 1\n```" }));
    drawer({ lens });
    await ask("What did you run?");
    expect((await screen.findByText("SELECT 1")).closest("pre")).toBeTruthy();
  });
});

describe("scope as a removable default (APP-ASSISTANT-CONTEXT)", () => {
  it("shows the active scope and sends it with the question", async () => {
    drawer();
    expect(screen.getByText("Denominator")).toBeInTheDocument();
    await ask("How many patients?");
    await waitFor(() => expect(askAssistant).toHaveBeenCalled());
    expect(askAssistant.mock.calls[0]![1].selection).toEqual(SELECTION);
  });

  it("dropping the chip WIDENS the question rather than narrowing it", async () => {
    // The chip is removable because a question broader than the selection is the case the catch-all
    // exists for. A scope that silently re-attached would make that question unaskable.
    drawer();
    fireEvent.click(screen.getByTitle("Ask without this scope"));
    expect(screen.queryByTitle("Ask without this scope")).toBeNull();
    await ask("Which measures read Exclusions?");
    await waitFor(() => expect(askAssistant).toHaveBeenCalled());
    expect(askAssistant.mock.calls[0]![1].selection).toBeUndefined();
  });

  it("prints the define when there is one and the library when there is not", () => {
    const { rerender } = drawer();
    expect(screen.getByText("Denominator")).toBeInTheDocument();
    rerender(<AssistantDrawer open onClose={vi.fn()}
      selection={{ library: "Exclusions", version: "2.0.0" }} />);
    expect(screen.getByText("Exclusions 2.0.0")).toBeInTheDocument();
  });
});

describe("provenance (SVC-ANSWER-PROVENANCE)", () => {
  it("warns when a figure is the assistant's own derivation, not the measure's", async () => {
    // The requirement's whole point: "I computed a different denominator" must not read as "the
    // denominator is different". Rendered from the WIRE, because a model can forget to say it.
    askAssistant.mockResolvedValue(answer({
      hasDerivedFigures: true,
      invocations: [{ tool: "query_data", arguments: { sql: "SELECT 1" }, provenance: "derived" }],
    }));
    drawer();
    await ask("How many are controlled?");
    expect(await screen.findByText(/computed by the assistant/i)).toBeInTheDocument();
  });

  it("does not warn when every figure is engine-computed", async () => {
    askAssistant.mockResolvedValue(answer({
      invocations: [{
        tool: "get_output", arguments: {}, provenance: "engine",
        handle: "acme_results.examplemeasure_1_0_0_081b9dc0.denominator",
      }],
    }));
    drawer();
    await ask("How many patients?");
    await screen.findByText(/11,154/);
    expect(screen.queryByText(/computed by the assistant/i)).toBeNull();
  });

  it("cites the address, which is what makes the answer reproducible", async () => {
    // SVC-TOOL-ADDRESS — a node's output is a place, so the citation is a relation anyone can re-read,
    // never a conversation id.
    askAssistant.mockResolvedValue(answer({
      invocations: [{
        tool: "get_output", arguments: {}, provenance: "engine",
        handle: "acme_results.examplemeasure_1_0_0_081b9dc0.denominator",
      }],
    }));
    drawer();
    await ask("How many patients?");
    fireEvent.click(await screen.findByText("1 source"));
    expect(screen.getByText("acme_results.examplemeasure_1_0_0_081b9dc0.denominator")).toBeInTheDocument();
  });

  it("labels each source by whose number it is", async () => {
    askAssistant.mockResolvedValue(answer({
      hasDerivedFigures: true,
      invocations: [
        { tool: "get_output", arguments: {}, provenance: "engine", handle: "cat.sch.denominator" },
        { tool: "query_data", arguments: { sql: "SELECT count(*) FROM t" }, provenance: "derived" },
        // A call that produced no figure is not a third kind of number, and does not appear.
        { tool: "get_specification", arguments: {}, provenance: "context" },
      ],
    }));
    drawer();
    await ask("How many are controlled?");
    fireEvent.click(await screen.findByText("2 sources"));
    expect(screen.getByText("measure")).toBeInTheDocument();
    expect(screen.getByText("assistant")).toBeInTheDocument();
  });
});

describe("transcript states", () => {
  it("shows the question immediately and a pending mark while the turn is in flight", async () => {
    let settle: (a: AssistantAnswer) => void = () => {};
    askAssistant.mockReturnValue(new Promise<AssistantAnswer>((r) => { settle = r; }));
    drawer();
    await ask("How many patients?");
    expect(screen.getByText("How many patients?")).toBeInTheDocument();
    expect(screen.getByText(/Working…/)).toBeInTheDocument();
    settle(answer());
    expect(await screen.findByText(/11,154/)).toBeInTheDocument();
  });

  it("overwrites the thinking line rather than growing a list under the question", async () => {
    // A real answer is a sequence of calls, so a growing list pushes the reader's own question off
    // the top of a drawer that is a third of the column. One line, the step now.
    let emit: (inv: unknown) => void = () => {};
    askAssistant.mockImplementation((_q: string, o: { onInvocation?: (i: unknown) => void }) => {
      emit = o.onInvocation!;
      return new Promise<AssistantAnswer>(() => {});
    });
    drawer();
    await ask("How many patients?");
    emit({ tool: "get_specification", arguments: {}, provenance: "context" });
    await screen.findByText("Reading the specification");
    emit({ tool: "query_data", arguments: {}, provenance: "derived" });
    await screen.findByText("Working out a figure");
    expect(screen.queryByText("Reading the specification")).toBeNull();
  });

  it("keeps the chain of thought behind a disclosure once the answer lands", async () => {
    // The account of the investigation is an AUDIT — read after the answer, not instead of it. It is
    // re-derived from the answer's own invocations, so a transcript that survives a reload holds the
    // same account as the one that watched it happen.
    askAssistant.mockResolvedValue(answer({
      invocations: [
        { tool: "get_specification", arguments: {}, provenance: "context" },
        { tool: "query_data", arguments: {}, provenance: "derived" },
      ],
    }));
    drawer();
    await ask("How many patients?");
    fireEvent.click(await screen.findByText("2 steps"));
    expect(screen.getByText("Reading the specification")).toBeInTheDocument();
    expect(screen.getByText("Working out a figure")).toBeInTheDocument();
  });

  it("collapses CONSECUTIVE repeats, and only those", async () => {
    // Four derivations in a row print one phrase four times, which reads as a stuck loop. A return to
    // a step after doing something else is a genuine return and stays — collapsing it would edit the
    // account of what happened.
    askAssistant.mockResolvedValue(answer({
      invocations: [
        { tool: "query_data", arguments: {}, provenance: "derived" },
        { tool: "query_data", arguments: {}, provenance: "derived" },
        { tool: "get_specification", arguments: {}, provenance: "context" },
        { tool: "query_data", arguments: {}, provenance: "derived" },
      ],
    }));
    drawer();
    await ask("How many are controlled?");
    expect(await screen.findByText("3 steps")).toBeInTheDocument();
  });

  it("names each tool AS IT LANDS, in the reader's terms (THEME-GLASSBOX)", async () => {
    // The reason streaming is worth having: a real answer is a SEQUENCE of calls, so a client that
    // waited for the whole loop would show a spinner for the entire investigation. What is shown is
    // the actual reads the answer will cite — and in clinical terms, because `get_freshness` is not
    // something a clinician should have to learn.
    let emit: (inv: unknown) => void = () => {};
    let settle: (a: AssistantAnswer) => void = () => {};
    askAssistant.mockImplementation((_q: string, o: { onInvocation?: (i: unknown) => void }) => {
      emit = o.onInvocation!;
      return new Promise<AssistantAnswer>((r) => { settle = r; });
    });
    drawer();
    await ask("How many patients?");

    await waitFor(() => expect(screen.getByLabelText("Working")).toBeInTheDocument());
    emit({ tool: "get_specification", arguments: {}, provenance: "context" });
    expect(await screen.findByText("Reading the specification")).toBeInTheDocument();
    emit({ tool: "get_freshness", arguments: {}, provenance: "context" });
    expect(await screen.findByText("Checking how current it is")).toBeInTheDocument();

    // ...and the trace is replaced by the answer, not appended to it.
    settle(answer());
    await screen.findByText(/11,154/);
    expect(screen.queryByLabelText("Working")).toBeNull();
  });

  it("shows a tool that failed rather than telling a nicer story than the run", async () => {
    // The loop hands a tool error back to the model and carries on — a bad query is something to fix,
    // not a dead conversation. Hiding the stumble would make the trace disagree with what happened.
    let emit: (inv: unknown) => void = () => {};
    askAssistant.mockImplementation((_q: string, o: { onInvocation?: (i: unknown) => void }) => {
      emit = o.onInvocation!;
      return new Promise<AssistantAnswer>(() => {});
    });
    drawer();
    await ask("How many patients?");
    await waitFor(() => expect(screen.getByLabelText("Working")).toBeInTheDocument());
    emit({ tool: "query_data", arguments: {}, provenance: "derived", error: "ReadOnlyViolation" });
    const step = await screen.findByText("Working out a figure");
    expect(step).toHaveAttribute("data-failed");
  });

  it("states a failure rather than dropping the turn", async () => {
    askAssistant.mockRejectedValue(new Error("The assistant could not answer (502)."));
    drawer();
    await ask("How many patients?");
    expect(await screen.findByRole("alert")).toHaveTextContent("could not answer (502)");
  });

  it("sends the prior turn's messages back, so the server holds no session", async () => {
    drawer();
    await ask("How many patients?");
    await screen.findByText(/11,154/);
    await ask("And how current is that?");
    await waitFor(() => expect(askAssistant).toHaveBeenCalledTimes(2));
    expect(askAssistant.mock.calls[1]![1].messages).toEqual([{ role: "assistant", content: "…" }]);
  });

  it("asks the entry point's question once, on the open that carried it", async () => {
    // Section 1.1 — an open-ended entry point opens the drawer WITH the scope attached. Asking twice
    // on a re-render would spend a metered call per keystroke elsewhere in the workspace.
    const { rerender } = render(<AssistantDrawer open onClose={vi.fn()} selection={SELECTION}
      initialQuestion="Why did these patients drop?" />);
    await waitFor(() => expect(askAssistant).toHaveBeenCalledTimes(1));
    rerender(<AssistantDrawer open onClose={vi.fn()} selection={SELECTION}
      initialQuestion="Why did these patients drop?" />);
    expect(askAssistant).toHaveBeenCalledTimes(1);
  });
});

describe("useAssistantShortcut — Control+` is the drawer's door (APP-ASSISTANT)", () => {
  function Host({ enabled }: { enabled: boolean }) {
    const [open, setOpen] = useState(false);
    useAssistantShortcut(enabled, useCallback(() => setOpen((v) => !v), []));
    return <div data-testid="state">{open ? "open" : "closed"}</div>;
  }
  const state = () => screen.getByTestId("state").textContent;
  const chord = (over: Partial<KeyboardEventInit> = {}) =>
    fireEvent.keyDown(window, { code: "Backquote", key: "`", ctrlKey: true, ...over });

  it("toggles — one act, so the keyboard and the bar button cannot disagree", () => {
    render(<Host enabled />);
    chord();
    expect(state()).toBe("open");
    chord();
    expect(state()).toBe("closed");
  });

  it("does nothing without a measure open — a door onto a room that is not there", () => {
    render(<Host enabled={false} />);
    chord();
    expect(state()).toBe("closed");
  });

  it("leaves ⌘` and a bare backtick alone", () => {
    // ⌘` is macOS's own window cycle, and a bare backtick is a character someone is typing. Matching
    // `code` rather than `key` is what makes the binding survive a layout with no backtick on that key.
    render(<Host enabled />);
    chord({ ctrlKey: false, metaKey: true });
    chord({ ctrlKey: false });
    expect(state()).toBe("closed");
  });
});
