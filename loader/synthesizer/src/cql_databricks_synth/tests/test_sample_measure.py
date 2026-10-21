"""The synthesizer over the repository's own sample measure, which declares its Measurement Period with no
default (SYNTH-PDS-ORACLE, SYNTH-PDS-STRATA, SYNTH-PDS-VERIFY, LANG-PARAMETER).

The fixture is the pinned ``examples/measures`` closure (``pins.json`` key ``BasicEncounterAndCondition``), built by
``python -m cql_databricks_synth fixtures``; the tests skip with that fix when it is absent.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from cql_databricks_synth import SynthesizerError, UnsupportedElm
from cql_databricks_synth.emit import write_cohort
from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.generate import generate, run_parameters
from cql_databricks_synth.library import Env
from cql_databricks_synth.strata import population_defines, proportion_strata, realistic_counts
from cql_databricks_synth.valuesets import ValueSetIndex
from cql_databricks_synth.verify import verify_cohort

PERIOD = "2026-01-01/2026-12-31"


@pytest.fixture(scope="module")
def fixtures():
    return dir_or_skip("BasicEncounterAndCondition")


@pytest.fixture(scope="module")
def env(fixtures) -> Env:
    return Env.from_paths([fixtures])


@pytest.fixture(scope="module")
def valuesets(fixtures) -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(fixtures)


def test_a_measure_with_no_period_default_needs_one_at_run_time(env, valuesets):
    with pytest.raises(UnsupportedElm, match="no default and no run-time value"):
        generate(env, valuesets, seed=1, per_stratum=1)


def test_the_sample_measure_generates_and_reverifies(tmp_path, env, valuesets):
    cohort = generate(env, valuesets, seed=3, per_stratum=4, measurement_period=PERIOD)
    by_stratum = {o.stratum: o for o in cohort.outcomes}
    assert by_stratum["!IPP"].patient_count == 4 and by_stratum["IPP"].patient_count == 4
    # Both checks are `exists`, which is never null, so the null stratum is reported unsatisfiable.
    assert by_stratum["null:IPP"].status == "unsatisfiable"
    write_cohort(cohort, tmp_path)
    assert verify_cohort(env, valuesets, tmp_path) == []
    rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert {row["measurement_period"] for row in rows} == {PERIOD}


def test_the_period_is_part_of_the_cohort_identity(env, valuesets):
    first = generate(env, valuesets, seed=3, per_stratum=1, measurement_period=PERIOD)
    other = generate(env, valuesets, seed=3, per_stratum=1, measurement_period="2025-01-01/2025-12-31")
    assert first.config_hash != other.config_hash


def test_a_cohort_measure_targets_its_initial_population(env):
    """SYNTH-PDS-STRATA — no numerator: the innermost population is the in-stratum, and the realistic mode gives it
    the whole scored share."""
    populations = population_defines(env)
    strata = proportion_strata(populations)
    assert [s.label for s in strata] == ["!IPP", "IPP", "null:IPP"]
    counts = realistic_counts(strata, populations, 1000)
    assert counts == {"!IPP": 300, "IPP": 690, "null:IPP": 10}


def test_run_parameters_read_inclusive_whole_days():
    (period,) = run_parameters(PERIOD).values()
    assert period.low == dt.datetime(2026, 1, 1) and period.high == dt.datetime(2026, 12, 31, 23, 59, 59, 999000)
    assert run_parameters(None) == {}


@pytest.mark.parametrize("bad", ["2026-01-01", "2026-13-01/2026-12-31", "2026-12-31/2026-01-01"])
def test_a_malformed_period_fails_loud(bad):
    with pytest.raises(SynthesizerError, match="measurement period"):
        run_parameters(bad)
