"""APP-ASSISTANT — the in-product tool loop, over the Databricks Foundation Model API.

The drawer's driver (`agent/README.md`). It is **one client of the tool
surface**, not a private backend: the definitions it sends are generated from the same `FastMCP`
server the `mcp-cql-databricks` App publishes, and every call is dispatched through that server **in
process** — no self-HTTP hop through our own `/mcp`.

**The definitions are GENERATED, never written** (`SVC-MCP-TOOLS`). ``list_tools()`` already carries
each tool's name, its model-facing description and its JSON Schema, so :func:`tool_definitions` only
reshapes them into the wrapper model serving expects. A hand-maintained second set is two sources for
one signature, which is the exact drift the shared tool module exists to prevent — and it would fail
silently, because a stale definition still produces a well-formed call.

**Model Serving exposes an OpenAI-compatible chat API**, reached as the signed-in user (`SEC-OBO`),
so the tool loop below is ours to write. Any chat endpoint on Model Serving works.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP

from cql_databricks_common import config

from .wiring import in_environment

log = logging.getLogger("cql_databricks.agent.server.ask")

#: How many model turns may call tools before an answer is required. An uncapped loop against a
#: metered endpoint is a bill rather than an answer, and exceeding the cap RAISES
#: (`KNOWLEDGE-FAIL-LOUD`): an answer composed from a half-finished investigation is the one failure
#: mode nobody can see in the output, because it reads exactly like a finished one.
#:
#: **24 LEAVES ROOM FOR A REAL INVESTIGATION.** An ordinary question reads the specification, the
#: output and its freshness, then runs several derivations over the handle. Five tools whose whole
#: design is that `get_output` returns a handle and `query_data` interrogates it (`SVC-MCP-TOOLS`)
#: mean a real answer is a SEQUENCE of calls, and a cap sized for one or two turns stops the model on
#: its first honest question.
MAX_ROUNDS = 24

#: Whose number a tool produces (`SVC-ANSWER-PROVENANCE`). It is the wire's copy of the fact
#: `model.Output.provenance` already states for the payload, restated here because the wire has to
#: classify every call and only one of them carries an `Output`.
#:
#: - ``engine`` — the compiler and the executor produced it; the same number the screen shows.
#: - ``derived`` — the agent's own derivation over the lakehouse. Legitimate; a different claim.
#: - ``context`` — produced no figure at all (a specification, a table list, freshness evidence, a
#:   skill). Not a third kind of number: a third kind of call.
PROVENANCE_BY_TOOL: dict[str, str] = {
    "get_output": "engine",
    "query_data": "derived",
    "get_specification": "context",
    "list_tables": "context",
    "get_freshness": "context",
    "list_skills": "context",
    "get_skill": "context",
    # AGENT-SAVED-RESULTS — a saved calculation's populations and score: the measure's own figures.
    "get_population_summary": "engine",
    "find_results": "context",
    "list_definitions": "context",
    "get_definition": "context",
    "list_value_sets": "context",
    "value_sets_with_code": "context",
    "check_cql": "context",
    # AGENT-INLINE-EXPERIMENTS — figures from CQL the agent ran inline are never the measure's own.
    "run_cql": "derived",
    "compare_cql": "derived",
}


class AssistantError(RuntimeError):
    """The loop could not produce an answer. Raised rather than returning a partial one."""


@dataclass(frozen=True)
class Selection:
    """APP-ASSISTANT-CONTEXT — what the user is looking at when they ask.

    **It PREFILLS tool arguments and is nothing else.** It reaches the model as one structured block
    the model reads arguments out of; the server never rewrites a call the model made. That is the
    difference between a default and ambient state, and it is what keeps the tools usable by a client
    that has no UI: every tool still takes its scope explicitly, and a caller without a selection is
    not a caller who can ask less.

    Every field is optional because every field is droppable — the chip is removable, and a question
    broader than the selection is the case the drawer exists for.
    """

    library: str | None = None
    version: str | None = None
    #: RUN-ADDRESS-LOCATION — where the library is read: ``store:<catalog>.<schema>`` or ``folder:<path>``.
    location: str | None = None
    define: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    result_set: dict[str, Any] | None = None

    #: What those identifiers are CALLED — the measure's title and the environment's name, exactly as
    #: the screen prints them. NOT tool arguments, and sent in their own block for that reason: the
    #: rule above is that the selection prefills arguments, and a title is not an argument to anything.
    #:
    #: They earn their place because an identifier is not a name a clinician recognises. Without them
    #: the assistant says "HypertensionBPControl" at a reader who calls it *Hypertension: BP Control*, or it
    #: spends a tool round-trip resolving what the screen already had on it. The environment is not an
    #: argument: the drawer passes it to :func:`ask_events`, which runs every tool in it
    #: (`AGENT-DEFAULT-ENVIRONMENT`); its name is here only so the answer can say whose data it read.
    measure_title: str | None = None
    environment_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The ARGUMENT half — the values a tool call may be filled from."""
        pairs = {
            "library": self.library,
            "version": self.version,
            "location": self.location,
            "define": self.define,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "result_set": self.result_set,
        }
        return {k: v for k, v in pairs.items() if v}

    def labels(self) -> dict[str, str]:
        """The LABEL half — what to call those things when talking to the reader."""
        pairs = {"measure_title": self.measure_title,
                 "environment_name": self.environment_name}
        return {k: v for k, v in pairs.items() if v}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Selection:
        data = data or {}
        return cls(
            library=data.get("library"),
            version=data.get("version"),
            location=data.get("location"),
            define=data.get("define"),
            period_start=data.get("periodStart") or data.get("period_start"),
            period_end=data.get("periodEnd") or data.get("period_end"),
            result_set=data.get("resultSet") or data.get("result_set"),
            measure_title=data.get("measureTitle") or data.get("measure_title"),
            environment_name=data.get("environmentName") or data.get("environment_name"))


