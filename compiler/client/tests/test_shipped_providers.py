"""CLIENT-DEFAULT-RESOLVER, CLIENT-DATA-PROVIDER, CLIENT-TERMINOLOGY-PROVIDER: the providers the client ships, and
the tag resolver that finds them.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from cql_databricks.compiler.data_providers import Frames, Functions, Tables, VolumeFolder
from cql_databricks.compiler.data_source import DataSourceLayoutError
from cql_databricks.compiler.roles import (Code, CodeMatch, DataFrameRelation, FunctionRelation, Layered, RetrieveRequest,
                                           TableRelation, ValueSet, ValueSetRef, VolumeRelation)
from cql_databricks.compiler.tagged import DeltaTerminology, TaggedDataSource, TaggedResolver
from cql_databricks.compiler.terminology_providers import ValueSetFolder, parse_value_set
from cql_databricks.data_bindings.tags import CatalogScope, TaggedSchema

MODELED = ["Patient", "Observation", "Condition", "MedicationRequest"]


class _Runner:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.statements = []

    def __call__(self, statement, params=None):
        self.statements.append((statement, dict(params or {})))
        for key, rows in self.answers.items():
            if key in statement:
                return rows(params) if callable(rows) else rows
        return []


class _Content:
    """A ContentResolver stand-in holding the tagged schemas in scope."""

    def __init__(self, data=(), terminology=()):
        self.data, self.terms = list(data), list(terminology)
        self.scopes = (CatalogScope("acme"),)
        self.generation = 1

    def data_source_schemas(self):
        return self.data

    def terminology_sources(self):
        return self.terms


def _objects(rows):
    return {"information_schema.routines": lambda p: rows}


class TaggedDataSourceTest(unittest.TestCase):
    def test_tables_views_and_functions_serve_their_types(self):
        """CLIENT-DEFAULT-RESOLVER: a retrieve_<Type> table or view is a table relation, a function a function
        relation; the identity is the schema's address."""
        run = _Runner(_objects([{"kind": "function", "name": "retrieve_observation"},
                                {"kind": "table", "name": "retrieve_patient"},
                                {"kind": "table", "name": "change_log"},
                                {"kind": "table", "name": "patient_raw"}]))
        source = TaggedDataSource("synthetic-population-small", "acme", "fhir_small", run)
        self.assertEqual(source.identity(), "acme.fhir_small")
        self.assertEqual(source.types(), {"patient", "observation"})  # as Unity Catalog stores them
        self.assertEqual(source.retrieve(RetrieveRequest(fhir_type="Patient")),
                         TableRelation(name="acme.fhir_small.retrieve_patient"))
        self.assertEqual(source.retrieve(RetrieveRequest(fhir_type="Observation")),
                         FunctionRelation(name="acme.fhir_small.retrieve_observation"))
        self.assertEqual(source.change_log(), TableRelation(name="acme.fhir_small.change_log"))
        source.types()
        self.assertEqual(len(run.statements), 1, "the layout is read once")

    def test_a_doubled_or_missing_type_fails_the_layout(self):
        for rows in ([{"kind": "function", "name": "retrieve_patient"}, {"kind": "table", "name": "retrieve_patient"}],
                     [{"kind": "table", "name": "patient"}]):
            source = TaggedDataSource("s", "acme", "fhir", _Runner(_objects(rows)))
            with self.assertRaises(DataSourceLayoutError):
                source.types()


class TaggedResolverTest(unittest.TestCase):
    def _resolver(self, **content):
        run = _Runner(_objects([{"kind": "table", "name": "retrieve_patient"}]))
        return TaggedResolver(_Content(**content), run), run

    def test_resolver_returns_a_source_by_name(self):
        small = TaggedSchema(tag="cql_databricks_data_source", name="synthetic-population-small", catalog="acme",
                             schema="fhir_small")
        large = TaggedSchema(tag="cql_databricks_data_source", name="synthetic-population-large", catalog="acme",
                             schema="fhir_large")
        resolver, _ = self._resolver(data=[small, large])
        self.assertEqual([s.name for s in resolver.data_sources()],
                         ["synthetic-population-large", "synthetic-population-small"])
        self.assertEqual(resolver.data("synthetic-population-small").identity(), "acme.fhir_small")
        self.assertIs(resolver.data("synthetic-population-small"), resolver.data("synthetic-population-small"))

    def test_a_name_not_in_scope_fails_naming_those_that_are(self):
        small = TaggedSchema(tag="cql_databricks_data_source", name="synthetic-population-small", catalog="acme",
                             schema="fhir_small")
        resolver, _ = self._resolver(data=[small])
        with self.assertRaises(LookupError) as caught:
            resolver.data("acme-fhir")
        self.assertIn("synthetic-population-small", str(caught.exception))

    def test_terminology_is_every_tagged_schema_earliest_scope_first(self):
        """CLIENT-DEFAULT-RESOLVER: the first schema in scope that holds a value set serves it."""
        late = TaggedSchema(tag="cql_databricks_terminology", name="b", catalog="shared", schema="vs", position=1)
        early = TaggedSchema(tag="cql_databricks_terminology", name="a", catalog="acme", schema="vs", position=0)
        resolver, _ = self._resolver(terminology=[late, early])
        layered = resolver.terminology().expansions([ValueSetRef(id="urn:vs")])
        self.assertEqual(layered, Layered(layers=(TableRelation(name="acme.vs.valueset"),
                                                  TableRelation(name="shared.vs.valueset"))))
        self.assertIsNone(self._resolver()[0].terminology())


