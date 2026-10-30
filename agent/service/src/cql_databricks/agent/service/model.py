"""RUN-CLIENT-RESULT-SETS, SVC-TOOL-FRESHNESS — selected outputs and successful-completion evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any



@dataclass(frozen=True)
class NodeAddress:
    """WHICH output a tool asks about: one define of one library version, for one data source and period.

    It names the scope and nothing else. Where the output is stored, and which of a table's rows are this
    scope's, are the evaluator's to say (`DEC-EVALUATOR-OPAQUE`): a tool asks it for a
    :class:`~cql_databricks.result.ResultReference` and reads through that. Until 2026-10-05 this class
    derived the table and the ``__eval_id`` itself, and when the evaluator's id gained terms the agent
    could not see (`RUN-EVAL-ID-INPUTS`), its reads silently missed every new row.
    """

    library: str
    version: str
    define: str
    data_source_id: str
    period_start: str | None = None
    period_end: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "library": self.library,
            "version": self.version,
            "define": self.define,
            "dataSourceId": self.data_source_id,
        }
        if self.period_start:
            d["periodStart"] = self.period_start
        if self.period_end:
            d["periodEnd"] = self.period_end
        return d


@dataclass(frozen=True)
class Column:
    """One column of a relation, as `information_schema` reports it."""

    name: str
    type: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type}


@dataclass
class OutputShape:
    """What is in the relation, without returning it.

    The shape is why `get_output` can answer for a relation of any size: an agent decides what to
    ask for next from the columns and the row count, and asks for it with ``query_data``.
    """

    columns: list[Column] = field(default_factory=list)
    rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"columns": [c.to_dict() for c in self.columns], "rows": self.rows}


@dataclass
class Output:
    """`get_output`'s answer: a handle, a bounded preview, and the shape (`SVC-MCP-TOOLS`).

    **Not the rows.** A define's output can be millions of rows, so the tool returns what lets the
    caller reason about them and then go and get exactly what it needs. ``provenance`` is
    ``engine`` on everything from this tool, and it is what `SVC-ANSWER-PROVENANCE` requires an
    answer to carry: this is what the compiler and the executor produced, and it is the same number
    the screen shows.
    """

    address: NodeAddress
    handle: str
    shape: OutputShape
    preview: list[list[Any]] = field(default_factory=list)
    preview_limit: int = 0
    #: ``True`` when the relation holds more rows than the preview shows — which is the ordinary
    #: case, and saying so stops a preview from being read as the answer.
    truncated: bool = False
    provenance: str = "engine"

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address.to_dict(),
            "handle": self.handle,
            "shape": self.shape.to_dict(),
            "preview": self.preview,
            "previewLimit": self.preview_limit,
            "truncated": self.truncated,
            "provenance": self.provenance,
        }


@dataclass
class Freshness:
    """RUN-CLIENT-REFRESH-RESUME — successful computation time, never source freshness."""

    handle: str
    exists: bool = False
    computed_at: str | None = None
    result_set: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"format": "cql-completion-v1", "handle": self.handle, "exists": self.exists,
                "computedAt": self.computed_at, "sourceFreshness": "not_tracked", "resultSet": self.result_set}
