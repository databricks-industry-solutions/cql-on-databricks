"""A measure's own realism config (SYNTH-PDS-MEASURE-CONFIG), against the pilot measure.

The config is the only place a measure-shaped fact may live, so the tests pin both halves: every key resolves
against the measure or the load fails naming it, and a resolved config visibly shapes the realistic mix while
the uniform mode and the oracle stay untouched.
"""

from __future__ import annotations

import argparse
import collections
import json
import random

import pytest

from cql_databricks_synth import SynthesizerError
from cql_databricks_synth.__main__ import _rates
from cql_databricks_synth.fill import _sample_numeric
from cql_databricks_synth.fixtures import CONFIG_NAME, dir_or_skip
from cql_databricks_synth.generate import generate
from cql_databricks_synth.library import Env
from cql_databricks_synth.measure_config import load
from cql_databricks_synth.solve import NumericDomain, _Chooser
from cql_databricks_synth.valuesets import ValueSetIndex

_FIXTURES = dir_or_skip("pilot")
_STRATUM = "IPP+DEN+!DENEX+NUM"


@pytest.fixture(scope="module")
def env() -> Env:
    return Env.from_paths([_FIXTURES])


@pytest.fixture(scope="module")
def valuesets() -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(_FIXTURES)


@pytest.fixture(scope="module")
def config(env, valuesets):
    return load(_FIXTURES / CONFIG_NAME, env, valuesets)


def _write(tmp_path, body: dict) -> str:
    path = tmp_path / "SynthesizerPilot.synth.json"
    path.write_text(json.dumps({"measure": "SynthesizerPilot", "version": "1.0.0", **body}))
    return str(path)


# -- resolution --------------------------------------------------------------------------

def test_the_pinned_config_resolves_against_the_measure(config):
    assert config.rates == {"in_ipp": 0.6, "exclusion_rate": 0.1, "numerator_rate": 0.5}
    # Keyed by the `or` the solver walks, one weight per branch in operand order.
    assert config.route_weights("SynthesizerPilot", "65:3-66:37") == [3.0, 1.0]
    multipliers = config.member_multipliers("http://example.org/fhir/ValueSet/cql-test-pilot-condition")
    assert multipliers == {("http://example.org/fhir/CodeSystem/cql-test", "pilot-condition-1"): 20.0}
    assert len(config.sha256) == 64


def test_every_unresolvable_key_is_reported_at_once(tmp_path, env, valuesets):
    path = _write(tmp_path, {
        "colours": {},
        "populations": {"Denominator": 0.9, "Nope": 0.5, "Numerator": 1.5},
        "routes": {"Numerator": {"x": 1}, "Denominator Exclusions": {"Pilot Exclusion Procedures": 1}},
        "quantities": {"Unknown Code": {"range": [1, 2]}},
        "members": {"Pilot Condition": {"not-a-member": 2, "pilot-condition-2": 0}},
    })
    with pytest.raises(SynthesizerError) as e:
        load(path, env, valuesets)
    message = str(e.value)
    for expected in ("unknown section 'colours'",
                     "'Denominator' has no rate of its own",
                     "'Nope' is not a population",
                     "populations.Numerator: rate must be a number from 0 to 1",
                     "'Numerator' is not an `or`",
                     "routes.Denominator Exclusions: weights name",
                     "'Unknown Code' is not one declared code or value set",
                     "'not-a-member' is not a member",
                     "members.Pilot Condition.pilot-condition-2: weight must be a positive number"):
        assert expected in message, expected


def test_a_config_for_another_measure_is_refused(tmp_path, env, valuesets):
    path = tmp_path / "Other.synth.json"
    path.write_text(json.dumps({"measure": "OtherMeasure", "version": "1.0.0"}))
    with pytest.raises(SynthesizerError, match="is for 'OtherMeasure'"):
        load(path, env, valuesets)


