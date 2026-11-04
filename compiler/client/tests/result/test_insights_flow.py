"""APP-COHORT-ALGEBRA, APP-DECISIVE-COUNT — where the cohort went, and why it left.

THE FAILURES THESE EXIST FOR, in the order they would hurt most:

1. **A COUNT THAT REPORTS ROWS UNDER A LABEL SAYING SUBJECTS.** A result relation is not one row per
   subject — a measured table may carry millions of rows for thousands of distinct subjects. Every operand
   here has to be collapsed to distinct met subjects BEFORE any join, and `COUNT(*)` is banned.

2. **A DECISIVE COUNT THAT DOES NOT PARTITION.** The whole reason the rail leads with the decisive
   figure rather than the any-cause one is that decisive counts plus a failed-two-or-more remainder
   partition the entering cohort exactly, so a reader who sums them gets the truth. If that ever stops
   holding, the figure loses the only property that made it the one to lead with.

3. **A CRITERION NOBODY CAN READ COUNTING AS "NOT FAILED".** An unlocatable relation silently inflates
   every sibling's decisive count and still produces a partition that adds up. The gate is omitted
   whole rather than partially decomposed.

4. **SUBJECT IDS REACHING THE BROWSER.** The PHI boundary of section 10.2, asserted by walking the
   payload — the one property no reviewer can check by reading.

Fixtures use mock synthetic measure data (ExampleMeasure), described as one complete selection: a dependency's
define, and a library compiled but with no relation materialized.
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from cql_databricks.result.insights.evaluation import MetricsUnavailable
from cql_databricks.result.insights.flow import GateRequest, cohort_flow
from closure_fixture import CATALOG, DATA_SOURCE, SELECTION_ID, FixedClosure, library, pinned


def _warehouse(warehouse_id, sql):
    """The warehouse these tests stand in for: the derivations read through an injected ``execute``
    (DERIVATIONS-TO-LIBRARY), and each test patches this hook where it patched the service's executor."""
    raise AssertionError("a test must patch _warehouse")


#: BIND-CONTRACT: the environment's Patient view on the contract, as `DESCRIBE` reports it.
PATIENT_VIEW_COLUMNS = [("resource", "struct<id:string,gender:string>", None), ("patient_id", "string", None)]


def _execute(warehouse_id="wh-1"):
    return lambda sql: (PATIENT_VIEW_COLUMNS if sql.startswith("DESCRIBE ") else _warehouse(warehouse_id, sql))


PERIOD = ("2022-01-01", "2022-12-31")

MEASURE_SCHEMA = "examplemeasure_1_0_0"
DEPENDENCY_SCHEMA = "exampledependency_1_0_0"

PATIENT_VIEW = "acme.data.patient"
MEASURE = "ExampleMeasure"
DEPENDENCY = "ExampleDependency"
UNMATERIALIZED = "ExampleUnmaterialized"

#: The relations the selection holds. A boolean define carries its own value column; a list-valued one carries
#: `__ordinal` + `__val`. The unmaterialized library holds none.
INITIAL = pinned(MEASURE_SCHEMA, "initial_population", {"__subject_id": "STRING", "Initial_Population": "BOOLEAN"})
DENOMINATOR = pinned(MEASURE_SCHEMA, "denominator", {"__subject_id": "STRING", "Denominator": "BOOLEAN"})
NUMERATOR = pinned(MEASURE_SCHEMA, "numerator", {"__subject_id": "STRING", "Numerator": "BOOLEAN"})
CRITERION_A = pinned(MEASURE_SCHEMA, "example_criterion_a",
                     {"__subject_id": "STRING", "Example_Criterion_A": "BOOLEAN"})
CRITERION_B = pinned(MEASURE_SCHEMA, "example_criterion_b",
                     {"__subject_id": "STRING", "Example_Criterion_B": "BOOLEAN"})
DENOMINATOR_EXCLUSIONS = pinned(MEASURE_SCHEMA, "denominator_exclusions",
                                {"__subject_id": "STRING", "Denominator_Exclusions": "BOOLEAN"})
