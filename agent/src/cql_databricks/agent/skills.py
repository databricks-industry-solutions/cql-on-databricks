"""SVC-MCP-SKILLS — the standing guidance both clients answer from (`agent/README.md`).

Two clients consume this module and neither may have a private copy: the Designer's own drawer, which
reads it in process and puts it in the FM API system prompt, and the external Genie Agent, which
receives it over MCP. The tools already work that way (`tools.py`); guidance that did not would
produce the failure the tool split exists to prevent one level up — an agent and a person given the
same five tools and two different accounts of how to use them.

**Two tiers, because they are paid for differently.**

- :data:`ANSWERING_INSTRUCTIONS` is SHORT and ALWAYS ON. It goes in FastMCP's ``instructions``,
  which every MCP client receives at ``initialize``, and it is the drawer's system prompt verbatim.
  It holds only the CROSS-TOOL rules — the ones no single tool's docstring owns.
- A **skill** is a full contract, and it is loaded ON DEMAND through ``list_skills`` /
  ``get_skill``. `clinical-explain` is ~8k tokens of prose contract that matters when the task is
  writing about a measure and is dead weight on "how many patients are in the denominator". Paying
  for it per conversation is what the progressive disclosure buys.

**Why instructions are code and a skill is a file.** ``ANSWERING_INSTRUCTIONS`` describes the tool
surface, so it has to change in the same edit as ``server.py``'s tool docstrings — keeping it beside
them is what stops the two from drifting. A skill is a standalone document with its own reviewers
(a clinician reads `clinical-explain`, not a Python file), so it stays a file and is served verbatim.

**There is deliberately no skill about how to call these tools.** MCP already carries that: each
tool's docstring is written for the model, and the server's ``instructions`` carry what spans them.
A skill restating either would be a third account of one contract.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cql_databricks.agent.skills")

# Skill bodies ship WITH this package (see `[tool.setuptools.package-data]` in agent/pyproject.toml).
# The prose contracts are the agent's own asset and are addressed relative to THIS module.
_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"


class UnknownSkillError(KeyError):
    """``get_skill`` was asked for a name that is not published.

    Raised rather than answered with an empty string (`KNOWLEDGE-FAIL-LOUD`): a model handed ""
    where it expected a contract writes prose against no contract at all and nothing says so.
    """


@dataclass(frozen=True)
class Skill:
    """One published contract: what it is for, and where its text lives.

    The description is CATALOG COPY — it is read by an agent deciding whether to spend the tokens on
    the body, so it says when to reach for the skill rather than summarising it. That is a different
    audience from the body's, which is why it lives here and not as a heading inside the file.
    """

    name: str
    description: str
    path: Path

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


#: Everything published, by name. A skill lives WITH THE FEATURE THAT OWNS IT — `clinical-explain`
#: is the explain path's voice contract (`SPEC-EXPLAIN-VOICE`) and stays under ``explain/`` — so this
#: is an explicit registry rather than a scan of one directory. Adding a skill is one entry.
SKILLS: dict[str, Skill] = {
    "clinical-explain": Skill(
        name="clinical-explain",
        description=(
            "The prose contract for explaining a clinical quality measure to a clinician: the "
            "persona, the banned vocabularies, the Include/Exclude shape, how to cite a figure "
            "rather than state one, and the coverage rules a finished explanation closes. Load it "
            "before writing or reviewing ANY prose about measure logic — a define's description, a "
            "population's rationale, or an answer that explains what a criterion does."),
        path=_SKILLS_ROOT / "clinical-explain" / "SKILL.md"),
}


#: The cross-tool contract, given to every client before it reads a single tool description.
#:
#: **Only what spans the tools belongs here.** What one tool does is in that tool's own docstring,
#: which MCP hands over with its schema; repeating it would be the drift this module exists to stop.
#: What is left is the four things a caller gets wrong when each tool is read in isolation:
#: whose number it is holding (`SVC-ANSWER-PROVENANCE`), that an output is a place and not an event
#: (`SVC-TOOL-ADDRESS`), that freshness is evidence to weigh rather than a verdict to relay
#: (`SVC-TOOL-FRESHNESS`), and that this surface is read-only so the honest answer to "change it" is
#: that it cannot (`KNOWLEDGE-FAIL-LOUD`).
ANSWERING_INSTRUCTIONS = """\
You are answering questions about a clinical quality measure and the results CQL on Databricks computed for it.
Your reader is usually a clinician who is accountable for the measure. They know medicine. They do
not know CQL, ELM, FHIR, or how the logic is organised.

