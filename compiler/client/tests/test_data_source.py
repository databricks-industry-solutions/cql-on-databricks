"""DATA-SOURCE-UC-TAG — the data-source layout check (``cql_databricks.compiler.data_source``).

Stdlib unittest over a fake SQL runner that answers the two ``information_schema`` reads.
"""
import unittest

from cql_databricks.compiler.data_source import (DataSourceLayoutError, change_log_all_statement, change_log_ddl,
                                                 check_layout, read_layout)

MODELED = ["Patient", "Encounter", "MedicationRequest"]


def _runner(routines, change_log_columns=(), period_log_columns=()):
    def run(statement, params=None):
        if "information_schema.routines" in statement:
            return [{"routine_name": r} for r in routines]
        if "information_schema.columns" in statement:
            columns = {"change_data_log": change_log_columns, "change_log": period_log_columns}[params["table"]]
            return [{"column_name": c} for c in columns]
        raise AssertionError(statement)
    return run


class LayoutTest(unittest.TestCase):
    def test_durable_callers_validate_bindings_without_inspecting_change_logs(self):
        """RUN-CLIENT-REUSE — log presence, layout and access do not gate durable computation."""
        def run(statement, params=None):
            self.assertIn("information_schema.routines", statement)
            return [{"routine_name": "retrieve_patient"}]
        layout = check_layout(run, "synthetic", "source", MODELED, include_change_logs=False)
        self.assertEqual(layout.bindings, {"Patient": "synthetic.source.retrieve_Patient"})
        self.assertIsNone(layout.change_log)
        self.assertIsNone(layout.change_data_log)

    def test_bindings_are_the_retrieve_functions_mapped_back_to_their_type(self):
        # UC lists names in lower case; the binding keeps the type's own spelling.
        run = _runner(["retrieve_patient", "retrieve_medicationrequest", "helper"],
                      ["data_type", "updated_at"])
        layout = check_layout(run, "main", "fhir", MODELED)
        self.assertEqual(dict(layout.bindings), {
            "MedicationRequest": "main.fhir.retrieve_MedicationRequest",
            "Patient": "main.fhir.retrieve_Patient"})
        self.assertEqual(layout.change_data_log, "main.fhir.change_data_log")

    def test_change_data_log_is_optional(self):
        layout = check_layout(_runner(["retrieve_patient"]), "main", "fhir", MODELED)
        self.assertIsNone(layout.change_data_log)

    def test_change_log_is_found_by_name_and_optional(self):
        """DATA-CHANGE-LOG-PERIODS — the relation naming the evaluation sets that changed."""
        layout = check_layout(_runner(["retrieve_patient"], period_log_columns=["parameters", "changed_at"]),
                              "main", "fhir", MODELED)
        self.assertEqual(layout.change_log, "main.fhir.change_log")
        self.assertIsNone(check_layout(_runner(["retrieve_patient"]), "main", "fhir", MODELED).change_log)

    def test_a_change_log_without_its_columns_is_a_gap(self):
        _, problems = read_layout(_runner(["retrieve_patient"], period_log_columns=["parameters"]),
                                  "main", "fhir", MODELED)
        self.assertEqual(problems, ["change_log is missing column changed_at"])

    def test_the_change_log_table_has_the_columns_the_check_looks_for(self):
        ddl = change_log_ddl("main", "fhir")
        self.assertTrue(ddl.startswith("CREATE TABLE IF NOT EXISTS main.fhir.change_log "
                                       "(parameters MAP<STRING, STRING>, changed_at TIMESTAMP)"))
        self.assertEqual(change_log_all_statement("main", "fhir"),
                         "INSERT INTO main.fhir.change_log VALUES (map(), current_timestamp())")

    def test_every_gap_is_named_at_once(self):
        run = _runner(["retrieve_unicorn"], ["data_type"])
        _, problems = read_layout(run, "main", "fhir", MODELED)
        self.assertEqual(problems, [
            "change_data_log is missing column updated_at",
            "no retrieve_<Type> functions",
            "retrieve_unicorn names no FHIR type the compiler models"])
        with self.assertRaises(DataSourceLayoutError):
            check_layout(run, "main", "fhir", MODELED)

    def test_a_leftover_ids_variant_is_a_layout_error(self):
        """BIND-RETRIEVE-SUBJECT is retired: a `retrieve_<type>_ids` function names no modeled type, so a
        schema still holding one fails loudly rather than being skipped."""
        _, problems = read_layout(_runner(["retrieve_patient", "retrieve_patient_ids"]), "main", "fhir", MODELED)
        self.assertEqual(problems, ["retrieve_patient_ids names no FHIR type the compiler models"])


if __name__ == "__main__":
    unittest.main()
