"""DEC-EVALUATOR-OPAQUE — a reference to one evaluation set's stored result, and the relation a caller reads.

The evaluator answers "where is this define's result for this scope" with a reference: the stored table's
fully qualified name and the evaluation set's filter. A caller never builds either. It reads
:meth:`ResultReference.relation`, which keeps the evaluation set's rows and drops the columns the
EVALUATOR adds (:data:`EVALUATOR_COLUMNS`); the compiler's own columns (``__subject_id``, ``__ordinal``,
``__val``) stay, because an ad-hoc ``cql()`` returns them too.

This is the API's isolation, not a security boundary: the caller reads the table with its own UC SELECT,
and a reader of the plan can see the table and the filter, at their own risk and with no contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The key the evaluator adds to every stored row (`RUN-RESULTS-BY-EVAL`).
EVAL_ID = "__eval_id"

#: The columns the evaluator adds to a stored result, and a caller never sees.
EVALUATOR_COLUMNS = (EVAL_ID,)


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@dataclass(frozen=True)
class ResultReference:
    """One evaluation set's rows of one stored result table. ``table`` is ``catalog.schema.table``."""

    table: str
    eval_id: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResultReference":
        return cls(table=str(raw["table"]), eval_id=str(raw["evalId"]))

    def to_dict(self) -> dict[str, str]:
        return {"table": self.table, "evalId": self.eval_id}

    def relation(self) -> str:
        """A FROM-able relation: this evaluation set's rows, without the evaluator's columns.

        >>> ResultReference("cat.lib_1_0.numerator", "e1").relation()
        "(SELECT * EXCEPT (`__eval_id`) FROM cat.lib_1_0.`numerator` WHERE `__eval_id` = 'e1')"
        """
        schema, name = self.table.rsplit(".", 1)
        dropped = ", ".join(_quote(c) for c in EVALUATOR_COLUMNS)
        return (f"(SELECT * EXCEPT ({dropped}) FROM {schema}.{_quote(name)} "
                f"WHERE {_quote(EVAL_ID)} = {_literal(self.eval_id)})")
