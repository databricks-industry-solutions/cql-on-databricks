"""BIND-PROVIDER-REQUEST, BIND-RELATION, BIND-NAMED-ARGS, CLIENT-DATA-PROVIDER, CLIENT-TERMINOLOGY-PROVIDER,
CLIENT-PROVIDER-IDENTITY: a run's providers turned into the compile request's relations.
"""
from __future__ import annotations

import unittest

from pyspark.sql.types import StringType, StructField, StructType

from cql_databricks.compiler.providers import ResourceSchema
from cql_databricks.compiler.relations import VIEW_PREFIX, ProviderError, bind
from cql_databricks.compiler.roles import (DataFrameRelation, DataProvider, FunctionRelation, Overlay, Retrieve,
                                           RetrieveRequest, TableRelation, TerminologySource, ValueSetRef,
                                           VolumeRelation)

PATIENT = ResourceSchema(fhir_type="Patient",
                         struct=StructType([StructField(n, StringType()) for n in
                                            ("id", "gender", "__resource_type", "__resource_id")]),
                         ddl="id STRING, gender STRING, __resource_type STRING, __resource_id STRING",
                         patient_context=True)


def _schemas(types):
    return {t: PATIENT for t in types}


class _Runner:
    """Records statements; answers the parameters query from ``functions``."""

    def __init__(self, functions=None):
        self.functions = functions or {}
        self.statements = []

    def __call__(self, statement, params=None):
        self.statements.append((statement, dict(params or {})))
        if "information_schema.routines" in statement:
            rows = []
            for name, declared in self.functions.items():
                catalog, schema, routine = name.split(".")
                if (catalog, schema) != (params["catalog"], params["schema"]):
                    continue
                if not declared:
                    rows.append({"routine": routine, "parameter": None, "position": None, "dflt": None})
                for i, (parameter, default) in enumerate(declared, start=1):
                    rows.append({"routine": routine, "parameter": parameter, "position": i, "dflt": default})
            return rows
        return []


class _Frame:
    def __init__(self):
        self.schema = StructType([StructField("id", StringType()), StructField("gender", StringType())])
        self.views = []

    def createOrReplaceTempView(self, name):
        self.views.append(name)


class _Provider(DataProvider):
    def __init__(self, relations, *, name="acme.fhir", reusable=True):
        self.relations, self.name, self.reusable = relations, name, reusable

    def identity(self):
        return self.name

    def types(self):
        return frozenset(self.relations)

    def retrieve(self, request):
        return self.relations[request.fhir_type]


class _Terms(TerminologySource):
    def __init__(self, name):
        self.name = name

    def identity(self):
        return self.name

    def expansions(self, refs):
        return TableRelation(name=f"{self.name}.valueset")

    def find_code(self, code, system):
        return []


def _retrieve(rid, fhir_type, **fields):
    return Retrieve(id=rid, request=RetrieveRequest(fhir_type=fhir_type, **fields))


