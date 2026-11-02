"""DATA-ENVIRONMENT-LAYOUT, DATA-CONTRACT — the shared population lander's pure parts. No Spark."""
from __future__ import annotations

import pytest

from shipped_sources.population import RESOURCE_TYPES, land, resource_ddl, store_table


def test_every_bound_type_has_a_struct_in_the_model():
    for fhir_type in RESOURCE_TYPES:
        assert resource_ddl(fhir_type).upper().startswith("STRUCT<")


def test_an_unmodeled_type_fails():
    with pytest.raises(ValueError, match="not a modeled FHIR type"):
        resource_ddl("NotAType")


def test_store_tables_live_in_the_data_store_named_for_the_type():
    assert store_table("c", "MedicationRequest") == "`c`.`data_store`.`medicationrequest`"


def test_a_population_with_an_unbound_type_fails():
    with pytest.raises(ValueError, match="no store table for FHIR types"):
        land(object(), catalog="c", raw_by_type={"Patient": None, "Spaceship": None},
             valid_from="2026-01-01", valid_to="2026-12-31")


def test_a_population_without_patients_fails():
    with pytest.raises(ValueError, match="needs Patient rows"):
        land(object(), catalog="c", raw_by_type={"Condition": None},
             valid_from="2026-01-01", valid_to="2026-12-31")
