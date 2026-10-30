"""SVC-MCP-TOOLS — the data tools, as plain functions (`agent/README.md`).

``get_specification`` · ``list_data_sources`` · ``list_tables`` · ``get_output`` · ``query_data`` ·
``get_freshness``.

**``get_output`` is why the list is short.** Given the output of a define, membership, distributions,
demographic cuts and cross-source comparison are the AGENT reasoning over data — not tools we specify,
build and maintain. An earlier draft had a tool per question shape, and the ceiling was the list of
questions we had thought of.

**Three DISCOVERY tools and three that answer.** `get_specification` says what a measure declares,
`list_data_sources` says what it can be computed against, `list_tables` says what has been. The
discovery half is what makes the surface usable by a caller that has never seen CQL on Databricks. A discovery tool adds no question
shape, which is the ceiling the count exists to protect.

**Every tool takes explicit arguments** (`APP-ASSISTANT-CONTEXT`). The UI's active selection
prefills them; nothing here reads ambient state. A tool that did would work inside CQL on Databricks and break
for every other client, and the other clients are the point (`SVC-MCP-TOOLS`).

**The warehouse arrives as a seam** (:class:`Warehouse`), not as an import of the SQL execution
helper: these are the tools an external agent reaches, so they are tested against a fake rather than
a workspace. Structural satisfaction, no inheritance — the storage-seam pattern in
``docs/coding-standards.md``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from cql_databricks.result import ResultsUnavailable
from cql_databricks.result.selection_reading import SelectedOutput, selection_address
from .model import Column, Freshness, NodeAddress, Output, OutputShape


#: How many rows a preview shows. Enough to see the shape of a row and no more — the preview exists
#: so a caller can tell what the columns MEAN, not so it can answer from them.
PREVIEW_LIMIT = 20

#: The ceiling on a `query_data` result. An agent that wants more should aggregate; handing a model
#: fifty thousand rows is not an answer, it is a context window.
QUERY_ROW_LIMIT = 500


class Warehouse(Protocol):
    """Executes SQL and returns rows. Satisfied structurally by the BFF's warehouse helper."""

    def __call__(self, sql: str) -> list[list[Any]] | None: ...


class ReadOnlyViolation(ValueError):
    """`query_data` was handed something that is not a single read.

    Raised, never worked around (`CORE-NO-SILENT-MISLOWER`): a tool that quietly stripped the
    offending clause and ran the rest would execute something nobody wrote.
    """


# ---------------------------------------------------------------------------
# 1. get_specification — CONTRIBUTED by the designer (AGENT-TOOL-REGISTRY), not defined here: the
#    Specification is the designer's, and the agent may not import it (DEC-AGENT-DESIGNER-CYCLE).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 2. list_data_sources
# ---------------------------------------------------------------------------

#: Supplies the registered Data Sources. Injected like the warehouse, so the tool is testable without
#: a Lakebase, and so exactly one component decides what a Data Source IS.
DataSourceLister = Callable[[], list[Any]]


def list_data_sources(*, lister: DataSourceLister) -> list[dict[str, Any]]:
    """Every registered Data Source: its id, its name, and what the data actually is.

    Source discovery is separate from complete-selection reads. A source ID does not select a
    result; those tools require the resultSet locator returned by Designer or a publishing Job.

    ``description`` is the source schema's COMMENT about the DATA — whose records, what period, real or
    fabricated (`DATA-SOURCE-DESCRIPTION`, `DATA-SOURCE-UC-TAG`) — and is absent when nobody wrote one,
    never synthesized from the source's other fields: that would describe the binding, not the data.
    Source-change declarations are not inspected (`RUN-CLIENT-REUSE`).
    """
    out: list[dict[str, Any]] = []
    for source in lister():
        entry: dict[str, Any] = {"id": source.id, "name": source.name}
        if source.description:
            entry["description"] = source.description
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# 3. list_tables
# ---------------------------------------------------------------------------

#: RUN-CLIENT-RESULT-SETS — exact compiler names from an explicitly selected complete result set.
ComputedLookup = Callable[[dict], list[str]]


