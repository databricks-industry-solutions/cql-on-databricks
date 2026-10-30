"""Distributional priors over the FHIR data model (SYNTH-PDS-PRIORS).

Two things are under test and they pull in opposite directions, which is the point:
priors must visibly SHAPE a realistic cohort, and must not disturb the oracle or the
uniform/adversarial mode that conformance fixtures depend on.

The scope rule — weights describe the FHIR data model, never a measure — is asserted
structurally in `test_no_measure_specific_keys`: it is the invariant most likely to erode
quietly as someone reaches for a quick fix to a single measure's output.
"""

from __future__ import annotations

import collections

import pytest

from cql_databricks_synth import distributions
from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.generate import generate
from cql_databricks_synth.library import Env
from cql_databricks_synth.solve import _Chooser
from cql_databricks_synth.valuesets import Coding, ValueSetIndex

_FIXTURES = dir_or_skip("pilot")


@pytest.fixture(scope="module")
def env() -> Env:
    return Env.from_paths([_FIXTURES])


@pytest.fixture(scope="module")
def valuesets() -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(_FIXTURES)


def _conditions(cohort) -> list[dict]:
    return [r for patient in cohort.patients for r in patient.resources
            if r["resourceType"] == "Condition"]


def _verification_mix(cohort) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    for condition in _conditions(cohort):
        status = condition.get("verificationStatus")
        counts[status["coding"][0]["code"] if status else distributions.ABSENT_VALUE] += 1
    return counts


# -- the scope rule --------------------------------------------------------------------

def test_no_measure_specific_keys():
    """Every prior keys on a FHIR type/element or a code system — never on a measure.

    A value-set OID or a define name appearing here would mean the module had started
    encoding one measure's clinical shape, which is the caller's business (the mix-mode
    rate flags), not this table's.
    """
    for resource_type, element in distributions.ELEMENT_PRIORS:
        assert resource_type[0].isupper(), resource_type
        assert "." not in resource_type and not resource_type[0].isdigit()
        assert element and not element[0].isdigit()
    for system in distributions.SYSTEM_MIX:
        assert system.startswith("http"), system
    # Which quantities a measure measures, and their plausible ranges, are measure-shaped facts: they come
    # from the measure's own config (SYNTH-PDS-MEASURE-CONFIG), so this module holds no table of them.
    assert not hasattr(distributions, "QUANTITY_RANGES")


# -- the mechanisms in isolation --------------------------------------------------------

def test_weighted_pick_respects_weights():
    chooser_hits: collections.Counter = collections.Counter()
    for i in range(4000):
        chooser = _Chooser(1, f"p{i}")
        chooser_hits[chooser.pick_weighted("site", [0.9, 0.1])] += 1
    assert 0.85 < chooser_hits[0] / 4000 < 0.95


def test_weighted_pick_with_equal_weights_is_flat():
    hits: collections.Counter = collections.Counter()
    for i in range(4000):
        hits[_Chooser(1, f"p{i}").pick_weighted("site", [1.0, 1.0, 1.0, 1.0])] += 1
    assert all(850 < n < 1150 for n in hits.values())


def test_order_weighted_is_a_permutation():
    order = _Chooser(1, "p").order_weighted("site", [5.0, 1.0, 0.0, 2.0])
    assert sorted(order) == [0, 1, 2, 3]


def test_zero_weight_options_stay_reachable():
    """A zero-weighted route must still be TRIED — a prior may not turn a satisfiable
    stratum into an unsatisfiable one."""
    order = _Chooser(1, "p").order_weighted("site", [1.0, 0.0, 0.0])
    assert sorted(order) == [0, 1, 2]


def test_member_weights_favour_the_home_system():
    snomed, icd10 = "http://snomed.info/sct", "http://hl7.org/fhir/sid/icd-10-cm"
    members = [Coding(system=snomed, code=f"s{i}", display="") for i in range(5)]
    members += [Coding(system=icd10, code=f"i{i}", display="") for i in range(5)]
    weights = distributions.member_weights(members, draw=0.0)
    home = distributions.home_system(sorted({icd10, snomed}), 0.0)
    share = sum(w for w, m in zip(weights, members) if m.system == home)
    assert share == pytest.approx(distributions.HOME_SYSTEM_SHARE, abs=1e-9)


def test_member_ranking_is_stable_across_patients():
    """Clustering only appears if the SAME codes are favoured cohort-wide; a per-patient
    ranking would average back to flat."""
    members = [Coding(system="http://snomed.info/sct", code=f"s{i}", display="") for i in range(6)]
    first = distributions.member_weights(members, draw=0.1)
    second = distributions.member_weights(members, draw=0.9)
    assert first == second


