"""The Databricks data binder (`CLIENT-EXEC-DISCOVERY`): a run reads the data source found by its tag.

No SDK, no workspace: the content resolver and the SQL runner are fakes.
"""
import unittest

from cql_databricks.compiler.discovery import DatabricksDataBinder
from cql_databricks.compiler.resolve import ResolutionError
from cql_databricks.data_bindings import CatalogScope, TaggedSchema


def _schema(name, catalog, schema="fhir"):
    return TaggedSchema(tag="cql_databricks_data_source", name=name, catalog=catalog, schema=schema)


class _Content:
    def __init__(self, *schemas):
        self._schemas = list(schemas)
        self.scopes = (CatalogScope("org"),)

    def data_source_schemas(self):
        return self._schemas


def _run(routines):
    def run(statement, params=None):
        return [{"routine_name": r} for r in routines]
    return run


class _Library:
    def __init__(self, data_source=None):
        self.data_source = data_source


class _Record:
    """The session record (CLIENT-SESSION-STATE), holding a mount or none."""

    def __init__(self, mount=None):
        self._mount = mount

    def mount(self):
        return self._mount

    def version(self):
        return 7


def _binder(content, routines=("retrieve_Condition", "retrieve_Patient"), mount=None):
    return DatabricksDataBinder(lambda: content, lambda: _run(routines), terminology_table="rt.runtime.valuesets",
                                record=_Record(mount))


class DatabricksDataBinderTest(unittest.TestCase):
    def test_the_only_source_in_scope_is_used(self):
        bound = _binder(_Content(_schema("acme_fhir", "org"))).bind(_Library(), ["Condition", "Patient"],
                                                                     durable=False)
        self.assertEqual((bound.data_source["catalog"], bound.data_source["schema"]), ("org", "fhir"))
        self.assertEqual(bound.terminology_table, "rt.runtime.valuesets")

    def test_the_named_source_is_used_among_several(self):
        content = _Content(_schema("acme_fhir", "org"), _schema("acme_fhir_test", "org", "test"))
        bound = _binder(content).bind(_Library("acme_fhir_test"), ["Condition"], durable=False)
        self.assertEqual(bound.data_source["schema"], "test")

    def test_several_sources_with_none_named_fail_naming_them(self):
        content = _Content(_schema("acme_fhir", "org"), _schema("acme_fhir_test", "org", "test"))
        with self.assertRaisesRegex(ResolutionError, "2 data sources in scope: acme_fhir, acme_fhir_test; "
                                                     "name one with data_source="):
            _binder(content).bind(_Library(), ["Condition"], durable=False)

    def test_no_source_fails_naming_what_the_cql_retrieves(self):
        with self.assertRaisesRegex(ResolutionError, "retrieves Condition, and no data source"):
            _binder(_Content()).bind(_Library(), ["Condition"], durable=False)

    def test_data_free_cql_resolves_nothing_even_with_several_sources(self):
        def never():
            raise AssertionError("a data-free compile looked up data sources")
        binder = DatabricksDataBinder(never, never, terminology_table="rt.runtime.valuesets", record=_Record())
        bound = binder.bind(_Library(), [], durable=False)
        self.assertIsNone(bound.data_source)
        self.assertEqual(bound.terminology_table, "rt.runtime.valuesets")

    def test_a_source_missing_a_retrieved_type_fails_before_the_run(self):
        with self.assertRaisesRegex(ResolutionError, "has no retrieve_Observation"):
            _binder(_Content(_schema("acme_fhir", "org"))).bind(_Library(), ["Condition", "Observation"],
                                                                durable=False)

    def test_routine_names_match_in_any_case(self):
        # Unity Catalog stores routine names in lower case.
        bound = _binder(_Content(_schema("acme_fhir", "org")), routines=("retrieve_condition",)).bind(
            _Library(), ["Condition"], durable=False)
        self.assertEqual(bound.data_source["routines"], ["retrieve_condition"])


class MountPrecedenceTest(unittest.TestCase):
    """CLIENT-SESSION-MOUNT — a named source beats the mount, and the mount beats discovery."""

    MOUNT = {"types": {"Condition": "retrieve_Condition__cqlmount", "Patient": "retrieve_Patient__cqlmount"},
             "valuesets": "__cqlmount_valuesets_3", "views": []}

    def _never(self):
        raise AssertionError("discovery ran while a mount was in place")

    def test_the_mount_replaces_discovery(self):
        binder = DatabricksDataBinder(self._never, self._never, terminology_table="rt.runtime.valuesets",
                                      record=_Record(self.MOUNT))
        bound = binder.bind(_Library(), ["Condition", "Patient"], durable=False)
        self.assertIsNone(bound.data_source)
        self.assertEqual(bound.sources["Condition"], {"function": "retrieve_Condition__cqlmount"})
        self.assertEqual(bound.terminology_table, "__cqlmount_valuesets_3")
        self.assertEqual(binder.generation(), 7)

    def test_a_named_source_beats_the_mount(self):
        bound = _binder(_Content(_schema("acme_fhir", "org")), mount=self.MOUNT).bind(
            _Library("acme_fhir"), ["Condition"], durable=False)
        self.assertEqual(bound.data_source["catalog"], "org")

    def test_an_unmounted_type_fails_naming_the_mounted_types(self):
        with self.assertRaisesRegex(ResolutionError, "retrieves Encounter, and the session mount has only "
                                                     "Condition, Patient"):
            _binder(_Content(), mount=self.MOUNT).bind(_Library(), ["Encounter"], durable=False)

    def test_a_durable_calculation_refuses_the_mount(self):
        with self.assertRaisesRegex(ResolutionError, "durable calculation"):
            _binder(_Content(), mount=self.MOUNT).bind(_Library(), ["Patient"], durable=True)


if __name__ == "__main__":
    unittest.main()
