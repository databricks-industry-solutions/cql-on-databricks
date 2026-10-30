"""RUN-CLIENT-LIFECYCLE — the capacity check on private result tables.

Result tables carry their completion evidence in UC tags (`RUN-CLIENT-CATALOG-CHECKPOINTS`).
"""
from __future__ import annotations

from cql_databricks.data_bindings.runner import SqlRunner

from ._catalog_completion import NAME_PATTERN
from ._durable_delta import table_sql


def _capacity(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("capacity must be a positive number of result tables")


def _result_tables(run: SqlRunner, destination: tuple[str, str]) -> set[str]:
    """RUN-CLIENT-TABLE-NAMES — tables in the destination named like results (``result_table_name``)."""
    return {row["tableName"] for row in run(f"SHOW TABLES IN {table_sql(destination)}")
            if row["isTemporary"] not in (True, "true") and NAME_PATTERN.fullmatch(row["tableName"])}


def check_capacity(run: SqlRunner, destination: tuple[str, str], names: set[str], capacity: int) -> None:
    """RUN-CLIENT-LIFECYCLE — reject new allocations beyond an explicit capacity before calculating rows.

    ``names`` are the result tables this calculation needs (RUN-CLIENT-TABLE-NAMES).
    """
    _capacity(capacity)
    required = _result_tables(run, destination) | set(names)
    if len(required) > capacity:
        raise RuntimeError(f"private result capacity exceeded: {len(required)} tables required, capacity {capacity}; "
                           "choose an explicit higher capacity or review owner-authorized retirement")
