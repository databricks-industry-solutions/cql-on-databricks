"""Solver-level tests: conflict detection and skeleton soundness
(SYNTH-PDS-STRATA, SYNTH-PDS-ORACLE)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cql_databricks_synth import Unsatisfiable
from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.solve import ABSENT, Solver, TemporalDomain, _dob_range
from cql_databricks_synth.valuesets import ValueSetIndex

_FIXTURES = dir_or_skip("pilot")
_VALUESETS = _FIXTURES  # expansions are generated beside the ELM (SYNTH-PDS-FIXTURE-PINS)


@pytest.fixture(scope="module")
def elm() -> dict:
    return json.loads((_FIXTURES / "SynthesizerPilot.elm.json").read_text())


@pytest.fixture(scope="module")
def valuesets() -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(_VALUESETS)


def _solver(elm, valuesets, patient_key="t#0", seed=42) -> Solver:
    return Solver(elm, valuesets, seed=seed, patient_key=patient_key)


def test_conflicting_define_demands_are_unsatisfiable(elm, valuesets):
    """Denominator aliases the Initial Population, so demanding them apart must surface
    as a contradiction naming the define — never a silently skipped stratum."""
    with pytest.raises(Unsatisfiable, match="Initial Population"):
        _solver(elm, valuesets).solve({"Initial Population": True, "Denominator": False})


def test_exists_is_never_null(elm, valuesets):
    with pytest.raises(Unsatisfiable, match="exists"):
        _solver(elm, valuesets).solve({"Has Pilot Diagnosis": None})


def test_numerator_skeleton_binds_determining_attributes(elm, valuesets):
    state = _solver(elm, valuesets).solve({"Numerator": True})
    (sketch,) = state.resources
    assert sketch.resource_type == "Observation"
    assert valuesets.contains(sketch.valueset_url, sketch.code.system, sketch.code.code)
    assert sketch.fields["status"] == "final"
    effective = sketch.fields["effective"]
    assert isinstance(effective, TemporalDomain)
    # jitter range sits strictly inside CY2026 — never on the period boundary
    assert effective.lo >= dt.datetime(2026, 1, 2)
    assert effective.hi <= dt.datetime(2026, 12, 31)


def test_null_target_marks_the_missing_element(elm, valuesets):
    state = _solver(elm, valuesets).solve({"Initial Population": None})
    assert state.birth_date is ABSENT or state.patient_fields.get("gender") is ABSENT


def test_temporal_domain_conflict_reports_both_sites():
    domain = TemporalDomain(lo=dt.datetime(2026, 1, 1), hi=dt.datetime(2026, 12, 31))
    domain.narrow(dt.datetime(2026, 6, 1), None, "site-a")
    with pytest.raises(Unsatisfiable) as excinfo:
        domain.narrow(None, dt.datetime(2026, 3, 1), "site-b")
    assert "site-a" in excinfo.value.conflicts and "site-b" in excinfo.value.conflicts


def test_dob_range_is_calendar_exact():
    lo, hi = _dob_range(dt.date(2026, 12, 31), 24, 64)
    assert lo == dt.date(1962, 1, 1)   # 65th birthday would land Dec 31 2026 -> too old
    assert hi == dt.date(2002, 12, 31)  # 24th birthday exactly on the as-of day counts
