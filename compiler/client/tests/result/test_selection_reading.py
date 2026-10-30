"""RUN-CLIENT-RESULT-SETS, PIPE-RESULT-DERIVATIONS — synthetic pinned counts and shared insights."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cql_databricks.compiler.providers import ReadCoordinates
from cql_databricks.result import durable
from cql_databricks.result._durable import ResultsUnavailable
from cql_databricks.result.insights.evaluation import locate
from cql_databricks.result.insights.flow import GateRequest, cohort_flow
from cql_databricks.result.insights.metrics import Cohort, measure_metrics
from cql_databricks.result.insights.profile import define_profile
from cql_databricks.result.insights.reads import read_metrics
from cql_databricks.result.results import PinnedRelation, count_relations, relation_columns, result_relation
from cql_databricks.result.selection_reading import (SelectedOutput, SelectionClosure, open_selection,
                                                        selection_address, selection_counts)
from test_catalog_runtime import DESTINATION
from test_durable_plan import plan


@pytest.fixture
def selected():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — real runtime/selection with synthetic catalog SQL."""
    from test_catalog_runtime import CatalogRunner
    runner = CatalogRunner()
    runner.columns = [("__subject_id", "string"), ("__val", "boolean")]
    compiled = plan()
    query = SimpleNamespace(compile_pipeline=lambda **kwargs: compiled, request={"data_source": "synthetic-all"})
    settings = dict(run=runner, destination=DESTINATION, session_id="session")
    results = durable.materialize(query, "Base", "Output", **settings)
    return SimpleNamespace(results=results, backend=results._backend, compiled=compiled, runner=runner,
                           query=query, settings=settings)


class Reader:
    """SPEC-METRIC — no result discovery or legacy evaluation filtering is allowed by this transport."""

    def __init__(self, results):
        self.results = results
        self.statements = []

    def __call__(self, statement):
        self.statements.append(statement)
        assert "information_schema" not in statement
        assert "__eval_id" not in statement
        assert "cql_completed_results" not in statement
        assert "COUNT(*)" not in statement
        if "'__entered' AS c" in statement:
            return [(0, "__entered", 2, None), (0, "__reached", 0, None), (0, "__passed", 0, None),
                    (0, "__multiple", 0, None), (0, "c0", 2, 2)]
        if " AS subjects," in statement:
            rows = []
            for outcome in self.results.outcomes:
                artifact = outcome.result.artifact
                label = f"{self.results.selection.selection_id}:{'.'.join(artifact.table)}"
                if f"'{label}' AS d" in statement:
                    rows.append((label, 0 if outcome.definition == "output" else 2, None, 0))
            return rows
        if "IS NULL THEN" in statement:
            return [(0, 2)]
        if "GROUP BY 1 ORDER BY n DESC, v" in statement:
            return [("true", 2)]
        if "COUNT(DISTINCT __subject_id) AS subjects FROM" in statement:
            return [(2,)]
        return []


def options(selected):
    """PIPE-RESULT-DERIVATIONS — selection identity, not a legacy independently reconstructed eval ID."""
    return dict(library="Synthetic", version="1", data_source_id="synthetic-all", period_start=None,
                period_end=None, eval_id=selected.results.selection.selection_id, draft="",
                closure=SelectionClosure(selected.results, root="Synthetic"),
                results_catalog=DESTINATION[0])


def test_counts_read_one_complete_selection_and_successful_empty_is_zero(selected):
    """RUN-CLIENT-RESULT-SETS — current per-definition completions cannot change these output counts."""
    reader = Reader(selected.results)
    assert selection_counts(selected.results, execute=reader) == {"Base": (2, None), "Output": (0, None)}
    assert len(reader.statements) == 1
    assert "VERSION AS OF" not in reader.statements[0]
    assert "CASE WHEN `__val` THEN __subject_id END" in reader.statements[0]
    selected.runner.fail_write = selected.results.results["Output"].result.artifact.table[2]
    with pytest.raises(RuntimeError, match="compute failure"):
        durable.materialize(selected.query, "Base", "Output", mode="recompute", **selected.settings)
    with pytest.raises(ResultsUnavailable):
        durable.open_result_set(selected.query, "Base", "Output", **selected.settings)


