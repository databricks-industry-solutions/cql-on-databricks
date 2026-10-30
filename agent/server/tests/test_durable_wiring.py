"""RUN-CLIENT-RESULT-SETS, SEC-OBO — real MCP wiring, synthetic caller and selection transports."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cql_databricks.agent.server import wiring
from agent.service.tests.durable_fixture import RESULT_SET, reference


def test_result_tools_open_explicit_selection_and_query_as_the_same_reader(monkeypatch):
    """RUN-CLIENT-PUBLISHED — no service-principal result reads, latest discovery or source logs.

    RUN-CLIENT-SESSION-REUSE — every tool reads on the signed-in user's one held session."""
    opened, statements = [], []
    selected = reference()
    results = SimpleNamespace(results={"Synthetic.Output": selected})
    monkeypatch.setattr(wiring.config, "warehouse_id", lambda: "synthetic-warehouse")
    monkeypatch.setenv("CQL_DATABRICKS_CATALOG_SCOPES", "synthetic_dev.cql,synthetic_shared")

    def rows(sql):
        statements.append(sql)
        assert "VERSION AS OF" not in sql
        assert "change_log" not in sql and "cql_completed_results" not in sql
        return [[0]] if "COUNT(*)" in sql else []
    runner = SimpleNamespace(rows=rows)
    def held(warehouse):
        assert warehouse == "synthetic-warehouse"
        return runner
    def open_selected(address, *, run, content, compiler, files):
        assert run is runner and address == RESULT_SET
        # CLIENT-DISCOVERY-SCOPE — the agent discovers only in the deployment's catalog scopes.
        assert [str(scope) for scope in content.scopes] == ["synthetic_dev.cql", "synthetic_shared"]
        assert compiler is not None
        # RUN-ADDRESS-LOCATION — a reopen can replay a folder request; the client is built only if it does.
        assert isinstance(files, wiring.WorkspaceLibraryFiles)
        opened.append(address)
        return results
    monkeypatch.setattr(wiring, "user_session", held)
    monkeypatch.setattr(wiring, "open_selection", open_selected)
    monkeypatch.setattr(wiring, "SelectedOutput", lambda value, name:
                        selected if value is results and name == "Synthetic.Output" else pytest.fail("output"))
    server = wiring.assistant_server()
    listed = asyncio.run(server.call_tool("list_tables", {"result_set": RESULT_SET}))
    assert "Synthetic.Output" in listed.content[0].text
    output = asyncio.run(server.call_tool("get_output", {"result_set": RESULT_SET, "define": "Synthetic.Output"}))
    assert '"rows":0' in output.content[0].text.replace(" ", "")
    completion = asyncio.run(server.call_tool("get_freshness", {
        "result_set": RESULT_SET, "define": "Synthetic.Output"}))
    assert "not_tracked" in completion.content[0].text
    assert len(opened) == 3 and len(statements) == 2


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
