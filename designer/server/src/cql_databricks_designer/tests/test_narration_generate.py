"""APP-NARRATIVE-OVERLAY, SPEC-EXPLAIN-VOICE — generation: the prompt, the visible retry, and rejection.

Against a fake completion, never a workspace or a metered model.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cql_databricks_designer.explain import generate as gen
from cql_databricks_designer.explain.checks import SCHEMA_PATH

from .test_explain_accounting import REFERENCE_SET, VALID


class _Client:
    """Replies in order, recording every request."""

    def __init__(self, *replies, finish_reason: str = "stop"):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.finish_reason = finish_reason

    def _create(self, *, model, messages):
        self.requests.append({"model": model, "messages": [dict(m) for m in messages]})
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=self.finish_reason)])


def _run(client) -> list[dict]:
    return list(gen.generate(library="SampleBP", version="1.0.0", source="library SampleBP version '1.0.0'",
                             reference_set=REFERENCE_SET, client=client, endpoint="chat-endpoint"))


def test_the_prompt_carries_the_contract_and_the_library():
    """SPEC-EXPLAIN-VOICE, SPEC-EXPLAIN-INPUTS — the writing rules and schema verbatim, then the library."""
    system, user = gen.messages(library="SampleBP", version="1.0.0", source="library SampleBP",
                                reference_set=REFERENCE_SET)
    assert gen.skill_text() in system["content"]
    assert SCHEMA_PATH.read_text(encoding="utf-8") in system["content"]
    assert "library SampleBP" in user["content"]
    assert '"Hypertension Diagnoses"' in user["content"]


def test_a_first_reply_that_passes_is_generated_in_one_attempt():
    client = _Client(VALID)
    events = _run(client)
    assert [e["type"] for e in events] == ["stage", "stage", "generated"]
    result = events[-1]["result"]
    assert result.instance == VALID and result.attempts == 1
    assert client.requests[0]["model"] == "chat-endpoint"


def test_a_failed_first_reply_is_sent_back_once_with_its_problems():
    """The retry is visible: the first attempt's problems are an event, and they are what the model is told."""
    client = _Client("not xml", VALID)
    events = _run(client)
    problems = [e for e in events if e["type"] == "problems"]
    assert len(problems) == 1 and problems[0]["attempt"] == 1
    assert problems[0]["problems"][0]["message"].startswith("not well-formed XML")
    retry = client.requests[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": "not xml"}
    assert "not well-formed XML" in retry[-1]["content"]
    assert events[-1]["result"].attempts == 2


def test_two_failures_reject_with_the_last_problems():
    client = _Client("not xml", VALID.replace('version="1.0.0" context', 'version="9" context'))
    with pytest.raises(gen.NarrationRejected) as rejected:
        _run(client)
    assert rejected.value.attempts == gen.MAX_ATTEMPTS == 2
    assert any("Identity is SampleBP@9" in p.message for p in rejected.value.problems)


def test_a_reply_cut_off_at_the_output_limit_is_a_problem_even_when_it_parses():
    client = _Client(VALID, VALID, finish_reason="length")
    with pytest.raises(gen.NarrationRejected) as rejected:
        _run(client)
    assert rejected.value.problems[0].message == "the reply was cut off at the endpoint's output limit"


def test_a_block_list_reply_is_read_for_its_text():
    """Some endpoints answer with reasoning and text blocks; only the text is the document."""
    client = _Client([{"type": "reasoning", "summary": []}, {"type": "text", "text": VALID}])
    assert _run(client)[-1]["result"].instance == VALID
