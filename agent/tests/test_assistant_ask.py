"""APP-ASSISTANT — the drawer's tool loop over the Foundation Model API.

Driven against a FAKE chat client (`_FakeClient`) so the loop is testable without a workspace or a
metered endpoint — the same seam discipline `tools.py` applies to the warehouse. What is pinned is
the part that is ours: that the definitions are generated rather than written, that a selection
prefills arguments without becoming ambient state, that provenance reaches the wire, and that the
loop refuses to answer from a half-finished investigation.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.tests.durable_fixture import RESULT_SET, reference

from cql_databricks.agent import ask as ask_module
from cql_databricks.agent import skills
from cql_databricks.agent.ask import (
    MAX_ROUNDS, AssistantError, Selection, ask, ask_events, tool_definitions)
from cql_databricks.agent.registry import ToolRegistry
from cql_databricks.agent.server import create_mcp


#: A registered Data Source, as `list_data_sources` reads one. A `SimpleNamespace` rather than the real
#: dataclass because the tool touches four attributes and the seam exists precisely so a test need not
#: build a registry (`docs/coding-standards.md`, the storage-seam pattern).
SOURCE = SimpleNamespace(id="sample-patients", name="Sample Patients",
                         description="Fabricated patient data for development",
                         change_data_log="cat.sch.change_log")


def _contributed() -> ToolRegistry:
    """The designer's contribution, stood in (AGENT-TOOL-REGISTRY)."""
    registry = ToolRegistry()

    @registry.tool
    def get_specification(library: str, version: str) -> dict | None:
        """What a measure DECLARES: its defines and populations. Start here; every other tool takes a
        define name that this one lists."""
        return {"library": library, "defines": []}

    return registry


@pytest.fixture
def server():
    return create_mcp(
        warehouse=lambda sql: [["a", "bigint"]] if "information_schema" in sql else [[7]],
        # DEC-EVALUATOR-OPAQUE — the evaluator's answer for every node: its stored table and the set's filter.
        computed_for=lambda *scope: ["denominator"],
        reference_for=lambda address, define: reference(),
        data_sources=lambda: [SOURCE],
        contributed=_contributed(),
    )


def _tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(
        name=name, arguments=json.dumps(arguments)))


class _FakeClient:
    """A chat completions client that replays a scripted list of assistant turns.

    Records every request, because half of what this module does is decide what the model is SENT.
    """

    def __init__(self, turns: list[SimpleNamespace]):
        self._turns = list(turns)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs) -> SimpleNamespace:
        # SNAPSHOT the messages. The loop appends to the same list across rounds, so recording the
        # reference would make every assertion read the FINAL state — the real client serializes the
        # request during the call, which is what these assertions are meant to reflect.
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        turn = self._turns.pop(0) if self._turns else SimpleNamespace(content="done", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=turn)])


def _answering(text: str = "The denominator holds 7 patients.") -> SimpleNamespace:
    return SimpleNamespace(content=text, tool_calls=[])


def _run(server, client, **kwargs):
    return ask("How many patients?", server=server, client=client, endpoint="test-endpoint",
               **kwargs)


class TestToolDefinitions:
    """SVC-MCP-TOOLS — one signature per tool, generated from the server that publishes it."""

    def test_every_published_tool_is_offered_to_the_model(self, server):
        names = {d["function"]["name"] for d in tool_definitions(server)}
        assert names == {"get_specification", "list_data_sources", "list_tables", "get_output",
                         "query_data", "get_freshness", "list_skills", "get_skill"}

    def test_the_definitions_carry_the_same_text_the_mcp_client_reads(self, server):
        # The whole reason to generate rather than write: a tool whose description improves for the
        # Genie Agent improves here in the same edit. A hand-written second set would drift SILENTLY,
        # because a stale definition still produces a well-formed call.
        import asyncio
        published = {t.name: t for t in asyncio.run(server.list_tools())}
        for definition in tool_definitions(server):
            function = definition["function"]
            assert function["description"] == published[function["name"]].description
            assert function["parameters"] == published[function["name"]].parameters

    def test_the_shape_is_what_model_serving_expects(self, server):
        for definition in tool_definitions(server):
            assert definition["type"] == "function"
            assert set(definition["function"]) == {"name", "description", "parameters"}


