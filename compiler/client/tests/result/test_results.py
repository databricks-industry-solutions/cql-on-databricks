"""DERIVATIONS-TO-LIBRARY, CORE-NO-SILENT-MISLOWER — the caller's warehouse ``Execute`` is its session's ``rows``:
typed rows by position once the statement finishes, a raise when it fails, never an empty answer that reads as
a zero (RUN-CLIENT-WAREHOUSE-CONNECTOR)."""
from __future__ import annotations

import pytest

from cql_databricks.data_bindings import ResolverError, WarehouseSession
from cql_databricks.result import results


class _Connection:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows, error

    def cursor(self):
        connection = self

        class Cursor:
            query_id, description = "q1", None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, statement, parameters=None):
                if connection.error:
                    raise RuntimeError(connection.error)
                self.description = [("d",), ("subjects",), ("elements",), ("ordinals",)]

            def fetchall(self):
                return connection.rows

        return Cursor()

    def close(self):
        pass


def test_counts_read_typed_rows_from_the_session():
    """SPEC-METRIC — the counting query's BIGINTs arrive as ints, not the JSON strings of the old API."""
    ref = ("c.s", "t")
    rows = [(results._label(ref), 3, None, 0)]
    execute = WarehouseSession(lambda: _Connection(rows)).rows
    assert results.count_relations(execute, {ref: {"__subject_id": "string"}}, eval_id="e") == {ref: (3, None)}


def test_failure_raises():
    with pytest.raises(ResolverError, match="FAILED: bad table"):
        WarehouseSession(lambda: _Connection(error="bad table")).rows("SELECT 1")
