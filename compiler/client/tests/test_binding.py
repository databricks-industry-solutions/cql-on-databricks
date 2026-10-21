"""The compile-time data binder seam (`CLIENT-EXEC-DISCOVERY`): ``exec()`` and ``compile_pipeline()`` ask the
library's binder for its data, told the types the library retrieves (`EMIT-DESCRIBE-RETRIEVE-TYPES`).

Stdlib unittest with fakes: no pyspark, no compile host.
"""
import unittest

from cql_databricks.compiler import CqlClient
from cql_databricks.compiler.binding import BoundData
from cql_databricks.compiler.providers import (CompileError, CompileProvider, CompileRequest, LibraryGraph,
                                               PipelinePlan, SqlCompileResult, SqlStatement)


class _Spark:
    def sql(self, text):
        return text


class _Provider(CompileProvider):
    def __init__(self, retrieve_types=("Condition", "Patient")):
        self.compiled = []
        self.describes = 0
        self._types = retrieve_types

    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        self.compiled.append(request)
        return SqlCompileResult(statements=[SqlStatement("X", "view", "CREATE TEMP VIEW `X` AS SELECT 1")],
                                target="X", defines=["X"])

    def compile_pipeline(self, request: CompileRequest) -> PipelinePlan:
        self.compiled.append(request)
        return PipelinePlan(datasets=[], compiler_version="test")

    def describe(self, request: CompileRequest) -> LibraryGraph:
        self.describes += 1
        return LibraryGraph(defines=["X"], dependencies={}, boundaries=[], materialized_defines=[],
                            compiler_version="test",
                            retrieve_types=None if self._types is None else list(self._types))


class _Binder:
    def __init__(self, bound=None):
        self.calls = []
        self._bound = bound or BoundData(data_source={"catalog": "c", "schema": "s", "routines": ["retrieve_Condition"]},
                                         terminology_table="rt.runtime.valuesets")

    def bind(self, library, retrieve_types, *, durable):
        self.calls.append((list(retrieve_types), durable))
        return self._bound


def _client(provider, binder=None, **sources):
    b = CqlClient.builder().provider(provider).spark(_Spark())
    for fhir_type, function in sources.items():
        b = b.source_binding(fhir_type, function)
    if binder is not None:
        b = b.data_binder(binder)
    return b.build()


class BinderTests(unittest.TestCase):
    def test_exec_sends_what_the_binder_chose_told_the_retrieved_types(self):
        provider, binder = _Provider(), _Binder()
        _client(provider, binder).cql('define "X": exists [Condition]').exec("X")
        self.assertEqual(binder.calls, [(["Condition", "Patient"], False)])
        sent = provider.compiled[-1]
        self.assertEqual(sent.data_source["schema"], "s")
        self.assertEqual(sent.terminology_table, "rt.runtime.valuesets")
        self.assertEqual(dict(sent.sources), {})

    def test_durable_compile_tells_the_binder(self):
        provider, binder = _Provider(), _Binder()
        _client(provider, binder).cql('define "X": 1').compile_pipeline(durable=True)
        self.assertEqual(binder.calls, [(["Condition", "Patient"], True)])
        self.assertEqual(provider.compiled[-1].data_source["catalog"], "c")

    def test_explicit_source_binding_beats_the_binder(self):
        provider, binder = _Provider(), _Binder()
        _client(provider, binder, Condition="my_retrieve").cql('define "X": 1').exec("X")
        self.assertEqual(binder.calls, [])
        self.assertIsNone(provider.compiled[-1].data_source)
        self.assertEqual(provider.compiled[-1].sources["Condition"]["function"], "my_retrieve")

    def test_no_binder_compiles_to_the_convention_without_a_describe(self):
        provider = _Provider()
        _client(provider).cql('define "X": 1').exec("X")
        self.assertEqual(provider.describes, 0)
        self.assertIsNone(provider.compiled[-1].data_source)

    def test_a_service_that_does_not_report_retrieved_types_fails_loudly(self):
        with self.assertRaisesRegex(CompileError, "EMIT-DESCRIBE-RETRIEVE-TYPES"):
            _client(_Provider(retrieve_types=None), _Binder()).cql('define "X": 1').exec("X")

    def test_an_explicit_valueset_table_beats_the_binders(self):
        provider = _Provider()
        client = (CqlClient.builder().provider(provider).spark(_Spark()).valueset_table("mine.vs")
                  .data_binder(_Binder()).build())
        client.cql('define "X": 1').exec("X")
        self.assertEqual(provider.compiled[-1].terminology_table, "mine.vs")


class ResourceSchemaTests(unittest.TestCase):
    """CLIENT-RESOURCE-SCHEMA — the model's schema, read once per client."""

    class _SchemaProvider(_Provider):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def resource_types(self):
            return ["Condition", "Patient"]

        def resource_schemas(self, types):
            self.reads += 1
            return {t: f"schema:{t}" for t in types}

    def test_one_type_or_all_read_once(self):
        provider = self._SchemaProvider()
        client = _client(provider)
        self.assertEqual(client.resource_schema("Patient"), "schema:Patient")
        self.assertEqual(sorted(client.resource_schema()), ["Condition", "Patient"])
        self.assertEqual(provider.reads, 1)

    def test_an_unmodeled_type_raises_naming_it(self):
        with self.assertRaisesRegex(CompileError, "'Widget'"):
            _client(self._SchemaProvider()).resource_schema("Widget")


if __name__ == "__main__":
    unittest.main()
