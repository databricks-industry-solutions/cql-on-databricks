"""RUN-CLIENT-CATALOG-CHECKPOINTS, RUN-CLIENT-ASYNC-CHECKPOINTS, RUN-CLIENT-RESULT-IDENTITY — synthetic catalog
checkpoints."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

import pytest

from cql_databricks.result._catalog_completion import (
    Completion, CompletionWriter, FORMAT, TAGS, TAG_COMPUTED_AT, TAG_FINGERPRINT, TAG_FORMAT, TAG_STATE,
    result_table_name, writer_digest,
)

OWNER = "synthetic-user"
W = writer_digest(OWNER)


def completion(index=1):
    fingerprint = f"{index:016x}" * 4
    return Completion(table=("synthetic_catalog", "results", result_table_name("Synthetic", "Output", fingerprint, W)),
                      fingerprint=fingerprint, computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def rows(item, tags=None, owner=OWNER):
    return [dict(table_catalog=item.table[0], table_schema=item.table[1], table_name=item.table[2],
                 table_owner=owner, tag_name=key, tag_value=value) for key, value in (item.tags() if tags is None else tags).items()]


def test_explicit_timestamp_round_trips_without_catalog_update_time():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — metadata edits cannot change the recorded execution time."""
    item = completion()
    assert Completion.from_tags(item.table, item.tags()) == item
    assert item.tags()[TAG_COMPUTED_AT] == "2026-01-01T00:00:00.000000+00:00"
    assert all(len(value) <= 256 for value in item.tags().values())
    assert "LAST_ALTERED" not in repr(item.tags())


def test_a_result_carries_exactly_four_tags():
    """RUN-CLIENT-RESULT-IDENTITY — format, state, computation time and fingerprint; no other hash."""
    assert TAGS == (TAG_FORMAT, TAG_STATE, TAG_COMPUTED_AT, TAG_FINGERPRINT)
    assert set(completion().tags()) == set(TAGS)
    assert completion().tags()[TAG_FINGERPRINT] == completion().fingerprint


def test_an_earlier_result_format_is_a_miss():
    """RUN-CLIENT-RESULT-IDENTITY — a table tagged by an earlier release is computed again once."""
    item = completion()
    assert FORMAT == "cql-catalog-result-v3"
    assert Completion.from_tags(item.table, item.tags() | {TAG_FORMAT: "cql-catalog-result-v2"}) is None


@pytest.mark.parametrize("tags", [{}, {TAG_FORMAT: FORMAT, TAG_STATE: "pending"}])
def test_existence_and_pending_markers_are_not_success(tags):
    """RUN-CLIENT-CATALOG-CHECKPOINTS — neither an existing table nor an old date proves completion."""
    assert Completion.from_tags(completion().table, tags) is None


def test_malformed_success_is_not_silently_reused_or_recomputed():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — reject contradictory completion metadata."""
    item = completion()
    with pytest.raises(ValueError, match="incomplete successful"):
        Completion.from_tags(item.table, {TAG_FORMAT: FORMAT, TAG_STATE: "complete"})
    with pytest.raises(ValueError, match="timezone"):
        replace(item, computed_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="fingerprint does not match"):
        replace(item, fingerprint="f" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        replace(item, fingerprint="a-workspace-job-id")


def test_table_names_read_as_library_define_fingerprint_and_writer():
    """RUN-CLIENT-TABLE-NAMES — no product prefix; library, define, fingerprint digits and the writer's digest."""
    fingerprint = "0123456789abcdef" * 4
    name = result_table_name("ExampleMeasure", "Initial Population", fingerprint, W)
    assert name == f"examplemeasure_initial_population_0123456789abcdef_{W}"
    assert not name.startswith("cql")
    assert len(result_table_name("L" * 300, "D" * 300, fingerprint, W)) == 80 + 1 + 120 + 1 + 16 + 1 + 8
    with pytest.raises(ValueError, match="no characters"):
        result_table_name("Synthetic", "???", fingerprint, W)


def test_invalidation_is_synchronous_and_does_not_advance_success_timestamp():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — stale success is hidden before replacing existing data."""
    calls = []
    with CompletionWriter(lambda sql: calls.append(sql)) as writer:
        writer.invalidate(completion().table)
        assert len(calls) == 1
        assert "'cql_result_state' = 'pending'" in calls[0]
        assert TAG_COMPUTED_AT not in calls[0]
        writer.submit(completion())
    assert len(calls) == 2
    assert "'cql_result_state' = 'complete'" in calls[1]
    assert TAG_COMPUTED_AT in calls[1]


def test_tagging_overlaps_work_and_exit_waits_for_completion():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — background publication is awaited, never fire-and-forget."""
    entered, release, finished = Event(), Event(), Event()
    def run(sql):
        entered.set()
        assert release.wait(3), "test did not release synthetic tag write"
        finished.set()
    with CompletionWriter(run) as writer:
        future = writer.submit(completion())
        assert entered.wait(3)
        assert not future.done() and not finished.is_set()
        release.set()
    assert finished.is_set() and future.done()
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit(completion(2))


def test_successful_work_is_checkpointed_even_when_later_data_execution_fails():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — a later query failure does not discard successful checkpoints."""
    calls = []
    with pytest.raises(RuntimeError, match="synthetic data query failed"):
        with CompletionWriter(lambda sql: calls.append(sql)) as writer:
            writer.submit(completion())
            raise RuntimeError("synthetic data query failed")
    assert len(calls) == 1 and TAG_COMPUTED_AT in calls[0]


def test_failed_upstream_tag_prevents_downstream_success_and_fails_caller():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — no advertised complete dependent with an unrecorded input."""
    release, calls = Event(), []
    def run(sql):
        calls.append(sql)
        assert release.wait(3)
        raise RuntimeError("synthetic tag failure")
    with pytest.raises(RuntimeError, match="synthetic tag failure"):
        with CompletionWriter(run) as writer:
            base = completion()
            writer.submit(base)
            writer.submit(completion(2), after=(base.table,))
            release.set()
    assert len(calls) == 1


def test_duplicate_or_forward_dependencies_fail_before_submission():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — reject cycles/ambiguous tables without deadlocking workers."""
    with CompletionWriter(lambda sql: None) as writer:
        writer.submit(completion())
        with pytest.raises(ValueError, match="duplicate"):
            writer.submit(completion())
        with pytest.raises(ValueError, match="not yet submitted"):
            writer.submit(completion(2), after=(completion(3).table,))
        with pytest.raises(ValueError, match="pending completion"):
            writer.invalidate(completion().table)


def test_foreground_error_keeps_background_failure_visible(caplog):
    """RUN-CLIENT-ASYNC-CHECKPOINTS — preserve the data error, but never hide the tag failure."""
    release = Event()
    def run(sql):
        assert release.wait(3)
        raise RuntimeError("synthetic background failure")
    with pytest.raises(ValueError, match="synthetic data failure"):
        with CompletionWriter(run) as writer:
            writer.submit(completion())
            release.set()
            raise ValueError("synthetic data failure")
    assert "synthetic background failure" in caplog.text
