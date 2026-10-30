"""PIPE-RESULTS-SCHEMA, RUN-CLIENT-RESULT-SETS — shared columns/counts for explicit result references.

Complete selections supply validated physical schemas and exact pinned versions. Legacy evaluation-
scoped callers retain catalog-column discovery until migration; neither reference kind falls back
to the other. Both use the same membership and element-grain rules through the caller's transport.

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
from .address import EVAL_ID_COLUMN
from .relation import (ORDINAL, element_count_expr, element_grain_available, literal, quote,
                       scoped_relation, subject_count_expr)

#: SQL in, rows out (each a sequence of column values), or None for no rows.
Execute = Callable[[str], "list[tuple] | None"]


#: How a result relation is addressed: ``(catalog.schema, table)``. A measure's defines are spread
#: across one schema PER LIBRARY (`PIPE-PER-LIBRARY-SCHEMA`), so a schema-relative table name cannot
#: identify a relation once the include closure is in play — the metric builder reads one schema per
#: library of a multi-library document.
@dataclass(frozen=True)
class PinnedRelation:
    """RUN-CLIENT-CATALOG-CHECKPOINTS — selected descriptors, not a historical Delta snapshot.

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


RelationRef = tuple[str, str] | PinnedRelation


def result_relation(ref: RelationRef, eval_id: str) -> str:
    """PIPE-RESULT-DERIVATIONS, RUN-CLIENT-RESULT-SETS — explicit legacy scope or exact selected version."""
    if isinstance(ref, PinnedRelation):
        if eval_id != ref.selection_id:
            raise ValueError("derivation must use its complete selection's identity")
        ref.validate()
        return f"(SELECT * FROM {table_sql(ref.artifact.table)})"
    return scoped_relation(*ref, eval_id)


def _label(ref: RelationRef) -> str:
    if isinstance(ref, PinnedRelation):
        return f"{ref.selection_id}:{'.'.join(ref.artifact.table)}"
    return f"{ref[0]}/{ref[1]}"


def relation_columns(results_catalog: str, refs: Iterable[RelationRef], *,
                     execute: Execute) -> dict[RelationRef, dict[str, str]]:
    """RUN-CLIENT-RESULT-SETS — selected schemas from pinned metadata; legacy schemas from one catalog read.

    A missing pinned artifact fails, including when its current table name exists with another ID.
    Only legacy references may be absent from catalog discovery without raising.

    Typed dispatch needs the TYPES, not just the names: a boolean define's membership is its own value
    while every other type's is presence (``relation.met_expr``). One ``information_schema.columns``
    query answers that for a whole measure INCLUDING its include closure, so the read costs two round
    trips no matter how many libraries or defines are involved.

    **A ref that comes back missing is the answer, not an error.** A result schema also holds the
    pipeline's own ``__staging_*`` and ``__materialization_*`` tables, and a library whose MVs were never
    materialized holds nothing else — so "this relation has no columns" is how the caller learns there is
    nothing to count, rather than by pattern-matching table names it should not know about.

    Filtered to the refs asked for rather than sweeping whole schemas: the columns of an unwanted staging
    table include its full FHIR resource STRUCT, which is kilobytes of type text per row.
    """
    refs = tuple(refs)
    pinned = {ref for ref in refs if isinstance(ref, PinnedRelation)}
    if len({ref.selection_id for ref in pinned}) > 1:
        raise ValueError("derivations cannot mix complete selections")
    relations = {}
    for ref in pinned:
        if ref.artifact.table[0] != results_catalog:
            raise ValueError("selected artifact is outside the requested results catalog")
        ref.validate()
        relations[ref] = dict(json.loads(ref.artifact.schema_ddl))
    wanted = {ref for ref in refs if not isinstance(ref, PinnedRelation)}
    if not wanted:
        return relations
    if pinned:
        raise ValueError("derivations cannot mix selected and legacy relations")
    schemas = ", ".join(literal(s.split(".")[-1]) for s in sorted({s for s, _ in wanted}))
    tables = ", ".join(literal(t) for t in sorted({t for _, t in wanted}))
    sql = (f"SELECT table_schema, table_name, column_name, data_type "
           f"FROM {results_catalog}.information_schema.columns "
           f"WHERE table_schema IN ({schemas}) AND table_name IN ({tables}) "
           f"ORDER BY table_schema, table_name, ordinal_position")
    rows = execute(sql) or []

    for schema_only, table, column, data_type in ((r[0], r[1], r[2], r[3]) for r in rows):
        ref = (f"{results_catalog}.{schema_only}", table)
        # The IN-lists are a cross product; this is the exact match. The evaluation key is not a result
        # column: counts select by it (`count_relations`), never count it.
        if ref in wanted and column != EVAL_ID_COLUMN:
            relations.setdefault(ref, {})[column] = data_type
    return relations


