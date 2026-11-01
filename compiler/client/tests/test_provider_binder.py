"""CLIENT-DATA-PROVIDER, BIND-PROVIDER-REQUEST, CLIENT-PROVIDER-IDENTITY: each compile binds through its providers,
asking describe with the compile's own request.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from cql_databricks.compiler.binding import bound_request
from cql_databricks.compiler.data_providers import Frames, Tables
from cql_databricks.compiler.provider_binder import ProviderBinder
from cql_databricks.compiler.providers import CompileRequest, LibraryGraph
from cql_databricks.compiler.relations import ProviderError
from cql_databricks.compiler.roles import Retrieve, RetrieveRequest, TableRelation, TerminologySource, ValueSetRef


class _Service:
    def __init__(self, retrieves):
        self.retrieves, self.described = retrieves, []

    def describe(self, request):
        self.described.append(request)
        return LibraryGraph(defines=[], dependencies={}, boundaries=[], materialized_defines=[], compiler_version="t",
                            retrieves=self.retrieves, value_sets=(ValueSetRef(id="urn:vs"),))


class _Terms(TerminologySource):
    def identity(self):
        return "acme.vs"

    def expansions(self, refs):
        return TableRelation(name="acme.vs.valueset")

    def find_code(self, code, system):
        return []


PATIENT = Retrieve(id="Patient_0c41d2e6a95f", request=RetrieveRequest(fhir_type="Patient"))


def _library(binder, service):
    return SimpleNamespace(binder=binder, provider=service)


class ProviderBinderTest(unittest.TestCase):
    def test_a_compile_sends_each_retrieve_its_relation_and_the_terminology(self):
        service = _Service((PATIENT,))
        binder = ProviderBinder(data=Tables({"Patient": "acme.fhir.patient"}), terminology=_Terms(),
                                run=lambda: (lambda s, p=None: []), schemas=lambda types: {})
        request = CompileRequest(cql="library L", target="Initial Population", parameters={"P": "1"})
        bound = bound_request(_library(binder, service), request, lambda: None)
        # BIND-PROVIDER-REQUEST: describe is asked with the compile's own request (target, parameters).
        self.assertIs(service.described[0], request)
        self.assertEqual(bound.data, {PATIENT.id: {"table": "acme.fhir.patient",
                                                   "identity": "tables:Patient=acme.fhir.patient"}})
        self.assertEqual(bound.terminology, [{"table": "acme.vs.valueset", "identity": "acme.vs"}])
        self.assertIn("data", bound.to_json())

    def test_providers_may_be_factories_called_on_first_compile(self):
        made = []
        binder = ProviderBinder(data=lambda: made.append(1) or Tables({"Patient": "acme.fhir.patient"}),
                                run=lambda: (lambda s, p=None: []), schemas=lambda types: {})
        self.assertEqual(made, [])
        bound_request(_library(binder, _Service((PATIENT,))), CompileRequest(cql="x"), lambda: None)
        bound_request(_library(binder, _Service((PATIENT,))), CompileRequest(cql="x"), lambda: None)
        self.assertEqual(made, [1])

    def test_a_durable_compile_refuses_a_dev_provider(self):
        """CLIENT-PROVIDER-IDENTITY: a run with a provider that is not reusable is never kept."""
        binder = ProviderBinder(data=Frames({"Patient": SimpleNamespace(schema=None)}), run=lambda: None,
                                schemas=lambda types: {}, spark=None)
        with self.assertRaises(ProviderError):
            bound_request(_library(binder, _Service(())), CompileRequest(cql="x"), lambda: None, durable=True)

    def test_a_dev_provider_never_reuses_a_cached_compile(self):
        frames = ProviderBinder(data=Frames({"Patient": object()}), run=lambda: None, schemas=lambda t: {})
        self.assertNotEqual(frames.generation(), frames.generation())
        tables = ProviderBinder(data=Tables({"Patient": "a.b.c"}), run=lambda: None, schemas=lambda t: {})
        self.assertEqual(tables.generation(), tables.generation())

    def test_an_older_service_that_reports_no_retrieves_fails(self):
        service = _Service(None)
        binder = ProviderBinder(data=Tables({"Patient": "a.b.c"}), run=lambda: None, schemas=lambda t: {})
        with self.assertRaises(Exception) as caught:
            bound_request(_library(binder, service), CompileRequest(cql="x"), lambda: None)
        self.assertIn("BIND-PROVIDER-REQUEST", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
