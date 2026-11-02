// APP-ASSISTANT — the conversation, as a bottom drawer (`agent/README.md`).
//
// WHY A DRAWER AND NOT A PANE. Three properties earn it the bottom of the reading column. The document
// stays visible ABOVE it, which matters because the question is usually about what is being read; a
// result table gets WIDTH, and a table is the one shape a narrow pane cannot show; and it is the one
// region of the workspace nothing else claims — left is the nav, right is the inspector, centre is the
// document. The rejected placement is a tab in the inspector: that pane's scope is *data about the
// selection* (`APP-INSPECTOR`). Scope travels as context instead (`APP-ASSISTANT-CONTEXT`), so the
// conversation can be placed anywhere.
//
// IT OVERLAYS, IT DOES NOT REFLOW (`APP-STABLE-LAYOUT`). The drawer shares the reading column's grid
// cell and is `align-self: end`, so opening it moves nothing: the document does not re-wrap, and the
// reader's place on the page is where they left it. Escape dismisses, like every other overlay.
//
// PROVENANCE IS RENDERED FROM THE WIRE, NEVER FROM THE PROSE (`SVC-ANSWER-PROVENANCE`). A model can
// write a careful sentence and still leave a derived number looking like a measure result; the answer
// carries `provenance` per tool call precisely so the client does not have to trust the sentence. The
// mark is DELIBERATELY HUELESS — it reuses the EXPLAIN wash (`--texpl-w`) rather than good/info/warn/
// crit — for the reason `APP-NARRATIVE-PROVENANCE` gives about narration: whose number a figure is,
// is not a STATE of the measure, so it must not borrow the vocabulary that encodes state.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { askAssistant, type AssistantAnswer, type AssistantInvocation, type AssistantSelection }
  from "../assistant-client.ts";
import { Markdown, type MarkdownSlots } from "../markdown.tsx";
import { Sparkle, X } from "../icons.tsx";
import { AnswerChart, parseChart } from "./AnswerChart.tsx";
import { ARTIFACT_FENCE, referenceSegment } from "./assistantRefs.ts";
import { FunnelView } from "./Funnel.tsx";
import { Segments } from "./MeasureNarrative.tsx";
import type { Funnel } from "./model.ts";
import type { SpecLens } from "./specLens.ts";

/** APP-ASSISTANT — Control+` opens and closes the drawer.
 *
 *  IT LIVES WITH THE DRAWER, not with the screen that mounts it. A shortcut is part of a control's
 *  contract — "how do I get to this thing" is the same question as "where is its button" — and putting
 *  it here is also the only way it gets a test: there is no `MeasureWorkspace.test.tsx` (the component
 *  fetches a catalogue, a compile and a run to render at all), so a keydown listener written inline
 *  there would be pinned by nothing.
 *
 *  BACKTICK, and CONTROL rather than the platform's command key. It is the console/drawer convention a
 *  reader already has in their fingers (VS Code, IntelliJ, the browser's own devtools drawer), it
 *  collides with nothing this app binds, and — unlike ⌘` , which macOS reserves for cycling an
 *  application's windows — the browser will actually deliver it. Matched on `event.code`, so a layout
 *  that puts no backtick on that key still has a `Backquote`.
 *
 *  NOT GUARDED BY A FOCUS TEST: Control+` types nothing, so there is no field it could steal a
 *  character from.
 *
 *  `enabled` is the same condition that renders the drawer's door in the bar — the assistant's scope is
 *  the open measure, so a shortcut that opened an empty drawer over the catalogue would be a door onto a
 *  room that is not there. The caller passes ONE toggle rather than open/close so the keyboard and the
 *  pointer cannot come to disagree about whether the drawer is open. */
export function useAssistantShortcut(enabled: boolean, toggle: () => void) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.metaKey || e.altKey || e.code !== "Backquote") return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, toggle]);
}

/** One exchange in the transcript. `answer` is absent while the turn is in flight.
 *
 *  `progress` is what arrives DURING it — one entry per tool call as it lands. It is not a duplicate
 *  of the answer's `invocations`: this is the live trace, and it is discarded the moment the answer
 *  (which carries the same calls, complete) replaces it. The account of the investigation is not lost
 *  with it — the finished turn re-derives it from `invocations` and keeps it behind a disclosure
 *  (`ThoughtLog`). */
interface Turn {
  question: string;
  progress: AssistantInvocation[];
  answer?: AssistantAnswer;
  error?: string;
}

