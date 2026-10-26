"""APP-NARRATIVE-OVERLAY, SPEC-EXPLAIN-VOICE — write one library's narration with the model, then check it.

The model gets the writing rules (`clinical-explain/SKILL.md`, the same file the assistant drawer serves),
the Specification schema, the library's CQL and its reference set (`SPEC-EXPLAIN-INPUTS`). It returns one
Specification instance, which :func:`explain.checks.check` must pass with no problem at all.

ONE RETRY, AND IT IS VISIBLE. A first reply that fails is sent back with its problems listed, once. The
caller sees both attempts as events. A second failure raises :class:`NarrationRejected` with the
problems, and nothing is shown or cached. The reply is never repaired here: a fence, a missing attribute
or a wrong span is the model's to fix, in the open.

One library per generation (`Narration is scoped to ONE library`, in the writing rules). An included
library is generated from its own request and cached under its own key.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from cql_databricks.agent.ask import answer_text
from cql_databricks.agent.skills import SKILLS

from .checks import NS, SCHEMA_PATH, Problem, check

#: The first reply and one retry with its problems. More attempts would be a bill, not a better chance.
MAX_ATTEMPTS = 2

#: The reply's output limit, asked for on every call. Left unset, the endpoint applies its own default
#: (8,192 on the FM API), and the model's reasoning plus one library's document runs past it.
MAX_OUTPUT_TOKENS = 32_000

#: The output rules the writing rules leave to the emitter. The writing rules say how to write; these say
#: what the reply must be so that it can be checked mechanically.
OUTPUT_RULES = f"""\
## What to return

Return ONE Specification instance and nothing else: no text before or after it, no Markdown code fence.
It must validate against the schema below, and it is checked mechanically against the reference set
before anyone reads it. A document that fails any check is sent back to you, once, and then rejected.

- The root is `<Specification xmlns="{NS}" schemaVersion="1.0">`. `Identity` carries the library name and
  version you are given, exactly. `Provenance` may be empty.
- Every prose element is `provenance="narrated" method="ai-drafted" reviewState="unreviewed"` and carries
  `sourceTag`, the tag it is a draft of. Never write `provenance="authored"`: you are not the author.
- Structural elements (`Population`, `Concept`, `Rule`) are `provenance="derived"`.
- Never write `Appendix`, `ValueSet`, `Parameter`, `Code`, `Metric`, `Badges`, `Copyright` or `Disclaimer`.
- Every define the reference set lists for this library is narrated: a `Population`, `Concept` or `Rule`
  element with that `define` and `library`, carrying its prose.
- Every identifier comes from the reference set: profiles, library-qualified define names, metric keys
  and line numbers. Close every item of "Before you finish" before you reply.
"""


def skill_text() -> str:
    """SPEC-EXPLAIN-VOICE — the writing rules, verbatim from the agent's published skill."""
    return SKILLS["clinical-explain"].read()


@lru_cache(maxsize=1)
def _schema_text() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def messages(*, library: str, version: str, source: str, reference_set: dict[str, Any]) -> list[dict[str, Any]]:
    """The first request: the contract as the system turn, the library as the user turn."""
    system = f"{skill_text()}\n\n{OUTPUT_RULES}\n## The schema\n\n```xml\n{_schema_text()}\n```\n"
    user = (f"Write the narration for the library `{library}` version `{version}`.\n\n"
            f"## Its CQL\n\n```cql\n{source}\n```\n\n"
            f"## Its reference set\n\n```json\n{json.dumps(reference_set, indent=2)}\n```\n")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _retry(problems: list[Problem]) -> str:
    listed = "\n".join(f"- [{p.kind}] {p.message}" for p in problems)
    return ("Your document failed these checks. Fix every one and return the complete corrected document, "
            f"the XML only:\n\n{listed}\n")


class NarrationRejected(RuntimeError):
    """Every attempt failed its checks. Carries the last attempt's problems."""

    def __init__(self, problems: list[Problem], attempts: int) -> None:
        super().__init__(f"the narration failed its checks after {attempts} attempts")
        self.problems = problems
        self.attempts = attempts


@dataclass(frozen=True)
class Generated:
    """A narration that passed every check."""

    instance: str
    attempts: int


def generate(*, library: str, version: str, source: str, reference_set: dict[str, Any],
             client: Any, endpoint: str) -> Iterator[dict[str, Any]]:
    """Write and check the narration, yielding progress events; the last one is ``generated``.

    Events: ``{"type": "stage", "stage": "writing"|"checking", "attempt": n}``, then for a failed attempt
    that is retried ``{"type": "problems", "attempt": n, "problems": [...]}``. The final event carries the
    :class:`Generated` result under ``"result"``. Raises :class:`NarrationRejected` when the last attempt
    fails. ``client`` is an OpenAI-compatible client for Model Serving (``serving_client()``).
    """
    conversation = messages(library=library, version=version, source=source, reference_set=reference_set)
    problems: list[Problem] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        yield {"type": "stage", "stage": "writing", "attempt": attempt}
        completion = client.chat.completions.create(model=endpoint, messages=conversation,
                                                    max_tokens=MAX_OUTPUT_TOKENS)
        choice = completion.choices[0]
        text = answer_text(choice.message.content).strip()
        yield {"type": "stage", "stage": "checking", "attempt": attempt}
        problems = check(text, reference_set)
        if getattr(choice, "finish_reason", None) == "length":
            problems.insert(0, Problem("invalid", "the reply was cut off at the endpoint's output limit"))
        if not problems:
            yield {"type": "generated", "result": Generated(instance=text, attempts=attempt)}
            return
        if attempt == MAX_ATTEMPTS:
            raise NarrationRejected(problems, attempt)
        yield {"type": "problems", "attempt": attempt, "problems": [p.to_dict() for p in problems]}
        conversation += [{"role": "assistant", "content": text}, {"role": "user", "content": _retry(problems)}]