@dataclass
class Invocation:
    """One tool call the loop made, and whose number it produced.

    **This is `SVC-ANSWER-PROVENANCE` on the wire**, which is where the requirement puts it: prose
    saying *I derived this* is a claim the model can forget to make, and a client that renders an
    agent-derived figure like a measure result has reintroduced the failure whether or not the
    sentence above it was careful. The client renders from `provenance`; the prose is the
    explanation, not the evidence.
    """

    tool: str
    arguments: dict[str, Any]
    provenance: str
    #: The relation the call addressed, when it addressed one (`SVC-TOOL-ADDRESS`). A figure's
    #: reproducible citation is this plus the fingerprint, never a conversation id.
    handle: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "tool": self.tool,
            "arguments": self.arguments,
            "provenance": self.provenance,
        }
        if self.handle:
            d["handle"] = self.handle
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class Answer:
    """What `/api/assistant/ask` returns."""

    text: str
    invocations: list[Invocation] = field(default_factory=list)
    #: The messages the next turn should send back, so the conversation is the CLIENT's state. The
    #: server holds no session — the same reason the tools hold no run id (`SVC-TOOL-ADDRESS`).
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_derived_figures(self) -> bool:
        """True when any answered figure is the agent's own derivation rather than the engine's."""
        return any(i.provenance == "derived" and not i.error for i in self.invocations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "invocations": [i.to_dict() for i in self.invocations],
            "messages": self.messages,
            "hasDerivedFigures": self.has_derived_figures,
        }


def tool_definitions(server: FastMCP) -> list[dict[str, Any]]:
    """The OpenAI-shaped tool definitions, GENERATED from the MCP server's own tools.

    One signature per tool, one description per tool. The description is what MCP hands an external
    agent and what model serving hands this one, which is the whole point of generating rather than
    writing: a tool whose contract improves for the Genie Agent improves here in the same edit.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters,
            },
        }
        for tool in asyncio.run(server.list_tools())
    ]


#: APP-ASSISTANT-MARKDOWN — what the DRAWER renders, told to the model that writes into it.
#:
#: **It lives here and NOT in `ANSWERING_INSTRUCTIONS`, which is the whole point.** That module is the
#: guidance both clients answer from — the Designer's drawer and the external agent over MCP — and it holds
#: only what is true of the TOOLS (`SVC-MCP-SKILLS`). This is true of one client's RENDERER: the set
#: below is exactly what `markdown.tsx` parses, and telling an external agent that its own chat surface
#: cannot show a heading would be a fact about our React component stated as though it were a fact
#: about the measure. An MCP client renders whatever it renders and needs no permission from us.
#:
#: **The list is a CONTRACT, so it is short and it is exact.** Advertising a construct the renderer
#: does not parse puts punctuation in the middle of a clinical answer; omitting one it does parse costs
#: a table the reader would have found easier to check than a paragraph of figures.
RENDERING_INSTRUCTIONS = """\
Write your answer in Markdown. The reader's surface renders exactly these:

