"""RUN-CLIENT-REQUEST-REOPEN, RUN-CLIENT-DRAFT-RESULTS — real public runtime over synthetic catalog transports, in a
synthetic environment."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cql_databricks.resolvers.interfaces import Resolvers
from cql_databricks.result import SchemaResultStore, durable
from cql_databricks.result.selection_reading import open_selection, selection_address
from test_catalog_runtime import CatalogRunner, store_over

#: RUN-CLIENT-DRAFT-RESULTS — a run writes the environment's draft_results and finds official results in its results.
DESTINATION = ("synthetic_catalog", "draft_results")
RESULTS = ("synthetic_catalog", "results")
from test_durable_plan import identity, plan


def readers(c):
    """CLIENT-RESOLVERS — a reader's resolvers; only the result resolver is read when the query is given."""
    return Resolvers(data=None, terminology=None, results=c.settings["results"], environment="synthetic")


@pytest.fixture
def calculation():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — only the warehouse boundary is synthetic."""
    run = CatalogRunner(destination=DESTINATION)
    compiled = plan()
    query = SimpleNamespace(compile_pipeline=lambda **options: compiled)
    settings = dict(run=run, results=store_over(DESTINATION, run, [RESULTS]), session_id="synthetic")
    return SimpleNamespace(run=run, query=query, compiled=compiled, settings=settings)


def test_public_compute_reuse_and_readonly_reopen(calculation):
    """RUN-CLIENT-REQUEST-REOPEN — an unchanged calculation and unchanged results have the same selection identity."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    assert [o.decision for o in first.outcomes] == ["computed", "computed"]
    c.run.calls.clear()
    reopened = durable.open_result_set(c.query, "Output", **c.settings)
    assert first.selection.selection_id == reopened.selection.selection_id
    assert [o.decision for o in reopened.outcomes] == ["reused", "reused"]
    assert all(sql.startswith("SELECT") for sql, _ in c.run.calls)
    # One lookup over the caller's own and the official results, one column read; no index or physical probes.
    assert len(c.run.calls) == 2
    assert "VERSION AS OF" not in reopened.sql("Output")
    assert "cql_result_" not in reopened.sql("Output")  # RUN-CLIENT-TABLE-NAMES


def test_failed_refresh_resumes_only_with_the_explicit_cutoff(calculation):
    """RUN-CLIENT-REFRESH-RESUME — caller-owned cutoff replaces hidden session state."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    cutoff = datetime.now(timezone.utc)
    c.run.fail_write = first.results["Output"].result.artifact.table[2]
    with pytest.raises(RuntimeError, match="compute failure") as failure:
        durable.materialize(c.query, "Output", mode="recompute", computed_since=cutoff, **c.settings)
    assert cutoff.isoformat() in failure.value.__notes__[0]
    c.run.fail_write = ""
    resumed = durable.materialize(c.query, "Output", mode="recompute", computed_since=cutoff, **c.settings)
    assert [o.decision for o in resumed.outcomes] == ["reused", "computed"]
    assert len(c.run.tables) == 2
    fresh = durable.materialize(c.query, "Output", mode="recompute", **c.settings)
    assert [o.decision for o in fresh.outcomes] == ["computed", "computed"]
    assert len(c.run.tables) == 2


@pytest.mark.parametrize("damage", ["upstream", "output", "tags"])
def test_incomplete_closure_reopen_fails_without_writes(calculation, damage):
    """RUN-CLIENT-COMPLETION — tags certify every dependency, never just the last table."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    table = first.outcomes[0 if damage == "upstream" else -1].result.artifact.table[2]
    if damage == "tags":
        c.run.tables[table].clear()
    else:
        del c.run.tables[table]
    c.run.calls.clear()
    with pytest.raises(durable.ResultsUnavailable):
        durable.open_result_set(c.query, "Output", **c.settings)
    assert not c.run.writes