/** What each tool is doing, in the reader's terms rather than the tool's.
 *
 *  The point of showing the trace is that a clinician can follow it, and
 *  `get_freshness` is not something they should have to learn. A tool with no phrase here still
 *  reports — it falls back to its own name — because an unnamed step is better than a silent one. */
const TOOL_PHRASE: Record<string, string> = {
  get_specification: "Reading the specification",
  list_tables: "Looking at what has been computed",
  get_output: "Reading a computed result",
  query_data: "Working out a figure",
  get_freshness: "Checking how current it is",
  list_skills: "Checking how to explain this",
  get_skill: "Checking how to explain this",
};

interface AssistantDrawerProps {
  open: boolean;
  onClose: () => void;
  /** The active scope, as the workspace resolves it. Shown as a removable chip. */
  selection: AssistantSelection;
  /** A question the entry point supplied, asked on open. */
  initialQuestion?: string;
  /** APP-ASSISTANT-REFS — what an answer's inline references resolve against: the SAME bundle the
   *  document resolves through (`specLens.ts`), so a define named in an answer and the same define
   *  named in a sentence above it are one object with one lens and one set of verbs. Optional, because
   *  an answer with no references needs none of it — and a drawer opened before there is a model to
   *  resolve against must still render its prose. */
  lens?: SpecLens;
  /** The population funnel a ```cql-databricks-funnel artifact renders — the document's own, not a second one
   *  built from the answer. */
  funnel?: Funnel | null;
}

export function AssistantDrawer(
  { open, onClose, selection, initialQuestion, lens, funnel }: AssistantDrawerProps,
) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  // APP-ASSISTANT-CONTEXT — the chip is REMOVABLE, so scope is a piece of drawer state rather than a
  // read of the workspace. A question broader than the selection is the case the catch-all exists for,
  // and a scope that silently re-attached itself would make that question unaskable.
  const [scoped, setScoped] = useState(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const ask = useCallback(async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || busy) return;
    setDraft("");
    setBusy(true);
    setTurns((prev) => [...prev, { question: trimmed, progress: [] }]);
    // THE CONVERSATION IS SENT BACK VERBATIM. `messages` is read off the LAST answered turn rather than
    // accumulated here: the server owns the shape (it has to satisfy the endpoint's tool-call pairing),
    // and a client that rebuilt it would be a second author of a format it cannot validate.
    const prior = [...turns].reverse().find((t) => t.answer)?.answer?.messages;
    try {
      const answer = await askAssistant(trimmed, {
        selection: scoped ? selection : undefined,
        messages: prior,
        // Each tool as it lands, onto the turn in flight — which is always the last one, because the
        // input is disabled while `busy`.
        onInvocation: (inv) => setTurns((prev) => prev.map((t, i) =>
          (i === prev.length - 1 ? { ...t, progress: [...t.progress, inv] } : t))),
      });
      setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, answer } : t)));
    } catch (e) {
      setTurns((prev) => prev.map((t, i) =>
        (i === prev.length - 1 ? { ...t, error: (e as Error).message } : t)));
    } finally {
      setBusy(false);
    }
  }, [busy, scoped, selection, turns]);

  // Escape dismisses, as it does for every other overlay in the workspace. Bound on the drawer's own
  // subtree rather than on `document`, so it cannot swallow an Escape meant for a popover above it.
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  }, [onClose]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // The entry point's question is asked once, on the open that carried it.
  const askedRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (open && initialQuestion && askedRef.current !== initialQuestion) {
      askedRef.current = initialQuestion;
      void ask(initialQuestion);
    }
    if (!open) askedRef.current = undefined;
  }, [open, initialQuestion, ask]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  if (!open) return null;

  return (
    <section className="mw-ask" aria-label="Assistant" onKeyDown={onKeyDown}>
      <header className="mw-ask-head">
        <span className="t">Assistant</span>
        {/* The scope chip. `APP-ASSISTANT-CONTEXT`: it PREFILLS tool arguments and is nothing else,
            so dropping it does not narrow what can be asked — it widens it. */}
        {scoped && scopeLabel(selection) && (
          <button className="mw-ask-scope" onClick={() => setScoped(false)}
            title="Ask without this scope">
            <span className="nm">{scopeLabel(selection)}</span>
            <X size={12} aria-hidden="true" />
          </button>
        )}
        <button className="mw-icon-btn" onClick={onClose} aria-label="Close assistant">
          <X size={16} aria-hidden="true" />
        </button>
      </header>

      <div className="mw-ask-body">
        {turns.length === 0 && (
          <div className="mw-ask-empty">
            <Sparkle size={18} aria-hidden="true" />
            <p>
              Ask about this measure — what a criterion counts, how many patients are in a population,
              or how current a figure is.
            </p>
            <p className="sub">
              It reads the specification and the computed results. It cannot change the measure,
              schedule a run, or recompute anything.
            </p>
          </div>
        )}

        {turns.map((turn, i) => (
          <article className="mw-ask-turn" key={i}>
            <p className="mw-ask-q">{turn.question}</p>
            {turn.error
              ? <p className="mw-ask-err" role="alert">{turn.error}</p>
              : turn.answer
                ? <AnswerBody answer={turn.answer} lens={lens} funnel={funnel ?? null} />
                : <Progress trace={turn.progress} />}
          </article>
        ))}
        <div ref={endRef} />
      </div>

      <form className="mw-ask-form" onSubmit={(e) => { e.preventDefault(); void ask(draft); }}>
        <textarea ref={inputRef} className="mw-ask-input" rows={1} value={draft}
          aria-label="Ask a question" placeholder="Ask about this measure…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a newline. A question is usually one line, and a Send button
            // that is the only way to submit makes the common case cost a pointer trip.
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(draft); }
          }} />
        <button className="mw-ask-send" type="submit" disabled={busy || !draft.trim()}>Ask</button>
      </form>
    </section>
  );
}