- paragraphs, separated by a blank line
- **bold** for the figure or the word that carries the sentence
- `inline code` for anything that must be read or typed exactly — a define name, a table handle
- [link text](https://example.org) — http and https only
- bulleted lists (`- item`) and numbered lists (`1. item`), ONE LINE per item, never nested
- pipe tables: a header row, a `| --- | --- |` rule, then one row per record
- fenced code blocks (```), for a query or a snippet the reader may want to run or copy

Nothing else is parsed. Headings, block quotes, images and nested lists reach the reader as the
literal characters you typed, so do not use them.

Prefer sentences. Reach for a list when you are enumerating criteria, and for a table when you are
reporting more than two figures side by side — a clinician checking a population against its
definition reads a table faster than a paragraph of numbers.

## Point at the measure instead of describing it

A link whose target begins `cql-databricks:` becomes a LIVE REFERENCE in the reader's document — the same
mark, the same popup and the same verbs they get from the specification itself. Use them: a reader who
can open the thing you named does not have to take your word for what it is.

Every name below comes from `get_specification`. Do not invent one, and do not reference something
that tool did not list — a reference that resolves to nothing renders as your words, which is a
wasted sentence rather than a broken one.

- A criterion or define — `[patients with an encounter during the period](cql-databricks:define/<library>/<define>)`
- A figure the MEASURE computed — `[](cql-databricks:figure/<library>/<define>?as=count)`, or `as=share` for
  it as a percentage. Write NO number of your own: the reader's client fills it in from the same
  result the screen shows, so a citation cannot be stale and cannot be wrong.
- A read — `[condition records](cql-databricks:read/<profile>)`, using the profile the specification states.
- A value set or code — `[encounter type](cql-databricks:valueset/<library>/<name>)`.

Percent-encode a name that contains spaces. The words in the brackets are the READER'S — write the
phrase your sentence needs, never the declared name in title case.

## Artifacts, for what a sentence cannot carry

Two fenced blocks render as pictures rather than as code:

- ```cql-databricks-funnel — the population funnel, with nothing inside the fence. It draws the measure's own
  attrition. Reach for it when the question is where patients drop.
- ```cql-databricks-chart — one chart, from figures YOU derived, as JSON:
  {"type": "bar", "title": "Patients by age band", "x": "Age band", "y": "Patients",
   "rows": [["18-44", 812], ["45-64", 2104]]}
  `type` is `bar` (a breakdown) or `line` (movement over an ordered dimension). ONE series: a question
  that wants two wants two blocks. Every figure in a chart is YOUR derivation and is labelled as such
  for the reader, so never chart a measure result to make it look like the measure's own picture —
  cite that with `cql-databricks:figure` instead.
"""


def _system_messages(selection: Selection, instructions: str) -> list[dict[str, Any]]:
    """The standing instructions, the drawer's rendering contract, and the selection as context.

    Three concerns, so up to three messages rather than one paste. The first two are always sent and
    are deliberately separate: ``instructions`` is the server's own (`ANSWERING_INSTRUCTIONS` plus what
    a contributing feature adds), the guidance every client of the tools answers from (`SVC-MCP-SKILLS`), and `RENDERING_INSTRUCTIONS` is true only of THIS client's
    renderer — merging them would put a fact about `markdown.tsx` into the text an external agent
    receives at `initialize`.

    The selection is JSON rather than a sentence
    (`APP-ASSISTANT-CONTEXT`). Prose describing what the user is looking at invites the model to
    answer *from* it; a labelled block of argument values invites it to pass them, which is the only
    job the selection has. An empty selection sends nothing at all rather than an empty object — a
    caller with no selection must read as a caller who did not scope the question, not as one who
    scoped it to nothing.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instructions},
        {"role": "system", "content": RENDERING_INSTRUCTIONS},
    ]
    scope, labels = selection.to_dict(), selection.labels()
    if not scope and not labels:
        return messages

    parts = ["The user is looking at this in CQL on Databricks right now."]
    if scope:
        parts.append(
            "TOOL ARGUMENTS — use these to fill arguments the question does not otherwise "
            "determine. They are defaults, not limits: if the question is broader, call the tools "
            "with what the question asks for.\n" + json.dumps(scope, indent=2))
    if labels:
        # Kept SEPARATE from the arguments, and told plainly what it is for. A title is not an
        # argument to any tool, and a model that passed one as `library` would address nothing.
        parts.append(
            "WHAT THESE ARE CALLED — use these names when you write about them, never the "
            "identifiers above. Do not pass them as tool arguments.\n" + json.dumps(labels, indent=2))
    messages.append({"role": "system", "content": "\n\n".join(parts)})
    return messages


def _call_tool(server: FastMCP, name: str, arguments: dict[str, Any],
               environment: str | None = None) -> tuple[str, Invocation]:
    """Dispatch one call through the MCP server IN PROCESS, in ``environment`` (`AGENT-DEFAULT-ENVIRONMENT`), and
    record what it was. The environment is bound around this one call, not across the loop: a streaming
    response runs each step in its own copy of the request context.

    A failed tool call is returned to the MODEL as its result rather than raised
    (`KNOWLEDGE-FAIL-LOUD` governs answers, and this is not the answer yet): a define that has never
    been computed, or a SQL error, is information the model should act on — by asking `get_freshness`
    why, or by fixing its query — and turning it into a 500 would replace a recoverable step with a
    dead conversation. It is recorded on the invocation, so the client can see the loop stumbled even
    when the prose does not mention it.
    """
    invocation = Invocation(tool=name, arguments=arguments,
                            provenance=PROVENANCE_BY_TOOL.get(name, "context"))
    try:
        with in_environment(environment):
            result = asyncio.run(server.call_tool(name, arguments))
    except Exception as e:  # noqa: BLE001 — handed to the model as a result; see the docstring
        log.info("APP-ASSISTANT: tool %s failed: %s", name, e)
        invocation.error = str(e)
        return f"ERROR: {e}", invocation

    structured = result.structured_content
    payload = structured.get("result", structured) if isinstance(structured, dict) else None
    if payload is None:
        payload = "".join(block.text for block in result.content if hasattr(block, "text"))
    if isinstance(payload, dict):
        # SVC-TOOL-ADDRESS — the handle is the reproducible citation, so it is lifted onto the
        # invocation rather than left for the client to dig out of a tool payload it does not parse.
        invocation.handle = payload.get("handle")
    return payload if isinstance(payload, str) else json.dumps(payload, default=str), invocation


def ask_events(question: str, *, server: FastMCP, selection: Selection | None = None,
               history: list[dict[str, Any]] | None = None, environment: str | None = None,
               client: Any = None, endpoint: str | None = None) -> Iterator[dict[str, Any]]:
    """The loop, **as a stream of events** — and the one implementation of it.

    A real answer is a SEQUENCE of calls, not one round: `get_output` returns a handle and
    `query_data` interrogates it (`SVC-MCP-TOOLS`), so an ordinary question spends several model turns
    before it has anything to say. A client with nothing to show has only a spinner for the whole
    investigation.

    So each tool result is emitted **as it lands**, and this is `THEME-GLASSBOX` rather than a progress
    bar: what the reader watches is the actual sequence of reads the answer will be built from — the
    specification, the output, its freshness — which is the same evidence the finished answer cites.
    A percentage would say how long to wait; this says what is being done.

    Events are ``tool`` (one invocation completed) then exactly one ``answer``. ``client`` and
    ``endpoint`` are injected for the reason the warehouse is in the service's `data.py`: testable against a fake
    completion rather than a workspace and a metered model.

    ``environment`` is the one the Designer has picked (`DOC-ENVIRONMENT-PICKER`); every tool runs in it, and None
    runs them in the default (`AGENT-DEFAULT-ENVIRONMENT`). The model never sees it as an argument.
    """
    client = client or serving_client()
    endpoint = endpoint or config.assistant_endpoint()
    definitions = tool_definitions(server)

    messages = list(history) if history else _system_messages(selection or Selection(), server.instructions or "")
    messages.append({"role": "user", "content": question})

    invocations: list[Invocation] = []
    for _ in range(MAX_ROUNDS):
        completion = client.chat.completions.create(
            model=endpoint, messages=messages, tools=definitions)
        choice = completion.choices[0].message
        calls = choice.tool_calls or []
        # The assistant turn goes back verbatim, tool calls included: the next request has to carry
        # the call the tool results answer, or the endpoint rejects the orphaned `tool` messages.
        messages.append(_assistant_message(choice, calls))

        if not calls:
            answer = Answer(text=answer_text(choice.content), invocations=invocations,
                            messages=messages)
            yield {"type": "answer", **answer.to_dict()}
            return

        for call in calls:
            arguments = json.loads(call.function.arguments or "{}")
            content, invocation = _call_tool(server, call.function.name, arguments, environment)
            invocations.append(invocation)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            yield {"type": "tool", "invocation": invocation.to_dict()}

    raise AssistantError(
        f"The assistant called tools {MAX_ROUNDS} times without reaching an answer. The last tools "
        f"it tried were {[i.tool for i in invocations[-3:]]}. Returning a partial answer would "
        "present a half-finished investigation as a conclusion.")


def ask(question: str, **kwargs: Any) -> Answer:
    """The same loop, drained to its answer.

    A thin CONSUMER of :func:`ask_events`, not a second implementation — there is one loop, and this
    is the shape a caller with nothing to show progress to wants (the tests, and anything calling in
    process). The terminal event is the answer by construction: `ask_events` either yields one or
    raises.
    """
    answer: Answer | None = None
    for event in ask_events(question, **kwargs):
        if event["type"] == "answer":
            answer = Answer(
                text=event["text"],
                invocations=[Invocation(**{**i, "arguments": i["arguments"]})
                             for i in event["invocations"]],
                messages=event["messages"])
    assert answer is not None, "ask_events yields an answer or raises"
    return answer


def answer_text(content: Any) -> str:
    """The prose out of a completion's ``content``, whichever shape the endpoint used.

    **Some chat endpoints answer with a LIST OF BLOCKS**, not a string:
    ``[{"type": "reasoning", ...}, {"type": "text", "text": "…"}]``. The default endpoint does, and
    without this the block list and a reasoning signature would land in the field the drawer renders. This takes the ``text`` blocks and drops the rest — the reasoning is
    the model's working, not its answer, and `THEME-GLASSBOX` is served by the invocation list, which
    says what was actually run rather than what the model said about running it.

    The block list still goes back to the endpoint VERBATIM on the next round (`_assistant_message`):
    it is what the endpoint handed us, signature included, and normalizing the conversation would
    strip the reasoning the model expects to see echoed.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _assistant_message(choice: Any, calls: list[Any]) -> dict[str, Any]:
    """The model's own turn, reshaped for the next request."""
    message: dict[str, Any] = {"role": "assistant", "content": choice.content or ""}
    if calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]
    return message


def serving_client() -> Any:
    """The Foundation Model API client, as the **signed-in user** (`APP-ASSISTANT`, `SEC-OBO`).

    No API key: the credentials and the endpoint are the workspace's.

    **On behalf of the user, not the App.** The prompt this client sends carries whatever the tool
    loop just read — measure results, define profiles, the rows behind a rate — so the call is a
    data-touching call and belongs on the same side of the seam as the reads that fed it. As the
    service principal the endpoint's own access control would answer for the App rather than for the
    reader, which is ambient authority in the one place the drawer's output is most likely to be quoted
    as fact.

    Built per request through `user_client()` and never cached: the forwarded token belongs to one
    user and expires on its own schedule, and the assistant is exactly the per-request path a cached
    identity would leak across. Locally the shared client already IS the signed-in user
    (`SEC-LOCAL-IDENTITY`), so this costs nothing there.

    The user needs `CAN QUERY` on the endpoint; the App's service principal no longer does.
    """
    from cql_databricks_common.workspace import user_client

    return user_client().serving_endpoints.get_open_ai_client()