EXCLUSION_A = pinned(MEASURE_SCHEMA, "example_exclusion_a", {"__subject_id": "STRING", "Example_Exclusion_A": "BOOLEAN"})
EXCLUSION_B = pinned(MEASURE_SCHEMA, "example_exclusion_b", {"__subject_id": "STRING", "Example_Exclusion_B": "BOOLEAN"})
ENCOUNTERS = pinned(DEPENDENCY_SCHEMA, "exampledependency_example_encounters",
                    {"__subject_id": "STRING", "__ordinal": "STRING", "__val": "STRUCT"})

CLOSURE = FixedClosure({
    MEASURE: library(MEASURE, "1.0.0", {
        "Initial Population": INITIAL, "Denominator": DENOMINATOR, "Numerator": NUMERATOR,
        "Example Criterion A": CRITERION_A, "Example Criterion B": CRITERION_B,
        "Denominator Exclusions": DENOMINATOR_EXCLUSIONS,
        "Example Exclusion A": EXCLUSION_A, "Example Exclusion B": EXCLUSION_B,
    }),
    DEPENDENCY: library(DEPENDENCY, "1.0.0", {"Example Encounters": ENCOUNTERS}),
    # Compiled, but its relations were never materialized (observed live): no entry for its define.
    UNMATERIALIZED: library(UNMATERIALIZED, "1.0.0", {}),
})

#: The Initial Population gate: three sibling criteria, entered from nothing (the stage bounds itself).
ENTRY_GATE = GateRequest(
    stage="Initial Population", stage_library=MEASURE, base=None, base_library=None, polarity="all-of",
    criteria=[(MEASURE, "Example Criterion A"), (MEASURE, "Example Criterion B"), (DEPENDENCY, "Example Encounters")],
)

#: The exclusion gate: a DISJUNCTION, where decisive reads as "excluded by exactly one reason".
EXCLUSION_GATE = GateRequest(
    stage="Denominator Exclusions", stage_library=MEASURE,
    base="Initial Population", base_library=MEASURE, polarity="any-of",
    criteria=[(MEASURE, "Example Exclusion A"), (MEASURE, "Example Exclusion B")],
)


def _rows_for(gate_index: int, *, entered: int, reached: int, passed: int, multiple: int,
              criteria: list[tuple[int, int]]) -> list[list]:
    """The crosstab rows the warehouse would return for one gate: (gate, cell, n, decisive)."""
    out: list[list] = [
        [str(gate_index), "__entered", entered, None],
        [str(gate_index), "__reached", reached, None],
        [str(gate_index), "__passed", passed, None],
        [str(gate_index), "__multiple", multiple, None],
    ]
    for ci, (any_cause, decisive) in enumerate(criteria):
        out.append([str(gate_index), f"c{ci}", any_cause, decisive])
    return out


#: A coherent entry gate. 149,882 entered, 42,318 passed, 7,440 failed two or more, and
#: the three decisive counts complete the partition: 42,318 + 7,440 + 52,090 + 38,204 + 9,830 = 149,882.
ENTRY_ROWS = _rows_for(
    0, entered=149_882, reached=42_318, passed=42_318, multiple=7_440,
    criteria=[(21_096, 9_830), (58_332, 38_204), (71_415, 52_090)],
)


def _build(rows=None, gates=None, **overrides):
    kwargs = dict(
        library=MEASURE, version="1.0.0",
        data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SELECTION_ID, draft="",
        gates=gates if gates is not None else [ENTRY_GATE],
        closure=CLOSURE, patient_relation=PATIENT_VIEW,
        results_catalog=CATALOG, execute=_execute(),
    )
    kwargs.update(overrides)
    with patch(f"{__name__}._warehouse",
               return_value=rows if rows is not None else ENTRY_ROWS):
        return cohort_flow(**kwargs)


def _captured_sql(gates=None) -> str:
    with patch(f"{__name__}._warehouse",
               return_value=ENTRY_ROWS) as execute:
        cohort_flow(
            library=MEASURE, version="1.0.0",
            data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SELECTION_ID, draft="",
            gates=gates if gates is not None else [ENTRY_GATE],
            closure=CLOSURE, patient_relation=PATIENT_VIEW,
            results_catalog=CATALOG, execute=_execute())
    execute.assert_called_once()
    return execute.call_args.args[1]


# ── the fan-out, which is the reason none of this may count rows ─────────────────────────────────

