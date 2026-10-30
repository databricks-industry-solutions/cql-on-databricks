"""DATA-SOURCE-UC-TAG, DATA-SYNTH-SMALL, DATA-SYNTH-LARGE — the data sources this repository ships: where their
change logs live, and the names they declare."""
from __future__ import annotations

from cql_databricks.compiler.data_source import CHANGE_DATA_LOG

from shipped_sources.sources import (SYNTHETIC_LARGE_SCHEMA, SYNTHETIC_LARGE_SOURCE_ID, SYNTHETIC_SMALL_SCHEMA,
                                    SYNTHETIC_SMALL_SOURCE_ID, change_data_log_fqn)


def test_the_shipped_change_logs_are_where_the_layout_check_looks():
    # The lander creates these views; the compiler finds a change log only beside the mounts, by name.
    for schema in (SYNTHETIC_SMALL_SCHEMA, SYNTHETIC_LARGE_SCHEMA):
        assert change_data_log_fqn("c", schema) == f"c.{schema}.{CHANGE_DATA_LOG}"


def test_the_shipped_sources_have_distinct_names_and_schemas():
    """A source's name is part of every result's address, so two sources sharing one would collide."""
    assert SYNTHETIC_SMALL_SOURCE_ID != SYNTHETIC_LARGE_SOURCE_ID
    assert SYNTHETIC_SMALL_SCHEMA != SYNTHETIC_LARGE_SCHEMA


def test_the_example_source_says_it_is_fabricated():
    """A fabricated count must never read as a measured one."""
    from shipped_sources.examples import SYNTHETIC_SMALL
    assert "abricated" in SYNTHETIC_SMALL.description
    assert SYNTHETIC_SMALL.source_id == SYNTHETIC_SMALL_SOURCE_ID
