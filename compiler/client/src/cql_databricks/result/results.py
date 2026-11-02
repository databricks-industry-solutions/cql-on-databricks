"""RUN-CLIENT-RESULT-SETS — shared columns and counts over one complete selection's pins.

Every relation here is a :class:`PinnedRelation` of one complete selection: its columns come from the selection's
recorded schema and its rows from the selected table, never from a catalog read. Every derivation counts and
describes a selection through this module, with the same membership and element-grain rules.

The derivations read result tables as the caller (`DEC-STATELESS-SERVICES`, DERIVATIONS-TO-LIBRARY),
so every read takes an :data:`Execute`, a callable from SQL to rows. On a SQL warehouse it is the caller's
session's ``rows`` (``warehouse_session(...).rows``, RUN-CLIENT-WAREHOUSE-CONNECTOR). **Counts are fan-out
immune and ``COUNT(*)`` is banned**: the rules are ``relation``'s.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ._catalog_runtime import CurrentArtifact
from ._durable import ResultsUnavailable
from ._durable_delta import table_sql
from .relation import ORDINAL, element_count_expr, element_grain_available, literal, subject_count_expr

#: SQL in, rows out (each a sequence of column values), or None for no rows.
Execute = Callable[[str], "list[tuple] | None"]


@dataclass(frozen=True)
class PinnedRelation:
    """RUN-CLIENT-CATALOG-CHECKPOINTS — selected descriptors, not a historical Delta snapshot.

    One result relation of one complete selection, addressed by its selected table.

    The internal name predates current-table reads. The selection ID moves when a result is recomputed, so a
    reopened selection that changed is refused; owners must serialize writes with reads that require a
    consistent selection.
    """

    artifact: CurrentArtifact
    selection_id: str
    usable: Callable[[CurrentArtifact], bool] = field(repr=False, compare=False)

    def validate(self) -> None:
        """RUN-CLIENT-OPTIMISTIC — validate local descriptors; actual SQL enforces table access."""
        if not self.usable(self.artifact):
            raise ResultsUnavailable("selected derivation artifact is no longer available")


def result_relation(ref: PinnedRelation, eval_id: str) -> str:
    """PIPE-RESULT-DERIVATIONS, RUN-CLIENT-RESULT-SETS — the selected table, read under its complete selection."""
    if eval_id != ref.selection_id:
        raise ValueError("derivation must use its complete selection's identity")
    ref.validate()
    return f"(SELECT * FROM {table_sql(ref.artifact.table)})"


def _label(ref: PinnedRelation) -> str:
    return f"{ref.selection_id}:{'.'.join(ref.artifact.table)}"


def relation_columns(results_catalog: str, refs: Iterable[PinnedRelation]) -> dict[PinnedRelation, dict[str, str]]:
    """RUN-CLIENT-RESULT-SETS — each relation's columns and types, from its selected schema descriptor.

    A missing pinned artifact fails, including when its current table name exists with another ID.

    Typed dispatch needs the TYPES, not just the names: a boolean define's membership is its own value
    while every other type's is presence (``relation.met_expr``). The descriptor carries them, so reading the
    columns costs no round trip.
    """
    refs = tuple(refs)
    if len({ref.selection_id for ref in refs}) > 1:
        raise ValueError("derivations cannot mix complete selections")
    relations = {}
    for ref in refs:
        if ref.artifact.table[0] != results_catalog:
            raise ValueError("selected artifact is outside the requested results catalog")
        ref.validate()
        relations[ref] = dict(json.loads(ref.artifact.schema_ddl))
    return relations


def count_relations(execute: Execute,
                    relations: dict[PinnedRelation, dict[str, str]], *, eval_id: str,
                    ) -> dict[PinnedRelation, tuple[int, int | None]]:
    """RUN-CLIENT-RESULT-SETS, SPEC-METRIC — count one complete selection's relations in one query.

    Returns ``{ref: (subjects, elements)}``. ``eval_id`` must be the complete selection ID.

    One ``UNION ALL`` rather than a count per table, and across schemas rather than within one: a
    measure has tens of defines whose figures are read side by side (``SPEC-ATTRITION``), so they should
    come from one point in time as well as one round trip. A figure taken a minute after its base is a
    figure that can contradict it.

    Each branch also reports how many NON-NULL ordinals its relation holds, which is what decides
    whether the element count may be reported at all — see ``relation.element_grain_available``.
    """
    if not relations:
        return {}

    by_label = {_label(ref): ref for ref in relations}
    if len(by_label) != len(relations):
        raise ValueError("duplicate result relation labels")
    branches = [_count_branch(ref, relations[ref], eval_id) for _, ref in sorted(by_label.items())]
    rows = execute("\nUNION ALL\n".join(branches)) or []

    counts: dict[PinnedRelation, tuple[int, int | None]] = {}
    for row in rows:
        ref = by_label[str(row[0])]
        subjects = int(row[1] or 0)
        elements = int(row[2]) if row[2] is not None else None
        ordinals = int(row[3] or 0)
        has_ordinal = ORDINAL in relations.get(ref, {})
        if not element_grain_available(has_ordinal, ordinals):
            elements = None
        if ref in counts:
            raise ValueError("duplicate count for result relation")
        counts[ref] = (subjects, elements)
    if counts.keys() != relations.keys():
        raise ResultsUnavailable("count query did not return every selected output")
    return counts


def _count_branch(ref: PinnedRelation, columns: dict[str, str], eval_id: str) -> str:
    """One relation's branch of the counting query: the two grains plus the ordinal-population probe.

    Every branch projects the same four columns so the ``UNION ALL`` is uniform — a relation with no
    element grain casts NULL rather than being counted by a differently-shaped query.

    The label carries the selection and the table's full name, so two selected tables with one name in
    different schemas stay distinct (two libraries in one closure routinely declare the same define name,
    `Denominator` in two variants of one measure).
    """
    element = element_count_expr(columns) or "CAST(NULL AS BIGINT)"
    ordinals = f"COUNT({ORDINAL})" if ORDINAL in columns else "CAST(0 AS BIGINT)"
    return (f"SELECT {literal(_label(ref))} AS d, "
            f"{subject_count_expr(columns)} AS subjects, "
            f"{element} AS elements, {ordinals} AS ordinals "
            f"FROM {result_relation(ref, eval_id)}")
