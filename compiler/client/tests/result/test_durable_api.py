"""RUN-CLIENT-DURABLE, RUN-CLIENT-OPTIMISTIC — public boundaries and loud SQL failures."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cql_databricks.result import durable
from cql_databricks.result._catalog_reader import CatalogReader
from cql_databricks.result._catalog_runtime import CurrentArtifact
from test_catalog_runtime import DESTINATION, DRAFT, ENVIRONMENT, CatalogRunner
from test_durable_plan import plan


def execute(query=None, **kwargs):
    settings = dict(run=CatalogRunner(), environment=ENVIRONMENT)
    targets = kwargs.pop("targets", ("Output",))
    return durable.materialize(query or SimpleNamespace(compile_pipeline=lambda **opts: plan()),
                               *targets, **(settings | kwargs))


def test_named_results_and_new_call_share_the_serial_runtime():
    """RUN-CLIENT-RESUME — public calls discover tags, with immutable named outcomes."""
    run = CatalogRunner()
    first = execute(run=run)
    second = execute(run=run)
    assert set(first.results) == {"Output"}
    assert first.results["Output"].decision == "computed"
    assert second.results["Output"].decision == "reused"
    assert len(run.writes) == 2
    assert first.selection.destination == second.selection.destination == DRAFT
    with pytest.raises(TypeError):
        first.results["other"] = first.results["Output"]


@pytest.mark.parametrize("kwargs,message", [
    ({"targets": ()}, "explicit definition targets"),
    ({"targets": ("Output", "Output")}, "duplicate"),
    ({"targets": ("missing",)}, "exactly one"),
    ({"purpose": "shared"}, "purpose must be private or published"),
    ({"mode": "changed_only"}, "unsupported reuse mode"),
    ({"capacity": 0}, "capacity"), ({"session_id": ""}, "session_id"),
])
def test_invalid_requests_fail_before_writes(kwargs, message):
    """RUN-CLIENT-DURABLE — no silent destination, mode conversion or result writes."""
    run = CatalogRunner()
    with pytest.raises(ValueError, match=message):
        execute(run=run, **kwargs)
    assert not run.writes


def test_cycles_fail_before_storage():
    """RUN-CLIENT-COMPLETION — an unsupported graph creates no result objects."""
    compiled = plan()
    compiled.datasets[0] = replace(compiled.datasets[0], reads=["output"])
    run = CatalogRunner()
    with pytest.raises(ValueError, match="dependency cycle"):
        execute(SimpleNamespace(compile_pipeline=lambda **opts: compiled), run=run)
    assert not run.calls


class SqlFailure(Exception):
    pass


@pytest.mark.parametrize("condition", ["TABLE_OR_VIEW_NOT_FOUND", "INSUFFICIENT_PERMISSIONS",
                                      "UNAUTHENTICATED", "CONNECTION_FAILURE", "UNSUPPORTED_FEATURE"])
def test_actual_read_errors_propagate_without_probes_or_recomputation(condition):
    """RUN-CLIENT-OPTIMISTIC — current reads do not infer versions or repair missing tables."""
    statements = []
    def fail(statement):
        statements.append(statement)
        raise SqlFailure(condition)
    backend = CatalogReader(DESTINATION, fail)
    artifact = CurrentArtifact(table=(*DESTINATION, "synthetic"), schema_ddl='[["__val","INT"]]')
    assert backend.usable(artifact) and not statements
    result = SimpleNamespace(result=SimpleNamespace(artifact=artifact))
    selected = durable.MaterializedResults({"Output": result}, (), backend, None)
    with pytest.raises(SqlFailure, match=condition):
        selected.read("Output")
    assert statements == ["SELECT * FROM `synthetic_catalog`.`results`.`synthetic`"]


def test_artifact_validation_is_local_and_rejects_wrong_destination():
    """RUN-CLIENT-OPTIMISTIC — external table modification is the customer's responsibility."""
    backend = CatalogReader(DESTINATION, lambda *args: pytest.fail("unexpected physical probe"))
    artifact = CurrentArtifact(table=(*DESTINATION, "synthetic"), schema_ddl='[["__val","INT"]]')
    assert backend.usable(artifact)
    with pytest.raises(ValueError, match="outside the result destination"):
        backend.usable(replace(artifact, table=("other", "results", "synthetic")))


@pytest.mark.parametrize("condition", ["INSUFFICIENT_PERMISSIONS", "CONNECTION_FAILURE",
                                      "TABLE_OR_VIEW_NOT_FOUND"])
def test_published_catalog_errors_never_write(condition):
    """RUN-CLIENT-PUBLISHED — preserve metadata read errors, never create replacement storage."""
    calls = []
    def run(statement, params=None):
        calls.append(statement)
        raise SqlFailure(condition)
    with pytest.raises(SqlFailure, match=condition):
        durable.resolve_results(SimpleNamespace(compile_pipeline=lambda **opts: plan()), "Output",
            run=run, environment=ENVIRONMENT)
    assert len(calls) == 1 and calls[0].startswith("SELECT")


@pytest.mark.parametrize("settings", [{"run": None}, {"spark": object()}])
def test_ambiguous_or_absent_sql_transport_is_rejected(settings):
    """RUN-CLIENT-DURABLE — no credential/transport fallback."""
    with pytest.raises(ValueError, match="exactly one"):
        execute(**settings)
