"""The synthesizer over the repository's own sample measures, which declare their Measurement Period with no
default (SYNTH-PDS-ORACLE, SYNTH-PDS-STRATA, SYNTH-PDS-VERIFY, SYNTH-PDS-MEASURE-CONFIG, LANG-PARAMETER).

The fixtures are the pinned ``examples/measures`` closures (``pins.json``, SYNTH-PDS-FIXTURE-PINS), built by
``python -m cql_databricks_synth fixtures``; the tests skip with that fix when one is absent.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from cql_databricks_synth import SynthesizerError, UnsupportedElm
from cql_databricks_synth.emit import write_cohort
from cql_databricks_synth.fixtures import CONFIG_NAME, dir_or_skip
from cql_databricks_synth.generate import generate, run_parameters
from cql_databricks_synth.library import Env
from cql_databricks_synth.measure_config import load
from cql_databricks_synth.strata import population_defines, proportion_strata, realistic_counts, realistic_rates
from cql_databricks_synth.valuesets import ValueSetIndex
from cql_databricks_synth.verify import verify_cohort

PERIOD = "2026-01-01/2026-12-31"

MEASURES = ["HypertensionBPControl", "DiabetesHighOrMissingA1c", "AdultFluImmunization", "ColorectalScreening"]


def _measure(name: str) -> tuple[Env, ValueSetIndex, object]:
    fixtures = dir_or_skip(name)
    env = Env.from_paths([fixtures], root=name)
    valuesets = ValueSetIndex.from_ndjson_dir(fixtures)
    return env, valuesets, fixtures


@pytest.mark.parametrize("name", MEASURES)
def test_a_measure_with_no_period_default_needs_one_at_run_time(name):
    env, valuesets, _ = _measure(name)
    with pytest.raises(UnsupportedElm, match="no default and no run-time value"):
        generate(env, valuesets, seed=1, per_stratum=1)


@pytest.mark.parametrize("name", MEASURES)
def test_the_sample_measure_generates_every_stratum_and_reverifies(tmp_path, name):
    """Every stratum but the one the measure rules out (its Denominator is its Initial Population) is built, and
    every patient re-verifies from the written files."""
    env, valuesets, _ = _measure(name)
    cohort = generate(env, valuesets, seed=3, per_stratum=2, measurement_period=PERIOD)
    unsatisfied = {o.stratum for o in cohort.outcomes if o.status != "satisfied"}
    assert unsatisfied == {"IPP+!DEN"}
    assert all(o.patient_count == 2 for o in cohort.outcomes if o.status == "satisfied")
    write_cohort(cohort, tmp_path)
    assert verify_cohort(env, valuesets, tmp_path) == []
    rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert {row["measurement_period"] for row in rows} == {PERIOD}


@pytest.mark.parametrize("name", MEASURES)
def test_a_realistic_cohort_shaped_by_the_config_reverifies(tmp_path, name):
    env, valuesets, fixtures = _measure(name)
    config = load(fixtures / CONFIG_NAME, env, valuesets)
    rates = realistic_rates(config)
    populations = population_defines(env)
    counts = realistic_counts(proportion_strata(populations), populations, 120, **rates)
    cohort = generate(env, valuesets, seed=4, counts=counts, priors=True, measurement_period=PERIOD,
                      config=config)
    assert cohort.meta.config_hash
    write_cohort(cohort, tmp_path)
    assert verify_cohort(env, valuesets, tmp_path) == []


def test_the_period_is_part_of_the_cohort_identity():
    env, valuesets, _ = _measure("HypertensionBPControl")
    first = generate(env, valuesets, seed=3, per_stratum=1, measurement_period=PERIOD)
    other = generate(env, valuesets, seed=3, per_stratum=1, measurement_period="2025-01-01/2025-12-31")
    assert first.config_hash != other.config_hash


def test_run_parameters_read_inclusive_whole_days():
    (period,) = run_parameters(PERIOD).values()
    assert period.low == dt.datetime(2026, 1, 1) and period.high == dt.datetime(2026, 12, 31, 23, 59, 59, 999000)
    assert run_parameters(None) == {}


@pytest.mark.parametrize("bad", ["2026-01-01", "2026-13-01/2026-12-31", "2026-12-31/2026-01-01"])
def test_a_malformed_period_fails_loud(bad):
    with pytest.raises(SynthesizerError, match="measurement period"):
        run_parameters(bad)
