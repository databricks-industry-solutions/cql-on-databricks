"""Retrieve-UDTF binding (BIND-RETRIEVE-UDF / BIND-MANIFEST) on the Python client — wire serialization,
builder/library plumbing, and the retrieve signature (stdlib unittest; no pyspark, no network: the provider
is a fake)."""
import unittest

from cql_databricks.compiler import CqlClient
from cql_databricks.compiler.runtime import retrieve_params_ddl
from cql_databricks.compiler.providers import CompileProvider, CompileRequest, SqlCompileResult


class SourcesWireTests(unittest.TestCase):
    def test_to_json_serializes_sources_sorted_drops_functionless_and_none_catalog(self):
        req = CompileRequest(elm="{}", sources={
            "Encounter": {"function": "fetch_encounter", "catalog": "acme.fhir"},
            "Condition": {"function": "fetch_condition"},   # optional catalog omitted
            "Broken": {"catalog": "x"},                     # no function → ignored
        })
        j = req.to_json()
        self.assertEqual(j["sources"], {
            "Condition": {"function": "fetch_condition"},
            "Encounter": {"function": "fetch_encounter", "catalog": "acme.fhir"},
        })

    def test_empty_sources_omits_the_key_entirely(self):
        self.assertNotIn("sources", CompileRequest(elm="{}").to_json())


class _CaptureProvider(CompileProvider):
    """Records the CompileRequest it was handed (no compile happens)."""
    def __init__(self):
        self.req = None

    def compile_sql(self, request):
        self.req = request
        return SqlCompileResult(statements=[], target="X", defines=["X"])


class BuilderPlumbingTests(unittest.TestCase):
    def test_builder_carries_the_binding_table_to_the_compile_request(self):
        fake = _CaptureProvider()
        client = (CqlClient.builder()
                  .provider(fake)
                  .source_binding("Encounter", "fetch_encounter", catalog="acme.fhir")
                  .source_binding("Condition", "fetch_condition")
                  .build())
        q = client.cql(cql='define "X": 1')
        q._compile("X", None)  # builds + hands over the CompileRequest (compile is data-free)
        self.assertEqual(fake.req.sources, {
            "Encounter": {"function": "fetch_encounter", "catalog": "acme.fhir"},
            "Condition": {"function": "fetch_condition"},
        })

    def test_no_source_binding_leaves_sources_empty(self):
        fake = _CaptureProvider()
        client = CqlClient.builder().provider(fake).build()
        client.cql(cql='define "X": 1')._compile("X", None)
        self.assertEqual(fake.req.sources, {})


class RetrieveParamsTests(unittest.TestCase):
    def test_the_seven_positional_parameters_each_with_an_absent_default(self):
        """BIND-EXPLICIT-PARAMS, EMIT-ROOT-INDEPENDENT — the signature every mounted and published retrieve
        declares; no parameter names a library."""
        ddl = retrieve_params_ddl()
        self.assertTrue(ddl.startswith("value_set STRING DEFAULT NULL, "))
        self.assertNotIn("library", ddl)
        self.assertIn("code_property STRING DEFAULT NULL, codes ARRAY<STRUCT<system STRING, code STRING>>", ddl)
        self.assertIn("parameters MAP<STRING, VARIANT> DEFAULT map()", ddl)
        self.assertNotIn("VARIANT, hints", ddl)  # the retired envelope pair


if __name__ == "__main__":
    unittest.main()