def test_every_relation_is_collapsed_to_distinct_subjects_before_any_join():
    # The measured denominator-exclusions relation carries 57.5 BILLION rows for 2,713 subjects. Once
    # each operand is DISTINCT-ed the joins are 1:1 over thousands of rows; without that they are not
    # joins, they are a cross product of two fan-outs.
    sql = _captured_sql()
    ctes = re.findall(r"s_\d+ AS \((.*?)\)(?:,|\n)", sql, re.S)
    assert ctes, "expected one subject CTE per relation"
    for body in ctes:
        assert "SELECT DISTINCT" in body
    # And every join is on the subject key.
    for join in re.findall(r"LEFT JOIN \w+ \w+ ON (.*?)(?=LEFT JOIN|\))", sql):
        assert "__subject_id" in join


def test_the_crosstab_never_counts_rows():
    assert "COUNT(*)" not in _captured_sql()


def test_membership_uses_the_shared_typed_predicate():
    # `relation.met_expr`, REUSED and not restated: a boolean define contributes its OWN VALUE, because
    # CQL's three-valued logic makes false and null both "not in"; any other type contributes presence.
    # A second copy here would let this reader call a subject met that `results/query.py` did not.
    sql = _captured_sql()
    assert "CASE WHEN `Initial_Population` THEN __subject_id END" in sql
    # The list-valued encounters relation has a STRUCT value column, so presence is the predicate.
    assert "CASE WHEN `__val` IS NOT NULL THEN __subject_id END" in sql


def test_one_statement_for_every_gate_however_many_there_are():
    # A count per gate would be several points in time as well as several round trips, and figures a
    # minute apart can contradict each other (the argument `count_relations` already makes).
    with patch(f"{__name__}._warehouse",
               return_value=ENTRY_ROWS) as execute:
        cohort_flow(
            library=MEASURE, version="1.0.0", data_source_id=DATA_SOURCE,
            period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SELECTION_ID, draft="",
            gates=[ENTRY_GATE, EXCLUSION_GATE], closure=CLOSURE, patient_relation=PATIENT_VIEW,
            results_catalog=CATALOG, execute=_execute())
    execute.assert_called_once()


def test_a_relation_used_by_two_gates_is_readned_once():
    # `Initial Population` is the entry gate's stage AND the exclusion gate's base. Two CTEs for it
    # would scan the same table twice inside one statement.
    sql = _captured_sql(gates=[ENTRY_GATE, EXCLUSION_GATE])
    assert sql.count("(SELECT * FROM `acme_results`.`examplemeasure_1_0_0`.`initial_population`)") == 1


# ── APP-DECISIVE-COUNT — the partition is the whole argument ──────────────────────────────────

def test_decisive_plus_multiple_plus_passed_partition_the_entering_cohort():
    # THE reason the rail leads with the decisive count rather than the any-cause one. If this stops
    # holding, the figure loses the only property that made it safe to add up.
    gate = _build().gates[0]
    total = gate.passed + gate.multiple + sum(c.decisive for c in gate.criteria)
    assert total == gate.entered


def test_any_cause_overlaps_and_is_never_part_of_the_partition():
    # These sum to 150,843 against a cohort of 149,882 — they overlap by construction, which is exactly
    # why they are shown BESIDE the decisive count and never instead of it.
    gate = _build().gates[0]
    assert sum(c.any_cause for c in gate.criteria) > gate.entered
    assert all(c.decisive <= c.any_cause for c in gate.criteria)


def test_criteria_come_back_named_though_they_travel_positionally():
    # Positional keys in the SQL (`c0`, `c1`) because define names are author-given text that collides
    # across libraries; the server holds the index -> (library, define) map.
    gate = _build().gates[0]
    assert [(c.library, c.define) for c in gate.criteria] == [
        (MEASURE, "Example Criterion A"), (MEASURE, "Example Criterion B"), (DEPENDENCY, "Example Encounters")]
    assert "'c0'" in _captured_sql()
    assert "Example Criterion A" not in _captured_sql()


