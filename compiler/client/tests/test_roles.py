"""CLIENT-DATA-PROVIDER, CLIENT-TERMINOLOGY-PROVIDER, CLIENT-KNOWLEDGE-PROVIDER, CLIENT-PROVIDER-OVERLAY,
CLIENT-PROVIDER-READ-WRITE, BIND-RETRIEVE-REQUEST, BIND-RELATION: the provider interfaces in the client core.
"""
from __future__ import annotations

import subprocess
import sys
import unittest

from cql_databricks.compiler.libraries import LibraryRef, LibrarySource
from cql_databricks.compiler.roles import (Code, DataFrameRelation, DataProvider, KnowledgeSource, Layered, Overlay,
                                           Retrieve, RetrieveRequest, TableRelation, TerminologySource,
                                           TerminologyStore, ValueSetRef, VolumeRelation, terminology_relations)


class _Tables(DataProvider):
    def __init__(self, name, types, *, reusable=True):
        self.name, self._types, self.reusable = name, frozenset(types), reusable

    def identity(self):
        return self.name

    def types(self):
        return self._types

    def retrieve(self, request):
        return TableRelation(name=f"{self.name}.retrieve_{request.fhir_type}")


class _Terms(TerminologySource):
    def __init__(self, name, sets):
        self.name, self.sets = name, sets

    def identity(self):
        return self.name

    def expansions(self, refs):
        return TableRelation(name=f"{self.name}.valueset")

    def find_code(self, code, system):
        return [ValueSetRef(id=vs) for vs, codes in self.sets.items() if code in codes]


class _Libraries(KnowledgeSource):
    def __init__(self, name, libs):
        self.name, self.libs = name, libs

    def identity(self):
        return self.name

    def library(self, name, version):
        cql = self.libs.get(name)
        return LibrarySource(name, version, cql=cql, origin=self.name) if cql else None

    def measure(self, url, version):
        return None

    def libraries(self):
        return [LibraryRef(n) for n in self.libs]


