"""CLIENT-RESOLVERS, CLIENT-DATA-RESOLVER, CLIENT-TERMINOLOGY-RESOLVER, CLIENT-KNOWLEDGE-RESOLVER,
CLIENT-RESOLVER-OVERLAY, CLIENT-RESOLVER-READ-WRITE, BIND-CALL-SITE, BIND-RETRIEVE-REQUEST, BIND-RELATION: the resolver
interfaces in the client core.
"""
from __future__ import annotations

import subprocess
import sys
import unittest

from cql_databricks.compiler.libraries import LibraryRef, LibrarySource
from cql_databricks.resolvers.interfaces import (CallSite, Code, DataFrameRelation, DataResolver, KnowledgeResolver,
                                               Layered, Overlay, RetrieveRequest, TableRelation, TerminologyResolver,
                                               TerminologyStore, ValueSetRef, VolumeRelation, layers)


def site(fhir_type: str, define: str | None = "D") -> CallSite:
    return CallSite(id=f"retrieve_{fhir_type.lower()}", kind=CallSite.RETRIEVE, library="L", version="1",
                    define=define, request=RetrieveRequest(fhir_type=fhir_type))


class _Tables(DataResolver):
    def __init__(self, name, types, *, reusable=True):
        self.name, self._types, self.reusable = name, frozenset(types), reusable

    def identity(self):
        return self.name

    def types(self):
        return self._types

    def retrieve(self, site):
        return TableRelation(name=f"{self.name}.{site.request.fhir_type.lower()}")


class _Schema(DataResolver):
    def identity(self):
        return "acme.data"

    def rule(self):
        return "acme.data"


class _Terms(TerminologyResolver):
    def __init__(self, name, sets):
        self.name, self.sets = name, sets

    def identity(self):
        return self.name

    def rule(self):
        return TableRelation(name=f"{self.name}.valueset")

    def find_code(self, code, system):
        return [ValueSetRef(id=vs) for vs, codes in self.sets.items() if code in codes]


class _Libraries(KnowledgeResolver):
    def __init__(self, name, libs):
        self.name, self.libs = name, libs

    def identity(self):
        return self.name

    def library(self, name, version, site=None):
        cql = self.libs.get(name)
        return LibrarySource(name, version, cql=cql, origin=self.name) if cql else None

    def measure(self, url, version):
        return None

    def libraries(self):
        return [LibraryRef(n) for n in self.libs]


class CallSiteTest(unittest.TestCase):
    def test_request_from_describe_ignores_unknown_fields(self):
        """BIND-RETRIEVE-REQUEST: a field this client doesn't know (a newer compiler's, or a retired one) is ignored,
        so an existing resolver keeps working."""
        raw = {"fhirType": "Observation", "valueSet": "urn:vs:a1c", "codes": [{"system": "s", "code": "c"}],
               "dateLow": "2026-01-01", "parameters": {"Measurement Period": {"low": "2026-01-01"}},
               "codeProperty": "code", "somethingNew": 1}
        request = RetrieveRequest.from_dict(raw)
        self.assertEqual((request.fhir_type, request.value_set, request.codes, request.date_low),
                         ("Observation", "urn:vs:a1c", (Code(system="s", code="c"),), "2026-01-01"))
        self.assertIsNone(request.date_high)
        self.assertEqual(request.parameters, {"Measurement Period": {"low": "2026-01-01"}})

    def test_sites_carry_their_context(self):
        """BIND-CALL-SITE: a retrieve site carries its request; a value-set site its value set; the context's own
        retrieve names no library or define."""
        retrieve = CallSite.from_dict({"id": "retrieve_0c41d2e6a95f", "kind": "retrieve", "library": "L",
                                       "version": "1.0.0", "define": "Tests", "request": {"fhirType": "Observation"}})
        self.assertEqual((retrieve.library, retrieve.version, retrieve.define), ("L", "1.0.0", "Tests"))
        self.assertEqual(retrieve.request.fhir_type, "Observation")
        vs = CallSite.from_dict({"id": "valueset_1", "kind": "valueset", "library": "L", "version": "1.0.0",
                                 "define": "Tests", "request": {"valueSet": "urn:vs:a1c", "version": None}})
        self.assertEqual(vs.request, ValueSetRef(id="urn:vs:a1c"))
        context = CallSite.from_dict({"id": "retrieve_2", "kind": "retrieve", "library": None, "version": None,
                                      "define": None, "request": {"fhirType": "Patient"}})
        self.assertIsNone(context.library)


class RuleTest(unittest.TestCase):
    def test_a_rule_answers_every_site_with_its_view(self):
        """BIND-CALL-SITE: a resolver that binds by rule answers a site with <schema>.<type in lower case>."""
        self.assertEqual(_Schema().retrieve(site("MedicationRequest")),
                         TableRelation(name="acme.data.medicationrequest"))
        self.assertIsNone(_Schema().types())

    def test_a_resolver_that_binds_neither_way_says_so(self):
        class _None(DataResolver):
            def identity(self):
                return "none"
        with self.assertRaises(NotImplementedError):
            _None().retrieve(site("Patient"))


