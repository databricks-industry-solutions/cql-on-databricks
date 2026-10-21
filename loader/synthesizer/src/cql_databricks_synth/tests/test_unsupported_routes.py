"""Queries in truth position, choice-type dispatch, and reporting the routes the solver
cannot invert (SYNTH-PDS-SKIPPED).

The reporting half matters more than it looks. A route the solver cannot invert is
skipped whenever a sibling route rescues the solve, which keeps generation correct but
leaves a whole branch of the measure permanently dark — and the cohort cannot show that,
because "this define is always empty" looks identical whether the logic or the solver is
the reason. These tests pin the difference being stated out loud.
"""

from __future__ import annotations

import pytest

from cql_databricks_synth import UnsupportedElm
from cql_databricks_synth.emit import coverage_markdown
from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.generate import CohortMeta, StratumOutcome, generate
from cql_databricks_synth.library import Env
from cql_databricks_synth.solve import Solver
from cql_databricks_synth.valuesets import ValueSetIndex

_FIXTURES = dir_or_skip("pilot")

_EXCLUSION_PROCEDURES = "Pilot Exclusion Procedures"


@pytest.fixture(scope="module")
def env() -> Env:
    return Env.from_paths([_FIXTURES])


@pytest.fixture(scope="module")
def valuesets() -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(_FIXTURES)


def _solver(env, valuesets, key="p0") -> Solver:
    return Solver(env, valuesets, seed=1, patient_key=key, priors=True)


# -- a query in truth position ---------------------------------------------------------

def test_query_in_truth_position_is_solvable(env, valuesets):
    """`define X: ([Procedure: A] union [Procedure: B]) P where …` is targeted directly
    rather than through `exists`, so it arrives at `_satisfy` as a bare Query."""
    solver = _solver(env, valuesets)
    solver.solve({_EXCLUSION_PROCEDURES: True})
    assert [r.resource_type for r in solver.state.resources] == ["Procedure"]


def test_query_route_populates_procedures_end_to_end(env, valuesets):
    """The symptom that started this: Procedure resources were never emitted at all."""
    cohort = generate(env, valuesets, seed=11, counts={"IPP+DEN+DENEX": 200}, priors=True)
    types = {r["resourceType"] for p in cohort.patients for r in p.resources}
    assert "Procedure" in types


def test_query_false_forbids_every_branch(env, valuesets):
    """Both arms of the union must be forbidden, not just the one the chooser picked."""
    solver = _solver(env, valuesets)
    solver.solve({_EXCLUSION_PROCEDURES: False})
    assert not [r for r in solver.state.resources if r.resource_type == "Procedure"]
    assert any(key[0] == "Procedure" for key in solver.state.forbidden)


# -- choice-type dispatch --------------------------------------------------------------

def _case(branches: list[dict], *, comparand: dict | None = None) -> dict:
    node = {"type": "Case", "caseItem": [{"when": {"type": "Null"}, "then": b}
                                         for b in branches],
            "else": {"type": "Null"}}
    if comparand is not None:
        node["comparand"] = comparand
    return node


def test_a_choice_element_resolves_to_one_slot(env, valuesets):
    """`effective[x]` read through a cast denotes exactly one decision variable: the `where`
    constrains `effective`, not some other element."""
    solver = _solver(env, valuesets)
    solver.solve({"Numerator": True})
    (sketch,) = [r for r in solver.state.resources if r.resource_type == "Observation"]
    assert "effective" in sketch.fields


def test_case_with_a_comparand_stays_unsupported(env, valuesets):
    """A real conditional, not a choice dispatch — collapsing it would silently pick."""
    solver = _solver(env, valuesets)
    node = _case([{"type": "Null"}], comparand={"type": "Literal", "value": "1"})
    with pytest.raises(UnsupportedElm, match="comparand"):
        solver._choice_dispatch_slot(node, {})


def test_case_with_no_resolvable_branch_stays_unsupported(env, valuesets):
    solver = _solver(env, valuesets)
    with pytest.raises(UnsupportedElm, match="no resolvable branch"):
        solver._choice_dispatch_slot(_case([{"type": "Null"}]), {})


# -- reporting the skips ---------------------------------------------------------------

def test_skipped_routes_are_recorded_on_a_successful_solve(env, valuesets):
    """The solve SUCCEEDS — that is the whole point. A skip that leaves no trace is the
    defect this records."""
    cohort = generate(env, valuesets, seed=11, counts={"IPP+DEN+DENEX": 60}, priors=True)
    denex = next(o for o in cohort.outcomes if o.stratum == "IPP+DEN+DENEX")
    assert denex.status == "satisfied"
    assert denex.skipped_routes, "an unsupported route was skipped but not reported"
    assert all(count > 0 for count in denex.skipped_routes.values())


def test_skip_messages_are_stable_across_route_order(env, valuesets):
    """One gap must report as one row. The aggregate message lists sub-failures in
    chooser order, so without sorting the same gap appears once per permutation."""
    reasons: set[str] = set()
    for seed in (3, 5, 11):
        cohort = generate(env, valuesets, seed=seed, counts={"IPP+DEN+DENEX": 40},
                          priors=True)
        for outcome in cohort.outcomes:
            reasons |= {r for r in outcome.skipped_routes if "SynthesizerPilot:" in r}
    assert reasons, "the pilot's uninvertible exclusion route was never reported"
    assert len(reasons) <= 1, f"one gap reported {len(reasons)} ways: {reasons}"


def _meta() -> CohortMeta:
    return CohortMeta(cohort_id="c", measure_id="M", measure_version="1", seed=1,
                      config_hash="abcdef123456", generator_version="0.1.0")


def test_coverage_report_lists_skipped_routes():
    outcome = StratumOutcome(stratum="S", status="satisfied", patient_count=5,
                             skipped_routes={"with-relationship at Foo:1": 5})
    report = coverage_markdown(_meta(), [outcome], 5)
    assert "Routes skipped as unsupported" in report
    assert "with-relationship at Foo:1" in report


def test_coverage_report_omits_the_section_when_nothing_was_skipped():
    outcome = StratumOutcome(stratum="S", status="satisfied", patient_count=5)
    assert "Routes skipped" not in coverage_markdown(_meta(), [outcome], 5)


def test_coverage_report_escapes_pipes_in_reasons():
    """Reasons carry `code:system|code` retrieve keys, which would break the table."""
    outcome = StratumOutcome(stratum="S", status="satisfied", patient_count=1,
                             skipped_routes={"forbidden code:http://x|123": 1})
    row = [line for line in coverage_markdown(_meta(), [outcome], 1).splitlines()
           if "forbidden" in line][0]
    assert r"\|" in row                                  # the literal pipe is escaped
    assert row.replace(r"\|", "").count("|") == 4        # 3 cells, no stray column