def test_address_checks_compiler_request_and_current_results(calculation):
    """RUN-CLIENT-REQUEST-REOPEN — no stale selection masquerades as the selected calculation."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    address = selection_address(first)
    assert "definitions" not in address and "outcomes" not in address
    assert "publisher" not in address and "population" not in address
    reopened = open_selection(address, query=c.query, run=c.run, resolvers=readers(c))
    assert reopened.selection.selection_id == first.selection.selection_id
    durable.materialize(c.query, "Output", mode="recompute", **c.settings)
    with pytest.raises(durable.ResultsUnavailable, match="replaced"):
        open_selection(address, query=c.query, run=c.run, resolvers=readers(c))


def test_results_takes_only_a_published_calculation(calculation):
    """RUN-CLIENT-DRAFT-RESULTS — a query that may bind cells, folders, volumes or DataFrames is refused before it
    computes; a calculation of a library from the environment's knowledge is written to results and reopened."""
    c = calculation
    from cql_databricks.result.calculation import PreparedCalculation

    own = {"data": "synthetic_catalog.data", "terminology": "synthetic_catalog.terminology",
           "knowledge": "synthetic_catalog.knowledge"}
    run = CatalogRunner(destination=RESULTS)
    official = store_over(RESULTS, run, [RESULTS],
                          requires={role: frozenset({identity}) for role, identity in own.items()})
    settings = dict(c.settings, run=run, results=official)
    with pytest.raises(PermissionError, match="only results of the environment's own published content"):
        durable.materialize(c.query, "Output", **settings)
    cells = PreparedCalculation("Synthetic", "1", "synthetic", ("Output",), c.compiled,
                                identities=own | {"knowledge": "cql"})
    with pytest.raises(PermissionError, match="knowledge is cql"):
        durable.materialize(cells, "Output", **settings)
    assert not run.writes
    published = PreparedCalculation("Synthetic", "1", "synthetic", ("Output",), c.compiled, identities=own)
    first = durable.materialize(published, "Output", **settings)
    assert {o.result.artifact.table[1] for o in first.outcomes} == {"results"}
    run.calls.clear()
    resolved = durable.resolve_results(published, "Output", **settings)
    assert resolved.results["Output"].result == first.results["Output"].result
    assert all(sql.startswith("SELECT") for sql, _ in run.calls)


@pytest.mark.parametrize("removed", ["population", "publisher", "bindings_revision", "data_as_of"])
def test_removed_identity_arguments_are_refused(calculation, removed):
    """RUN-CLIENT-RESULT-IDENTITY — the population label and publisher are no longer part of a result."""
    c = calculation
    with pytest.raises(TypeError, match=removed):
        durable.materialize(c.query, "Output", **(c.settings | {removed: "synthetic"}))


@pytest.mark.parametrize("kwargs,match", [
    ({"mode": "changed_only"}, "unsupported reuse"), ({"session_id": ""}, "session_id"),
    ({"computed_since": datetime(2020, 1, 1)}, "timezone"),
    ({"computed_since": datetime.now(timezone.utc) + timedelta(days=1)}, "future"),
])
def test_invalid_public_request_never_writes(calculation, kwargs, match):
    """RUN-CLIENT-DURABLE — malformed requests fail before starting computation."""
    c = calculation
    with pytest.raises(ValueError, match=match):
        durable.materialize(c.query, "Output", **(c.settings | kwargs))
    assert not c.run.writes


def test_capacity_counts_distinct_tables_not_refreshes(calculation):
    """RUN-CLIENT-LIFECYCLE — replacing a table consumes no additional capacity."""
    c = calculation
    with pytest.raises(RuntimeError, match="capacity"):
        durable.materialize(c.query, "Output", **(c.settings | {"results": SchemaResultStore(DESTINATION, c.run,
                                                                                             capacity=1)}))
    assert not c.run.writes
    for mode in ("skip_if_computed", "recompute"):
        durable.materialize(c.query, "Output", mode=mode,
                            **(c.settings | {"results": SchemaResultStore(DESTINATION, c.run, capacity=2)}))
    assert len(c.run.tables) == 2


def test_legacy_session_or_serialized_plan_is_not_a_reopen_path():
    """RUN-CLIENT-REQUEST-REOPEN — fail loud; no retained index/portable-plan fallback."""
    for value in ("old-session", {"definitions": []}):
        with pytest.raises(ValueError, match="calculation request"):
            durable.open_result_set(value)


