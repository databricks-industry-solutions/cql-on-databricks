"""SPEC-METRIC, SPEC-ATTRITION — the real, warehouse-backed measure metric set.

Fixtures use mock synthetic measure data (ExampleMeasure plus its include closure): the state layer's
real naming — a root measure's defines recorded bare, a dependency's recorded library-prefixed — and
a library whose defines are compiled but whose MVs do not exist.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from cql_databricks.result.insights.metrics import Cohort, MetricsUnavailable, cohort_count_sql, measure_metrics
from cql_databricks.result.insights.evaluation import StateClosure
from cql_databricks.result.address import evaluation_version


def _warehouse(warehouse_id, sql):
    """The warehouse these tests stand in for: the derivations read through an injected ``execute``
    (DERIVATIONS-TO-LIBRARY), and each test patches this hook where it patched the service's executor."""
    raise AssertionError("a test must patch _warehouse")


def _execute(warehouse_id="wh-1"):
    return lambda sql: _warehouse(warehouse_id, sql)


CATALOG = "acme_results"
DATA_SOURCE = "conformance"
PERIOD = ("2022-01-01", "2022-12-31")
SUFFIX = evaluation_version(DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1])

MEASURE_SCHEMA = f"{CATALOG}.examplemeasure_1_0_0"
DEPENDENCY_SCHEMA = f"{CATALOG}.exampledependency_1_0_0"


class _Row:
    def __init__(self, id_: str, name: str, version: str) -> None:
        self.id, self.library_name, self.library_version = id_, name, version


class _Define:
    def __init__(self, define_name: str, mv_name: str) -> None:
        self.define_name, self.mv_name = define_name, mv_name


class _State:
    """The two state-layer reads the builder makes, with the live naming reproduced."""

    def __init__(self) -> None:
        self._libraries = [
            _Row("lib-measure", "ExampleMeasure", f"1.0.0_{SUFFIX}"),
            _Row("lib-dep", "ExampleDependency", f"1.0.0_{SUFFIX}"),
            _Row("lib-unmaterialized", "ExampleUnmaterialized", f"1.0.0_{SUFFIX}"),
            # Another ExampleMeasure variant, live in the same evaluation and declaring the same define
            # names — the real `SPEC-ANCHOR` collision, not a contrived one.
            _Row("lib-variant", "ExampleMeasureVariant", f"1.0.0_{SUFFIX}"),
            # A DIFFERENT evaluation of the same measure — must not be picked up.
            _Row("lib-other", "ExampleMeasure", "1.0.0_deadbeef"),
        ]
        self._defines = {
            "lib-measure": [_Define("Initial_Population", "initial_population"),
                           _Define("Numerator", "numerator"),
                           _Define("Example_Results",
                                   "example_results")],
            # A dependency's defines are recorded LIBRARY-PREFIXED.
            "lib-dep": [_Define("ExampleDependency_Example_Encounters",
                                "exampledependency_example_encounters")],
            # Compiled, but its MVs were never materialized (observed live).
            "lib-unmaterialized": [_Define("ExampleUnmaterialized_Example_Unrun",
                                    "exampleunmaterialized_example_unrun")],
            "lib-variant": [_Define("Numerator", "numerator")],
            "lib-other": [_Define("Numerator", "numerator")],
        }

    def list_libraries(self, data_source_id: str):
        return list(self._libraries) if data_source_id == DATA_SOURCE else []

    def list_defines(self, library_id: str):
        return list(self._defines.get(library_id, []))


DEFINES: list[tuple[str, str, str | None]] = [
    ("ExampleMeasure", "Initial Population", "Boolean"),
    ("ExampleMeasure", "Numerator", "Boolean"),
    ("ExampleMeasure", "Example Results", "List<String>"),
    ("ExampleDependency", "Example Encounters", "List<String>"),
    ("ExampleUnmaterialized", "Example Unrun", "Boolean"),
    ("ExampleMeasure", "Never Reached", "Boolean"),
]

#: Only the relations that EXIST. The unmaterialized MV is absent, mirroring the live schema.
COLUMNS = {
    (MEASURE_SCHEMA, "initial_population"): {"__subject_id": "STRING",
                                            "Initial_Population": "BOOLEAN"},
    (MEASURE_SCHEMA, "numerator"): {"__subject_id": "STRING", "Numerator": "BOOLEAN"},
    (MEASURE_SCHEMA, "example_results"): {
        "__subject_id": "STRING", "__ordinal": "STRING", "__val": "STRUCT"},
    (DEPENDENCY_SCHEMA, "exampledependency_example_encounters"): {
        "__subject_id": "STRING", "__ordinal": "STRING", "__val": "STRUCT"},
}

