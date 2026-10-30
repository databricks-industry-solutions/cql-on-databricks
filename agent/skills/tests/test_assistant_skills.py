"""SVC-MCP-SKILLS, DEPLOY-AGENT-SKILLS — one contract, served to every client.

The skills have readers that must not diverge: the server, which serves them to the Designer's drawer and to
an external MCP client, and the deploy, which publishes the same files to Genie One and Genie Code. The
transport half is pinned in the server's `test_assistant_server.py`; what is pinned here is the loader and the
FILE it reads — a skill folder whose frontmatter is missing or names another skill.
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

    def test_the_published_skills_are_the_explain_contract_the_research_workflows_and_the_import(self):
        assert [s.name for s in skills.list_skills()] == [
            "clinical-explain", "cql-authoring", "experiment", "explain-cql", "import-quality-measures",
            "population-question"]

    def test_the_import_skill_carries_no_deployment_placeholder(self):
        """CONTENT-IMPORT-SKILL — served verbatim over MCP, so it finds the deployment's values itself."""
        body = skills.get_skill("import-quality-measures")
        assert "__CQL_DATABRICKS_" not in body
        assert "cql_databricks.data_bindings.measure_import" in body

    def test_every_research_skill_tells_the_agent_to_review_its_cql_with_the_user(self):
        """AGENT-CQL-REVIEW — the skills that lead to running CQL say to show it, or offer to."""
        for name in ("experiment", "cql-authoring", "population-question"):
            body = skills.get_skill(name).lower()
            assert "review" in body and "show" in body, name


class TestSkillFile:
    """A skill is its folder's ``SKILL.md``: frontmatter naming the folder, over the body."""

    def test_every_skill_opens_with_frontmatter_naming_its_folder(self):
        for skill in skills.list_skills():
            assert skill.read().startswith(f"---\nname: {skill.path.parent.name}\ndescription: \""), skill.name

    def test_a_quoted_description_is_read_unescaped(self):
        assert '"what was the exclusion criteria?"' in skills.SKILLS["explain-cql"].description

    def test_a_file_without_frontmatter_fails_loudly(self, tmp_path):
        path = tmp_path / "no-frontmatter" / "SKILL.md"
        path.parent.mkdir()
        path.write_text("# A body with no frontmatter\n", encoding="utf-8")
        with pytest.raises(skills.SkillFileError, match="must open with"):
            skills._load(path)

    def test_a_file_naming_another_skill_fails_loudly(self, tmp_path):
        path = tmp_path / "experiment" / "SKILL.md"
        path.parent.mkdir()
        path.write_text('---\nname: explain-cql\ndescription: "x"\n---\n', encoding="utf-8")
        with pytest.raises(skills.SkillFileError, match="its folder's"):
            skills._load(path)


class TestGetSkill:
    def test_the_body_is_the_file_verbatim(self):
        # SPEC-EXPLAIN-VOICE — verbatim is the requirement, not the convenient implementation. What a
        # person reads, what the deploy publishes and what the agent is served have to be the same
        # bytes, which they are not if this ever summarises or trims.
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