def test_compiler_names_are_used_exactly_without_legacy_normalization(selected):
    """RUN-CLIENT-RESULT-SETS — definition names come from the selected compiler metadata."""
    closure = SelectionClosure(selected.results, root="Synthetic")
    libraries = closure.closure("synthetic-all", selected.results.selection.selection_id, root="Synthetic", draft="")
    assert isinstance(locate(libraries, "Synthetic", "Output", DESTINATION[0]), PinnedRelation)
    assert locate(libraries, "Synthetic", "output", DESTINATION[0]) is None
    assert locate(libraries, "Other", "Output", DESTINATION[0]) is None


def test_selection_handles_do_not_obey_legacy_sdp_draft_naming(selected):
    """RUN-CLIENT-RESULT-SETS — a selected library carries metadata, not a legacy table address."""
    closure = SelectionClosure(selected.results, root="Synthetic", draft="durable.opaque-selection")
    libraries = closure.closure("synthetic-all", selected.results.selection.selection_id,
                                root="Synthetic", draft="durable.opaque-selection")
    assert libraries["Synthetic"].scope.draft == "durable.opaque-selection"
    assert isinstance(locate(libraries, "Synthetic", "Output", DESTINATION[0]), PinnedRelation)


@pytest.mark.parametrize("changes", [{"data_source_id": "other"}, {"eval_id": "other"},
                                     {"root": "Other"}, {"draft": "other"}])
def test_closure_rejects_a_different_request(selected, changes):
    # The data source comes from the calculation request, not from the result's identity.
    """RUN-CLIENT-RESULT-SETS — do not relabel a previous selection as a different request."""
    closure = SelectionClosure(selected.results, root="Synthetic")
    settings = dict(data_source_id="synthetic-all", eval_id=selected.results.selection.selection_id,
                    root="Synthetic", draft="")
    with pytest.raises(ValueError, match="does not match"):
        closure.closure(**(settings | changes))


def test_actual_count_failure_propagates_without_a_physical_probe(selected):
    """RUN-CLIENT-OPTIMISTIC — derivation SQL owns access failures; readers do not preflight tables."""
    statements = []
    def denied(sql):
        statements.append(sql)
        raise PermissionError("synthetic denied read")
    with pytest.raises(PermissionError, match="synthetic denied read"):
        selection_counts(selected.results, execute=denied)
    assert len(statements) == 1
    assert "DESCRIBE" not in statements[0] and "information_schema" not in statements[0]


def test_metrics_profile_and_flow_share_the_pinned_relation_path(selected):
    """PIPE-RESULT-DERIVATIONS — count, profile and flow SQL all retain exact selection versions."""
    reader = Reader(selected.results)
    metrics = measure_metrics(**options(selected), defines=[("Synthetic", "Output", "Boolean")],
                              cohorts={("Synthetic", "Output"): Cohort()},
                              patient_function="synthetic_source.retrieve_Patient", execute=reader)
    assert metrics.defines[0].subjects == 0
    profile = define_profile(**options(selected), define="Output", define_library="Synthetic",
                             patient_function=None, execute=reader)
    assert profile.values.subjects == 0
    cohort_flow(**options(selected), gates=[GateRequest(stage="Output", stage_library="Synthetic", base="Base",
                                                        base_library="Synthetic", polarity="all",
                                                        criteria=[("Synthetic", "Output")])],
                patient_function=None, execute=reader)
    result_queries = [statement for statement in reader.statements if re.search(r"_[0-9a-f]{16}`", statement)]
    assert len(result_queries) >= 5
    assert all("VERSION AS OF" not in statement for statement in result_queries)


def test_retrieve_descriptors_survive_selection_metadata_for_read_metrics(selected):
    """RUN-READ-BARRIER, RUN-CLIENT-RESULT-SETS — use compiler descriptors, not inferred scan names."""
    selected.compiled.datasets[0] = replace(selected.compiled.datasets[0],
                                            retrieve_read=ReadCoordinates(resource_type="Patient", profile="synthetic"))
    results = durable.materialize(selected.query, "Output", **selected.settings)
    reader = Reader(results)
    payload = read_metrics(**(options(selected) | {
        "eval_id": results.selection.selection_id, "closure": SelectionClosure(results, root="Synthetic")}),
        reads=[{"key": "read", "type": "Patient", "profiles": ["synthetic"]}], execute=reader)
    assert payload["reads"] == [{"key": "read", "subjects": 2}]
    origin = json.loads(results.selection.definitions[0].origin_json)
    assert origin["retrieve_read"]["resource_type"] == "Patient"