class DeltaTerminologyTest(unittest.TestCase):
    def test_find_code_reads_the_valueset_table_as_the_caller(self):
        """AGENT-VALUE-SET-LOOKUP."""
        run = _Runner({"SELECT DISTINCT valueset_id": [{"valueset_id": "urn:vs:a1c", "version": "1",
                                                        "system": "http://loinc.org"}]})
        found = DeltaTerminology("acme.vs", run).find_code("4548-4", "http://loinc.org")
        self.assertEqual(found, [CodeMatch(id="urn:vs:a1c", version="1", system="http://loinc.org")])
        statement, params = run.statements[0]
        self.assertIn("`acme`.`vs`.`valueset`", statement)
        self.assertEqual(params, {"code": "4548-4", "system": "http://loinc.org"})

    def test_publish_replaces_each_value_set_by_id_and_version(self):
        run = _Runner()
        report = DeltaTerminology("acme.vs", run).publish([
            ValueSet(id="urn:vs:a", version="1", codes=(Code(system="s", code="c1"), Code(system="s", code="c'2")))])
        self.assertEqual(report.written, ("urn:vs:a",))
        self.assertTrue(run.statements[0][0].startswith("DELETE FROM `acme`.`vs`.`valueset`"))
        self.assertIn("('urn:vs:a', 's', 'c\\'2', '1')", run.statements[1][0])

    def test_publish_refuses_a_value_set_with_no_expansion(self):
        run = _Runner()
        with self.assertRaises(ValueError):
            DeltaTerminology("acme.vs", run).publish([ValueSet(id="urn:vs:empty")])
        self.assertEqual(run.statements, [])


class ShippedDataProvidersTest(unittest.TestCase):
    def test_tables_and_functions_serve_their_types_by_name(self):
        tables = Tables({"Patient": "acme.fhir.patient"})
        self.assertEqual(tables.retrieve(RetrieveRequest(fhir_type="Patient")), TableRelation(name="acme.fhir.patient"))
        self.assertEqual(tables.identity(), "tables:Patient=acme.fhir.patient")
        functions = Functions({"Observation": "acme.fhir.observations"})
        self.assertEqual(functions.retrieve(RetrieveRequest(fhir_type="Observation")),
                         FunctionRelation(name="acme.fhir.observations"))
        with self.assertRaises(ValueError):
            Tables({"Patient": "patient"})  # a table is catalog.schema.table

    def test_volume_folder_finds_its_types_from_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("Patient.ndjson", "Condition.ndjson", "notes.txt"):
                open(os.path.join(d, name), "w").close()
            folder = VolumeFolder("/Volumes/acme/fhir/raw", types=None)
            folder.path = d  # the volume, as a notebook sees it through /Volumes
            self.assertEqual(folder.types(), {"Patient", "Condition"})
        bundles = VolumeFolder("/Volumes/acme/fhir/bundles", layout="bundles", types=["Patient"])
        self.assertEqual(bundles.retrieve(RetrieveRequest(fhir_type="Patient")),
                         VolumeRelation(path="/Volumes/acme/fhir/bundles", fhir_type="Patient", layout="bundles"))
        with self.assertRaises(ValueError):
            VolumeFolder("/Volumes/acme/fhir/bundles", layout="bundles")
        with self.assertRaises(ValueError):
            VolumeFolder("/Workspace/Users/me/fhir")

    def test_frames_are_never_reusable(self):
        """CLIENT-DATA-PROVIDER: a DataFrame provider is for development."""
        frames = Frames({"Patient": object()}, name="mapping-v2")
        self.assertFalse(frames.reusable)
        self.assertEqual(frames.identity(), "dataframes:mapping-v2")
        self.assertIsInstance(frames.retrieve(RetrieveRequest(fhir_type="Patient")), DataFrameRelation)


class ValueSetFolderTest(unittest.TestCase):
    def test_a_value_set_must_carry_its_expansion(self):
        """CLIENT-TERMINOLOGY-PROVIDER: one with only compose fails, naming it."""
        with self.assertRaises(ValueError) as caught:
            parse_value_set({"resourceType": "ValueSet", "url": "urn:vs:compose", "compose": {}}, "vs.json")
        self.assertIn("urn:vs:compose", str(caught.exception))
        vs = parse_value_set({"resourceType": "ValueSet", "url": "urn:vs:a", "version": "2", "expansion": {
            "contains": [{"system": "s", "code": "a", "contains": [{"system": "s", "code": "a1"}]}]}}, "vs.json")
        self.assertEqual([c.code for c in vs.codes], ["a", "a1"])

    def test_a_volume_folder_is_read_by_sql_and_a_workspace_folder_on_spark(self):
        volume = ValueSetFolder("/Volumes/acme/vs/json")
        self.assertEqual(volume.expansions([]), VolumeRelation(path="/Volumes/acme/vs/json", fhir_type="ValueSet",
                                                               layout="files"))
        with self.assertRaises(ValueError):
            ValueSetFolder("/Workspace/Users/me/vs")  # needs spark=

    def test_find_code_reads_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a1c.json"), "w") as f:
                json.dump({"resourceType": "ValueSet", "url": "urn:vs:a1c",
                           "expansion": {"contains": [{"system": "http://loinc.org", "code": "4548-4"}]}}, f)
            folder = ValueSetFolder("/Volumes/acme/vs/json")
            folder.path = d
            self.assertEqual(folder.find_code("4548-4", None),
                             [CodeMatch(id="urn:vs:a1c", version=None, system="http://loinc.org")])
            self.assertEqual(folder.find_code("4548-4", "http://snomed.info/sct"), [])


if __name__ == "__main__":
    unittest.main()