def test_failed_sibling_refresh_cannot_reopen_a_partial_current_selection(calculation):
    """RUN-CLIENT-CATALOG-CHECKPOINTS — a pending table is never read as a completed result."""
    c = calculation
    first = durable.materialize(c.query, "Base", "Output", **c.settings)
    c.run.fail_write = first.results["Output"].result.artifact.table[2]
    with pytest.raises(RuntimeError, match="compute failure"):
        durable.materialize(c.query, "Base", "Output", mode="recompute", **c.settings)
    c.run.calls.clear()
    with pytest.raises(durable.ResultsUnavailable):
        durable.resolve_results(c.query, "Base", "Output", **c.settings)
    assert not c.run.writes


@pytest.mark.parametrize("missing", ["selection", "artifact", "upstream_artifact"])
def test_missing_tags_or_tables_fail_without_calculation(calculation, missing):
    """RUN-CLIENT-COMPLETION — no incomplete closure is returned by request reopening."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    if missing == "selection":
        for tags in c.run.tables.values():
            tags.clear()
    else:
        del c.run.tables[first.outcomes[0 if missing == "upstream_artifact" else -1].result.artifact.table[2]]
    c.run.calls.clear()
    with pytest.raises(durable.ResultsUnavailable):
        durable.resolve_results(c.query, "Output", **c.settings)
    assert not c.run.writes


def test_changed_calculation_does_not_resolve_a_sessions_old_results(calculation):
    """RUN-CLIENT-RESULT-IDENTITY — the same session label cannot bypass the fingerprint."""
    c = calculation
    durable.materialize(c.query, "Output", **c.settings)
    other = replace(c.compiled, datasets=[replace(d, durable=identity("c" * 64)) if d.name == "base" else d
                                          for d in c.compiled.datasets])
    c.run.calls.clear()
    with pytest.raises(durable.ResultsUnavailable):
        durable.resolve_results(SimpleNamespace(compile_pipeline=lambda **options: other), "Output", **c.settings)
    assert not c.run.writes


def test_foreground_failure_still_drains_prior_successful_tags(calculation):
    """RUN-CLIENT-ASYNC-CHECKPOINTS — no abandoned checkpoint threads or index-release step."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    c.run.fail_write = first.results["Output"].result.artifact.table[2]
    with pytest.raises(RuntimeError, match="compute failure"):
        durable.materialize(c.query, "Output", mode="recompute", **c.settings)
    assert c.run.tables[first.outcomes[0].result.artifact.table[2]]["cql_result_state"] == "complete"
    assert c.run.tables[c.run.fail_write]["cql_result_state"] == "pending"


@pytest.mark.parametrize("failure", ["output", "tags"])
def test_public_retry_retains_explicit_cutoff_and_completed_defines(calculation, failure):
    """RUN-CLIENT-REFRESH-RESUME — no hidden index: the retry carries the same UTC cutoff."""
    c = calculation
    first = durable.materialize(c.query, "Output", **c.settings)
    cutoff = datetime.now(timezone.utc)
    output = first.results["Output"].result.artifact.table[2]
    if failure == "output":
        c.run.fail_write = output
    else:
        c.run.fail_tag = output
    with pytest.raises(RuntimeError, match="failure") as error:
        durable.materialize(c.query, "Output", mode="recompute", computed_since=cutoff, **c.settings)
    assert cutoff.isoformat() in error.value.__notes__[0]
    c.run.fail_write = c.run.fail_tag = ""
    resumed = durable.materialize(c.query, "Output", mode="recompute", computed_since=cutoff, **c.settings)
    assert [o.decision for o in resumed.outcomes] == ["reused", "computed"]
    assert resumed.selection.computed_since == cutoff
    assert len(c.run.tables) == 2


def test_error_without_add_note_keeps_original_failure(calculation, monkeypatch, caplog):
    """BUILD-CLIENT-COMPAT — recovery context works without BaseException.add_note."""
    class ComputationFailure(RuntimeError):
        add_note = None
    failure = ComputationFailure("synthetic computation failed")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(durable, "_execute", fail)
    c = calculation
    with pytest.raises(ComputationFailure) as caught:
        durable.materialize(c.query, "Output", mode="recompute", **c.settings)
    assert caught.value is failure
    assert "computed_since=" in caplog.text
    assert "Retry with computed_since=" in failure.__notes__[0]
