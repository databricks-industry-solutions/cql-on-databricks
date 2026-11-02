"""BIND-CALL-SITE: ``exec()`` and ``compile_pipeline()`` bind each compile through the library's binder, and a
library with none binds nothing.

Stdlib unittest with fakes: no pyspark, no compile host.
"""
import unittest

from cql_databricks.compiler import CqlClient
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
    """A binder that records each request it binds and binds every retrieve by one rule."""

    def __init__(self):
        self.calls = []

    def bind_request(self, library, request, describe, *, durable):
        from dataclasses import replace

        self.calls.append(durable)
        return replace(request, data={"rule": {"schema": "c.s", "identity": "c.s"}})


def _client(provider, binder=None):
    b = CqlClient.builder().provider(provider).spark(_Spark())
    if binder is not None:
        b = b.data_binder(binder)
    return b.build()


class BinderTests(unittest.TestCase):
    def test_exec_sends_what_the_binder_bound(self):
        provider, binder = _Provider(), _Binder()
        _client(provider, binder).cql('define "X": exists [Condition]').exec("X")
        self.assertEqual(binder.calls, [False])
        self.assertEqual(provider.compiled[-1].to_json()["data"], {"rule": {"schema": "c.s", "identity": "c.s"}})

    def test_durable_compile_tells_the_binder(self):
        provider, binder = _Provider(), _Binder()
        _client(provider, binder).cql('define "X": 1').compile_pipeline(durable=True)
        self.assertEqual(binder.calls, [True])

    def test_no_binder_binds_nothing_and_asks_no_describe(self):
        provider = _Provider()
        _client(provider).cql('define "X": 1').exec("X")
        self.assertEqual(provider.describes, 0)
        body = provider.compiled[-1].to_json()
        self.assertNotIn("data", body)
        self.assertNotIn("terminology", body)

    def test_the_retired_builder_bindings_are_gone(self):
        """BIND-CALL-SITE: no per-type function, catalog or value-set table on the builder."""
        for retired in ("source_binding", "data_catalog", "fhir_catalog", "valueset_table"):
            self.assertFalse(hasattr(CqlClient.builder(), retired), retired)


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