def count_relations(execute: Execute,
                    relations: dict[RelationRef, dict[str, str]], *, eval_id: str,
                    ) -> dict[RelationRef, tuple[int, int | None]]:
    """RUN-CLIENT-RESULT-SETS, SPEC-METRIC — count one selected or legacy evaluation in one query.

    Returns ``{ref: (subjects, elements)}``. For pinned references, ``eval_id`` must be the complete
    selection ID. Legacy references require their row-level evaluation ID to avoid summing periods.

    One ``UNION ALL`` rather than a count per table, and across schemas rather than within one: a
    measure has tens of defines whose figures are read side by side (``SPEC-ATTRITION``), so they should
    come from one point in time as well as one round trip. A figure taken a minute after its base is a
    figure that can contradict it.

    Each branch also reports how many NON-NULL ordinals its relation holds, which is what decides
    whether the element count may be reported at all — see ``relation.element_grain_available``.
    """
    if not relations:
        return {}
    if any(isinstance(ref, PinnedRelation) for ref in relations) and not all(
            isinstance(ref, PinnedRelation) for ref in relations):
        raise ValueError("derivations cannot mix selected and legacy relations")

    by_label = {_label(ref): ref for ref in relations}
    if len(by_label) != len(relations):
        raise ValueError("duplicate result relation labels")
    branches = [_count_branch(ref, relations[ref], eval_id) for _, ref in sorted(by_label.items())]
    rows = execute("\nUNION ALL\n".join(branches)) or []

    counts: dict[RelationRef, tuple[int, int | None]] = {}
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
    if any(isinstance(ref, PinnedRelation) for ref in relations) and counts.keys() != relations.keys():
        raise ResultsUnavailable("count query did not return every selected output")
    return counts


def _count_branch(ref: RelationRef, columns: dict[str, str], eval_id: str) -> str:
    """One relation's branch of the counting query: the two grains plus the ordinal-population probe.

    Every branch projects the same four columns so the ``UNION ALL`` is uniform — a relation with no
    element grain casts NULL rather than being counted by a differently-shaped query.

    The label carries the SCHEMA as well as the table, separated by ``/``: schema names contain dots, so
    a dotted label could not be split back into its parts, and two libraries in one closure routinely
    declare the same define name (`Denominator` in two variants of one measure).
    """
    element = element_count_expr(columns) or "CAST(NULL AS BIGINT)"
    ordinals = f"COUNT({ORDINAL})" if ORDINAL in columns else "CAST(0 AS BIGINT)"
    relation = (result_relation(ref, eval_id) if isinstance(ref, PinnedRelation)
                else f"{ref[0]}.{quote(ref[1])} WHERE {EVAL_ID_COLUMN} = {literal(eval_id)}")
    return (f"SELECT {literal(_label(ref))} AS d, "
            f"{subject_count_expr(columns)} AS subjects, "
            f"{element} AS elements, {ordinals} AS ordinals "
            f"FROM {relation}")
