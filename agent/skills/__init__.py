"""SVC-MCP-SKILLS, DEPLOY-AGENT-SKILLS — the agent's skills, and the code that reads them (`agent/README.md`).

**A skill is a folder here holding one ``SKILL.md``**: ``name`` and ``description`` frontmatter over the body, the
standard skill format Genie One and Genie Code both load. The file is the whole definition. The server serves it
verbatim through ``get_skill``, and the deploy publishes the same bytes as a Unity Catalog skill and to the
workspace skills folder (``scripts/deploy/publish_skills.py``), so the three cannot drift.

**Loaded on demand.** A skill is a full contract, and ``list_skills`` / ``get_skill`` hand it over only when the
task needs it. ``clinical-explain`` is ~8k tokens that matter when the task is writing about a measure and are
dead weight on "how many patients are in the denominator". The always-on tier is the server's standing
instructions (``cql_databricks.agent.server.instructions``).

**Adding a skill is adding a folder.** Nothing here lists them: every folder holding a ``SKILL.md`` is a skill.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cql_databricks.agent.skills")

# The package is this folder (`package-dir` in `agent/skills/pyproject.toml`), so each skill's folder sits
# beside this module, in a checkout and in an installed package alike.
_ROOT = Path(__file__).resolve().parent

#: The frontmatter every ``SKILL.md`` opens with. ``description`` is double-quoted, with ``\\"`` for a quote.
_FRONTMATTER = re.compile(r'\A---\nname: (?P<name>[a-z0-9-]+)\ndescription: "(?P<description>(?:[^"\\]|\\.)*)"\n---\n')


class UnknownSkillError(KeyError):
    """``get_skill`` was asked for a name that is not published.

    Raised rather than answered with an empty string (`KNOWLEDGE-FAIL-LOUD`): a model handed ""
    where it expected a contract writes prose against no contract at all and nothing says so.
    """


class SkillFileError(ValueError):
    """A ``SKILL.md`` whose frontmatter is missing or names another skill. Fails at import (`KNOWLEDGE-FAIL-LOUD`)."""


@dataclass(frozen=True)
class Skill:
    """One published contract: what it is for, and where its text lives.

    The description is CATALOG COPY — it is read by an agent deciding whether to spend the tokens on
    the body, so it says when to reach for the skill rather than summarising it.
    """

    name: str
    description: str
    path: Path

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


def _load(path: Path) -> Skill:
    match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        raise SkillFileError(f"{path} must open with `---`, `name: <name>`, `description: \"...\"` and `---`")
    if match["name"] != path.parent.name:
        raise SkillFileError(f"{path} is named {match['name']!r}; a skill's name is its folder's, {path.parent.name!r}")
    return Skill(name=match["name"], description=re.sub(r"\\(.)", r"\1", match["description"]), path=path)


#: Everything published, by name: every folder here that holds a ``SKILL.md``.
SKILLS: dict[str, Skill] = {
    skill.name: skill for skill in (_load(p) for p in sorted(_ROOT.glob("*/SKILL.md")))}


def list_skills() -> list[Skill]:
    """Every published skill, by name. The catalog an agent reads before spending tokens on a body."""
    return sorted(SKILLS.values(), key=lambda s: s.name)


def get_skill(name: str) -> str:
    """One skill's ``SKILL.md``, verbatim.

    Verbatim is the requirement, not the easy implementation (`SPEC-EXPLAIN-VOICE`): this is the same
    file a person reads and the deploy publishes, so prose a person writes and prose the agent writes are
    governed by the same bytes.
    """
    skill = SKILLS.get(name)
    if skill is None:
        raise UnknownSkillError(
            f"No skill named {name!r}. Published skills: {sorted(SKILLS)}. "
            "Call list_skills to see what each one is for.")
    log.debug("SVC-MCP-SKILLS: serving skill %s from %s", name, skill.path)
    return skill.read()