def test_pins_reject_mixed_selection_legacy_and_missing_counts(selected):
    """RUN-CLIENT-RESULT-SETS — a caller cannot silently mix selections or turn absent counts into zero."""
    artifact = selected.results.outcomes[0].result.artifact
    ref = PinnedRelation(artifact, selected.results.selection.selection_id, selected.backend.usable)
    other = replace(ref, selection_id="another-selection")
    reader = Reader(selected.results)
    with pytest.raises(ValueError, match="mix complete selections"):
        relation_columns(DESTINATION[0], [ref, other], execute=reader)
    with pytest.raises(ValueError, match="mix selected and legacy"):
        relation_columns(DESTINATION[0], [ref, ("synthetic.results", "output")], execute=reader)
    with pytest.raises(ValueError, match="selection's identity"):
        result_relation(ref, "another-selection")
    with pytest.raises(ResultsUnavailable, match="every selected output"):
        count_relations(lambda sql: [], {ref: {"__val": "boolean"}}, eval_id=ref.selection_id)
    assert not reader.statements


def test_portable_locator_opens_only_its_complete_selection_as_the_reader(selected):
    """RUN-CLIENT-PUBLISHED, RUN-CLIENT-RESULT-IDENTITY — a locator names a destination, never a publisher."""
    address = selection_address(selected.results)
    assert "publisher" not in address and "population" not in address
    selected.runner.calls.clear()
    reopened = open_selection(address, query=selected.query, run=selected.runner)
    assert reopened.selection.selection_id == selected.results.selection.selection_id
    output = SelectedOutput(reopened, "Output")
    assert "VERSION AS OF" not in output.relation()
    assert output.columns == [("__subject_id", "string"), ("__val", "boolean")]
    assert output.origin["define"] == "Output"
    assert all(not sql.startswith(("CREATE", "MERGE", "INSERT", "UPDATE"))
               and "cql_completed_results" not in sql for sql, _ in selected.runner.calls)


def test_locator_refuses_replaced_selection_and_corrupt_artifact(selected):
    """RUN-CLIENT-RESULT-SETS — no independent-latest discovery or partial closure reads."""
    address = selection_address(selected.results)
    durable.materialize(selected.query, "Base", "Output", mode="recompute", **selected.settings)
    with pytest.raises(ResultsUnavailable, match="replaced"):
        open_selection(address, query=selected.query, run=selected.runner)
    latest = durable.open_result_set(selected.query, "Base", "Output", **selected.settings)
    del selected.runner.tables[latest.outcomes[0].result.artifact.table[2]]
    with pytest.raises(ResultsUnavailable):
        open_selection(selection_address(latest), query=selected.query, run=selected.runner)


def test_explicit_purpose_does_not_adopt_private_results_as_job_published(selected):
    """RUN-CLIENT-PUBLISHED — the recorded lifecycle is validated on a read, never changed."""
    address = selection_address(selected.results)
    assert open_selection(address, query=selected.query, run=selected.runner, purpose="private").selection.selection_id == selected.results.selection.selection_id
    with pytest.raises(PermissionError, match="separate"):
        open_selection(address, query=selected.query, run=selected.runner, purpose="published")


@pytest.mark.parametrize("address", [None, {}, {"destination": ["synthetic", "results"]},
                                    {"destination": ["synthetic"], "session_id": "session",
                                     "selection_id": "selection"}])
def test_invalid_locators_fail_before_sql(address):
    """RUN-CLIENT-RESULT-SETS — explicit complete addresses are mandatory."""
    with pytest.raises(ValueError, match="address"):
        open_selection(address, run=lambda *args: pytest.fail("invalid locator issued SQL"))


def test_locator_does_not_turn_permission_failure_into_a_miss(selected):
    """SEC-OBO — preserve the caller's denied read; no alternate credential or discovery path."""
    def denied(*args, **kwargs):
        raise PermissionError("synthetic denied read")
    with pytest.raises(PermissionError, match="synthetic denied read"):
        open_selection(selection_address(selected.results), query=selected.query, run=denied)