class TestContext:
    """APP-ASSISTANT-CONTEXT — the selection prefills arguments; it is never ambient state."""

    def test_the_standing_instructions_lead_every_conversation(self, server):
        client = _FakeClient([_answering()])
        _run(server, client)
        assert client.requests[0]["messages"][0] == {
            "role": "system", "content": skills.ANSWERING_INSTRUCTIONS}

    def test_the_selection_arrives_as_structured_values_not_prose(self, server):
        # Prose describing what the user is looking at invites the model to answer FROM it. A
        # labelled block of argument values invites it to pass them, which is the only job it has.
        client = _FakeClient([_answering()])
        _run(server, client, selection=Selection(
            library="BasicEncounterAndCondition", version="1.0.0", data_source_id="ds-1"))
        scope = client.requests[0]["messages"][2]["content"]
        payload = json.loads(scope[scope.index("{"):])
        assert payload == {"library": "BasicEncounterAndCondition", "version": "1.0.0",
                           "data_source_id": "ds-1"}

    def test_no_selection_sends_no_scope_block_at_all(self, server):
        # Not an empty object: a caller with no selection must read as one who did not scope the
        # question, not as one who scoped it to nothing. The two standing system messages (the
        # answering contract and the rendering one) are always there; the scope block is not.
        client = _FakeClient([_answering()])
        _run(server, client, selection=Selection())
        assert [m["role"] for m in client.requests[0]["messages"]] == ["system", "system", "user"]

    def test_the_rendering_contract_is_sent_and_is_NOT_the_shared_guidance(self, server):
        # APP-ASSISTANT-MARKDOWN — the drawer renders markdown, so the model is TOLD the set that
        # renders. It is a separate message on purpose: `ANSWERING_INSTRUCTIONS` is what BOTH clients
        # answer from (`SVC-MCP-SKILLS`), and what `markdown.tsx` parses is true of one of them.
        client = _FakeClient([_answering()])
        _run(server, client, selection=Selection())
        assert client.requests[0]["messages"][1] == {
            "role": "system", "content": ask_module.RENDERING_INSTRUCTIONS}
        assert "Markdown" not in skills.ANSWERING_INSTRUCTIONS

    def test_the_advertised_constructs_are_the_ones_the_renderer_parses(self, server):
        # The list is a CONTRACT with `markdown.tsx`. Advertising a construct it does not parse puts
        # punctuation in the middle of a clinical answer, which is the failure this text prevents.
        contract = ask_module.RENDERING_INSTRUCTIONS
        for construct in ("**bold**", "`inline code`", "[link text]", "- item", "1. item",
                          "| --- | --- |", "```"):
            assert construct in contract
        # ...and the ones it does not parse are named as not parsed, rather than left to be guessed.
        assert "Headings, block quotes, images and nested lists" in contract

    def test_the_reference_vocabulary_is_taught_to_THIS_client_only(self, server):
        # APP-ASSISTANT-REFS — `cql-databricks:` targets render as the document's own live references in the
        # drawer, and as a raw address at anybody else's reader. So the vocabulary is taught in the
        # drawer's own system message and is absent from the guidance every MCP client receives at
        # `initialize` — Genie One is never told a syntax it has no renderer for.
        contract = ask_module.RENDERING_INSTRUCTIONS
        for address in ("cql-databricks:define/", "cql-databricks:figure/", "cql-databricks:read/", "cql-databricks:valueset/",
                        "cql-databricks-funnel", "cql-databricks-chart"):
            assert address in contract
            assert address not in skills.ANSWERING_INSTRUCTIONS
        # The MCP server hands `ANSWERING_INSTRUCTIONS` over verbatim; this is that claim, at the seam.
        assert "cql-databricks:" not in (server.instructions or "")

    def test_a_cited_figure_is_addressed_rather_than_stated(self, server):
        # SVC-ANSWER-PROVENANCE — the client resolves `cql-databricks:figure` against the metric set the screen
        # renders, so a citation cannot be stale and cannot be wrong. The instruction has to SAY that,
        # or a model writes the number beside the reference and reintroduces exactly the staleness the
        # reference exists to remove.
        contract = ask_module.RENDERING_INSTRUCTIONS
        assert "Write NO number of your own" in contract
        # ...and the chart, whose figures the model DOES write, is labelled as the model's own.
        assert "Every figure in a chart is YOUR derivation" in contract

    def test_the_labels_are_sent_and_are_NOT_tool_arguments(self, server):
        # The reason the two halves are separate blocks: `measure_title` is not an argument to
        # anything, and a model that passed it as `library` would address a relation that does not
        # exist. It is sent so the assistant writes "Encounter and Condition" rather than
        # "BasicEncounterAndCondition" at a clinician who calls it the former.
        client = _FakeClient([_answering()])
        _run(server, client, selection=Selection(
            library="BasicEncounterAndCondition", version="1.0.0",
            data_source_id="sample-patients",
            measure_title="Encounter and Condition",
            data_source_name="Sample Patients"))
        content = client.requests[0]["messages"][2]["content"]
        assert "Encounter and Condition" in content
        assert "Sample Patients" in content
        # ...and the model is told, in the block itself, which half is which.
        assert "TOOL ARGUMENTS" in content
        assert "Do not pass them as tool arguments" in content
        # The argument block still carries only arguments.
        arguments = json.loads(content[content.index("{"):content.index("}") + 1])
        assert "measure_title" not in arguments and "data_source_name" not in arguments

    def test_labels_alone_still_reach_the_model(self, server):
        # A selection can carry a title and no addressable scope — a measure open with no data source
        # chosen is the ordinary first minute of a session. Saying nothing then would leave the
        # assistant unable to name what is on the screen.
        client = _FakeClient([_answering()])
        _run(server, client, selection=Selection(measure_title="Encounter and Condition"))
        assert "Encounter and Condition" in client.requests[0]["messages"][2]["content"]

    def test_the_server_never_rewrites_a_call_the_model_made(self, server):
        # The line between a default and ambient state. The model fills arguments from the scope
        # block; nothing is injected behind it, so what the client is shown is what actually ran.
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "get_specification", {"library": "EncounterDuringPeriod", "version": "1.0.0"})]),
            _answering()])
        answer = _run(server, client, selection=Selection(library="BasicEncounterAndCondition", version="1.0.0"))
        assert answer.invocations[0].arguments == {"library": "EncounterDuringPeriod", "version": "1.0.0"}


