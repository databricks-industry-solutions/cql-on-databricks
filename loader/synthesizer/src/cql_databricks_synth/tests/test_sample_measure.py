"""Run-time measurement periods for the synthesizer (SYNTH-PDS-ORACLE, LANG-PARAMETER).

The tests that generated and re-verified a cohort over a pinned sample measure were bound to a sample measure
that is removed. They come back with the pinned QI-Core sample measures and their realism configs
(SYNTH-PDS-FIXTURE-PINS, SYNTH-PDS-MEASURE-CONFIG).
"""

from __future__ import annotations

import datetime as dt

import pytest

from cql_databricks_synth import SynthesizerError
from cql_databricks_synth.generate import run_parameters

PERIOD = "2026-01-01/2026-12-31"


def test_run_parameters_read_inclusive_whole_days():
    (period,) = run_parameters(PERIOD).values()
    assert period.low == dt.datetime(2026, 1, 1) and period.high == dt.datetime(2026, 12, 31, 23, 59, 59, 999000)
    assert run_parameters(None) == {}


@pytest.mark.parametrize("bad", ["2026-01-01", "2026-13-01/2026-12-31", "2026-12-31/2026-01-01"])
def test_a_malformed_period_fails_loud(bad):
    with pytest.raises(SynthesizerError, match="measurement period"):
        run_parameters(bad)
