"""DEPLOY-ENVIRONMENT-TAG-POLICIES — the environment tags are governed, and only the deployer and the admins assign
them. A synthetic SDK boundary: no live policies or roles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("environment_tag_policies",
                                               _ROOT / "scripts/deploy/environment_tag_policies.py")
policies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policies)


class Workspace:
    def __init__(self, held=None, rules=None):
        from databricks.sdk.errors import NotFound

        self.config = SimpleNamespace(account_id="synthetic-account")
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name="deployer@synthetic.example"))
        self.held = dict(held or {})
        self.created, self.updated, self.rule_updates = [], [], []
        self.rules = dict(rules or {})

        def get(key):
            if key not in self.held:
                raise NotFound(key)
            return self.held[key]

        def create(policy):
            self.created.append(policy)
            made = SimpleNamespace(id=f"id-{policy.tag_key}", values=policy.values)
            self.held[policy.tag_key] = made
            return made

        def update(key, policy, update_mask):
            self.updated.append((key, sorted(v.name for v in policy.values), update_mask))

        def get_rule_set(name, etag):
            return SimpleNamespace(grant_rules=self.rules.get(name, []), etag="e1")

        def update_rule_set(name, rule_set):
            self.rule_updates.append((name, [(r.role, list(r.principals)) for r in rule_set.grant_rules]))

        self.tag_policies = SimpleNamespace(get_tag_policy=get, create_tag_policy=create, update_tag_policy=update)
        self.account_access_control_proxy = SimpleNamespace(get_rule_set=get_rule_set, update_rule_set=update_rule_set)


def test_each_environment_tag_is_governed_with_its_values():
    workspace = Workspace()
    policies.provision(workspace, [])
    assert {p.tag_key: [v.name for v in p.values] for p in workspace.created} == {
        "cql_databricks_environment": [], "cql_databricks_environment_version": ["3"],
        "cql_databricks_environment_default": ["true"]}


def test_the_deployer_and_each_admin_may_assign_them():
    workspace = Workspace()
    policies.provision(workspace, ["admin@synthetic.example", "synthetic-admins"])
    assert workspace.rule_updates[0] == (
        "accounts/synthetic-account/tagPolicies/id-cql_databricks_environment/ruleSets/default",
        [("roles/tagPolicy.assigner", ["users/deployer@synthetic.example", "users/admin@synthetic.example",
                                       "groups/synthetic-admins"])])
    assert len(workspace.rule_updates) == 3


def test_a_second_deploy_changes_nothing():
    from databricks.sdk.service.iam import GrantRule
    from databricks.sdk.service.tags import Value

    held = {key: SimpleNamespace(id=f"id-{key}", values=[Value(name=v) for v in values])
            for key, values in policies.POLICIES.items()}
    rules = {f"accounts/synthetic-account/tagPolicies/id-{key}/ruleSets/default":
             [GrantRule(role="roles/tagPolicy.manager", principals=["users/other@synthetic.example"]),
              GrantRule(role="roles/tagPolicy.assigner", principals=["users/deployer@synthetic.example"])]
             for key in policies.POLICIES}
    workspace = Workspace(held, rules)
    policies.provision(workspace, [])
    assert (workspace.created, workspace.updated, workspace.rule_updates) == ([], [], [])


def test_other_values_are_reset_and_other_rules_kept():
    from databricks.sdk.service.iam import GrantRule
    from databricks.sdk.service.tags import Value

    key = "cql_databricks_environment_default"
    name = f"accounts/synthetic-account/tagPolicies/id-{key}/ruleSets/default"
    workspace = Workspace({key: SimpleNamespace(id=f"id-{key}", values=[Value(name="yes")])},
                          {name: [GrantRule(role="roles/tagPolicy.manager", principals=["users/other@synthetic.example"])]})
    policies.provision(workspace, [])
    assert (key, ["true"], "values") in workspace.updated
    assert (name, [("roles/tagPolicy.manager", ["users/other@synthetic.example"]),
                   ("roles/tagPolicy.assigner", ["users/deployer@synthetic.example"])]) in workspace.rule_updates


def test_an_admin_is_named_as_access_control_names_it():
    assert policies.principal("a@synthetic.example") == "users/a@synthetic.example"
    assert policies.principal("01234567-89ab-cdef-0123-456789abcdef") == "servicePrincipals/01234567-89ab-cdef-0123-456789abcdef"
    assert policies.principal("synthetic-admins") == "groups/synthetic-admins"
    with pytest.raises(ValueError):
        policies.principal(" ")


def test_admins_is_a_list_and_empty_by_default():
    assert policies.admins_of({}) == []
    assert policies.admins_of({"admins": {"value": ["a@synthetic.example"]}}) == ["a@synthetic.example"]
    with pytest.raises(SystemExit, match="must be a list"):
        policies.admins_of({"admins": {"value": "a@synthetic.example"}})


def test_the_deploy_governs_the_tags_before_it_tags_a_catalog():
    deploy = (_ROOT / "scripts/deploy/deploy.sh").read_text()
    bundle = (_ROOT / "databricks.yml").read_text()
    assert "content: bash ./scripts/deploy/environment-tag-policies.sh" in bundle
    assert deploy.index('"$here/scripts/deploy/environment-tag-policies.sh"') < deploy.index("provision-examples.py\"")


def test_governing_the_tags_can_be_turned_off_with_a_warning():
    """An account at its tag-policy limit turns governance off; the deploy says the tags are not governed."""
    import yaml
    deploy = (_ROOT / "scripts/deploy/deploy.sh").read_text()
    variables = yaml.safe_load((_ROOT / "databricks.yml").read_text())["variables"]
    assert variables["govern_environment_tags"]["default"] is True
    assert "the environment tags are NOT governed" in deploy
    assert deploy.index("govern_environment_tags must be true or false") > deploy.index("environment-tag-policies.sh")
