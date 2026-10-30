"""DATA-SOURCE-UC-TAG, DATA-CONTRACT, BIND-RETRIEVE-UDF — the shared population lander's pure parts. No Spark."""
from __future__ import annotations

import pytest

from shipped_sources.population import (RESOURCE_TYPES, RETRIEVE_PARAMS_DDL, PopulationSource, land, mount_table,
                                       resource_ddl, retrieve_udtf_ddl)

SOURCE = PopulationSource(source_id="s", schema="sch", description="Fabricated test patients")


def test_every_bound_type_has_a_struct_in_the_model():
    for fhir_type in RESOURCE_TYPES:
        assert resource_ddl(fhir_type).upper().startswith("STRUCT<")


def test_an_unmodeled_type_fails():
    with pytest.raises(ValueError, match="not a modeled FHIR type"):
        resource_ddl("NotAType")


def test_the_retrieve_udtf_declares_the_engine_contract():
    ddl = retrieve_udtf_ddl("c.sch.retrieve_Patient", "Patient", "c.sch.retrieve_patient_data",
                            "`__subject_id` STRING, `__resource` STRUCT<id: STRING>", "Example's patients")
    assert f"CREATE OR REPLACE FUNCTION c.sch.retrieve_Patient({RETRIEVE_PARAMS_DDL})\n" in ddl
    assert "RETURN SELECT `__subject_id`, `__resource` FROM c.sch.retrieve_patient_data" in ddl
    assert "Example''s patients" in ddl  # the comment is quoted


def test_mounts_live_in_the_source_schema():
    assert mount_table("c", "sch", "MedicationRequest") == "c.sch.retrieve_medicationrequest_data"


def test_a_population_with_an_unbound_type_fails():
    with pytest.raises(ValueError, match="no mount for FHIR types"):
        land(object(), catalog="c", source=SOURCE, raw_by_type={"Patient": None, "Spaceship": None},
             valid_from="2026-01-01", valid_to="2026-12-31")


def test_a_population_without_patients_fails():
    with pytest.raises(ValueError, match="needs Patient rows"):
        land(object(), catalog="c", source=SOURCE, raw_by_type={"Condition": None},
             valid_from="2026-01-01", valid_to="2026-12-31")
