"""SPEC-METRIC — the shape of a define's result relation, and what a count over it means.

The engine writes ONE relation per define and the relation's own columns say what a count over it
means. Two readers ask that question: ``run.py`` reads a relation through a Spark DataFrame (the
ad-hoc tier), and ``results/query.py`` reads the materialized views through the SQL warehouse. Both
need the same membership predicate, so the predicate lives here once — a second copy would let one
reader call a subject "met" that the other did not, which is the two-surfaces-disagreeing failure
``APP-SPEC-MODEL`` exists to prevent.

**THE VALUE COLUMN IS NOT ALWAYS ``__val``.** Measured against a live results schema
(a multi-library measure's results schema):

===========================  ==================================================================
relation shape               columns
===========================  ==================================================================
list-valued define           ``__subject_id``, ``__ordinal``, ``__val``
boolean define               ``__subject_id``, ``Numerator`` (BOOLEAN)
scalar define                ``__subject_id``, ``Lowest_Reading_A_…`` (STRUCT)
===========================  ==================================================================

So a reader that looks for ``__val`` by name finds nothing on two of the three shapes. The value
column is identified by ELIMINATION — whatever is not a key the emit owns — which is stable under
both the current emit and the ``__val`` rename in flight.

**EVERY COUNT IS FAN-OUT IMMUNE, by construction.** A relation is not one row per subject: the same
measured table carries 66,232,823 rows for 2,713 distinct subjects, and its ``denominator_exclusions``
sibling carries 57,565,315,157. Those rows are a join fan-out, not data (whose fan-out is an emit
question, not this module's), so no count here may depend on row cardinality. A subject count is
``COUNT(DISTINCT __subject_id)``; an element count is ``COUNT(DISTINCT __subject_id, __ordinal)``.
``COUNT(*)`` is banned from every metric path.
"""
from __future__ import annotations

from collections.abc import Iterable

#: The subject key every result relation carries.
SUBJECT_ID = "__subject_id"

#: The per-element key on a list-valued relation. Its presence is what gives a relation an element
#: grain at all; see ``element_grain_available`` for why presence is not enough.
ORDINAL = "__ordinal"

#: Columns the emit owns. Everything else on the relation is the define's value.
#: ``__enzyme__row__id__`` appears on the pipeline's internal materialization tables rather than on a
#: served result, but it is a key either way and must never be mistaken for a define's value.
RESERVED = frozenset({SUBJECT_ID, ORDINAL, "__enzyme__row__id__"})

def patient_rows(view: str, execute) -> str:
    """BIND-CONTRACT, DATA-SUBJECT-KEY: an environment's Patient view read as ``(__subject_id, __resource)``, the
    subject key a result carries and the resource, as a subquery. The key follows the engine's rule: the view's
    ``patient_id`` when it returns one, else ``resource.id``. The view's columns are read once with ``DESCRIBE``; a
    view without ``resource`` fails, naming it."""
    columns = {str(r[0]) for r in execute(f"DESCRIBE {view}") or [] if r and r[0] and not str(r[0]).startswith("#")}
    if "resource" not in columns:
        raise ValueError(f"the Patient view {view} returns no `resource` column; a data view returns the resource "
                         f"as a struct `resource` (BIND-CONTRACT)")
    key = "CAST(patient_id AS STRING)" if "patient_id" in columns else "CAST(resource.id AS STRING)"
    return f"(SELECT {key} AS {SUBJECT_ID}, resource AS __resource FROM {view})"


def quote(identifier: str) -> str:
    """Backtick-quote a column or relation identifier for Spark SQL."""
    return "`" + identifier.replace("`", "``") + "`"


def literal(text: str) -> str:
    """Single-quote a string literal. Define names are author-given text and reach SQL as literals."""
    return "'" + text.replace("'", "''") + "'"


def value_column(columns: Iterable[str]) -> str | None:
    """The column carrying the define's value, or ``None`` when the relation carries only keys.

    By elimination, never by name — see the module docstring for the three live relation shapes.
    """
    return next((c for c in columns if c not in RESERVED), None)