def test_an_exclusion_gate_counts_excluded_by_exactly_one_reason():
    # POLARITY INVERTS THE DEFINITION and nothing else. An entry conjunction fails a criterion by NOT
    # meeting it; the exclusion disjunction by MEETING it. One operator, flipped — not a second path.
    entry_sql = _captured_sql(gates=[ENTRY_GATE])
    assert "IS NULL THEN 1 ELSE 0 END AS f0" in entry_sql

    excl_sql = _captured_sql(gates=[EXCLUSION_GATE])
    assert "IS NOT NULL THEN 1 ELSE 0 END AS f0" in excl_sql


def test_reached_and_passed_are_both_reported():
    # They are equal only where the population is EXACTLY the conjunction of its Concepts. Collapsing
    # them would hide a shortfall by attributing it to a criterion.
    rows = _rows_for(0, entered=100, reached=38, passed=42, multiple=5,
                     criteria=[(20, 10), (30, 25), (40, 18)])
    gate = _build(rows=rows).gates[0]
    assert gate.reached == 38
    assert gate.passed == 42


# ── absent, and the difference between a missing figure and a wrong one ──────────────────────────

def test_a_gate_with_an_unlocatable_criterion_is_omitted_not_partially_decomposed():
    # STRICTER THAN `metrics.py`'s "absent, not zero", and deliberately. There a missing relation is a
    # missing figure; here it is a WRONG one — a criterion nobody can read counts as "not failed" for
    # every subject, inflating every sibling's decisive count while still summing to a partition that
    # looks right.
    gate = GateRequest(
        stage="Denominator", stage_library=MEASURE, base="Initial Population", base_library=MEASURE,
        polarity="all-of",
        criteria=[(MEASURE, "Example Criterion A"),
                  (UNMATERIALIZED, "Example Unrun")],  # compiled, never materialized
    )
    flow = _build(rows=[], gates=[gate])
    assert flow.gates == []
    assert flow.omitted == ["Denominator"]
    assert "Denominator" in flow.caveat
    # The caveat STATES the omission and does not claim a cause: whether the define was unreached or
    # the library uncompiled is not something this read can tell apart.
    assert "not decomposed" in flow.caveat


def test_a_gate_with_no_criteria_is_omitted_rather_than_reported_as_nobody_failing():
    # A population written as one unfactored boolean has no siblings to attribute anything to. That is
    # a true report about an under-factored measure, and "0 departures for 0 reasons" is not.
    gate = GateRequest(stage="Numerator", stage_library=MEASURE, base="Denominator", base_library=MEASURE,
                       polarity="all-of", criteria=[])
    flow = _build(rows=[], gates=[gate])
    assert flow.gates == []
    assert flow.omitted == ["Numerator"]


def test_a_usable_gate_survives_beside_an_omitted_one():
    flow = _build(rows=ENTRY_ROWS, gates=[
        ENTRY_GATE,
        GateRequest(stage="Denominator", stage_library=MEASURE, base=None, base_library=None,
                    polarity="all-of", criteria=[(UNMATERIALIZED, "Example Unrun")]),
    ])
    assert [g.stage for g in flow.gates] == ["Initial Population"]
    assert flow.omitted == ["Denominator"]


def test_no_evaluation_raises_rather_than_returning_an_empty_flow():
    # Never run and ran-and-found-nothing are different states; the rail must not render the second
    # when the first is true.
    with pytest.raises(MetricsUnavailable):
        _build(data_source_id="not-a-source")


def test_a_clean_flow_carries_no_caveat():
    assert _build().caveat is None
    assert _build().omitted == []


# ── the PHI boundary (section 10.2, property 2) ──────────────────────────────────────────────────

def test_the_payload_carries_no_subject_ids():
    # SUBJECT-LEVEL DATA NEVER REACHES THE SPA. The warehouse returns a crosstab of counts and the
    # browser receives figures; a cohort export is a separate, deliberate action against the same
    # query. Asserted by walking the payload because it is the one property no reviewer can check by
    # reading — nothing in the shape of the code makes a leak obvious.
    payload = _build(gates=[ENTRY_GATE, EXCLUSION_GATE], rows=ENTRY_ROWS + _rows_for(
        1, entered=42_318, reached=3_904, passed=38_414, multiple=392,
        criteria=[(1_486, 1_204), (902, 861)])).to_dict()

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                assert "subject" not in k.lower(), f"{path}.{k} names a subject"
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            # Every leaf is a count, a label, or absent. Never an identifier.
            assert isinstance(node, (int, str, type(None))), f"{path} is {type(node)}"
            if isinstance(node, str):
                assert "__subject_id" not in node, f"{path} carries a subject key"

    walk(payload)
    # And the figures did survive the walk — a payload of nothing would pass every assertion above.
    assert payload["gates"][0]["entered"] == 149_882
    assert payload["gates"][1]["criteria"][0]["decisive"] == 1_204


