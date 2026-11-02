"""BIND-CALL-SITE, BIND-RELATION, CLIENT-DATA-RESOLVER, CLIENT-TERMINOLOGY-RESOLVER, CLIENT-RESOLVER-IDENTITY: a run's
resolvers turned into the compile request's rules and site maps, and the binder that does it at each compile.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from pyspark.sql.types import StringType, StructField, StructType

from cql_databricks.compiler.binding import bound_request
from cql_databricks.resolvers.data import DataFrameDataResolver, SchemaDataResolver, TableDataResolver
from cql_databricks.compiler.providers import CompileRequest, LibraryGraph, ResourceSchema
from cql_databricks.compiler.relations import VIEW_PREFIX, ResolverError, bind
from cql_databricks.compiler.resolver_binder import ResolverBinder
from cql_databricks.resolvers.interfaces import (CallSite, DataFrameRelation, DataResolver, Overlay, RetrieveRequest,
                                               TableRelation, TerminologyResolver, ValueSetRef, VolumeRelation)
from cql_databricks.resolvers.terminology import FolderTerminologyResolver, TableTerminologyResolver

PATIENT = ResourceSchema(fhir_type="Patient",
                         struct=StructType([StructField(n, StringType()) for n in
                                            ("id", "gender", "__resource_type", "__resource_id")]),
                         ddl="id STRING, gender STRING, __resource_type STRING, __resource_id STRING",
                         patient_context=True)


def _schemas(types):
    return {t: PATIENT for t in types}


class _Runner:
    def __init__(self):
        self.statements = []

    def __call__(self, statement, params=None):
        self.statements.append((statement, dict(params or {})))
        return []


class _Frame:
    def __init__(self):
        self.schema = StructType([StructField("id", StringType()), StructField("gender", StringType())])
        self.views = []

    def createOrReplaceTempView(self, name):
        self.views.append(name)


class _PerSite(DataResolver):
    def __init__(self, relations, *, name="acme.fhir", reusable=True):
        self.relations, self.name, self.reusable = relations, name, reusable

    def identity(self):
        return self.name

    def types(self):
        return frozenset(self.relations)

    def retrieve(self, site):
        return self.relations[site.request.fhir_type]


class _SiteTerms(TerminologyResolver):
    def identity(self):
        return "per-site"

    def expansions(self, site):
        return TableRelation(name=f"vs.{site.define.lower()}.valueset")

    def find_code(self, code, system):
        return []


def _retrieve(sid, fhir_type, define="D"):
    return CallSite(id=sid, kind=CallSite.RETRIEVE, library="L", version="1", define=define,
                    request=RetrieveRequest(fhir_type=fhir_type))


def _value_set(sid, define="D"):
    return CallSite(id=sid, kind=CallSite.VALUE_SET, library="L", version="1", define=define,
                    request=ValueSetRef(id="urn:vs:a"))


TERMS = TableTerminologyResolver("acme.terminology.valueset", _Runner())


class BindTest(unittest.TestCase):
    def test_rules_need_no_call_sites(self):
        """BIND-CALL-SITE, BIND-PUBLISHED-VIEW: resolvers that bind by rule are sent as their rules."""
        bound = bind(None, data=SchemaDataResolver("acme.data"), terminology=TERMS, run=_Runner(), schemas=_schemas)
        self.assertEqual(bound.data, {"rule": {"schema": "acme.data", "identity": "acme.data"}})
        self.assertEqual(bound.terminology, {"rule": [{"table": "acme.terminology.valueset",
                                                       "identity": "acme.terminology"}]})
        self.assertEqual(bound.terminology_tables, ("acme.terminology.valueset",))
        self.assertTrue(bound.reusable)

    def test_a_per_site_resolver_answers_each_retrieve_site(self):
        """BIND-CALL-SITE: two retrieves of one type in two defines are two sites, each answered."""
        data = _PerSite({"Condition": TableRelation(name="acme.fhir.condition")})
        sites = [_retrieve("retrieve_a", "Condition", "A"), _retrieve("retrieve_b", "Condition", "B"),
                 _value_set("valueset_c")]
        bound = bind(sites, data=data, terminology=TERMS, run=_Runner(), schemas=_schemas)
        self.assertEqual(bound.data, {"sites": {
            "retrieve_a": {"table": "acme.fhir.condition", "identity": "acme.fhir"},
            "retrieve_b": {"table": "acme.fhir.condition", "identity": "acme.fhir"}}})

    def test_a_per_site_terminology_answers_each_value_set_site(self):
        bound = bind([_value_set("valueset_a", "A"), _value_set("valueset_b", "B")], data=None,
                     terminology=_SiteTerms(), run=_Runner(), schemas=_schemas)
        self.assertEqual(bound.terminology["sites"]["valueset_a"], [{"table": "vs.a.valueset", "identity": "per-site"}])
        self.assertEqual(bound.terminology["sites"]["valueset_b"][0]["table"], "vs.b.valueset")

    def test_a_per_site_resolver_without_sites_fails(self):
        with self.assertRaises(ResolverError):
            bind(None, data=_PerSite({"Patient": TableRelation(name="a.b.c")}), terminology=None, run=_Runner(),
                 schemas=_schemas)

    def test_a_type_the_resolver_does_not_serve_fails_naming_it(self):
        """CLIENT-DATA-RESOLVER: a retrieved type outside types() fails before the run."""
        with self.assertRaises(ResolverError) as caught:
            bind([_retrieve("retrieve_a", "Condition")], data=TableDataResolver({"Patient": "acme.fhir.patient"}),
                 terminology=None, run=_Runner(), schemas=_schemas)
        self.assertIn("Condition", str(caught.exception))

    def test_retrieves_with_no_data_resolver_fail(self):
        with self.assertRaises(ResolverError):
            bind([_retrieve("retrieve_a", "Patient")], data=None, terminology=None, run=_Runner(), schemas=_schemas)

    def test_data_free_cql_binds_nothing(self):
        bound = bind([], data=None, terminology=None, run=_Runner(), schemas=_schemas)
        self.assertEqual((bound.data, bound.terminology, bound.reusable), ({}, {}, True))

    def test_a_dataframe_on_a_warehouse_fails_naming_the_resolver(self):
        """BIND-RELATION: one path, no silent fallback: nothing is materialized for the warehouse."""
        data = DataFrameDataResolver({"Patient": _Frame()}, name="dev-frames")
        with self.assertRaises(ResolverError) as caught:
            bind([_retrieve("retrieve_a", "Patient")], data=data, terminology=None, run=_Runner(), schemas=_schemas)
        self.assertIn("dev-frames", str(caught.exception))
        self.assertIn("warehouse", str(caught.exception))

    def test_a_dataframe_on_spark_is_reshaped_into_a_view_the_client_owns(self):
        """BIND-RELATION, BIND-RESHAPE: and a development resolver makes the run unkept (CLIENT-RESOLVER-IDENTITY)."""
        frame, run = _Frame(), _Runner()
        bound = bind([_retrieve("retrieve_a", "Patient")], data=DataFrameDataResolver({"Patient": frame}),
                     terminology=None, run=run, schemas=_schemas, spark=object())
        view = bound.data["sites"]["retrieve_a"]["table"]
        self.assertTrue(view.startswith(VIEW_PREFIX))
        self.assertEqual(frame.views, [bound.views[0]])
        created = [s for s, _ in run.statements if s.startswith("CREATE OR REPLACE TEMPORARY VIEW")]
        self.assertEqual(len(created), 1)
        self.assertIn("named_struct(", created[0])
        self.assertFalse(bound.reusable)

    def test_a_volume_relation_reads_its_ndjson_through_the_reshape(self):
        run = _Runner()
        data = _PerSite({"Patient": VolumeRelation(path="/Volumes/c/s/v/fhir", fhir_type="Patient")})
        bound = bind([_retrieve("retrieve_a", "Patient")], data=data, terminology=None, run=run, schemas=_schemas)
        self.assertIn("read_files('/Volumes/c/s/v/fhir/Patient.ndjson', format => 'text')", run.statements[0][0])
        self.assertEqual(bound.views, (bound.data["sites"]["retrieve_a"]["table"],))

    def test_terminology_layers_are_sent_in_order_with_each_identity(self):
        """CLIENT-RESOLVER-OVERLAY: the first layer that holds a value set serves it."""
        dev = TableTerminologyResolver("dev.vs.valueset", _Runner())
        bound = bind(None, data=None, run=_Runner(), schemas=_schemas, terminology=Overlay(dev, TERMS))
        self.assertEqual([t["table"] for t in bound.terminology["rule"]],
                         ["dev.vs.valueset", "acme.terminology.valueset"])

    def test_a_volume_of_value_sets_becomes_a_view_and_refuses_compose_only_sets(self):
        """CLIENT-TERMINOLOGY-RESOLVER: a value set must carry its expansion."""
        run = _Runner()
        bound = bind(None, data=None, run=run, schemas=_schemas,
                     terminology=FolderTerminologyResolver("/Volumes/acme/vs/json"))
        self.assertTrue(bound.terminology["rule"][0]["table"].startswith(VIEW_PREFIX))
        self.assertIn("explode(concat(", run.statements[-1][0])

        class _Bare(_Runner):
            def __call__(self, statement, params=None):
                super().__call__(statement, params)
                return [{"url": "urn:vs:compose"}] if "IS NULL" in statement else []

        bare = _Bare()
        with self.assertRaises(ResolverError) as caught:
            bind(None, data=None, run=bare, schemas=_schemas,
                 terminology=FolderTerminologyResolver("/Volumes/acme/vs/json"))
        self.assertIn("urn:vs:compose", str(caught.exception))


class _Service:
    def __init__(self, sites):
        self.sites, self.described = sites, []

    def describe(self, request):
        self.described.append(request)
        return LibraryGraph(defines=[], dependencies={}, boundaries=[], materialized_defines=[], compiler_version="t",
                            sites=self.sites)


def _library(binder, service):
    return SimpleNamespace(binder=binder, provider=service)


class ResolverBinderTest(unittest.TestCase):
    def test_a_compile_bound_by_rule_makes_no_describe_call(self):
        """BIND-CALL-SITE, SVC-COMPILE-BATCH: one compiler call."""
        service = _Service(None)
        binder = ResolverBinder(data=SchemaDataResolver("acme.data"), terminology=TERMS, run=_Runner,
                                schemas=_schemas)
        bound = bound_request(_library(binder, service), CompileRequest(cql="library X"))
        self.assertEqual(service.described, [])
        self.assertEqual(bound.data["rule"]["schema"], "acme.data")
        self.assertEqual(bound.to_json()["data"], {"rule": {"identity": "acme.data", "schema": "acme.data"}})

    def test_a_per_site_resolver_asks_describe_with_the_compiles_own_request(self):
        service = _Service((_retrieve("retrieve_a", "Patient"),))
        binder = ResolverBinder(data=TableDataResolver({"Patient": "acme.fhir.patient"}), terminology=TERMS,
                                run=_Runner, schemas=_schemas)
        request = CompileRequest(cql="library X")
        bound = bound_request(_library(binder, service), request)
        self.assertEqual(service.described, [request])
        self.assertEqual(bound.data["sites"]["retrieve_a"]["table"], "acme.fhir.patient")

    def test_resolvers_may_be_factories_called_on_first_compile(self):
        made = []
        binder = ResolverBinder(data=lambda: made.append(1) or SchemaDataResolver("acme.data"), terminology=None,
                                run=_Runner, schemas=_schemas)
        self.assertEqual(made, [])
        bound_request(_library(binder, _Service(None)), CompileRequest(cql="library X"))
        self.assertEqual(made, [1])

    def test_a_durable_compile_refuses_a_development_resolver(self):
        """CLIENT-RESOLVER-IDENTITY: its results are never kept."""
        binder = ResolverBinder(data=DataFrameDataResolver({"Patient": _Frame()}), terminology=None, run=_Runner,
                                spark=object(), schemas=_schemas)
        with self.assertRaises(ResolverError):
            bound_request(_library(binder, _Service((_retrieve("retrieve_a", "Patient"),))),
                          CompileRequest(cql="library X"), durable=True)

    def test_a_development_resolver_never_reuses_a_cached_compile(self):
        binder = ResolverBinder(data=DataFrameDataResolver({"Patient": _Frame()}), run=_Runner, schemas=_schemas)
        self.assertNotEqual(binder.generation(), binder.generation())
        rule = ResolverBinder(data=SchemaDataResolver("acme.data"), terminology=TERMS, run=_Runner, schemas=_schemas)
        self.assertEqual(rule.generation(), ("acme.data", "acme.terminology"))

    def test_an_older_service_that_reports_no_sites_fails(self):
        binder = ResolverBinder(data=TableDataResolver({"Patient": "acme.fhir.patient"}), run=_Runner,
                                schemas=_schemas)
        with self.assertRaisesRegex(Exception, "call sites"):
            bound_request(_library(binder, _Service(None)), CompileRequest(cql="library X"))


if __name__ == "__main__":
    unittest.main()
