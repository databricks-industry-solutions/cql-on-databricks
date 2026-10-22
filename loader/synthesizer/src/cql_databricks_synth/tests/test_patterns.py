"""One CQL pattern per define, each solved true and false and re-evaluated (SYNTH-PDS-ECQM, SYNTH-PDS-VERIFY).

The fixture is ``__fixtures__/patterns/``: a library and a helper library written for these tests, pinned in
``pins.json`` as ``patterns`` (SYNTH-PDS-FIXTURE-PINS). Each define is the smallest CQL that needs one of the
solver's strategies, so a regression names the pattern rather than a measure that happens to use it.
"""

from __future__ import annotations

import pytest

from cql_databricks_synth.fill import materialize
from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.generate import _ground_truth
from cql_databricks_synth.library import Env
from cql_databricks_synth.solve import Solver
from cql_databricks_synth.valuesets import ValueSetIndex

_FIXTURES = dir_or_skip("patterns")

PATTERNS = [
    "Age Through A Define",
    "Procedure In Period",
    "Condition Present In Period",
    "Condition Not Refuted",
    "Discharged With Disposition",
    "Visit With No Disposition",
    "Systolic Below 140",
    "Latest Lab Has No Result",
    "Vaccine Not Given",
]


@pytest.fixture(scope="module")
def env() -> Env:
    return Env.from_paths([_FIXTURES], root="SynthesizerPatterns")


@pytest.fixture(scope="module")
def valuesets() -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(_FIXTURES)


def _patient(env, valuesets, define: str, want: bool) -> list[dict]:
    solver = Solver(env, valuesets, seed=5, patient_key=f"{define}#{want}")
    state = solver.solve({define: want})
    return materialize(state, patient_id="pds-5-0000000", seed=5, patient_key=f"{define}#{want}",
                       period_lo=solver.period_lo, period_hi=solver.period_hi)


@pytest.mark.parametrize("want", [True, False])
@pytest.mark.parametrize("define", PATTERNS)
def test_each_pattern_solves_both_ways_and_reverifies(env, valuesets, define, want):
    resources = _patient(env, valuesets, define, want)
    _, expected = _ground_truth(env, valuesets, resources, {})
    assert expected[define] is want


def test_a_kept_empty_retrieve_keeps_the_resource_another_define_needs(env, valuesets):
    """The visit is an Encounter, so `not exists [Encounter] where …` cannot forbid every Encounter: the visit stays
    and fails the discharge's filter."""
    resources = _patient(env, valuesets, "Visit With No Disposition", True)
    (visit,) = [r for r in resources if r["resourceType"] == "Encounter"]
    assert "hospitalization" not in visit


def test_a_backbone_field_is_placed_nested(env, valuesets):
    resources = _patient(env, valuesets, "Discharged With Disposition", True)
    (discharge,) = [r for r in resources if r["resourceType"] == "Encounter"]
    coding = discharge["hospitalization"]["dischargeDisposition"]["coding"][0]
    assert coding["code"] == "patterns-disposition-1"


def test_an_immunization_names_its_patient_and_its_recorded_date(env, valuesets):
    """FHIR R4: an Immunization names the patient in `patient`, and `recorded` is a plain dateTime."""
    resources = _patient(env, valuesets, "Vaccine Not Given", True)
    (vaccine,) = [r for r in resources if r["resourceType"] == "Immunization"]
    assert vaccine["patient"] == {"reference": "Patient/pds-5-0000000"} and "subject" not in vaccine
    assert "recorded" in vaccine and "recordedDateTime" not in vaccine


def test_an_uncompared_component_gets_its_profile_unit(env, valuesets):
    """The diastolic component is demanded present and compared nowhere, so its unit is the blood pressure
    profile's."""
    resources = _patient(env, valuesets, "Systolic Below 140", False)
    (reading,) = [r for r in resources if r["resourceType"] == "Observation"]
    units = {c["code"]["coding"][0]["code"]: c["valueQuantity"]["code"] for c in reading["component"]}
    assert units == {"8480-6": "mm[Hg]", "8462-4": "mm[Hg]"}