def test_the_wire_omits_absent_fields_rather_than_carrying_nulls():
    gate = _build().to_dict()["gates"][0]
    assert "base" not in gate  # the entry gate enters from no declared population
    assert gate["stage"] == "Initial Population"
    assert gate["criteria"][2] == {
        "library": DEPENDENCY, "define": "Example Encounters", "decisive": 52_090, "anyCause": 71_415}


# ── the entering cohort, which is where a gate silently reports nobody failing ────────────────────

def test_a_base_less_gate_enters_from_the_EVALUATED_cohort_not_the_stage_itself():
    # THE BUG THIS EXISTS FOR, found by reading the generated SQL rather than by a fixture: with no
    # declared base the entering cohort fell back to the STAGE's own relation. The first gate on the
    # spine is the Initial Population and there is no population above it, so the gate was asking which
    # criterion the people who ALREADY PASSED had failed — answer: none of them, every time. The rail
    # would have rendered a confident set of zeroes, and every fixture with hand-written counts would
    # have kept agreeing with itself.
    sql = _captured_sql(gates=[ENTRY_GATE])
    assert f"FROM {PATIENT_VIEW})" in sql
    assert "s_all AS (SELECT DISTINCT __subject_id" in sql
    # The entering cohort of g_0 is the evaluated set; the stage is joined only to answer `reached`.
    g0 = re.search(r"g_0 AS \((.*?)\),\nk_0", sql, re.S).group(1)
    assert "FROM s_all b" in g0
    assert "LEFT JOIN s_0 st" in g0


def test_a_gate_WITH_a_base_enters_from_that_population_and_needs_no_binding():
    sql = _captured_sql(gates=[EXCLUSION_GATE])
    # Resolve the CTE names by what they SCAN rather than by their index — the numbering is an
    # implementation detail and asserting on it makes the test a copy of the code.
    cte_for = dict(re.findall(r"(s_\d+) AS \(SELECT DISTINCT.*?FROM \(SELECT \* FROM \S+\.`(\w+)`\)", sql))
    entering = next(n for n, t in cte_for.items() if t == "initial_population")
    stage = next(n for n, t in cte_for.items() if t == "denominator_exclusions")

    g0 = re.search(r"g_0 AS \((.*?)\),\nk_0", sql, re.S).group(1)
    assert f"FROM {entering} b" in g0          # the declared base is the entering cohort
    assert f"LEFT JOIN {stage} st" in g0       # the stage is joined only to answer `reached`
    assert PATIENT_VIEW not in sql               # nothing needed the evaluated cohort


def test_the_binding_call_names_no_measure():
    # EMIT-ROOT-INDEPENDENT — the data source is the population, so the
    # evaluated cohort is the bound Patient retrieve called with no argument.
    sql = _captured_sql(gates=[ENTRY_GATE])
    assert f"FROM {PATIENT_VIEW})" in sql
    assert "library" not in sql


def test_a_base_less_gate_is_omitted_when_the_source_has_no_patient_binding():
    # With no binding there is no cohort for those subjects to have departed FROM, so the figure cannot
    # be stated — the same reason an unlocatable criterion omits a gate.
    flow = _build(rows=[], gates=[ENTRY_GATE], patient_relation=None)
    assert flow.gates == []
    assert flow.omitted == ["Initial Population"]


def test_a_based_gate_still_works_without_a_patient_binding():
    rows = _rows_for(0, entered=42_318, reached=3_904, passed=38_414, multiple=392,
                     criteria=[(1_486, 1_204), (902, 861)])
    flow = _build(rows=rows, gates=[EXCLUSION_GATE], patient_relation=None)
    assert [g.stage for g in flow.gates] == ["Denominator Exclusions"]
