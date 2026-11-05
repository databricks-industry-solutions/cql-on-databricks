"""CLIENT-ENVIRONMENT, CLIENT-ENVIRONMENT-RESOLVER, CLIENT-DATA-RESOLVER, CLIENT-TERMINOLOGY-RESOLVER,
BIND-PUBLISHED-VIEW: the environment resolver, and the resolvers the client ships.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from cql_databricks.resolvers.data import (DataFrameDataResolver, SchemaDataResolver, TableDataResolver,
                                                    VolumeDataResolver)
from cql_databricks.resolvers.environment import (DEFAULT_TAG, NAME_TAG, VERSION_TAG, Environment,
                                                 EnvironmentLookupError, EnvironmentResolver)
from cql_databricks.resolvers.interfaces import (CallSite, CodeMatch, DataFrameRelation, RetrieveRequest,
                                               TableRelation, VolumeRelation)
from cql_databricks.resolvers.terminology import (FolderTerminologyResolver, TableTerminologyResolver,
                                                           parse_value_set)


def site(fhir_type):
    return CallSite(id="retrieve_1", kind=CallSite.RETRIEVE, library="L", version="1", define="D",
                    request=RetrieveRequest(fhir_type=fhir_type))


class _Runner:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.statements = []

    def __call__(self, statement, params=None):
        self.statements.append((statement, dict(params or {})))
        for key, rows in self.answers.items():
            if key in statement:
                return rows(statement, params) if callable(rows) else rows
        return []


def _tags(*catalogs):
    """catalog_tags rows: each catalog as (name, version, default, comment)."""
    rows = []
    for catalog, name, version, default, comment in catalogs:
        for tag, value in ((NAME_TAG, name), (VERSION_TAG, version), (DEFAULT_TAG, default)):
            if value is not None:
                rows.append({"catalog": catalog, "tag": tag, "value": value, "comment": comment})
    return {"information_schema.catalog_tags": rows}


class EnvironmentResolverTest(unittest.TestCase):
    def test_environments_are_found_in_one_query_of_catalog_tags(self):
        """CLIENT-ENVIRONMENT: the name, layout version and default tags; the description is the comment."""
        run = _Runner(_tags(("cql_main", "synthetic-population-small", "3", "true", "Example measures"),
                            ("cql_large", "synthetic-population-large", "3", None, None),
                            ("other", None, "3", None, None)))
        resolver = EnvironmentResolver(run)
        self.assertEqual([e.name for e in resolver.environments()],
                         ["synthetic-population-large", "synthetic-population-small"])
        default = resolver.default()
        self.assertEqual((default.catalog, default.version, default.description),
                         ("cql_main", "3", "Example measures"))
        self.assertEqual(resolver.get("synthetic-population-large").catalog, "cql_large")
        resolver.default()
        self.assertEqual(len(run.statements), 1, "the environments are read once")

    def test_two_defaults_or_none_fail_naming_what_was_found(self):
        """CLIENT-ENVIRONMENT-RESOLVER: never a guess."""
        two = EnvironmentResolver(_Runner(_tags(("a", "one", "3", "true", None), ("b", "two", "3", "TRUE", None))))
        with self.assertRaises(EnvironmentLookupError) as caught:
            two.default()
        self.assertIn("a, b", str(caught.exception))
        none = EnvironmentResolver(_Runner(_tags(("a", "one", "3", None, None))))
        with self.assertRaises(EnvironmentLookupError) as caught:
            none.default()
        self.assertIn(DEFAULT_TAG, str(caught.exception))
        self.assertIn("one (a)", str(caught.exception))

    def test_a_name_not_found_or_doubled_fails(self):
        resolver = EnvironmentResolver(_Runner(_tags(("a", "one", "3", "true", None))))
        with self.assertRaises(EnvironmentLookupError) as caught:
            resolver.get("two")
        self.assertIn("one", str(caught.exception))
        doubled = EnvironmentResolver(_Runner(_tags(("a", "one", "3", None, None), ("b", "one", "3", None, None))))
        with self.assertRaises(EnvironmentLookupError):
            doubled.environments()

    def test_an_unsupported_layout_version_fails_naming_both(self):
        """CLIENT-ENVIRONMENT: the version tells the client whether it can read the environment."""
        resolver = EnvironmentResolver(_Runner(_tags(("a", "one", "9", "true", None))))
        with self.assertRaises(EnvironmentLookupError) as caught:
            resolver.default()
        self.assertIn("'9'", str(caught.exception))
        self.assertIn("reads 3", str(caught.exception))

    def test_an_environments_resolvers_bind_its_published_views(self):
        """BIND-PUBLISHED-VIEW, DATA-ENVIRONMENT-LAYOUT: by rule, by standard name; a catalog that needs quoting
        is quoted."""
        resolver = EnvironmentResolver(_Runner())
        env = Environment(name="e", catalog="cql_main", version="3")
        self.assertEqual(resolver.data(env).rule(), "cql_main.data")
        terminology = resolver.terminology(env)
        self.assertEqual(terminology.rule(), TableRelation(name="cql_main.knowledge.valueset"))
        # DATA-ENVIRONMENT-LAYOUT, DEC-NO-CONTENT-PUBLISHING: there is no store, and nothing here writes knowledge.
        self.assertFalse(hasattr(terminology, "store"))
        self.assertEqual(resolver.knowledge(env).identity(), "knowledge:cql_main")
        self.assertEqual(Environment(name="e", catalog="my-cat", version="3").schema("data"), "`my-cat`.data")


class DataResolverTest(unittest.TestCase):
    def test_a_schema_binds_by_rule(self):
        data = SchemaDataResolver("acme.data")
        self.assertEqual((data.rule(), data.identity(), data.types()), ("acme.data", "acme.data", None))
        self.assertEqual(data.retrieve(site("Patient")), TableRelation(name="acme.data.patient"))
        with self.assertRaises(ValueError):
            SchemaDataResolver("data")

    def test_tables_serve_their_types_by_name(self):
        tables = TableDataResolver({"Patient": "acme.fhir.patient"})
        self.assertEqual(tables.retrieve(site("Patient")), TableRelation(name="acme.fhir.patient"))
        self.assertEqual(tables.identity(), "tables:Patient=acme.fhir.patient")
        self.assertIsNone(tables.rule())
        with self.assertRaises(ValueError):
            TableDataResolver({"Patient": "patient"})  # a table is catalog.schema.table

    def test_volume_folder_finds_its_types_from_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("Patient.ndjson", "Condition.ndjson", "notes.txt"):
                open(os.path.join(d, name), "w").close()
            folder = VolumeDataResolver("/Volumes/acme/fhir/raw", types=None)
            folder.path = d  # the volume, as a notebook sees it through /Volumes
            self.assertEqual(folder.types(), {"Patient", "Condition"})
        bundles = VolumeDataResolver("/Volumes/acme/fhir/bundles", layout="bundles", types=["Patient"])
        self.assertEqual(bundles.retrieve(site("Patient")),
                         VolumeRelation(path="/Volumes/acme/fhir/bundles", fhir_type="Patient", layout="bundles"))
        with self.assertRaises(ValueError):
            VolumeDataResolver("/Volumes/acme/fhir/bundles", layout="bundles")
        with self.assertRaises(ValueError):
            VolumeDataResolver("/Workspace/Users/me/fhir")

    def test_dataframes_are_never_reusable(self):
        """CLIENT-DATA-RESOLVER: a DataFrame resolver is for development."""
        frames = DataFrameDataResolver({"Patient": object()}, name="mapping-v2")
        self.assertFalse(frames.reusable)
        self.assertEqual(frames.identity(), "dataframes:mapping-v2")
        self.assertIsInstance(frames.retrieve(site("Patient")), DataFrameRelation)


class TableTerminologyTest(unittest.TestCase):
    def test_find_code_reads_the_published_view_as_the_caller(self):
        """AGENT-VALUE-SET-LOOKUP."""
        run = _Runner({"SELECT DISTINCT valueset_id, version, system": [
            {"valueset_id": "urn:vs:a1c", "version": "1", "system": "http://loinc.org"}]})
        found = TableTerminologyResolver("acme.terminology.valueset", run).find_code("4548-4", "http://loinc.org")
        self.assertEqual(found, [CodeMatch(id="urn:vs:a1c", version="1", system="http://loinc.org")])
        statement, params = run.statements[0]
        self.assertIn("`acme`.`terminology`.`valueset`", statement)
        self.assertEqual(params, {"code": "4548-4", "system": "http://loinc.org"})

    def test_a_table_of_value_sets_is_only_read(self):
        """DEC-NO-CONTENT-PUBLISHING: the client writes no terminology."""
        self.assertFalse(hasattr(TableTerminologyResolver("acme.knowledge.valueset", _Runner()), "publish"))


class FolderTerminologyTest(unittest.TestCase):
    def test_a_value_set_must_carry_its_expansion(self):
        """CLIENT-TERMINOLOGY-RESOLVER: one with only compose fails, naming it."""
        with self.assertRaises(ValueError) as caught:
            parse_value_set({"resourceType": "ValueSet", "url": "urn:vs:compose", "compose": {}}, "vs.json")
        self.assertIn("urn:vs:compose", str(caught.exception))
        vs = parse_value_set({"resourceType": "ValueSet", "url": "urn:vs:a", "version": "2", "expansion": {
            "contains": [{"system": "s", "code": "a", "contains": [{"system": "s", "code": "a1"}]}]}}, "vs.json")
        self.assertEqual([c.code for c in vs.codes], ["a", "a1"])

    def test_a_volume_folder_binds_by_rule_and_a_workspace_folder_needs_spark(self):
        volume = FolderTerminologyResolver("/Volumes/acme/vs/json")
        self.assertEqual(volume.rule(), VolumeRelation(path="/Volumes/acme/vs/json", fhir_type="ValueSet",
                                                       layout="files"))
        with self.assertRaises(ValueError):
            FolderTerminologyResolver("/Workspace/Users/me/vs")  # needs spark=

    def test_find_code_reads_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a1c.json"), "w") as f:
                json.dump({"resourceType": "ValueSet", "url": "urn:vs:a1c",
                           "expansion": {"contains": [{"system": "http://loinc.org", "code": "4548-4"}]}}, f)
            folder = FolderTerminologyResolver("/Volumes/acme/vs/json")
            folder.path = d
            self.assertEqual(folder.find_code("4548-4", None),
                             [CodeMatch(id="urn:vs:a1c", version=None, system="http://loinc.org")])
            self.assertEqual(folder.find_code("4548-4", "http://snomed.info/sct"), [])


if __name__ == "__main__":
    unittest.main()
