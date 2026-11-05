"""CLIENT-ENVIRONMENT-CACHE, AGENT-DEFAULT-ENVIRONMENT — the agent's tool calls find a user's environments once per
process, not once per call, and never hand one user's list to another."""
from __future__ import annotations

from cql_databricks.agent.server import wiring
from cql_databricks_common.workspace import bind_forwarded_identity

ROWS = {"a@example.com": "a_cat", "b@example.com": "b_cat"}


def _runner(user: str, reads: list[str]):
    def run(sql, params=None):
        if "governed_tags" in sql:
            return [{"tag_key": t} for t in ("cql_databricks_environment", "cql_databricks_environment_version",
                                             "cql_databricks_environment_default")]
        reads.append(user)
        catalog = ROWS[user]
        return [{"catalog": catalog, "tag": "cql_databricks_environment", "value": "example", "comment": None},
                {"catalog": catalog, "tag": "cql_databricks_environment_version", "value": "3", "comment": None},
                {"catalog": catalog, "tag": "cql_databricks_environment_default", "value": "true", "comment": None}]
    return run


def test_tool_calls_by_one_user_find_the_environments_once():
    reads: list[str] = []
    bind_forwarded_identity("token-a", "a@example.com")
    try:
        for _ in range(3):
            _, environment = wiring.tool_environment(_runner("a@example.com", reads))
            assert environment.catalog == "a_cat"
        bind_forwarded_identity("token-b", "b@example.com")
        _, environment = wiring.tool_environment(_runner("b@example.com", reads))
        assert environment.catalog == "b_cat"
    finally:
        bind_forwarded_identity(None, None)
    assert reads == ["a@example.com", "b@example.com"]
