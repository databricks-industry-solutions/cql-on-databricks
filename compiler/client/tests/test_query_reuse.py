"""CLIENT-QUERY-REUSE, RUN-RECOMPUTE-OVERRIDE: ``cql(...).exec()`` on Spark reads kept results through the client's result
resolver, and makes a view only for each define it needs that has none.

Stdlib unittest with fakes: no pyspark, no compile host.
"""
import re
import unittest
from datetime import datetime, timezone

from cql_databricks.compiler import CqlClient
from cql_databricks.compiler.providers import (CompileProvider, CompileRequest, DatasetRecipe, DurableIdentity,
                                               LibraryGraph, PipelinePlan, SqlCompileResult, SqlStatement)
from cql_databricks.compiler.resolver_binder import ResolverBinder
from cql_databricks.resolvers.data import SchemaDataResolver
from cql_databricks.resolvers.interfaces import KeptResult, ResultResolver, TableRef

CQL = 'library Synthetic version \'1\'\ndefine "Base": 7\ndefine "Output": Base + 3'
KEPT = TableRef(catalog="acme", schema="results", table="synthetic_base_aaaaaaaaaaaaaaaa_00000000")


def _identity(digit):
    return DurableIdentity(format="cql-result-identity-v1", fingerprint=digit * 64, reusable=True)


class _Spark:
    def __init__(self):
        self.statements = []

    def sql(self, text):
        self.statements.append(text)
        return text


class _Provider(CompileProvider):
    def __init__(self):
        self.pipelines, self.compiled = [], []

    def compile_pipeline(self, request: CompileRequest) -> PipelinePlan:
        self.pipelines.append(request)
        return PipelinePlan(compiler_version="synthetic", datasets=[
            DatasetRecipe(name="base", target="Base", reads=[], statements=[], query="SELECT 7 AS __val",
                          library="Synthetic", library_version="1", define="Base", durable=_identity("a")),
            DatasetRecipe(name="output", target="Output", reads=["base"], statements=[],
                          query="SELECT __val + 3 AS __val FROM base", library="Synthetic", library_version="1",
                          define="Output", durable=_identity("b")),
        ])

    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        self.compiled.append(request)
        return SqlCompileResult(statements=[SqlStatement("Output", "view", "CREATE TEMP VIEW `Output` AS SELECT 10")],
                                target="Output", defines=["Base", "Output"])

    def describe(self, request: CompileRequest) -> LibraryGraph:
        return LibraryGraph(defines=["Base", "Output"], dependencies={}, boundaries=[], materialized_defines=[],
                            compiler_version="synthetic")


class _Results(ResultResolver):
    """Finds ``Base``'s kept result, and records each lookup's fingerprints."""

    def __init__(self, kept=True):
        self.kept, self.lookups = kept, []

    def find(self, keys, *, computed_since=None):
        self.lookups.append(sorted(k.fingerprint[0] for k in keys))
        found = KeptResult(table=KEPT, fingerprint="a" * 64, computed_at=datetime.now(timezone.utc))
        return {"a" * 64: found} if self.kept and any(k.fingerprint == "a" * 64 for k in keys) else {}

    @property
    def destination(self):
        return ("acme", "draft_results")

    def holds(self, table):
        return True


def _shapes(statement, params=None):
    """DATA-MODEL-SUBSET: the only statement a bind sends, the data views' shapes."""
    assert "information_schema.columns" in statement, statement
    return [{"table_name": "patient", "column_name": "id", "full_data_type": "string"}]


class _Development(SchemaDataResolver):
    reusable = False


def _query(results, *, data=None, cql=CQL, recompute=None):
    provider, spark = _Provider(), _Spark()
    binder = ResolverBinder(data=data or SchemaDataResolver("acme.data"), run=lambda: _shapes, schemas=lambda types: {},
                            results=results)
    client = CqlClient.builder().provider(provider).spark(spark).data_binder(binder).build()
    return client.cql(cql, recompute=recompute), provider, spark


