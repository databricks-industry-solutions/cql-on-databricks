"""RUN-CLIENT-RESULT-SETS, SEC-OBO — real MCP wiring, synthetic caller and selection transports."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cql_databricks.agent.server import wiring
from cql_databricks.resolvers.interfaces import ResultStore
from agent.service.tests.durable_fixture import RESULT_SET, reference


#: CLIENT-ENVIRONMENT — the catalog tags the caller can see: a default environment and one other.
TAGS = [{"catalog": "synthetic", "tag": "cql_databricks_environment", "value": "synthetic", "comment": None},
        {"catalog": "synthetic", "tag": "cql_databricks_environment_version", "value": "3", "comment": None},
        {"catalog": "synthetic", "tag": "cql_databricks_environment_default", "value": "true", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment", "value": "other", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment_version", "value": "3", "comment": None}]


class Runner:
    """The signed-in user's held session: ``__call__`` answers the environment query, ``rows`` the tool reads."""

    def __init__(self, rows=None):
        self.statements = []
        self._rows = rows

    def __call__(self, sql, params=None):
        assert "catalog_tags" in sql, sql
        return TAGS

    def rows(self, sql):
        self.statements.append(sql)
        return self._rows(sql)


def test_result_tools_open_explicit_selection_and_query_as_the_same_reader(monkeypatch):
    """RUN-CLIENT-PUBLISHED — no service-principal result reads, latest discovery or source logs.

    RUN-CLIENT-SESSION-REUSE — every tool reads on the signed-in user's one held session.
    RUN-CLIENT-REQUEST-REOPEN — a selection reopens with the content of the environment its calculation names."""
    opened = []
    selected = reference()
    results = SimpleNamespace(results={"Synthetic.Output": selected})
    monkeypatch.setattr(wiring.config, "warehouse_id", lambda: "synthetic-warehouse")
    address = {**RESULT_SET, "calculation": {"environment": "synthetic"}}

    def rows(sql):
        assert "VERSION AS OF" not in sql
        assert "change_log" not in sql and "cql_completed_results" not in sql
        return [[0]] if "COUNT(*)" in sql else []
    runner = Runner(rows)

    def held(warehouse):
        assert warehouse == "synthetic-warehouse"
        return runner

    def open_selected(given, *, run, resolvers, content, compiler, files):
        assert run is runner and given == address
        # RUN-CLIENT-REQUEST-REOPEN — read-only, addressed by the schema the selection was written to.
        assert resolvers.environment == "synthetic" and resolvers.results.destination == ("synthetic", "results")
        assert not hasattr(resolvers.results, "writes")
        # CLIENT-ENVIRONMENT — the environment's own content, by its standard schemas; no catalog scopes.
        assert content.catalog == "synthetic"
        assert compiler is not None
        # RUN-ADDRESS-LOCATION — a reopen can replay a folder request; the client is built only if it does.
        assert isinstance(files, wiring.WorkspaceLibraryFiles)
        opened.append(given)
        return results
    monkeypatch.setattr(wiring, "user_session", held)
    monkeypatch.setattr(wiring, "open_selection", open_selected)
    monkeypatch.setattr(wiring, "SelectedOutput", lambda value, name:
                        selected if value is results and name == "Synthetic.Output" else pytest.fail("output"))
    server = wiring.assistant_server()
    listed = asyncio.run(server.call_tool("list_tables", {"result_set": address}))
    assert "Synthetic.Output" in listed.content[0].text
    output = asyncio.run(server.call_tool("get_output", {"result_set": address, "define": "Synthetic.Output"}))
    assert '"rows":0' in output.content[0].text.replace(" ", "")
    completion = asyncio.run(server.call_tool("get_freshness", {
        "result_set": address, "define": "Synthetic.Output"}))
    assert "not_tracked" in completion.content[0].text
    assert len(opened) == 3 and len(runner.statements) == 2


def test_a_selection_that_names_no_environment_is_refused(monkeypatch):
    """CLIENT-ENVIRONMENT — a reopen never guesses the environment."""
    monkeypatch.setattr(wiring.config, "warehouse_id", lambda: "synthetic-warehouse")
    monkeypatch.setattr(wiring, "user_session", lambda warehouse: Runner())
    monkeypatch.setattr(wiring, "open_selection", lambda *args, **kwargs: pytest.fail("opened without environment"))
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="names no environment"):
        asyncio.run(wiring.assistant_server().call_tool("list_tables", {"result_set": RESULT_SET}))


def test_the_tools_run_in_the_default_environment_or_the_one_the_drawer_passes(monkeypatch):
    """AGENT-DEFAULT-ENVIRONMENT — no argument picks the environment: Genie and MCP get the default, and the
    Designer's drawer binds its own around the call (`DOC-ENVIRONMENT-PICKER`)."""
    monkeypatch.setattr(wiring.config, "runtime_catalog", lambda: "synthetic")
    research = wiring.research(lambda: Runner())
    assert research.saved().resolvers.environment == "synthetic"
    with wiring.in_environment("other"):
        saved = research.saved()
        assert saved.resolvers.data.identity() == "other_cat.data"
        # RUN-CLIENT-RESULT-OWNERSHIP — the user's own draft results, then the official ones; never written.
        results = saved.resolvers.results
        assert (results.dev.schema, results.dev.reuse, results.base.schema, results.base.reuse) == (
            ("other_cat", "draft_results"), "own", ("other_cat", "results"), "official")
        assert not isinstance(results, ResultStore)
    assert research.saved().resolvers.environment == "synthetic"


def test_the_drawer_environment_reaches_a_tool_dispatched_in_process(monkeypatch):
    """AGENT-DEFAULT-ENVIRONMENT — the drawer's loop binds the environment around each in-process tool call."""
    from cql_databricks.agent.server.ask import _call_tool
    from fastmcp import FastMCP

    seen = []
    server = FastMCP("t")

    @server.tool
    def which() -> str:
        """The environment the call runs in."""
        seen.append(wiring._environment.get())
        return "ok"

    _call_tool(server, "which", {}, "other")
    _call_tool(server, "which", {})
    assert seen == ["other", None]


def test_missing_reader_fails_without_opening_any_selection(monkeypatch):
    """SEC-OBO — authentication failure never chooses service credentials."""
    def denied():
        raise PermissionError("synthetic caller missing")
    monkeypatch.setattr(wiring, "user_session", lambda warehouse: denied())
    monkeypatch.setattr(wiring.config, "warehouse_id", lambda: "synthetic-warehouse")
    monkeypatch.setattr(wiring, "open_selection", lambda *args, **kwargs: pytest.fail("opened without caller"))
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="synthetic caller missing"):
        asyncio.run(wiring.assistant_server().call_tool("list_tables", {"result_set": RESULT_SET}))
