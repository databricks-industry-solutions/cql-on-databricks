"""Unit tests for the the synthesizer evaluator's three-valued semantics
(SYNTH-PDS-ORACLE, SYNTH-PDS-IR-CONSUMER)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cql_databricks_synth import UnsupportedElm
from cql_databricks_synth.evaluator import Bundle, CqlInterval, Evaluator
from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.valuesets import ValueSetIndex

_FIXTURES = dir_or_skip("pilot")
_VALUESETS = _FIXTURES  # expansions are generated beside the ELM (SYNTH-PDS-FIXTURE-PINS)


@pytest.fixture(scope="module")
def evaluator() -> Evaluator:
    elm = json.loads((_FIXTURES / "SynthesizerPilot.elm.json").read_text())
    return Evaluator(elm, ValueSetIndex.from_ndjson_dir(_VALUESETS), Bundle())


def _literal(value) -> dict:
    if value is None:
        return {"type": "Null"}
    if isinstance(value, bool):
        return {"type": "Literal", "valueType": "{urn:hl7-org:elm-types:r1}Boolean",
                "value": "true" if value else "false"}
    if isinstance(value, int):
        return {"type": "Literal", "valueType": "{urn:hl7-org:elm-types:r1}Integer",
                "value": str(value)}
    return {"type": "Literal", "valueType": "{urn:hl7-org:elm-types:r1}String", "value": value}


@pytest.mark.parametrize("left,right,expected", [
    (True, True, True), (True, False, False), (False, None, False), (True, None, None),
    (None, None, None),
])
def test_and_is_three_valued(evaluator, left, right, expected):
    node = {"type": "And", "operand": [_literal(left), _literal(right)]}
    assert evaluator.eval(node) is expected


@pytest.mark.parametrize("left,right,expected", [
    (False, False, False), (True, None, True), (False, None, None), (None, None, None),
])
def test_or_is_three_valued(evaluator, left, right, expected):
    node = {"type": "Or", "operand": [_literal(left), _literal(right)]}
    assert evaluator.eval(node) is expected


def test_equal_propagates_null(evaluator):
    node = {"type": "Equal", "operand": [_literal(None), _literal("female")]}
    assert evaluator.eval(node) is None


def test_measurement_period_default_is_cy2026(evaluator):
    period = evaluator.parameter("Measurement Period")
    assert isinstance(period, CqlInterval)
    assert period.effective_low() == dt.datetime(2026, 1, 1)
    # closed-open interval: `end of` is the predecessor of the open bound
    assert period.effective_high() == dt.datetime(2027, 1, 1) - dt.timedelta(milliseconds=1)


def test_interval_membership_respects_closure(evaluator):
    interval = CqlInterval(low=1, high=10, low_closed=True, high_closed=False)
    assert interval.effective_high() == 9


def test_unsupported_node_fails_loud(evaluator):
    with pytest.raises(UnsupportedElm, match="Slice"):
        evaluator.eval({"type": "Slice", "locator": "1:1"})


def test_day_precision_truncates_both_operands(evaluator):
    """`day of` comparisons truncate to the day: same-day instants compare equal."""
    node = {"type": "SameAs", "precision": "Day", "operand": [
        {"type": "DateTime", "year": _literal(2026), "month": _literal(3),
         "day": _literal(5), "hour": _literal(9)},
        {"type": "DateTime", "year": _literal(2026), "month": _literal(3),
         "day": _literal(5), "hour": _literal(22)},
    ]}
    assert evaluator.eval(node) is True
    node["operand"][1]["day"] = _literal(6)
    assert evaluator.eval(node) is False


def test_calculate_age_at_counts_completed_years(evaluator):
    node = {"type": "CalculateAgeAt", "precision": "Year", "operand": [
        {"type": "Date", "year": _literal(2000), "month": _literal(6), "day": _literal(15)},
        {"type": "Date", "year": _literal(2026), "month": _literal(6), "day": _literal(14)},
    ]}
    assert evaluator.eval(node) == 25  # the day before the birthday
    node["operand"][1]["day"] = _literal(15)
    assert evaluator.eval(node) == 26  # the birthday itself


def test_defines_evaluate_null_on_an_empty_bundle(evaluator):
    """No Patient resource: the birth date is null, so Age In Range is null — never false.
    But `exists` of an empty retrieve IS false, and false wins the conjunction, so the
    Initial Population collapses to false: exactly the three-valued asymmetry the
    manifest must capture."""
    assert evaluator.define("Age In Range") is None
    assert evaluator.define("Initial Population") is False