class RetrieveRequestTest(unittest.TestCase):
    def test_request_from_describe_ignores_unknown_fields(self):
        """BIND-RETRIEVE-REQUEST: a field this client doesn't know (a newer compiler's, or a retired one) is
        ignored, so an existing provider keeps working."""
        raw = {"fhirType": "Observation", "valueSet": "urn:vs:a1c", "codes": [{"system": "s", "code": "c"}],
               "dateLow": "2026-01-01", "parameters": {"Measurement Period": {"low": "2026-01-01"}},
               "codeProperty": "code", "somethingNew": 1}
        request = RetrieveRequest.from_dict(raw)
        self.assertEqual(request.fhir_type, "Observation")
        self.assertEqual(request.value_set, "urn:vs:a1c")
        self.assertEqual(request.codes, (Code(system="s", code="c"),))
        self.assertEqual(request.date_low, "2026-01-01")
        self.assertIsNone(request.date_high)
        self.assertEqual(request.parameters, {"Measurement Period": {"low": "2026-01-01"}})

    def test_only_fhir_type_is_required(self):
        """BIND-RETRIEVE-REQUEST: every field but the type has a default."""
        self.assertEqual(RetrieveRequest(fhir_type="Patient").field_names(),
                         {"fhir_type", "value_set", "codes", "date_property", "date_low", "date_high",
                          "parameters"})

    def test_retrieve_carries_its_id(self):
        retrieve = Retrieve.from_dict({"id": "Patient_0c41d2e6a95f", "request": {"fhirType": "Patient"}})
        self.assertEqual(retrieve.id, "Patient_0c41d2e6a95f")
        self.assertEqual(retrieve.request.fhir_type, "Patient")


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
        """CLIENT-PROVIDER-OVERLAY: per FHIR type for data."""
        overlay = Overlay(_Tables("dev", ["Observation"]), _Tables("base", ["Patient", "Observation"]))
        self.assertIsInstance(overlay, DataProvider)
        self.assertEqual(overlay.types(), {"Patient", "Observation"})
        self.assertEqual(overlay.retrieve(RetrieveRequest(fhir_type="Observation")).name,
                         "dev.retrieve_Observation")
        self.assertEqual(overlay.retrieve(RetrieveRequest(fhir_type="Patient")).name, "base.retrieve_Patient")
        self.assertEqual(overlay.identity(), "overlay(dev,base)")
        self.assertIsNone(overlay.change_log())

    def test_overlay_is_not_reusable_when_any_layer_is_not(self):
        """CLIENT-PROVIDER-OVERLAY, CLIENT-PROVIDER-IDENTITY: one dev layer makes the whole run unkept."""
        self.assertTrue(Overlay(_Tables("a", ["Patient"]), _Tables("b", ["Patient"])).reusable)
        self.assertFalse(Overlay(_Tables("a", ["Patient"], reusable=False), _Tables("b", ["Patient"])).reusable)
        self.assertFalse(Overlay(_Tables("a", ["Patient"]), _Tables("b", ["Patient"], reusable=False)).reusable)

    def test_overlays_nest(self):
        inner = Overlay(_Tables("a", ["Condition"]), _Tables("b", ["Patient"]))
        outer = Overlay(_Tables("dev", ["Observation"], reusable=False), inner)
        self.assertEqual(outer.identity(), "overlay(dev,overlay(a,b))")
        self.assertEqual(outer.retrieve(RetrieveRequest(fhir_type="Condition")).name, "a.retrieve_Condition")
        self.assertFalse(outer.reusable)

    def test_terminology_overlay_layers_dev_before_base(self):
        """CLIENT-PROVIDER-OVERLAY: per value set for terminology. The request reads a value set from the first
        relation that holds it (BIND-PROVIDER-REQUEST), so dev's set replaces base's."""
        overlay = Overlay(_Terms("dev", {"vs1": {"c1"}}), Overlay(_Terms("mid", {}), _Terms("base", {"vs2": {"c1"}})))
        self.assertIsInstance(overlay, TerminologySource)
        layered = overlay.expansions([ValueSetRef(id="vs1")])
        self.assertIsInstance(layered, Layered)
        self.assertEqual([r.name for r in terminology_relations(layered)],
                         ["dev.valueset", "mid.valueset", "base.valueset"])
        self.assertEqual([r.id for r in overlay.find_code("c1", None)], ["vs1", "vs2"])

    def test_single_terminology_source_is_one_relation(self):
        self.assertEqual(terminology_relations(_Terms("t", {}).expansions([])),
                         (TableRelation(name="t.valueset"),))

    def test_knowledge_overlay_serves_each_library_from_the_first_layer_that_has_it(self):
        """CLIENT-PROVIDER-OVERLAY: per library for knowledge."""
        overlay = Overlay(_Libraries("cells", {"A": "library A"}),
                          _Libraries("store", {"A": "library A version '2'", "B": "library B"}))
        self.assertIsInstance(overlay, KnowledgeSource)
        self.assertEqual(overlay.library("A", None).origin, "cells")
        self.assertEqual(overlay.library("B", None).origin, "store")
        self.assertIsNone(overlay.library("C", None))
        self.assertEqual(list(overlay.libraries()), [LibraryRef("A"), LibraryRef("B")])

    def test_overlay_of_two_roles_is_refused(self):
        with self.assertRaises(TypeError):
            Overlay(_Tables("a", ["Patient"]), _Terms("t", {}))

    def test_an_overlay_is_not_a_store(self):
        """CLIENT-PROVIDER-READ-WRITE: publishing goes to a store, never through an overlay of sources."""
        overlay = Overlay(_Terms("dev", {}), _Terms("base", {}))
        self.assertNotIsInstance(overlay, TerminologyStore)
        self.assertFalse(hasattr(overlay, "publish"))


class CoreNeutralityTest(unittest.TestCase):
    def test_roles_import_no_platform_sdk_or_spark(self):
        """CLIENT-DATA-PROVIDER: the interfaces live in the client core, which imports no platform SDK."""
        code = ("import sys; import cql_databricks.compiler.roles; "
                "bad = [m for m in sys.modules if m.split('.')[0] in ('databricks', 'pyspark')]; "
                "print(','.join(bad)); sys.exit(1 if bad else 0)")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