/** One step of the trace, as the reader reads it: the phrase, and whether that step failed. */
interface Step {
  phrase: string;
  failed: boolean;
}

/** The trace in the reader's terms, with CONSECUTIVE REPEATS COLLAPSED.
 *
 *  A real answer is a sequence of calls, and a model working through one calls `query_data` four
 *  times in a row — which the raw trace prints as "Working out a figure" four times. Repetition reads
 *  as a stuck loop rather than as progress, and it is the least informative thing on the line. Only
 *  ADJACENT repeats collapse: reading the specification, deriving a figure, then reading the
 *  specification again is a genuine return, and flattening it would edit the account of what
 *  happened. A failed step never merges into a successful one — the stumble is the information.
 */
function steps(trace: AssistantInvocation[]): Step[] {
  const out: Step[] = [];
  for (const inv of trace) {
    const step = { phrase: TOOL_PHRASE[inv.tool] ?? inv.tool, failed: Boolean(inv.error) };
    const last = out[out.length - 1];
    if (last && last.phrase === step.phrase && last.failed === step.failed) continue;
    out.push(step);
  }
  return out;
}

/** The turn in flight — ONE LINE, the step now, overwritten as the loop moves.
 *
 *  The reader is served by saying what is being done, not by keeping every line that was ever
 *  true on the screen. A growing list pushes the reader's own question off the top of a drawer that
 *  is a third of the column, and by the time the answer arrives it is a wall of text nobody read —
 *  the account of the investigation belongs to the FINISHED answer, where it is one press away
 *  (`ThoughtLog`) and can be read at leisure. Nothing is lost: the same calls arrive complete on the
 *  answer's `invocations`.
 */
function Progress({ trace }: { trace: AssistantInvocation[] }) {
  const now = steps(trace).at(-1);
  return (
    <p className="mw-ask-thinking" aria-label="Working" aria-live="polite">
      <span className="mw-spin" aria-hidden="true" />
      <span className="ph" data-failed={now?.failed ? "" : undefined}>{now?.phrase ?? "Working…"}</span>
    </p>
  );
}

/** How the answer was reached, kept and collapsed — the same disclosure the sources take.
 *
 *  It is the LIVE LINE's log, and it is here rather than above because a reader reads the answer
 *  first and audits it second. Derived from the answer's own `invocations` rather than from the
 *  progress the drawer held: the answer carries the same calls, complete, and re-deriving it from the
 *  wire means a transcript that survives a reload holds the same account as the one that watched it
 *  happen.
 */
function ThoughtLog({ trace }: { trace: AssistantInvocation[] }) {
  const log = steps(trace);
  if (log.length === 0) return null;
  return (
    <details className="mw-ask-thought">
      <summary>{log.length} step{log.length === 1 ? "" : "s"}</summary>
      <ol className="mw-ask-trace">
        {log.map((step, i) => (
          <li key={i} data-failed={step.failed ? "" : undefined}>{step.phrase}</li>
        ))}
      </ol>
    </details>
  );
}