def test_a_quantity_named_by_value_set_covers_every_member(tmp_path, env, valuesets):
    path = _write(tmp_path, {"quantities": {"Pilot Lab Test": {"range": [4, 6]}}})
    config = load(path, env, valuesets)
    members = valuesets.members("http://example.org/fhir/ValueSet/cql-test-pilot-lab-test")
    assert {config.quantity_range(m.code) for m in members} == {(4.0, 6.0)}


def test_a_quantity_range_narrows_a_realistic_reading(tmp_path, env, valuesets):
    path = _write(tmp_path, {"quantities": {"Pilot Lab Test": {"range": [4, 6]}}})
    config = load(path, env, valuesets)
    code = valuesets.members("http://example.org/fhir/ValueSet/cql-test-pilot-lab-test")[0].code
    rng = random.Random(1)
    readings = [_sample_numeric(rng, NumericDomain(lo=0.0, hi=100.0, unit="%"), True, code, config)
                for _ in range(50)]
    assert all(4.0 <= r <= 6.0 for r in readings)


# -- rates -------------------------------------------------------------------------------

def _args(**flags) -> argparse.Namespace:
    names = ("out_of_ipp_rate", "exclusion_rate", "numerator_rate", "null_rate", "exception_rate")
    return argparse.Namespace(**{name: flags.get(name) for name in names})


def test_a_rate_flag_wins_over_the_config_which_wins_over_the_default(config):
    rates = _rates(_args(numerator_rate=0.9), config)
    assert rates["numerator_rate"] == 0.9            # the flag
    assert rates["out_of_ipp"] == pytest.approx(0.4)  # the config's 0.6 in the initial population
    assert rates["exclusion_rate"] == 0.1             # the config
    assert rates["null_rate"] == 0.01                 # the default


# -- shaping the cohort ---------------------------------------------------------------

def _condition_codes(cohort) -> collections.Counter:
    return collections.Counter(
        coding["code"] for patient in cohort.patients for r in patient.resources
        if r["resourceType"] == "Condition" for coding in r["code"]["coding"])


def test_member_weights_shape_the_realistic_cohort(env, valuesets, config):
    shaped = _condition_codes(generate(env, valuesets, seed=11, counts={_STRATUM: 200}, priors=True,
                                       config=config))
    plain = _condition_codes(generate(env, valuesets, seed=11, counts={_STRATUM: 200}, priors=True))
    share = lambda counts: counts["pilot-condition-1"] / sum(counts.values())  # noqa: E731
    assert share(shaped) > 0.5
    assert share(shaped) > 2 * share(plain)


def test_route_weights_reach_the_solver_at_their_or(env, valuesets, config, monkeypatch):
    seen: list[tuple[str, list[float]]] = []
    original = _Chooser.order_weighted

    def record(self, site, weights):
        seen.append((site, list(weights)))
        return original(self, site, weights)

    monkeypatch.setattr(_Chooser, "order_weighted", record)
    generate(env, valuesets, seed=3, counts={"IPP+DEN+DENEX": 3}, priors=True, config=config)
    assert ("or@SynthesizerPilot:65:3-66:37", [3.0, 1.0]) in seen


def test_the_config_is_part_of_the_cohort_identity(env, valuesets, config):
    shaped = generate(env, valuesets, seed=5, counts={_STRATUM: 2}, priors=True, config=config)
    plain = generate(env, valuesets, seed=5, counts={_STRATUM: 2}, priors=True)
    assert shaped.config_hash != plain.config_hash


def test_a_config_outside_the_realistic_mix_is_refused(env, valuesets, config):
    with pytest.raises(SynthesizerError, match="realistic mix only"):
        generate(env, valuesets, seed=5, counts={_STRATUM: 1}, config=config)


def test_a_shaped_cohort_still_verifies(env, valuesets, config):
    """SYNTH-PDS-VERIFY: generate() re-evaluates every patient, so reaching the end is the assertion that the
    config moved choices without moving any patient out of its stratum."""
    cohort = generate(env, valuesets, seed=7, per_stratum=2, priors=True, config=config)
    assert cohort.patients
