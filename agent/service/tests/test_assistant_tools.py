"""SVC-MCP-TOOLS, SVC-TOOL-ADDRESS, SVC-TOOL-FRESHNESS — the assistant's five tools.

These pin the properties an EXTERNAL caller depends on, which is the whole reason the surface exists
(`agent/README.md`): that where an output is stored is the evaluator's answer, never the
agent's derivation (`DEC-EVALUATOR-OPAQUE`), that `get_output` answers with a handle instead of rows, that `query_data` is a read, and that
`get_freshness` reports evidence and refuses to render a verdict.

The warehouse is a fake — the tools take it as a seam precisely so their contract is testable without
a workspace.
"""
from __future__ import annotations

import pytest

from cql_databricks.agent.service import (
    NodeAddress, ReadOnlyViolation, get_freshness, get_output, list_tables, query_data)
from cql_databricks.result import ResultsUnavailable
from agent.service.tests.durable_fixture import RESULT_SET, reference

CATALOG = "acme_results"

ADDRESS = NodeAddress(
    library="HypertensionBPControl", version="1.0.0", define="Initial Population",
    environment="acme-prod", period_start="2026-01-01", period_end="2026-12-31")

#: What the evaluator answers for ADDRESS (`DEC-EVALUATOR-OPAQUE`): the stored table and the set's filter.
REFERENCE = reference()


class FakeWarehouse:
    """Answers by matching a fragment of the SQL. Records every statement it was given."""

    def __init__(self, answers: dict[str, list[list]] | None = None):
        self.answers = answers or {}
        self.statements: list[str] = []

    def __call__(self, sql: str):
        self.statements.append(sql)
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return rows
        return None

    def sql_containing(self, fragment: str) -> str:
        return next(s for s in self.statements if fragment in s)


# ---------------------------------------------------------------------------
# DEC-EVALUATOR-OPAQUE — the evaluator says where an output is; the agent never derives it
# ---------------------------------------------------------------------------

class TestReference:
    def test_the_address_names_the_scope_and_nothing_about_storage(self):
        # Until 2026-10-05 the address derived the table and the `__eval_id`; when the evaluator's id gained
        # terms the agent could not see (RUN-EVAL-ID-INPUTS), every new row was silently missed.
        assert not any(hasattr(ADDRESS, attr) for attr in ("eval_id", "table", "schema", "handle", "deployment"))

    def test_the_handle_is_the_references_relation_without_the_evaluators_columns(self):
        out = get_output(ADDRESS, REFERENCE, warehouse=FakeWarehouse({"COUNT(*)": [[0]]}))
        assert out.handle == REFERENCE.relation()
        assert "VERSION AS OF" not in out.handle and "__eval_id" not in out.handle
        # The evaluator's key is dropped; the compiler's own columns stay (ad-hoc `cql()` returns them too).
        assert [c.name for c in out.shape.columns] == ["__subject_id", "__val"]

    def test_an_unselected_output_fails_without_queries(self):
        """RUN-CLIENT-RESULT-SETS — absence is not a successful empty calculation."""
        warehouse = FakeWarehouse()
        with pytest.raises(ResultsUnavailable):
            get_output(ADDRESS, None, warehouse=warehouse)
        with pytest.raises(ResultsUnavailable):
            get_freshness(ADDRESS, None)
        assert warehouse.statements == []


# ---------------------------------------------------------------------------
# get_output — the primitive
# ---------------------------------------------------------------------------

