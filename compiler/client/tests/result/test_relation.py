"""SPEC-METRIC, SPEC-ATTRITION — the relation-shape rules both result readers share.

Every case here is measured against a live results schema
(``<results catalog>.examplemeasure_1_0_0_081b9dc0``, 2026-08-03) rather than assumed, because
the assumption is what the rules replace: that a relation is one row per subject, and that the value
column is called ``__val``.
"""
from __future__ import annotations

from cql_databricks.result.relation import (
    element_count_expr, element_grain_available, met_expr, value_column)


# --- value_column: the three live relation shapes ----------------------------

def test_list_valued_relation_values_are_val():
    """`__subject_id, __ordinal, __val` — the shape a list-valued define writes."""
    assert value_column(["__subject_id", "__ordinal", "__val"]) == "__val"


def test_boolean_relation_value_is_named_after_the_define():
    """The defect reading `__val` by name would have caused: a boolean define's relation has no `__val`
    at all — its value column carries the define's own name (`Numerator BOOLEAN`)."""
    assert value_column(["__subject_id", "Numerator"]) == "Numerator"


def test_scalar_relation_value_is_named_after_the_define():
    """Same for a scalar: `Lowest_Reading_A_On_Most_Recent_Day STRUCT`."""
    cols = ["__subject_id", "Lowest_Reading_A_On_Most_Recent_Day"]
    assert value_column(cols) == "Lowest_Reading_A_On_Most_Recent_Day"


def test_ordinal_is_never_mistaken_for_a_value():
    """Column ORDER must not decide it: `__ordinal` precedes `__val` on the wire and picking the first
    non-subject column is what put an element ordinal in the Patient Lens."""
    assert value_column(["__subject_id", "__ordinal"]) is None


def test_bookkeeping_columns_are_not_values():
    """`__enzyme__row__id__` rides on the pipeline's own materialization tables — a key, not a value."""
    assert value_column(["__subject_id", "__enzyme__row__id__", "Denominator"]) == "Denominator"


def test_keys_only_relation_has_no_value():
    assert value_column(["__subject_id"]) is None


# --- met_expr: membership -----------------------------------------------------

def test_boolean_membership_is_the_value_itself():
    """CQL's three-valued logic: `false` and `null` are both 'not in', so only `true` counts."""
    assert met_expr("Numerator", "boolean") == "CASE WHEN `Numerator` THEN __subject_id END"


def test_information_schema_spelling_of_boolean_is_recognized():
    """The warehouse reader gets `BOOLEAN` from `information_schema`; pyspark says `boolean`. One rule."""
    assert met_expr("Numerator", "BOOLEAN") == "CASE WHEN `Numerator` THEN __subject_id END"


def test_non_boolean_membership_is_presence():
    """A count or a score means something at zero — computed is not the same as absent."""
    assert met_expr("Encounter Count", "integer") == (
        "CASE WHEN `Encounter Count` IS NOT NULL THEN __subject_id END")


def test_element_relation_applies_the_value_predicate():
    """SPEC-ATTRITION — the fix to "a relation with `__ordinal` means present ⇒ met". That was true while
    such a relation held only qualifying elements; the value now sits on the same table and on a measured
    deck 96% of those rows are false, so presence reported every subject the relation touched as met."""
    assert met_expr("__val", "struct") == "CASE WHEN `__val` IS NOT NULL THEN __subject_id END"


def test_keys_only_relation_counts_bare_subjects():
    assert met_expr(None, None) == "__subject_id"


def test_value_column_is_quoted():
    """Value columns carry the define's name, which is author-given text with spaces and backticks."""
    assert met_expr("Odd`Name", "integer").startswith("CASE WHEN `Odd``Name` IS NOT NULL")


# --- element_count_expr: the element grain -----------------------------------

def test_element_count_is_a_composite_distinct():
    """The count must be immune to the join fan-out: the measured `numerator` relation holds 66,232,823
    rows for 2,713 subjects, so `COUNT(*)` would have reported 66 million "encounters". Duplicate rows
    from a join share their `(subject, ordinal)` pair, which the composite distinct collapses."""
    assert element_count_expr(["__subject_id", "__ordinal", "__val"]) == (
        "COUNT(DISTINCT __subject_id, __ordinal)")


def test_relation_without_ordinal_has_no_element_grain():
    """A Boolean says one thing about a subject, so an element count would be an invented dimension."""
    assert element_count_expr(["__subject_id", "Numerator"]) is None


# --- element_grain_available: the emit gap, stated ---------------------------

def test_element_grain_is_unavailable_while_ordinals_are_null():
    """The gap this exists for. `__ordinal` is NULL on every row the emit writes — measured:
    `example_condition_diagnosis` has 87 rows, 15 distinct subjects, 0 non-null ordinals. SQL drops
    null components from a multi-column DISTINCT, so the composite evaluates to 0 for every list-valued
    define. Reporting that 0 would claim "returned no elements" where the truth is "the key that
    identifies an element carries no values" — opposite claims."""
    assert not element_grain_available(has_ordinal=True, populated_ordinals=0)


def test_element_grain_is_available_once_ordinals_are_populated():
    """Nothing here has to change when the emit starts projecting the ordinal — the count is already the
    right one, and it becomes reportable on its own."""
    assert element_grain_available(has_ordinal=True, populated_ordinals=87)


def test_no_ordinal_column_means_no_element_grain():
    assert not element_grain_available(has_ordinal=False, populated_ordinals=0)


def test_an_empty_list_is_not_membership():
    """SPEC-METRIC — a list-valued define's subject is a member only when the list has an element.

    The emit writes one row per subject with an empty list for those who have none, so presence
    counted every subject: MedicationDocumentation's Denominator read 19 where 12 patients had an encounter.
    """
    expected = "CASE WHEN size(`__val`) > 0 THEN __subject_id END"
    assert met_expr("__val", "ARRAY") == expected        # information_schema.columns.data_type
    assert met_expr("__val", "array") == expected        # pyspark typeName()
    assert met_expr("__val", "array<struct<id:string>>") == expected