def list_tables(computed: ComputedLookup, result_set: dict) -> list[str]:
    """RUN-CLIENT-RESULT-SETS — compiler-issued target names in one complete selection."""
    return sorted(computed(result_set))


# ---------------------------------------------------------------------------
# 4. get_output — the primitive
# ---------------------------------------------------------------------------

def get_output(address: NodeAddress, reference: SelectedOutput | None, *,
               warehouse: Warehouse, preview_limit: int = PREVIEW_LIMIT) -> Output:
    """RUN-CLIENT-RESULT-SETS — validated pins and recorded schema, including successful empty results."""
    if reference is None:
        raise ResultsUnavailable("the requested output has no complete selection")
    handle = reference.relation()
    shape = OutputShape(columns=[Column(name, kind) for name, kind in reference.columns])
    count = _rows(warehouse, f"SELECT COUNT(*) FROM {handle}")
    if len(count) != 1 or not count[0]:
        raise ResultsUnavailable("the result count query returned no evidence")
    shape.rows = int(count[0][0])
    preview = _rows(warehouse, f"SELECT * FROM {handle} LIMIT {int(preview_limit)}")
    return Output(address=address, handle=handle, shape=shape,
                  preview=[list(row) for row in preview], preview_limit=preview_limit,
                  truncated=shape.rows > len(preview))


# ---------------------------------------------------------------------------
# 5. query_data
# ---------------------------------------------------------------------------

#: A read starts with SELECT or WITH. Anything else is refused by name rather than by a deny-list of
#: verbs, because a deny-list is a list of the ones we thought of.
_READ_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

#: Comments are stripped before the shape is judged, so `/* */ DROP TABLE x` cannot enter disguised
#: as something that starts with neither verb.
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def query_data(sql: str, *, warehouse: Warehouse,
               row_limit: int = QUERY_ROW_LIMIT) -> list[list[Any]]:
    """Extract over a handle, or over a source's own tables. **The agent's own derivation.**

    Whatever this returns is `SVC-ANSWER-PROVENANCE`'s second kind of number: legitimate, and NOT
    the same claim as `get_output`'s. The caller must carry that distinction into the answer, or
    *I computed a different denominator* reads as *the denominator is different*.

    **One statement, and it must be a read.** Enforced structurally here — a single statement that
    starts with SELECT or WITH — and enforced for real by the permissions of the principal the
    warehouse runs as, which is where a governance answer belongs rather than in a regex. This
    check exists so an obvious mistake fails loudly at the boundary, not so it can be the only
    guard.
    """
    stripped = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql)).strip().rstrip(";")
    if ";" in stripped:
        raise ReadOnlyViolation(
            "query_data takes ONE statement; found a `;` separating two. Ask again as a single "
            "SELECT, or make two calls.")
    if not _READ_START.match(stripped):
        raise ReadOnlyViolation(
            "query_data runs reads only — the statement must begin with SELECT or WITH. "
            "Computing a result is a write, and writes are proposals (propose_* tools), never a "
            "side effect of a question.")
    return [list(r) for r in _rows(warehouse, f"SELECT * FROM ({stripped}) LIMIT {int(row_limit)}")]


# ---------------------------------------------------------------------------
# 6. get_freshness
# ---------------------------------------------------------------------------

def get_freshness(address: NodeAddress, reference: SelectedOutput | None) -> Freshness:
    """RUN-CLIENT-REFRESH-RESUME — evidence, not a verdict: completed_at is successful computation.

    The cql-completion-v1 payload replaces table modification time and source-change marks.
    Source freshness is explicitly not tracked. Empty completed outputs still exist.
    """
    if reference is None:
        raise ResultsUnavailable("the requested output has no complete selection")
    handle = reference.relation()
    return Freshness(handle=handle, exists=True, computed_at=reference.completed.completed_at.isoformat(),
                     result_set=selection_address(reference.results))


def _rows(warehouse: Warehouse, sql: str) -> list[list[Any]]:
    """Execute and normalize to a list of rows; a warehouse that answers ``None`` means no rows."""
    return list(warehouse(sql) or [])
