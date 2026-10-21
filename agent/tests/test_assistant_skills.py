"""SVC-MCP-SKILLS — one contract, served to every client.

`skills.py` is a seam with three consumers that must not diverge: the EXPLAIN generator
(`SPEC-EXPLAIN-VOICE`), the Designer's assistant drawer, and an external Genie Agent over MCP. The transport half
is pinned in `test_assistant_server.py`; what is pinned here is the loader and the FILE it resolves —
because the way this surface actually breaks is not a bad function, it is a registry entry pointing
at a path that a rename moved, which raises nothing until an agent asks for the skill.
"""
from __future__ import annotations

import pytest

from cql_databricks.agent import skills


class TestRegistry:
    def test_every_published_skill_resolves_to_a_real_file(self):
        # The failure this catches: a directory rename that updates the prose references and leaves
        # the registry pointing at nothing. Nothing imports the path, so it fails first for an agent.
        for skill in skills.list_skills():
            assert skill.path.is_file(), f"{skill.name} points at {skill.path}, which does not exist"

    def test_the_catalog_tells_an_agent_when_to_reach_for_the_skill(self):
        # Catalog copy is what a client chooses on before spending the tokens on a body, so a
        # description that only names the skill again has cost the tier split its whole point.
        for skill in skills.list_skills():
            assert len(skill.description) > 80, f"{skill.name}'s description is too thin to choose on"

    def test_clinical_explain_is_published(self):
        assert [s.name for s in skills.list_skills()] == ["clinical-explain"]


class TestGetSkill:
    def test_the_body_is_the_file_verbatim(self):
        # SPEC-EXPLAIN-VOICE — verbatim is the requirement, not the convenient implementation. Prose
        # a person writes into an instance and prose the product generates have to be governed by
        # the same bytes, which they are not if this ever summarises or trims.
        skill = skills.SKILLS["clinical-explain"]
        assert skills.get_skill("clinical-explain") == skill.path.read_text(encoding="utf-8")

    def test_it_is_the_voice_contract_and_not_some_other_file(self):
        body = skills.get_skill("clinical-explain")
        assert "Writing the narrative for a clinical measure" in body
        assert "SPEC-EXPLAIN-VOICE" in body

    def test_an_unknown_name_fails_loudly_and_says_what_is_published(self):
        # KNOWLEDGE-FAIL-LOUD: answering "" would have the model write prose against no contract at
        # all, with the absence invisible in the output. The error names the alternatives because its
        # reader is a model that can act on them.
        with pytest.raises(skills.UnknownSkillError) as caught:
            skills.get_skill("clinical-narrative")
        assert "clinical-explain" in str(caught.value)
        assert "list_skills" in str(caught.value)