class TestProvenance:
    """SVC-ANSWER-PROVENANCE — two kinds of number, distinguished on the WIRE and not only in prose."""

    def test_an_engine_computed_figure_is_labelled_engine(self, server):
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[_tool_call("c1", "get_output", {
                "result_set": RESULT_SET, "define": "Synthetic.Output"})]),
            _answering()])
        answer = _run(server, client)
        assert answer.invocations[0].provenance == "engine"
        assert not answer.has_derived_figures

    def test_an_agent_derived_figure_is_labelled_derived(self, server):
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "query_data", {"sql": "SELECT count(*) FROM t"})]),
            _answering()])
        answer = _run(server, client)
        assert answer.invocations[0].provenance == "derived"
        # The flag the client renders on: prose saying "I derived this" is a claim the model can
        # forget to make, and a derived number rendered like a measure result is the exact failure.
        assert answer.has_derived_figures

    def test_a_call_that_produces_no_figure_is_neither(self, server):
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "get_freshness", {
                    "result_set": RESULT_SET, "define": "Synthetic.Output"})]),
            _answering()])
        assert _run(server, client).invocations[0].provenance == "context"

    def test_the_handle_travels_so_the_answer_can_be_reproduced(self, server):
        # SVC-TOOL-ADDRESS — the citation is the address, never a conversation id, and it is lifted
        # onto the invocation rather than left inside a payload the client does not parse.
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[_tool_call("c1", "get_output", {
                "result_set": RESULT_SET, "define": "Synthetic.Output"})]),
            _answering()])
        handle = _run(server, client).invocations[0].handle
        assert handle == reference().relation()

    def test_provenance_survives_onto_the_wire_shape(self, server):
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "query_data", {"sql": "SELECT 1"})]),
            _answering()])
        payload = _run(server, client).to_dict()
        assert payload["invocations"][0]["provenance"] == "derived"
        assert payload["hasDerivedFigures"] is True


class TestEventStream:
    """APP-ASSISTANT — the loop reports each tool AS IT LANDS (`THEME-GLASSBOX`).

    A real answer is a SEQUENCE of calls, so a client that only saw the end would show a spinner for
    the whole investigation. What the reader watches is the same evidence the finished answer cites.
    """

    def test_a_tool_is_reported_before_the_answer_exists(self, server):
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "get_specification", {"library": "BasicEncounterAndCondition", "version": "1.0.0"})]),
            _answering()])
        events = list(ask_events("How many patients?", server=server, client=client,
                                 endpoint="test-endpoint"))
        assert [e["type"] for e in events] == ["tool", "answer"]
        assert events[0]["invocation"]["tool"] == "get_specification"

    def test_the_terminal_event_carries_the_whole_answer(self, server):
        # The stream is not a substitute for the answer: the last event has the prose, every
        # invocation, and the conversation to send back. A client that dropped every `tool` event
        # would still be correct, only blind.
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "query_data", {"sql": "SELECT 1"})]),
            _answering()])
        final = list(ask_events("q", server=server, client=client, endpoint="test-endpoint"))[-1]
        assert final["type"] == "answer"
        assert final["text"] == "The denominator holds 7 patients."
        assert [i["tool"] for i in final["invocations"]] == ["query_data"]
        assert final["messages"][-1]["role"] == "assistant"

    def test_a_failed_tool_is_reported_rather_than_hidden(self, server):
        # The loop hands the error back to the model and carries on, so the trace has to say the
        # stumble happened — otherwise it tells a nicer story than the run.
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "query_data", {"sql": "DROP TABLE patients"})]),
            _answering()])
        events = list(ask_events("q", server=server, client=client, endpoint="test-endpoint"))
        assert events[0]["invocation"]["error"]

    def test_running_out_of_rounds_raises_rather_than_yielding_an_answer(self, server):
        # KNOWLEDGE-FAIL-LOUD — the events already emitted are a real trace of what ran; what must not
        # happen is a terminal `answer` composed from a half-finished investigation.
        forever = [SimpleNamespace(content="", tool_calls=[
            _tool_call(f"c{i}", "query_data", {"sql": "SELECT 1"})]) for i in range(MAX_ROUNDS + 2)]
        stream = ask_events("q", server=server, client=_FakeClient(forever), endpoint="e")
        emitted = []
        with pytest.raises(AssistantError):
            for event in stream:
                emitted.append(event)
        assert emitted and all(e["type"] == "tool" for e in emitted)


