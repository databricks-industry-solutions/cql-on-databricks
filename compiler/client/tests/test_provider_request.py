"""BIND-PROVIDER-REQUEST: the one-field-per-role compile request on the wire, and the retrieves ``describe``
returns for the client to bind.
"""
from __future__ import annotations

import unittest

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import CompileRequest, RemoteProvider


class RoleRequestTest(unittest.TestCase):
    def test_role_request_carries_one_field_per_role(self):
        request = CompileRequest(
            cql="library L", data={"Patient_0c41d2e6a95f": {"table": "acme.fhir.patient", "identity": "acme.fhir"}},
            terminology=[{"table": "acme.terminology.valueset", "identity": "acme.terminology"}],
            deps=(LibrarySource("Helpers", "1.0.0", cql="library Helpers version '1.0.0'"),),
            udf_schema="acme.runtime")
        body = request.to_json()
        self.assertEqual(body["data"], {"Patient_0c41d2e6a95f": {"table": "acme.fhir.patient",
                                                                 "identity": "acme.fhir"}})
        self.assertEqual(body["terminology"], [{"table": "acme.terminology.valueset", "identity": "acme.terminology"}])
        self.assertEqual(body["knowledge"], [{"cql": "library Helpers version '1.0.0'", "name": "Helpers",
                                              "version": "1.0.0"}])
        self.assertEqual(body["runtime"], {"schema": "acme.runtime"})
        for replaced in ("sources", "dataSource", "deps", "libraries"):
            self.assertNotIn(replaced, body)

    def test_data_free_role_request_still_sends_the_roles(self):
        """CQL that retrieves nothing binds no relation, and still says so."""
        body = CompileRequest(cql="define X: 1", data={}).to_json()
        self.assertEqual((body["data"], body["terminology"], body["knowledge"]), ({}, [], []))

    def test_role_request_refuses_a_field_it_replaces(self):
        with self.assertRaises(ValueError) as caught:
            CompileRequest(cql="x", data={}, sources={"Patient": {"function": "f"}}).to_json()
        self.assertIn("sources", str(caught.exception))
        with self.assertRaises(ValueError):
            CompileRequest(cql="x", data={}, terminology_table="acme.vs").to_json()

    def test_a_request_without_data_keeps_its_old_form(self):
        body = CompileRequest(cql="x", udf_schema="acme.runtime").to_json()
        self.assertEqual(body["sources"], {"udfs": {"schema": "acme.runtime"}})
        self.assertNotIn("data", body)


class DescribeRetrievesTest(unittest.TestCase):
    def test_describe_returns_each_retrieve_with_its_id_and_request(self):
        class Provider(RemoteProvider):
            def _post(self, path, request):
                return {"defines": [], "retrieveTypes": ["Observation"], "retrieves": [
                    {"id": "Observation_3f2a9c01b7de",
                     "request": {"fhirType": "Observation", "valueSet": "urn:vs:a1c", "codes": [],
                                 "dateProperty": None, "dateLow": None, "dateHigh": None, "parameters": {}}}],
                        "valueSets": [{"id": "urn:vs:a1c", "version": None}]}

        graph = Provider("http://compile.example").describe(CompileRequest(cql="x"))
        self.assertEqual([r.id for r in graph.retrieves], ["Observation_3f2a9c01b7de"])
        self.assertEqual(graph.retrieves[0].request.value_set, "urn:vs:a1c")
        self.assertEqual([v.id for v in graph.value_sets], ["urn:vs:a1c"])

    def test_an_older_service_reports_no_retrieves(self):
        class Provider(RemoteProvider):
            def _post(self, path, request):
                return {"defines": []}

        self.assertIsNone(Provider("http://compile.example").describe(CompileRequest(cql="x")).retrieves)


if __name__ == "__main__":
    unittest.main()
