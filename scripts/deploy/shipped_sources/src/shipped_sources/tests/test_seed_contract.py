"""PROC-TDD — the Spark-free mount naming and tagging helpers (DATA-CONTRACT). No Spark, no compiler."""
from __future__ import annotations

from shipped_sources.seed_fhir import contract


def test_valueset_id_from_filename_strips_engine_prefix():
    # The engine filters `WHERE valueset_id = '<last-url-segment>'`; file is valueset-<segment>.ndjson.
    assert contract.valueset_id_from_filename("valueset-diabetes") == "diabetes"
    assert contract.valueset_id_from_filename("valueset-1.2.3.4.5.1001") == "1.2.3.4.5.1001"
    assert contract.valueset_id_from_filename("diabetes") == "diabetes"  # already canonical


def test_cql_tblproperties_always_has_type_and_known_concepts():
    obs = contract.cql_tblproperties("Observation")
    assert obs["cql.fhir.type"] == "Observation"
    assert "system:http://loinc.org" in obs["cql.concepts"]
    # An unknown-coverage type omits cql.concepts (unknown is KEPT by the resolver, never pruned).
    pat = contract.cql_tblproperties("Patient")
    assert pat["cql.fhir.type"] == "Patient"
    assert "cql.concepts" not in pat


def test_cql_tblproperties_valid_window_optional():
    props = contract.cql_tblproperties("Condition", valid_from="2000-01-01", valid_to="2030-12-31")
    assert props["cql.valid.from"] == "2000-01-01"
    assert props["cql.valid.to"] == "2030-12-31"
    assert "cql.valid.from" not in contract.cql_tblproperties("Condition")