class TestLoop:
    def test_a_tool_result_goes_back_paired_with_the_call_that_asked_for_it(self, server):
        # The endpoint rejects an orphaned `tool` message, so the assistant turn must go back
        # verbatim WITH its tool_calls. Getting this wrong fails only on the second round.
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "get_specification", {"library": "BasicEncounterAndCondition", "version": "1.0.0"})]),
            _answering()])
        _run(server, client)
        second = client.requests[1]["messages"]
        assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"
        assert second[-1] == {"role": "tool", "tool_call_id": "c1",
                              "content": second[-1]["content"]}

    def test_a_failing_tool_is_reported_to_the_model_not_raised(self, server):
        # A rejected query is information the model should act on — by fixing it. Raising would
        # replace a recoverable step with a dead conversation; it is recorded so the client can see
        # the loop stumbled even where the prose does not mention it.
        client = _FakeClient([
            SimpleNamespace(content="", tool_calls=[
                _tool_call("c1", "query_data", {"sql": "DROP TABLE patients"})]),
            _answering("I can only read data.")])
        answer = _run(server, client)
        assert answer.invocations[0].error
        assert answer.text == "I can only read data."
        assert "ERROR" in client.requests[1]["messages"][-1]["content"]

    def test_the_conversation_comes_back_so_the_client_owns_it(self, server):
        # No server-side session, for the reason the tools carry no run id: an answer reproducible
        # only from inside the conversation that produced it is not reproducible.
        answer = _run(server, _FakeClient([_answering()]))
        assert [m["role"] for m in answer.messages] == ["system", "system", "user", "assistant"]

    def test_prior_turns_are_replayed_rather_than_rebuilt(self, server):
        history = [{"role": "system", "content": "earlier"},
                   {"role": "user", "content": "first question"}]
        client = _FakeClient([_answering()])
        _run(server, client, history=history)
        assert client.requests[0]["messages"][:2] == history

    def test_a_block_list_answer_is_flattened_to_prose(self, server):
        # The default chat endpoint answers with a list of content blocks, not a string, so the field the drawer renders was getting a
        # block list and a base64 reasoning signature. The reasoning is the model's working, not its
        # answer — the invocation list is what makes the run inspectable.
        blocks = [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
                  {"type": "text", "text": "The denominator holds 7 patients."}]
        answer = _run(server, _FakeClient([SimpleNamespace(content=blocks, tool_calls=[])]))
        assert answer.text == "The denominator holds 7 patients."

    def test_the_block_list_goes_back_to_the_endpoint_verbatim(self, server):
        # Normalizing the CONVERSATION would strip the reasoning signature the endpoint expects to
        # see echoed. Only the client-facing text is flattened.
        blocks = [{"type": "reasoning", "signature": "abc"}, {"type": "text", "text": "hi"}]
        answer = _run(server, _FakeClient([SimpleNamespace(content=blocks, tool_calls=[])]))
        assert answer.messages[-1]["content"] == blocks

    def test_it_refuses_to_answer_from_a_half_finished_investigation(self, server):
        # KNOWLEDGE-FAIL-LOUD — an uncapped loop against a metered endpoint is a bill, and a partial
        # answer rendered as prose is indistinguishable from a finished one.
        forever = [SimpleNamespace(content="", tool_calls=[
            _tool_call(f"c{i}", "query_data", {"sql": "SELECT 1"})]) for i in range(MAX_ROUNDS + 2)]
        with pytest.raises(AssistantError) as caught:
            _run(server, _FakeClient(forever))
        assert str(MAX_ROUNDS) in str(caught.value)