class RelationTest(unittest.TestCase):
    def test_volume_relation_reads_only_volumes(self):
        """BIND-RELATION: a volume relation names a /Volumes folder; a workspace folder is never data."""
        VolumeRelation(path="/Volumes/c/s/v/fhir", fhir_type="Patient")
        with self.assertRaises(ValueError):
            VolumeRelation(path="/Workspace/Users/me/fhir", fhir_type="Patient")
        with self.assertRaises(ValueError):
            VolumeRelation(path="/Volumes/c/s/v/fhir", fhir_type="Patient", layout="csv")

    def test_dataframe_relation_repr_does_not_print_the_frame(self):
        self.assertEqual(repr(DataFrameRelation(frame=object(), fhir_type="Patient")),
                         "DataFrameRelation(fhir_type='Patient')")


class OverlayTest(unittest.TestCase):
    def test_data_overlay_serves_each_type_from_the_first_layer_that_has_it(self):
        """CLIENT-RESOLVER-OVERLAY: per FHIR type for data, over a base that binds by rule."""
        overlay = Overlay(_Tables("dev", ["Observation"]), _Schema())
        self.assertIsInstance(overlay, DataResolver)
        self.assertIsNone(overlay.rule(), "an overlay binds per site")
        self.assertEqual(overlay.retrieve(site("Observation")).name, "dev.observation")
        self.assertEqual(overlay.retrieve(site("Patient")).name, "acme.data.patient")
        self.assertEqual(overlay.identity(), "overlay(dev,acme.data)")
        self.assertIsNone(overlay.change_log())
        with self.assertRaises(TypeError):
            Overlay(_Schema(), _Tables("base", ["Patient"]))  # the development layer names its types

    def test_overlay_is_not_reusable_when_any_layer_is_not(self):
        """CLIENT-RESOLVER-OVERLAY, CLIENT-RESOLVER-IDENTITY: one dev layer makes the whole run unkept."""
        self.assertTrue(Overlay(_Tables("a", ["Patient"]), _Tables("b", ["Patient"])).reusable)
        self.assertFalse(Overlay(_Tables("a", ["Patient"], reusable=False), _Tables("b", ["Patient"])).reusable)
        self.assertFalse(Overlay(_Tables("a", ["Patient"]), _Tables("b", ["Patient"], reusable=False)).reusable)

    def test_terminology_overlay_binds_by_rule_when_both_layers_do(self):
        """CLIENT-RESOLVER-OVERLAY: per value set for terminology; the request reads a value set from the first
        relation that holds it (BIND-CALL-SITE), so dev's set replaces base's."""
        overlay = Overlay(_Terms("dev", {"vs1": {"c1"}}), Overlay(_Terms("mid", {}), _Terms("base", {"vs2": {"c1"}})))
        self.assertIsInstance(overlay, TerminologyResolver)
        rule = overlay.rule()
        self.assertIsInstance(rule, Layered)
        self.assertEqual([r.name for r in layers(rule)], ["dev.valueset", "mid.valueset", "base.valueset"])
        self.assertEqual([r.id for r in overlay.find_code("c1", None)], ["vs1", "vs2"])

    def test_knowledge_overlay_serves_each_library_from_the_first_layer_that_has_it(self):
        """CLIENT-RESOLVER-OVERLAY: per library for knowledge."""
        overlay = Overlay(_Libraries("cells", {"A": "library A"}),
                          _Libraries("store", {"A": "library A version '2'", "B": "library B"}))
        self.assertIsInstance(overlay, KnowledgeResolver)
        self.assertEqual(overlay.library("A", None).origin, "cells")
        self.assertEqual(overlay.library("B", None).origin, "store")
        self.assertIsNone(overlay.library("C", None))
        self.assertEqual(list(overlay.libraries()), [LibraryRef("A"), LibraryRef("B")])

    def test_overlay_of_two_roles_is_refused(self):
        with self.assertRaises(TypeError):
            Overlay(_Tables("a", ["Patient"]), _Terms("t", {}))

    def test_an_overlay_is_not_a_store(self):
        """CLIENT-RESOLVER-READ-WRITE: publishing goes to a store, never through an overlay."""
        overlay = Overlay(_Terms("dev", {}), _Terms("base", {}))
        self.assertNotIsInstance(overlay, TerminologyStore)
        self.assertFalse(hasattr(overlay, "publish"))


class CoreNeutralityTest(unittest.TestCase):
    def test_resolvers_import_no_platform_sdk_or_spark(self):
        """CLIENT-RESOLVERS: the interfaces live in the client core, which imports no platform SDK."""
        code = ("import sys; import cql_databricks.resolvers.interfaces, cql_databricks.resolvers.environment; "
                "bad = [m for m in sys.modules if m.split('.')[0] in ('databricks', 'pyspark')]; "
                "print(','.join(bad)); sys.exit(1 if bad else 0)")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
