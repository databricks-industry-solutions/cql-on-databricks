"""RUN-CLIENT-MEASURE-SCORE, SPEC-POPULATION-CASCADE — the score is the one figure that is cascaded.

The cascade SQL runs on sqlite, as `test_insights_metrics.py` runs it. Synthetic diabetes measure:
p1-p5 have diabetes (Initial Population, Denominator), p5 is in hospice (Denominator Exclusions), and the
Numerator define, "HbA1c > 9% or missing", is true for p1, p2, p5 and the non-diabetic p6-p9.
"""
from __future__ import annotations

import sqlite3

import pytest

from cql_databricks.result.insights.evaluation import MetricsUnavailable
from cql_databricks.result.insights.score import measure_score, parse_scoring, subjects_sql

DIABETES = {
    "Initial Population": ["p1", "p2", "p3", "p4", "p5"],
    "Denominator": ["p1", "p2", "p3", "p4", "p5"],
    "Denominator Exclusions": ["p5", "p9"],
    "Numerator": ["p1", "p2", "p5", "p6", "p7", "p8", "p9"],
}


def _sqlite(tables: dict[str, list[str]]):
    db = sqlite3.connect(":memory:")
    names = {}
    for i, (population, subjects) in enumerate(tables.items()):
        names[population] = f"t{i}"
        db.execute(f"CREATE TABLE t{i} (__subject_id TEXT)")
        db.executemany(f"INSERT INTO t{i} VALUES (?)", [(s,) for s in subjects])
    subjects = {p: f"SELECT DISTINCT __subject_id FROM {t} WHERE __subject_id IS NOT NULL" for p, t in names.items()}
    return subjects, lambda sql: db.execute(sql).fetchall()


def test_a_proportion_counts_the_numerator_inside_the_denominator_less_exclusions():
    """The raw Numerator (7) outnumbers the Denominator (5); the score counts p1 and p2 of p1-p4."""
    subjects, execute = _sqlite(DIABETES)
    score = measure_score(scoring="proportion", declared=DIABETES, subjects=subjects, execute=execute)
    assert (score.kind, score.numerator, score.base, score.value) == ("rate", 2, 4, 0.5)


def test_removing_an_exclusion_changes_the_score_though_the_numerator_define_is_unchanged():
    """The experiment case: drop the exclusion criterion. The Numerator define's own result is the same;
    the score now counts p5 in both the numerator and the base."""
    tables = {**DIABETES, "Denominator Exclusions": []}
    subjects, execute = _sqlite(tables)
    score = measure_score(scoring="proportion", declared=tables, subjects=subjects, execute=execute)
    assert (score.numerator, score.base) == (3, 5)


def test_an_exception_is_removed_only_outside_the_numerator():
    tables = {**DIABETES, "Denominator Exceptions": ["p1", "p3"]}
    subjects, execute = _sqlite(tables)
    score = measure_score(scoring="proportion", declared=tables, subjects=subjects, execute=execute)
    # p3 is an exception and leaves the base; p1 met the Numerator, so its exception does not remove it.
    assert (score.numerator, score.base) == (2, 3)


def test_a_proportion_with_no_denominator_takes_the_initial_population():
    tables = {"Initial Population": ["p1", "p2"], "Numerator": ["p1", "p9"]}
    subjects, execute = _sqlite(tables)
    score = measure_score(scoring="proportion", declared=tables, subjects=subjects, execute=execute)
    assert (score.numerator, score.base) == (1, 2)


def test_an_empty_base_states_no_rate():
    tables = {"Initial Population": [], "Numerator": ["p1"]}
    subjects, execute = _sqlite(tables)
    score = measure_score(scoring="proportion", declared=tables, subjects=subjects, execute=execute)
    assert (score.base, score.value) == (0, None)


def test_a_ratio_removes_each_side_s_exclusions():
    tables = {"Initial Population": ["p1", "p2", "p3", "p4"], "Denominator": ["p1", "p2", "p3"],
              "Denominator Exclusions": ["p3"], "Numerator": ["p1", "p4", "p9"], "Numerator Exclusions": ["p4"]}
    subjects, execute = _sqlite(tables)
    score = measure_score(scoring="ratio", declared=tables, subjects=subjects, execute=execute)
    assert (score.kind, score.numerator, score.base) == ("ratio", 1, 2)


def test_a_cohort_reports_its_initial_population():
    tables = {"Initial Population": ["p1", "p1", "p2"]}
    subjects, execute = _sqlite(tables)
    score = measure_score(scoring="cohort", declared=tables, subjects=subjects, execute=execute)
    assert (score.kind, score.numerator) == ("count", 2)


def test_continuous_variable_is_stated_unsupported_not_approximated():
    score = measure_score(scoring="continuous-variable", declared=["Initial Population"], subjects={},
                          execute=lambda sql: pytest.fail("nothing to count"))
    assert score.kind == "unsupported" and score.value is None


def test_a_declared_population_with_no_result_raises():
    subjects, execute = _sqlite(DIABETES)
    del subjects["Denominator Exclusions"]
    with pytest.raises(MetricsUnavailable, match="Denominator Exclusions"):
        measure_score(scoring="proportion", declared=DIABETES, subjects=subjects, execute=execute)


def test_an_undeclared_scoring_is_refused():
    with pytest.raises(ValueError, match="declared scoring"):
        measure_score(scoring=None, declared=DIABETES, subjects={}, execute=lambda sql: [])


def test_scoring_is_read_from_the_measure_resource():
    coded = {"scoring": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/measure-scoring",
                                     "code": "proportion"}]}}
    assert parse_scoring(coded) == "proportion"
    assert parse_scoring({"scoring": {"text": "Proportion measure"}}) is None
    assert parse_scoring(None) is None


def test_the_met_predicate_follows_the_value_type():
    assert "CASE WHEN `__val` THEN" in subjects_sql("t", {"__subject_id": "string", "__val": "boolean"})
    assert "size(`__val`) > 0" in subjects_sql("t", {"__subject_id": "string", "__val": "array<string>"})