/** APP-ASSISTANT-REFS — what an answer may mean that markdown does not.
 *
 *  Two slots, and both DECLINE by default. A `cql-databricks:` target the parser does not recognise, a
 *  reference the lens cannot resolve, an absent lens, a chart block that is not valid JSON — every one
 *  of them falls through to the plain rendering the drawer would have done anyway. That is the
 *  degradation the document's own bindings follow (`SPEC-DEFINE-REF`: unresolved, it is just words),
 *  and it is what makes this safe to hand a model: the worst a mistyped citation costs is the citation.
 */
function answerSlots(lens: SpecLens | undefined, funnel: Funnel | null): MarkdownSlots {
  return {
    link: (text, href) => {
      const segment = referenceSegment(text, href);
      if (!segment || !lens) return null;
      // THE DOCUMENT'S OWN RENDERER, not a copy of it. This is the whole reason `Segments` is exported:
      // the phrase gets the link ink, the lens popup and the verbs a phrase in the specification gets,
      // because it IS the same control (`SPEC-REF-LENS`).
      return <Segments segments={[segment]} metrics={lens.inspector.metrics} reads={lens.reads} />;
    },
    fence: (lang, body) => {
      const artifact = ARTIFACT_FENCE[lang];
      if (artifact === "funnel") {
        // The DOCUMENT's funnel, not one rebuilt from the answer: the drawer is about the measure
        // above it, and a second construction of one chart is two answers to one question.
        return funnel ? <FunnelView funnel={funnel} /> : null;
      }
      if (artifact === "chart") {
        const spec = parseChart(body);
        return spec ? <AnswerChart spec={spec} /> : null;
      }
      return null;
    },
  };
}

/** The answer, with its provenance stated as structure rather than left to the prose. */
function AnswerBody({ answer, lens, funnel }: {
  answer: AssistantAnswer; lens?: SpecLens; funnel: Funnel | null;
}) {
  const derived = answer.invocations.filter((i) => i.provenance === "derived" && !i.error);
  const engine = answer.invocations.filter((i) => i.provenance === "engine" && !i.error);
  const slots = useMemo(() => answerSlots(lens, funnel), [lens, funnel]);
  return (
    <div className="mw-ask-a">
      {/* APP-ASSISTANT-MARKDOWN — the answer is MARKDOWN, through the same renderer the document
          above it quotes a steward with (`markdown.tsx`). It is the app's only markdown dialect, so a
          list in the drawer and a list in the specification are one thing rendered one way; and the
          model is told the supported set (`assistant/ask.py`), which is what keeps an advertised
          construct from arriving as punctuation. */}
      <div className="mw-ask-prose"><Markdown text={answer.text} slots={slots} /></div>

      {/* SVC-ANSWER-PROVENANCE — the standing warning, once per answer rather than per number. A
          derived figure and a measure result are both legitimate and are NOT the same claim. */}
      {answer.hasDerivedFigures && (
        <p className="mw-ask-derived">
          Some figures above were computed by the assistant from the result tables, not by the measure.
          They may differ from what the document reports.
        </p>
      )}

      {/* The trail: how it was reached, and what it was reached from. Both collapsed, side by side —
          a reader reads the answer first and audits it second, and the two disclosures answer the two
          different questions an audit asks (what did it DO, and whose numbers are these). */}
      <div className="mw-ask-trail">
        <ThoughtLog trace={answer.invocations} />
        {(engine.length > 0 || derived.length > 0) && (
          <details className="mw-ask-sources">
            <summary>
              {engine.length + derived.length} source{engine.length + derived.length === 1 ? "" : "s"}
            </summary>
            <ul>
              {engine.map((inv, i) => <SourceRow key={`e${i}`} inv={inv} />)}
              {derived.map((inv, i) => <SourceRow key={`d${i}`} inv={inv} />)}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

/** One source line. The HANDLE is the citation — an address anyone can re-read,
 *  which is what lets someone who was not in the conversation reproduce the answer. */
function SourceRow({ inv }: { inv: AssistantInvocation }) {
  return (
    <li className="mw-ask-src" data-provenance={inv.provenance}>
      <span className="pv">{inv.provenance === "engine" ? "measure" : "assistant"}</span>
      <span className="wh">{inv.handle ?? String(inv.arguments.sql ?? inv.tool)}</span>
    </li>
  );
}

/** What the chip prints: the narrowest true thing about the scope. */
function scopeLabel(sel: AssistantSelection): string {
  if (sel.define) return sel.define;
  if (sel.library) return sel.version ? `${sel.library} ${sel.version}` : sel.library;
  return "";
}