def test_element_weights_unknown_element_has_no_opinion():
    assert distributions.element_weights("Condition", "severity", ["mild"]) is None


def test_element_weights_unknown_values_have_no_opinion():
    assert distributions.element_weights("Condition", "verificationStatus", ["bogus"]) is None


def test_plausible_domain_narrows_to_the_supplied_band():
    assert distributions.plausible_domain(0.0, 139.0, (85.0, 185.0)) == (85.0, 139.0)


def test_plausible_domain_yields_to_a_disjoint_solved_domain():
    """The measure's constraint is truth; plausibility is decoration and must not win."""
    assert distributions.plausible_domain(200.0, 240.0, (85.0, 185.0)) == (200.0, 240.0)


def test_plausible_domain_without_a_band_is_the_solved_domain():
    assert distributions.plausible_domain(1.0, 2.0, None) == (1.0, 2.0)


# -- end to end, against the real measure ----------------------------------------------

def test_priors_shape_verification_status(env, valuesets):
    """The pilot's diagnosis criterion (`verificationStatus is not null implies` one of four codes)
    offers five equally-valid outcomes, absent and four codes; unshaped they land ~20% each, shaped
    they follow the element's real mix."""
    cohort = generate(env, valuesets, seed=11, counts={"IPP+DEN+!DENEX+NUM": 400},
                      priors=True)
    mix = _verification_mix(cohort)
    total = sum(mix.values())
    assert mix["confirmed"] / total > 0.5
    assert mix["differential"] / total < 0.05


def test_uniform_mode_stays_flat(env, valuesets):
    """Conformance fixtures depend on flat coverage — priors must not leak into the
    mode whose whole purpose is exercising every arm."""
    cohort = generate(env, valuesets, seed=11, counts={"IPP+DEN+!DENEX+NUM": 400})
    mix = _verification_mix(cohort)
    total = sum(mix.values())
    assert mix["confirmed"] / total < 0.35


def test_priors_cluster_diagnosis_codes(env, valuesets):
    """The headline symptom: an unshaped cohort spreads a value set flat across its
    members, so no code stands out."""
    shaped = generate(env, valuesets, seed=11, counts={"IPP+DEN+!DENEX+NUM": 400},
                      priors=True)
    flat = generate(env, valuesets, seed=11, counts={"IPP+DEN+!DENEX+NUM": 400})

    def top_share(cohort) -> float:
        codes = collections.Counter(
            coding["code"] for condition in _conditions(cohort)
            for coding in condition.get("code", {}).get("coding", []))
        return codes.most_common(1)[0][1] / sum(codes.values())

    assert top_share(shaped) > 2 * top_share(flat)


def test_priors_are_deterministic(env, valuesets):
    """SYNTH-PDS-DETERMINISM survives weighted selection: same seed, same cohort."""
    first = generate(env, valuesets, seed=5, counts={"IPP+DEN+!DENEX+NUM": 40}, priors=True)
    second = generate(env, valuesets, seed=5, counts={"IPP+DEN+!DENEX+NUM": 40}, priors=True)
    assert [p.resources for p in first.patients] == [p.resources for p in second.patients]


def test_priors_change_the_cohort_id(env, valuesets):
    """Two cohorts at one seed that differ in shaping are different data and must not
    share an identity."""
    shaped = generate(env, valuesets, seed=5, counts={"IPP+DEN+!DENEX+NUM": 5}, priors=True)
    flat = generate(env, valuesets, seed=5, counts={"IPP+DEN+!DENEX+NUM": 5})
    assert shaped.config_hash != flat.config_hash


def test_priors_preserve_the_oracle(env, valuesets):
    """SYNTH-PDS-VERIFY is what makes shaping safe: generate() verifies every patient
    inline, so reaching the assertions at all is most of the test.

    The assertion itself is the stronger claim — priors change WHICH satisfying patient
    gets built, never WHICH strata are satisfiable. (`IPP+!DEN` is unsatisfiable for the
    pilot either way: its denominator is its initial population.)
    """
    shaped = generate(env, valuesets, seed=3, per_stratum=2, priors=True)
    flat = generate(env, valuesets, seed=3, per_stratum=2)
    assert {(o.stratum, o.status) for o in shaped.outcomes} == \
           {(o.stratum, o.status) for o in flat.outcomes}
    assert len(shaped.patients) == len(flat.patients)