COUNTS = {
    (MEASURE_SCHEMA, "initial_population"): (0, None),
    (MEASURE_SCHEMA, "numerator"): (2, None),
    (MEASURE_SCHEMA, "example_results"): (33, None),
    (DEPENDENCY_SCHEMA, "exampledependency_example_encounters"): (17, 41),
}


def _everyone(defines):
    """SPEC-POPULATION-CASCADE — every define counted among every evaluated subject."""
    return {(lib, name): Cohort() for lib, name, _ in defines}


def _build(_subject_rows=((("2713"),),), **overrides):
    kwargs = dict(
        library="ExampleMeasure", version="1.0.0",
        data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SUFFIX, draft="",
        defines=DEFINES, closure=StateClosure(_State()),
        patient_relation="acme.data.patient",
        results_catalog=CATALOG, execute=_execute(),
    )
    kwargs.update(overrides)
    kwargs.setdefault("cohorts", _everyone(kwargs["defines"]))
    with patch("cql_databricks.result.insights.metrics.relation_columns", return_value=COLUMNS), \
         patch("cql_databricks.result.insights.metrics.count_relations", return_value=COUNTS), \
         patch(f"{__name__}._warehouse",
               return_value=[list(r) for r in _subject_rows]):
        return measure_metrics(**kwargs)


def _captured_sql() -> str:
    """The SQL the cohort read actually issued — the only warehouse call this module makes directly."""
    with patch("cql_databricks.result.insights.metrics.relation_columns", return_value=COLUMNS), \
         patch("cql_databricks.result.insights.metrics.count_relations", return_value=COUNTS), \
         patch(f"{__name__}._warehouse",
               return_value=[["2713"]]) as execute:
        measure_metrics(
            library="ExampleMeasure", version="1.0.0",
            data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SUFFIX, draft="",
            defines=DEFINES, cohorts=_everyone(DEFINES), closure=StateClosure(_State()),
            patient_relation="acme.data.patient",
            results_catalog=CATALOG, execute=_execute())
    execute.assert_called_once()
    return execute.call_args.args[1]


def test_provenance_is_run():
    """The point of the whole exercise: these figures came from a run, and say so."""
    assert _build().provenance == "run"


def test_evaluated_total_comes_from_the_patient_binding():
    """D3 / SPEC-METRIC — the base a COMMON define's share is taken against. NOT a result table: that
    relation's subject set is whatever its joins left, so it is a lower bound on the cohort."""
    assert _build().subjects == 2713


def test_the_patient_binding_is_called_with_no_argument():
    """BIND-PUBLISHED-VIEW, EMIT-ROOT-INDEPENDENT — the data source is the population, so the evaluated total is
    the bound Patient retrieve called with every argument at its default; no argument names a measure."""
    sql = _captured_sql()
    assert "FROM acme.data.patient" in sql
    assert "library" not in sql
    # Fan-out immune like every other count in this module (`relation`): a mount is not one row per
    # subject, and the cohort is a subject count.
    assert "COUNT(DISTINCT __subject_id)" in sql


def test_a_patient_binding_with_no_subjects_raises_rather_than_stating_a_zero_base():
    """SPEC-METRIC / CORE-NO-SILENT-MISLOWER — a base of 0 is not a base, and a document that renders
    "0 → 67" beside a chart headed 67 is the two-surfaces-disagreeing failure with an extra step. An
    evaluation whose cohort is empty while its populations are not is incoherent, and saying so is the
    only honest answer."""
    with pytest.raises(MetricsUnavailable, match="holds no subjects"):
        _build(_subject_rows=[["0"]])


def test_a_root_measure_define_resolves_by_its_bare_name():
    m = _build()
    numerator = next(d for d in m.defines if d.define == "Numerator")
    assert numerator.subjects == 2
    assert numerator.library == "ExampleMeasure"


def test_a_dependency_define_resolves_by_its_library_prefixed_name():
    """The live naming this exists for: `pipeline_defines` records a dependency's define as
    `ExampleDependency_Example_Encounters`, while the client asks about `Example Encounters`
    in library `ExampleDependency`."""
    m = _build()
    check = next(d for d in m.defines if d.library == "ExampleDependency")
    assert check.define == "Example Encounters"
    assert check.subjects == 17
    assert check.elements == 41


def test_a_define_with_no_relation_is_ABSENT_not_zero():
    """Two live causes, one correct behaviour: a define the measure never reaches is not compiled into
    the pipeline, and a library whose MVs have not materialized has the define compiled but no table.
    Emitting 0 would state "no subject met this" where the truth is "this was never evaluated here"."""
    names = {d.define for d in _build().defines}
    assert "Never Reached" not in names
    assert "Example Unrun" not in names


