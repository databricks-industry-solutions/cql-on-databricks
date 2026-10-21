"""RUN-CLIENT-CATALOG-CHECKPOINTS, RUN-CLIENT-ASYNC-CHECKPOINTS, RUN-CLIENT-RESULT-IDENTITY — synthetic catalog
checkpoints."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

import pytest

from cql_databricks.result._catalog_completion import (
    Completion, CompletionWriter, FORMAT, TAGS, TAG_COMPUTED_AT, TAG_FINGERPRINT, TAG_FORMAT, TAG_STATE, discover,
    discover_in_sources, result_table_name,
)


def completion(index=1):
    fingerprint = f"{index:016x}" * 4
    return Completion(table=("synthetic_catalog", "results", result_table_name("Synthetic", "Output", fingerprint)),
                      fingerprint=fingerprint, computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def rows(item, tags=None):
    return [dict(table_catalog=item.table[0], table_schema=item.table[1], table_name=item.table[2],
                 tag_name=key, tag_value=value) for key, value in (item.tags() if tags is None else tags).items()]


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


def test_one_lookup_distinguishes_completed_pending_untagged_and_missing():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — bulk discovery has no manifest, DESCRIBE or result scan."""
    complete, pending, untagged, missing = (completion(index) for index in range(1, 5))
    calls = []
    def run(sql, params):
        calls.append((sql, params))
        return rows(complete) + rows(pending, pending.tags() | {TAG_STATE: "pending"}) + rows(untagged, {None: None})
    found = discover(run, complete.table[:2], {item.fingerprint: item.table[2]
                                               for item in (complete, pending, untagged, missing)})
    assert len(calls) == 1
    assert found[complete.fingerprint].completion == complete
    assert found[pending.fingerprint].completion is None
    assert found[untagged.fingerprint].completion is None
    assert missing.fingerprint not in found
    sql, params = calls[0]
    assert "LEFT JOIN" in sql and "information_schema`.`table_tags" in sql
    assert params["schema"] == "results"
    assert json.loads(params["tables"]) == [item.table[2] for item in (complete, pending, untagged, missing)]
    assert not any(word in sql for word in ("DESCRIBE", "COUNT", "LAST_ALTERED", "cql_completed_results"))


def test_lookup_rejects_foreign_and_duplicate_metadata():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — local integrity checks need no extra warehouse calls."""
    item = completion()
    with pytest.raises(ValueError, match="unrequested"):
        discover(lambda *args: rows(completion(2)), item.table[:2], {item.fingerprint: item.table[2]})
    with pytest.raises(ValueError, match="duplicate"):
        discover(lambda *args: rows(item) + rows(item), item.table[:2], {item.fingerprint: item.table[2]})
    assert discover(lambda *args: pytest.fail("empty lookup issued SQL"), item.table[:2], {}) == {}


def test_table_names_read_as_library_and_define():
    """RUN-CLIENT-TABLE-NAMES — no product prefix; library, define and fingerprint digits only."""
    fingerprint = "0123456789abcdef" * 4
    name = result_table_name("ExampleMeasure", "Initial Population", fingerprint)
    assert name == "examplemeasure_initial_population_0123456789abcdef"
    assert not name.startswith("cql")
    assert len(result_table_name("L" * 300, "D" * 300, fingerprint)) == 80 + 1 + 120 + 1 + 16
    with pytest.raises(ValueError, match="no characters"):
        result_table_name("Synthetic", "???", fingerprint)
    with pytest.raises(ValueError, match="does not match its fingerprint"):
        discover(lambda *args: pytest.fail("issued SQL"), ("c", "s"), {fingerprint: "synthetic_output_" + "f" * 16})
    other = "0123456789abcdef" + "f" * 48
    with pytest.raises(ValueError, match="one result table name"):
        discover(lambda *args: pytest.fail("issued SQL"), ("c", "s"),
                 {fingerprint: name, other: result_table_name("ExampleMeasure", "Initial Population", other)})


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


def scoped_rows(item, tags=None):
    return [dict(catalog_name=item.table[0], schema_name=item.table[1], table_name=item.table[2],
                 tag_name=key, tag_value=value) for key, value in (item.tags() if tags is None else tags).items()]


def test_scopes_are_searched_by_table_name_in_one_statement():
    """CLIENT-DISCOVERY-SCOPE, RUN-CLIENT-RESULT-IDENTITY — another scope holds a calculation under its own name."""
    first = replace(completion(), table=("catalog_a", "shared", completion().table[2]))
    second = replace(completion(), table=("catalog_b", "shared", completion().table[2]))
    pending = replace(completion(2), table=("catalog_a", "shared", completion(2).table[2]))
    calls = []
    def run(sql, params):
        calls.append((sql, params))
        return scoped_rows(second) + scoped_rows(first) + scoped_rows(pending, pending.tags() | {TAG_STATE: "pending"})
    sources = [("catalog_a", "shared"), ("catalog_b", "shared")]
    found = discover_in_sources(run, sources, [first.table[2], pending.table[2]])
    assert found == {first.table[2]: [first, second]}
    assert len(calls) == 1
    sql, params = calls[0]
    assert sql.count("UNION ALL") == 1 and "table_name IN" in sql
    assert "owner" not in sql and "cql_publisher" not in sql and "cql_calculation" not in sql
    assert json.loads(params["tables"]) == sorted([first.table[2], pending.table[2]])


def test_scope_lookup_rejects_tables_it_did_not_ask_for():
    """CLIENT-DISCOVERY-SCOPE — a lookup that returns another scope's or another name's table fails loud."""
    item = replace(completion(), table=("catalog_a", "shared", completion().table[2]))
    outside = replace(item, table=("catalog_a", "other", item.table[2]))
    with pytest.raises(ValueError, match="outside the requested scopes"):
        discover_in_sources(lambda *args: scoped_rows(outside), [item.table[:2]], [item.table[2]])
    assert discover_in_sources(lambda *args: pytest.fail("issued SQL"), [item.table[:2]], []) == {}