class TestGetOutput:
    def _warehouse(self, rows: int = 4210):
        return FakeWarehouse({
            "information_schema.columns": [["subject_id", "STRING"], ["__ordinal", "BIGINT"]],
            "COUNT(*)": [[rows]],
            "LIMIT 20": [["p1", 1], ["p2", 1]],
        })

    def test_returns_a_handle_and_a_shape_rather_than_the_rows(self):
        out = get_output(ADDRESS, REFERENCE, warehouse=self._warehouse())
        assert out.handle == REFERENCE.relation()
        assert [c.name for c in out.shape.columns] == ["__subject_id", "__val"]
        assert out.shape.rows == 4210
        assert len(out.preview) == 2          # the preview, not the answer
        assert out.truncated is True          # ...and it says so

    def test_marks_its_data_as_engine_computed(self):
        # SVC-ANSWER-PROVENANCE — this is what the compiler and executor produced, and the caller
        # has to be able to tell it apart from its own derivation.
        assert get_output(ADDRESS, REFERENCE, warehouse=self._warehouse()).provenance == "engine"

    def test_a_successful_empty_result_has_a_recorded_shape(self):
        out = get_output(ADDRESS, REFERENCE, warehouse=FakeWarehouse({"COUNT(*)": [[0]]}))
        assert [column.name for column in out.shape.columns] == ["__subject_id", "__val"]
        assert out.shape.rows == 0 and not out.truncated

    def test_missing_count_evidence_fails_instead_of_reporting_zero(self):
        with pytest.raises(ResultsUnavailable, match="count query"):
            get_output(ADDRESS, REFERENCE, warehouse=FakeWarehouse())

    def test_never_refreshes(self):
        # SVC-TOOL-FRESHNESS: recomputing is a write, and writes are proposals. A read tool that
        # helpfully recomputed would be a second execution path nobody asked for.
        w = self._warehouse()
        get_output(ADDRESS, REFERENCE, warehouse=w)
        for sql in w.statements:
            assert not any(verb in sql.upper() for verb in ("CREATE", "REPLACE", "INSERT", "REFRESH"))


# ---------------------------------------------------------------------------
# query_data — the agent's own derivation
# ---------------------------------------------------------------------------

class TestQueryData:
    def test_runs_a_read(self):
        w = FakeWarehouse({"SELECT": [[42]]})
        assert query_data("SELECT count(*) FROM x", warehouse=w) == [[42]]

    def test_accepts_a_CTE(self):
        w = FakeWarehouse({"WITH": [[1]]})
        assert query_data("WITH a AS (SELECT 1) SELECT * FROM a", warehouse=w) == [[1]]

    @pytest.mark.parametrize("sql", [
        "DROP TABLE acme_results.x",
        "INSERT INTO x SELECT * FROM y",
        "CREATE OR REPLACE TABLE x AS SELECT 1",
        "  update x set y = 1",
    ])
    def test_refuses_anything_that_is_not_a_read(self, sql):
        with pytest.raises(ReadOnlyViolation):
            query_data(sql, warehouse=FakeWarehouse())

    def test_refuses_a_second_statement_smuggled_after_a_read(self):
        with pytest.raises(ReadOnlyViolation):
            query_data("SELECT 1; DROP TABLE x", warehouse=FakeWarehouse())

    def test_a_comment_cannot_disguise_a_write(self):
        # The shape is judged after comments are stripped, or `/* */ DROP …` enters as something
        # that starts with neither verb.
        with pytest.raises(ReadOnlyViolation):
            query_data("/* SELECT */ DROP TABLE x", warehouse=FakeWarehouse())
        with pytest.raises(ReadOnlyViolation):
            query_data("-- SELECT\nDELETE FROM x", warehouse=FakeWarehouse())

    def test_caps_the_rows_it_will_return(self):
        w = FakeWarehouse({"SELECT": [[1]]})
        query_data("SELECT * FROM huge", warehouse=w, row_limit=25)
        assert "LIMIT 25" in w.sql_containing("huge")


# ---------------------------------------------------------------------------
# get_freshness — evidence, never a verdict
# ---------------------------------------------------------------------------

class TestGetFreshness:
    def test_reports_successful_completion_not_table_or_source_change_time(self):
        """RUN-CLIENT-REFRESH-RESUME — explicit replacement for the legacy freshness payload."""
        evidence = get_freshness(ADDRESS, REFERENCE).to_dict()
        assert evidence == {"format": "cql-completion-v1", "handle": REFERENCE.relation(),
                            "exists": True, "computedAt": "2026-01-01T00:00:00+00:00",
                            "sourceFreshness": "not_tracked",
                            "resultSet": RESULT_SET | {"calculation": REFERENCE.results.request}}
        assert "stale" not in evidence and "sourceChangeDigest" not in evidence


class TestDiscovery:
    def test_names_are_from_one_complete_selection(self):
        asked = []
        def computed(address):
            asked.append(address)
            return ["Synthetic.Output", "Synthetic.Base"]
        assert list_tables(computed, RESULT_SET) == ["Synthetic.Base", "Synthetic.Output"]
        assert asked == [RESULT_SET]