def test_zero_is_reported_when_the_relation_EXISTS():
    """The other half of the same rule — a real zero is a real answer and must not be hidden."""
    initial = next(d for d in _build().defines if d.define == "Initial Population")
    assert initial.subjects == 0


def test_the_caveat_counts_the_absences_without_claiming_a_cause():
    caveat = _build().caveat
    assert caveat is not None
    assert "2 of 6" in caveat
    assert "absent rather than zero" in caveat


def test_no_caveat_when_every_define_resolved():
    m = _build(defines=DEFINES[:4])
    assert m.caveat is None


def test_one_define_name_in_two_libraries_resolves_to_two_relations():
    """SPEC-ANCHOR — one name in two libraries is two defines, and each has its OWN results schema. Both
    ExampleMeasure variants are live in this evaluation and both declare `Numerator`; resolving them to one
    relation would print one measure's figure under the other's heading."""
    variant_schema = f"{CATALOG}.examplemeasurevariant_1_0_0"
    columns = {**COLUMNS,
               (variant_schema, "numerator"): {"__subject_id": "STRING", "Numerator": "BOOLEAN"}}
    counts = {**COUNTS, (variant_schema, "numerator"): (91, None)}
    defines = [("ExampleMeasure", "Numerator", "Boolean"),
               ("ExampleMeasureVariant", "Numerator", "Boolean")]
    with patch("cql_databricks.result.insights.metrics.relation_columns", return_value=columns), \
         patch("cql_databricks.result.insights.metrics.count_relations", return_value=counts), \
         patch(f"{__name__}._warehouse", return_value=[["2713"]]):
        m = measure_metrics(
            library="ExampleMeasure", version="1.0.0",
            data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SUFFIX, draft="",
            defines=defines, cohorts=_everyone(defines), closure=StateClosure(_State()),
            patient_relation="acme.data.patient",
            results_catalog=CATALOG, execute=_execute())
    by_library = {d.library: d.subjects for d in m.defines}
    assert by_library == {"ExampleMeasure": 2,
                          "ExampleMeasureVariant": 91}


def test_another_evaluation_of_the_same_library_is_not_read():
    """Every library compiled for one evaluation shares the version suffix; a row carrying a different
    one belongs to a different data source or period and its counts would be a different measure's."""
    m = _build()
    assert len([d for d in m.defines if d.define == "Numerator"]) == 1


def test_no_evaluation_raises_rather_than_returning_an_empty_set():
    """CORE-NO-SILENT-MISLOWER — "not run here" and "ran and found nothing" are different states, and a
    document must not render the second when the first is true."""
    with pytest.raises(MetricsUnavailable, match="has not been run"):
        _build(data_source_id="never-run")


def test_no_patient_binding_raises_rather_than_reporting_zero_subjects():
    """SPEC-METRIC requires a share's base be STATED, and 0 evaluated subjects is not a base."""
    with pytest.raises(MetricsUnavailable, match="no Patient view"):
        _build(patient_relation=None)


def test_counts_are_one_evaluation_sets():
    """RUN-RESULTS-BY-EVAL — the counts are asked for this evaluation set's rows only."""
    with patch("cql_databricks.result.insights.metrics.relation_columns", return_value=COLUMNS), \
         patch("cql_databricks.result.insights.metrics.count_relations", return_value=COUNTS) as count, \
         patch(f"{__name__}._warehouse", return_value=[["2713"]]):
        measure_metrics(
            library="ExampleMeasure", version="1.0.0",
            data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SUFFIX, draft="",
            defines=DEFINES, cohorts=_everyone(DEFINES), closure=StateClosure(_State()),
            patient_relation="acme.data.patient",
            results_catalog=CATALOG, execute=_execute())
    assert count.call_args.kwargs["eval_id"] == SUFFIX


# SPEC-POPULATION-CASCADE — every define is counted inside the cohort the client names.

def _subjects(table: str) -> str:
    """The met-subject SQL `measure_metrics` builds per define, over a plain sqlite table."""
    return f"SELECT DISTINCT __subject_id FROM {table} WHERE __subject_id IS NOT NULL"


def _run_on_sqlite(tables: dict[str, list[str]], sql: str) -> dict[tuple[int, int], int]:
    import sqlite3
    db = sqlite3.connect(":memory:")
    for table, subjects in tables.items():
        db.execute(f"CREATE TABLE {table} (__subject_id TEXT)")
        db.executemany(f"INSERT INTO {table} VALUES (?)", [(s,) for s in subjects])
    return {(k, d): n for k, d, n in db.execute(sql).fetchall()}


