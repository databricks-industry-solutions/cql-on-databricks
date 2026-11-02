"""BIND-CALL-SITE: the one-field-per-role compile request on the wire, with rules and site maps, and the call sites
``describe`` returns for a resolver that binds per site.
"""
from __future__ import annotations

import unittest

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import CompileRequest, RemoteProvider
from cql_databricks.compiler.resolvers import CallSite


class RoleRequestTest(unittest.TestCase):
    def test_role_request_carries_one_field_per_role(self):
        request = CompileRequest(
            cql="library L",
            data={"rule": {"schema": "acme.data", "identity": "acme.data"},
                  "sites": {"retrieve_0c41d2e6a95f": {"table": "hist.data.patient", "identity": "hist.data"}}},
            terminology={"rule": [{"table": "acme.terminology.valueset", "identity": "acme.terminology"}]},
            deps=(LibrarySource("Helpers", "1.0.0", cql="library Helpers version '1.0.0'"),),
            udf_schema="acme.runtime")
        body = request.to_json()
        self.assertEqual(body["data"]["rule"], {"identity": "acme.data", "schema": "acme.data"})
        self.assertEqual(body["data"]["sites"]["retrieve_0c41d2e6a95f"]["table"], "hist.data.patient")
        self.assertEqual(body["terminology"], {"rule": [{"identity": "acme.terminology",
                                                         "table": "acme.terminology.valueset"}]})
        self.assertEqual(body["knowledge"], [{"cql": "library Helpers version '1.0.0'", "name": "Helpers",
                                              "version": "1.0.0"}])
        self.assertEqual(body["runtime"], {"schema": "acme.runtime"})
        for replaced in ("sources", "dataSource", "deps", "libraries"):
            self.assertNotIn(replaced, body)

    def test_a_request_that_binds_nothing_sends_no_role(self):
        """CQL that retrieves nothing binds no relation; the compiler refuses a retrieve in it, naming the site."""
        body = CompileRequest(cql="define X: 1").to_json()
        for role in ("data", "terminology", "knowledge"):
            self.assertNotIn(role, body)

    def test_the_fields_the_roles_replaced_are_gone(self):
        for retired in ("sources", "data_source", "terminology_table", "libraries"):
            with self.assertRaises(TypeError):
                CompileRequest(cql="x", **{retired: "x"})


class DescribeSitesTest(unittest.TestCase):
    def test_describe_returns_each_call_site_with_its_context(self):
        class Provider(RemoteProvider):
            def _post(self, path, request):
                return {"defines": [], "retrieveTypes": ["Observation"], "sites": [
                    {"id": "retrieve_3f2a9c01b7de", "kind": "retrieve", "library": "L", "version": "1.0.0",
                     "define": "Tests", "request": {"fhirType": "Observation", "valueSet": "urn:vs:a1c", "codes": [],
                                                    "dateProperty": None, "dateLow": None, "dateHigh": None,
                                                    "parameters": {}}},
                    {"id": "valueset_9a", "kind": "valueset", "library": "L", "version": "1.0.0", "define": "Tests",
                     "request": {"valueSet": "urn:vs:a1c", "version": None}}],
                        "valueSets": [{"id": "urn:vs:a1c", "version": None}]}

        graph = Provider("http://compile.example").describe(CompileRequest(cql="x"))
        self.assertEqual([s.id for s in graph.sites], ["retrieve_3f2a9c01b7de", "valueset_9a"])
        self.assertEqual([s.kind for s in graph.sites], [CallSite.RETRIEVE, CallSite.VALUE_SET])
        self.assertEqual(graph.sites[0].request.value_set, "urn:vs:a1c")
        self.assertEqual(graph.sites[0].define, "Tests")
        self.assertEqual([v.id for v in graph.value_sets], ["urn:vs:a1c"])

    def test_an_older_service_reports_no_sites(self):
        class Provider(RemoteProvider):
            def _post(self, path, request):
                return {"defines": []}

        self.assertIsNone(Provider("http://compile.example").describe(CompileRequest(cql="x")).sites)


if __name__ == "__main__":
    unittest.main()
