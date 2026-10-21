"""DERIVATIONS-TO-LIBRARY, CORE-NO-SILENT-MISLOWER — the caller's warehouse executor: rows when it finishes, a raise
when it fails or never finishes, never an empty answer that reads as a zero."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cql_databricks.result import results


def _response(state, rows=None, error=None):
    return SimpleNamespace(statement_id="s1", status=SimpleNamespace(state=SimpleNamespace(value=state),
                                                                     error=SimpleNamespace(message=error)),
                           result=SimpleNamespace(data_array=rows) if rows is not None else None)


class _Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.statements = []
        self.statement_execution = self

    def execute_statement(self, *, warehouse_id, statement, wait_timeout):
        self.statements.append((warehouse_id, statement))
        return self.responses.pop(0)

    def get_statement(self, statement_id):
        return self.responses.pop(0)


def test_rows_once_the_statement_finishes(monkeypatch):
    monkeypatch.setattr(results.time, "sleep", lambda s: None)
    client = _Client(_response("RUNNING"), _response("SUCCEEDED", [["1", "2"]]))
    assert results.warehouse_execute(client, "wh")("SELECT 1") == [["1", "2"]]
    assert client.statements == [("wh", "SELECT 1")]


def test_failure_and_no_finish_raise(monkeypatch):
    monkeypatch.setattr(results.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="FAILED.*bad table"):
        results.warehouse_execute(_Client(_response("FAILED", error="bad table")), "wh")("SELECT 1")
    monkeypatch.setattr(results, "_POLL_ATTEMPTS", 2)
    with pytest.raises(RuntimeError, match="did not finish"):
        results.warehouse_execute(_Client(*[_response("RUNNING")] * 3), "wh")("SELECT 1")
    with pytest.raises(ValueError, match="warehouse id"):
        results.warehouse_execute(_Client(), "")