def test_the_numerator_is_counted_inside_the_denominator_less_exclusions():
    """The defect this exists for: a Numerator written as "no HbA1c in the period" is true for every
    patient without diabetes too, so counted across everyone it outnumbered its Denominator. Synthetic:
    p1-p5 have diabetes (Initial Population and Denominator), p5 is in hospice (Exclusions), and the
    Numerator's own define is true for p1, p2, p5 and the non-diabetic p6-p9."""
    tables = {"ipp": ["p1", "p2", "p3", "p4", "p5"], "den": ["p1", "p2", "p3", "p4", "p5"],
              "denex": ["p5", "p9"], "num": ["p1", "p2", "p5", "p6", "p7", "p8", "p9"]}
    ipp, den, denex, num = 0, 1, 2, 3
    sql = cohort_count_sql(
        [_subjects(t) for t in tables],
        [([ipp, den], [denex]), ([ipp, den], [])],
        [(0, num), (1, denex)])
    got = _run_on_sqlite(tables, sql)
    assert got[(0, num)] == 2        # p1, p2 — not p5 (excluded), not p6-p9 (no diabetes)
    assert got[(0, -1)] == 4         # the cohort: Denominator less Exclusions, p1-p4
    assert got[(1, denex)] == 1      # p5 — p9 is in hospice but was never in the Denominator
    assert got[(1, -1)] == 5


def test_a_cohort_counts_subjects_not_rows():
    """`relation.py` — a result relation is not one row per subject; the cascade counts people."""
    tables = {"ipp": ["p1", "p1", "p2"], "num": ["p1", "p1", "p1", "p3"]}
    got = _run_on_sqlite(tables, cohort_count_sql([_subjects(t) for t in tables], [([0], [])], [(0, 1)]))
    assert got == {(0, -1): 2, (0, 1): 1}


def _scored_build(cohorts, rows):
    """`_build` with the cohort statement answered by ``rows`` and the Patient binding by 2,713."""
    def warehouse(_wh, sql):
        return [["2713"]] if "data.patient" in sql else rows
    defines = DEFINES[:2]
    with patch("cql_databricks.result.insights.metrics.relation_columns", return_value=COLUMNS), \
         patch("cql_databricks.result.insights.metrics.count_relations", return_value=COUNTS), \
         patch(f"{__name__}._warehouse", side_effect=warehouse) as execute:
        m = measure_metrics(
            library="ExampleMeasure", version="1.0.0",
            data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SUFFIX, draft="",
            defines=defines, cohorts=cohorts, closure=StateClosure(_State()),
            patient_relation="acme.data.patient",
            results_catalog=CATALOG, execute=_execute())
    return m, [c.args[1] for c in execute.call_args_list]


def test_a_scored_define_reports_met_counted_and_its_cohort():
    ipp = ("ExampleMeasure", "Initial Population")
    cohorts = {ipp: Cohort(), ("ExampleMeasure", "Numerator"): Cohort(all_of=(ipp,))}
    # Cohort 0 is the Initial Population, of 0 subjects here; the Numerator is s_0.
    m, statements = _scored_build(cohorts, [[0, -1, 0], [0, 0, 0]])
    numerator = next(d for d in m.defines if d.define == "Numerator")
    assert (numerator.subjects, numerator.counted, numerator.cohort_subjects) == (2, 0, 0)
    assert numerator.to_dict()["cohortSubjects"] == 0
    initial = next(d for d in m.defines if d.define == "Initial Population")
    assert (initial.counted, initial.cohort_subjects) == (initial.subjects, 2713)
    cascade = next(s for s in statements if "k_0" in s)
    # One evaluation set's rows, through the typed membership predicate.
    assert cascade.count(SUFFIX) == 2
    assert 'CASE WHEN `Numerator` THEN __subject_id END' in cascade


def test_everyone_needs_no_cascade_statement():
    m, statements = _scored_build(_everyone(DEFINES[:2]), [])
    assert len(statements) == 1 and "data.patient" in statements[0]
    assert all(d.counted == d.subjects and d.cohort_subjects == 2713 for d in m.defines)


def test_a_cohort_population_with_no_relation_raises():
    """A Numerator counted without the Exclusions it must leave out is a wrong number, not a missing one."""
    unrun = ("ExampleUnmaterialized", "Example Unrun")
    cohorts = {**_everyone(DEFINES[:2]),
               ("ExampleMeasure", "Numerator"): Cohort(all_of=(("ExampleMeasure", "Initial Population"),),
                                                       none_of=(unrun,))}
    with pytest.raises(MetricsUnavailable, match="Example Unrun"):
        _scored_build(cohorts, [])


def test_every_define_needs_a_cohort():
    with pytest.raises(ValueError, match="needs a cohort"):
        _scored_build({}, [])


def test_a_cohort_that_only_removes_is_refused():
    with pytest.raises(ValueError, match="cohort it removes them from"):
        Cohort(none_of=(("ExampleMeasure", "Numerator"),))