class BindTest(unittest.TestCase):
    def test_table_and_function_relations_are_sent_by_name_with_the_providers_identity(self):
        """BIND-RELATION, BIND-NAMED-ARGS: a function takes only the declared parameters that name request
        fields; one with a default that names none is left to it."""
        run = _Runner({"acme.fhir.retrieve_observation": [("value_set", "NULL"), ("code_property", "NULL"),
                                                          ("date_low", "NULL")]})
        provider = _Provider({"Patient": TableRelation(name="acme.fhir.retrieve_patient"),
                              "Observation": FunctionRelation(name="acme.fhir.retrieve_Observation")})
        bound = bind([_retrieve("Patient_a", "Patient"), _retrieve("Observation_b", "Observation")], [],
                     data=provider, terminology=None, run=run, schemas=_schemas)
        self.assertEqual(bound.data, {
            "Patient_a": {"table": "acme.fhir.retrieve_patient", "identity": "acme.fhir"},
            "Observation_b": {"function": "acme.fhir.retrieve_Observation", "parameters": ["value_set", "date_low"],
                              "identity": "acme.fhir"}})
        self.assertTrue(bound.reusable)
        self.assertEqual(bound.views, ())
        # One parameters query per schema, as the caller.
        self.assertEqual(sum("information_schema" in s for s, _ in run.statements), 1)

    def test_a_function_with_no_parameters_is_called_with_none(self):
        run = _Runner({"acme.fhir.retrieve_patient": []})
        bound = bind([_retrieve("Patient_a", "Patient")], [], run=run, schemas=_schemas, terminology=None,
                     data=_Provider({"Patient": FunctionRelation(name="acme.fhir.retrieve_patient")}))
        self.assertEqual(bound.data["Patient_a"]["parameters"], [])

    def test_a_parameter_no_request_field_supplies_fails_before_the_run(self):
        """BIND-NAMED-ARGS: a declared parameter that names no request field and has no default."""
        run = _Runner({"acme.fhir.retrieve_patient": [("tenant", None)]})
        with self.assertRaises(ProviderError) as caught:
            bind([_retrieve("Patient_a", "Patient")], [], run=run, schemas=_schemas, terminology=None,
                 data=_Provider({"Patient": FunctionRelation(name="acme.fhir.retrieve_patient")}))
        self.assertIn("tenant", str(caught.exception))

    def test_a_missing_function_fails_before_the_run(self):
        with self.assertRaises(ProviderError) as caught:
            bind([_retrieve("Patient_a", "Patient")], [], run=_Runner(), schemas=_schemas, terminology=None,
                 data=_Provider({"Patient": FunctionRelation(name="acme.fhir.retrieve_patient")}))
        self.assertIn("acme.fhir.retrieve_patient", str(caught.exception))

    def test_a_type_the_provider_does_not_serve_fails_naming_it(self):
        """CLIENT-DATA-PROVIDER: a retrieved type outside types() fails before the run."""
        with self.assertRaises(ProviderError) as caught:
            bind([_retrieve("Condition_a", "Condition")], [], run=_Runner(), schemas=_schemas, terminology=None,
                 data=_Provider({"Patient": TableRelation(name="acme.fhir.patient")}))
        self.assertIn("Condition", str(caught.exception))
        self.assertIn("acme.fhir", str(caught.exception))

    def test_retrieves_with_no_data_provider_fail(self):
        with self.assertRaises(ProviderError):
            bind([_retrieve("Patient_a", "Patient")], [], data=None, terminology=None, run=_Runner(),
                 schemas=_schemas)

    def test_data_free_cql_binds_nothing(self):
        bound = bind([], [], data=None, terminology=None, run=_Runner(), schemas=_schemas)
        self.assertEqual((bound.data, bound.terminology, bound.reusable), ({}, [], True))

    def test_a_dataframe_on_a_warehouse_fails_naming_the_provider(self):
        """BIND-RELATION: one path, no silent fallback: nothing is materialized for the warehouse."""
        provider = _Provider({"Patient": DataFrameRelation(frame=_Frame(), fhir_type="Patient")},
                             name="dev-frames", reusable=False)
        with self.assertRaises(ProviderError) as caught:
            bind([_retrieve("Patient_a", "Patient")], [], data=provider, terminology=None, run=_Runner(),
                 schemas=_schemas, spark=None)
        self.assertIn("dev-frames", str(caught.exception))
        self.assertIn("warehouse", str(caught.exception))

    def test_a_dataframe_on_spark_is_reshaped_into_a_view_the_client_owns(self):
        """BIND-RELATION, BIND-RESHAPE: and a dev provider makes the run unkept (CLIENT-PROVIDER-IDENTITY)."""
        frame, run = _Frame(), _Runner()
        provider = _Provider({"Patient": DataFrameRelation(frame=frame, fhir_type="Patient")},
                             name="dev-frames", reusable=False)
        bound = bind([_retrieve("Patient_a", "Patient")], [], data=provider, terminology=None, run=run,
                     schemas=_schemas, spark=object())
        view = bound.data["Patient_a"]["table"]
        self.assertTrue(view.startswith(VIEW_PREFIX))
        self.assertEqual(frame.views, [bound.views[0]])
        created = [s for s, _ in run.statements if s.startswith("CREATE OR REPLACE TEMPORARY VIEW")]
        self.assertEqual(len(created), 1)
        self.assertIn(f"`{view}`", created[0])
        self.assertIn("named_struct(", created[0])
        self.assertFalse(bound.reusable)

    def test_a_volume_relation_reads_its_ndjson_through_the_reshape(self):
        run = _Runner()
        provider = _Provider({"Patient": VolumeRelation(path="/Volumes/c/s/v/fhir", fhir_type="Patient")})
        bound = bind([_retrieve("Patient_a", "Patient")], [], data=provider, terminology=None, run=run,
                     schemas=_schemas)
        sql = run.statements[0][0]
        self.assertIn("read_files('/Volumes/c/s/v/fhir/Patient.ndjson', format => 'text')", sql)
        self.assertIn("from_json(", sql)
        self.assertEqual(bound.views, (bound.data["Patient_a"]["table"],))

    def test_a_volume_of_bundles_keeps_the_entries_of_the_type(self):
        run = _Runner()
        provider = _Provider({"Patient": VolumeRelation(path="/Volumes/c/s/v/bundles", fhir_type="Patient",
                                                        layout="bundles")})
        bind([_retrieve("Patient_a", "Patient")], [], data=provider, terminology=None, run=run, schemas=_schemas)
        sql = run.statements[0][0]
        self.assertIn("wholeText => true", sql)
        self.assertIn("get_json_object(`value`, '$.resourceType') = 'Patient'", sql)

    def test_terminology_layers_are_sent_in_order_with_each_identity(self):
        """BIND-PROVIDER-REQUEST, CLIENT-PROVIDER-OVERLAY: the first layer that holds a value set serves it."""
        bound = bind([], [ValueSetRef(id="urn:vs:a")], data=None, run=_Runner(), schemas=_schemas,
                     terminology=Overlay(_Terms("dev"), _Terms("acme.terminology")))
        self.assertEqual(bound.terminology, [
            {"table": "dev.valueset", "identity": "overlay(dev,acme.terminology)"},
            {"table": "acme.terminology.valueset", "identity": "overlay(dev,acme.terminology)"}])


    def test_a_volume_of_value_sets_becomes_a_view_and_refuses_compose_only_sets(self):
        """CLIENT-TERMINOLOGY-PROVIDER: a value set must carry its expansion."""
        from cql_databricks.compiler.terminology_providers import ValueSetFolder

        run = _Runner()
        bound = bind([], [ValueSetRef(id="urn:vs:a")], data=None, run=run, schemas=_schemas,
                     terminology=ValueSetFolder("/Volumes/acme/vs/json"))
        self.assertTrue(bound.terminology[0]["table"].startswith(VIEW_PREFIX))
        self.assertIn("explode(concat(", run.statements[-1][0])

        class _Bare(_Runner):
            def __call__(self, statement, params=None):
                super().__call__(statement, params)
                return [{"url": "urn:vs:compose"}] if "IS NULL" in statement else []

        bare = _Bare()
        with self.assertRaises(ProviderError) as caught:
            bind([], [], data=None, run=bare, schemas=_schemas, terminology=ValueSetFolder("/Volumes/acme/vs/json"))
        self.assertIn("urn:vs:compose", str(caught.exception))
        self.assertFalse(any(s.startswith("CREATE") for s, _ in bare.statements))

if __name__ == "__main__":
    unittest.main()
