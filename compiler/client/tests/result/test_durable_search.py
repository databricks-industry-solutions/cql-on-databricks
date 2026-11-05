"""RUN-CLIENT-RESULT-IDENTITY — listing completed results in a store's destination by their tags."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from cql_databricks.result import SchemaResultStore
from cql_databricks.result.store import _kept
from test_catalog_completion import completion
from test_catalog_runtime import DESTINATION


def test_search_is_one_bounded_parameterized_query_without_manifest_probes():
    """RUN-CLIENT-RESULT-IDENTITY — one bounded query over the four tags, no manifest and no caller lookup."""
    result = replace(completion(), table=(*DESTINATION, completion().table[2]))
    statements = []
    def run(statement, params=None):
        statements.append((statement, params))
        return [{"catalog_name": result.table[0], "schema_name": result.table[1], "table_name": result.table[2],
                 "tags_json": json.dumps(result.tags())}]
    cutoff = datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=3)))
    found = SchemaResultStore(DESTINATION, run).search(fingerprint=result.fingerprint, completed_after=cutoff, limit=20)
    assert found == (_kept(result),)
    assert len(statements) == 1
    statement, params = statements[-1]
    assert "metadata['cql_fingerprint'] = :fingerprint" in statement
    assert "metadata['cql_computed_at'] >= :computed_since" in statement
    assert statement.endswith("DESC, table_name LIMIT 20")
    assert params["fingerprint"] == result.fingerprint
    assert params["computed_since"] == "2026-01-01T21:00:00.000000+00:00"


def test_search_without_a_fingerprint_lists_every_completed_result():
    """RUN-CLIENT-RESULT-IDENTITY — the destination is the only scope; no publisher or caller filter."""
    statements = []
    def run(statement, params=None):
        statements.append((statement, params))
        return []
    assert SchemaResultStore(DESTINATION, run).search() == ()
    [(statement, params)] = statements
    assert "cql_fingerprint'] =" not in statement and "current_user" not in statement
    assert params == {"schema": DESTINATION[1], "format": "cql-catalog-result-v3", "state": "complete"}


@pytest.mark.parametrize("removed", ["publisher", "calculation_hash", "library", "library_version", "define_name",
                                     "compiler_version", "population"])
def test_removed_search_filters_are_refused(removed):
    """RUN-CLIENT-RESULT-IDENTITY — these are no longer recorded on a result, so they cannot filter one."""
    with pytest.raises(TypeError, match=removed):
        SchemaResultStore(DESTINATION, lambda *args: []).search(**{removed: "synthetic"})


def test_inconsistent_search_rows_are_rejected():
    """RUN-CLIENT-COMPLETION — corrupted metadata is not a search result."""
    result = replace(completion(), table=(*DESTINATION, completion().table[2]))
    other = replace(completion(2), table=(*DESTINATION, completion(2).table[2]))
    def run(statement, params=None):
        return [{"catalog_name": other.table[0], "schema_name": other.table[1], "table_name": other.table[2],
                 "tags_json": json.dumps(other.tags())}]
    with pytest.raises(ValueError, match="inconsistent"):
        SchemaResultStore(DESTINATION, run).search(fingerprint=result.fingerprint)