**Say whose number you are holding.** A figure from `get_output` is engine-computed: the compiler and
the executor produced it, and it is the same number the CQL on Databricks screen shows. A figure you worked out
with `query_data` is YOUR derivation. Both are legitimate and they are not the same claim, so label
them. Never present something you computed as the measure's own result — if the two disagree, both
may be right, and saying which is which is the whole answer.

**Use one complete selection.** Pass the result_set locator supplied by Designer or a publishing Job
to list_tables, get_output and get_freshness. It pins exact versions, including dependencies. Use the
exact library-qualified define names from list_tables. Do not infer addresses or combine independent
latest outputs into a measure. If no locator is supplied, ask for one. A superseded locator fails;
select its replacement explicitly. Cite the selected address and successful computation time.

**Freshness is evidence, not a verdict.** get_freshness returns successful computation time in the
cql-completion-v1 payload, including successful empty results. Source freshness is not tracked. The
timestamp is neither source-read time nor proof that the source is unchanged. An explicit recompute
is required after source data or terminology contents change; these read-only tools do not run it.

**These tools are read-only.** You cannot change the measure, schedule a run, bind a data source, or
recompute anything — not even to make an answer fresher. When you are asked to, say plainly that you
cannot do it yet. Do not approximate it, and do not describe the change you would have made as though
it were made.

**Re-explaining is the ONE exception, and only in your answer.** You WILL be asked to explain a
criterion again — the Designer's own Explain control sends that request, scoped to one define
(`SPEC-EXPLAIN-AGAIN`). Write the fresh explanation: that is generation, it is what you are for, and
refusing it because the surface is read-only would be reading the rule as being about the SUBJECT when
it is about the STORE. What you must not do is imply the document changed. Your explanation is an
answer beside the specification, not a replacement for the paragraph in it — **persisting a narration
is Designer's own act**, there is no tool here that does it, and a reader who thinks their measure's
published prose was rewritten by a chat has been misled about an artifact other people review. If they
want it to become the document's words, that is a step they take in Designer.

**Load the contract before you write prose about the logic.** `list_skills` shows what is published;
`get_skill` returns it. Explaining what a criterion does is writing narration, and it is governed by
the same contract the product's own explanations are — an answer that reads in a different voice from
the document above it is a second author on one measure. This is what keeps a re-explanation
comparable to the paragraph it was asked about: same persona, same banned vocabularies, same rule that
a figure is cited rather than stated.
"""


def standing_instructions(contributed: str = "") -> str:
    """`ANSWERING_INSTRUCTIONS`, with what a contributing feature adds placed after its first paragraph.

    AGENT-TOOL-REGISTRY — a feature that registers tools may say how to start with them (the designer's
    ``get_specification`` is the map of what can be addressed). Where that tool is not served — the
    agent's own App (`APP-PER-FEATURE`) — the instructions must not name it.
    """
    if not contributed:
        return ANSWERING_INSTRUCTIONS
    intro, rest = ANSWERING_INSTRUCTIONS.split("\n\n", 1)
    return f"{intro}\n\n{contributed.strip()}\n\n{rest}"


def list_skills() -> list[Skill]:
    """Every published skill, by name. The catalog an agent reads before spending tokens on a body."""
    return sorted(SKILLS.values(), key=lambda s: s.name)


def get_skill(name: str) -> str:
    """One skill's text, verbatim.

    Verbatim is the requirement, not the easy implementation (`SPEC-EXPLAIN-VOICE`): this is the same
    bytes the EXPLAIN generator is handed and the same file a person reads, so prose a person writes and
    prose the product generates stay indistinguishable.
    """
    skill = SKILLS.get(name)
    if skill is None:
        raise UnknownSkillError(
            f"No skill named {name!r}. Published skills: {sorted(SKILLS)}. "
            "Call list_skills to see what each one is for.")
    log.debug("SVC-MCP-SKILLS: serving skill %s from %s", name, skill.path)
    return skill.read()