class QueryReuseTest(unittest.TestCase):
    def test_a_target_with_a_kept_result_reads_its_table_and_makes_no_view(self):
        results = _Results()
        query, provider, spark = _query(results)
        frame = query.exec("Base")
        self.assertEqual(frame, "SELECT * FROM `acme`.`results`.`synthetic_base_aaaaaaaaaaaaaaaa_00000000`")
        self.assertEqual(spark.statements, [frame])
        self.assertEqual(results.lookups, [["a"]])
        self.assertEqual(provider.compiled, [])
        # RUN-CLIENT-RESULT-IDENTITY: the durable plan, with no object prefix.
        (request,) = provider.pipelines
        self.assertTrue(request.durable)
        self.assertIsNone(request.object_prefix)

    def test_a_define_not_found_is_a_view_reading_the_kept_table_in_place_of_what_it_reads(self):
        results = _Results()
        query, _, spark = _query(results)
        frame = query.exec("Output")
        (view,) = spark.statements[:-1]
        name = re.match(r"CREATE TEMPORARY VIEW `(cql_[0-9a-f]{12}_1)` AS ", view)[1]
        self.assertIn("`base` AS (SELECT * FROM `acme`.`results`.`synthetic_base_aaaaaaaaaaaaaaaa_00000000`)", view)
        self.assertEqual(frame, f"SELECT * FROM `{name}`")
        self.assertEqual(results.lookups, [["a", "b"]])

    def test_each_exec_makes_its_views_under_its_own_prefix(self):
        """EMIT-SQL-OBJECT-PREFIX: a frame from an earlier exec keeps reading what it read."""
        query, _, _ = _query(_Results(kept=False))
        first, second = query.exec("Output"), query.exec("Output")
        self.assertNotEqual(first, second)

    def test_several_targets_return_a_frame_each_from_one_lookup(self):
        results = _Results()
        query, _, _ = _query(results)
        frames = query.exec("Base", "Output")
        self.assertEqual(sorted(frames), ["Base", "Output"])
        self.assertEqual(results.lookups, [["a", "b"]])

    def test_recompute_compiles_the_whole_library_and_reads_no_kept_result(self):
        """RUN-RECOMPUTE-OVERRIDE"""
        results = _Results()
        query, provider, _ = _query(results, recompute=True)
        query.exec("Output")
        self.assertEqual(results.lookups, [])
        self.assertEqual(len(provider.compiled), 1)
        query, provider, _ = _query(results)
        query.with_recompute().exec("Output")
        self.assertEqual(results.lookups, [])
        self.assertEqual(len(provider.compiled), 1)

    def test_cql_with_no_library_header_is_compiled(self):
        """RUN-CLIENT-TABLE-NAMES: kept results are named by their library, so it has none."""
        results = _Results()
        query, provider, _ = _query(results, cql='define "Output": 10')
        query.exec("Output")
        self.assertEqual(results.lookups, [])
        self.assertEqual(len(provider.compiled), 1)

    def test_a_resolver_that_is_never_reused_is_compiled(self):
        """CLIENT-RESOLVER-IDENTITY"""
        results = _Results()
        query, provider, _ = _query(results, data=_Development("acme.dev"))
        query.exec("Output")
        self.assertEqual(results.lookups, [])
        self.assertEqual(len(provider.compiled), 1)

    def test_no_result_resolver_compiles(self):
        query, provider, _ = _query(None)
        query.exec("Output")
        self.assertEqual(provider.pipelines, [])
        self.assertEqual(len(provider.compiled), 1)

    def test_boundaries_are_refused_while_reading_kept_results(self):
        query, _, _ = _query(_Results())
        with self.assertRaisesRegex(TypeError, "recompute=True"):
            query.exec("Output", boundaries=["Base"])

    def test_a_target_that_is_not_a_define_fails_naming_it(self):
        query, _, _ = _query(_Results())
        with self.assertRaisesRegex(KeyError, "Missing"):
            query.exec("Missing")


if __name__ == "__main__":
    unittest.main()
