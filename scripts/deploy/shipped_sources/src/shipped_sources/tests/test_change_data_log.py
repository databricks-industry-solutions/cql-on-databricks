"""DATA-CHANGE-LOG — the exemplar change log this repository's own sources ship.

The digest reader that consumed the log for the evaluator App's run fingerprint retired with the App
(EVALUATOR-REMOVE); what stays is the contract's shape and the exemplar DDL the loaders and the conformance
provisioner run.
"""
from __future__ import annotations

import pytest

from shipped_sources.change_data_log import (
    CHANGE_DATA_LOG_NAME, UPDATED_AT_COLUMN, change_data_log_ddl,
)


# --- the exemplar DDL ----------------------------------------------------------------------------


def test_the_exemplar_declares_one_row_per_table():
    """One row per table, keyed by the FHIR type it feeds — the shape the contract asks for."""
    ddl = change_data_log_ddl("cat.conformance.change_data_log", {
        "Encounter": "cat.corpus.encounter_resource",
        "Patient": "cat.corpus.patient_resource",
    })
    assert ddl.count("UNION ALL") == 1
    assert "'Encounter' AS `data_type`" in ddl
    assert "'Patient' AS `data_type`" in ddl
    assert "FROM cat.corpus.encounter_resource" in ddl
    assert "FROM cat.corpus.patient_resource" in ddl


def test_the_exemplar_marks_change_time_not_a_clinical_date():
    """The rule that matters most: `max` per type only sees changes ABOVE that type's mark, so a
    SERVICE date would let a backdated encounter loaded today move nothing. Digesting the vector
    fixed the CROSS-type hole and does nothing for this one — it stays a contract rule."""
    ddl = change_data_log_ddl("v", {"Encounter": "t"})
    assert "max(`_metadata`.`file_modification_time`)" in ddl
    assert f"AS `{UPDATED_AT_COLUMN}`" in ddl


def test_the_exemplar_is_deterministic():
    """Two renderings of one input must be byte-identical, or the view churns on every provision."""
    tables = {"Patient": "t1", "Encounter": "t2"}
    assert change_data_log_ddl("v", tables) == change_data_log_ddl("v", dict(reversed(
        list(tables.items()))))


def test_an_empty_exemplar_fails_loud():
    """An empty log claims nothing, and the reader treats that as everything having changed — so
    creating one is a mistake to refuse rather than to deploy (`CORE-NO-SILENT-MISLOWER`)."""
    with pytest.raises(ValueError, match="no tables"):
        change_data_log_ddl("v", {})


def test_the_conventional_name_is_beside_the_mounts():
    """PROC-DRY — the lander creates the view and the compiler's layout check finds it; one definition."""
    from shipped_sources.sources import change_data_log_fqn
    assert change_data_log_fqn("acme", "example_patients") == f"acme.example_patients.{CHANGE_DATA_LOG_NAME}"