def met_expr(value_col: str | None, type_name: str | None) -> str:
    """SQL for "this subject met the define", to be wrapped in ``COUNT(DISTINCT …)``.

    A boolean define contributes its OWN VALUE: CQL's three-valued logic makes ``false`` and ``null``
    both "not in", so only ``true`` is membership. A list contributes membership only when it holds at
    least one element: the emit stores a list-valued define as one row per subject, with an EMPTY list
    for a subject that has none (``coalesce(…, array())``), and an episode-based population's subject
    is in it only when the list is non-empty. Any other type contributes
    presence — a count, a score or a selected resource means something at zero, unlike one that is null.

    ``SPEC-ATTRITION`` — the predicate applies WHENEVER a value column exists, ``__ordinal`` or not.
    The older rule ("a relation with ``__ordinal`` means present ⇒ met") was true while such a
    relation held only qualifying elements; it stopped being true when the value landed on the same
    table, and on a measured deck 96% of those rows are false. Presence would have reported every
    subject the relation touched as having met the criterion.
    """
    if value_col is None:
        return SUBJECT_ID
    if _is_boolean(type_name):
        return f"CASE WHEN {quote(value_col)} THEN {SUBJECT_ID} END"
    if _is_array(type_name):
        return f"CASE WHEN size({quote(value_col)}) > 0 THEN {SUBJECT_ID} END"
    return f"CASE WHEN {quote(value_col)} IS NOT NULL THEN {SUBJECT_ID} END"


def _is_boolean(type_name: str | None) -> bool:
    """Whether a type name denotes a boolean, across both readers' spellings.

    pyspark's ``dataType.typeName()`` says ``boolean``; ``information_schema.columns.data_type`` says
    ``BOOLEAN``. One predicate, so one normalization.
    """
    return bool(type_name) and type_name.strip().lower() == "boolean"


def _is_array(type_name: str | None) -> bool:
    """Whether a type name denotes a list: pyspark says ``array``, ``information_schema`` says ``ARRAY``,
    and a full type string reads ``array<…>``."""
    return bool(type_name) and type_name.strip().lower().split("<", 1)[0] == "array"


def subject_count_expr(columns: dict[str, str]) -> str:
    """``COUNT(DISTINCT …)`` over the membership predicate for a relation's ``{column: type}`` map."""
    col = value_column(columns)
    return f"COUNT(DISTINCT {met_expr(col, columns.get(col) if col else None)})"


def element_count_expr(columns: Iterable[str]) -> str | None:
    """``COUNT(DISTINCT __subject_id, __ordinal)`` — the element grain, or ``None`` when there is none.

    The COMPOSITE distinct is what makes the figure immune to the join fan-out: duplicate rows from a
    join share their ``(subject, ordinal)`` pair, so the composite collapses them where ``COUNT(*)``
    would have reported 66 million "encounters" for 2,713 subjects.

    A relation with no ``__ordinal`` has no element grain — a boolean says one thing about a subject,
    so an element count there would be an invented dimension.
    """
    if ORDINAL not in set(columns):
        return None
    return f"COUNT(DISTINCT {SUBJECT_ID}, {ORDINAL})"


def element_grain_available(has_ordinal: bool, populated_ordinals: int) -> bool:
    """Whether an element count may be REPORTED, given how many non-null ordinals the relation holds.

    **A GAP, STATED RATHER THAN PAPERED OVER.** ``__ordinal`` is currently NULL on every row the emit
    writes. SQL's multi-column ``COUNT(DISTINCT …)`` drops any row with a null, so the composite
    distinct evaluates to 0 on every list-valued define. Reporting that would say "this define
    returned no elements" when the truth is "the key that identifies an element carries no values" —
    opposite claims, and the first one is a lie the reader cannot detect.

    So the element count is ABSENT until the emit populates ``__ordinal``. Projecting the ordinal is
    an engine-repo question (``APP-DECOUPLE``), not one to work around here, and deriving an
    element identity from anything else on the relation would be exactly the client-side approximation
    the standards forbid.

    This is one rule evaluated against the relation in front of it, not a fallback path: the element
    count is the composite distinct, and a relation whose ordinals are empty has no element grain to
    report.
    """
    return has_ordinal and populated_ordinals > 0
