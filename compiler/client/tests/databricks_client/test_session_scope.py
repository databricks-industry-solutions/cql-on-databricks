"""L1 unit tests for the session library header-less CQL joins (SVC-NOTEBOOK-SESSION-SCOPE).

What the client owns is tested here: which strings join, that the session library is sent with each new
fragment and kept in the Spark session, that a fragment joins only if the whole library still compiles, and
what a bare ``exec()`` runs. The merge itself is the compiler's, tested against the real translator in
``CqlFragmentsSpec``; the provider here stands in for it.
"""
import re
import unittest
from unittest import mock

import cql_databricks.compiler.session_libraries as session_mod
import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.compiler import connect
from cql_databricks.compiler.session_state import reset
from cql_databricks.compiler.providers import CompileError, LibraryGraph, MergedCql
from cql_databricks.compiler.session_state import SessionRecord

UDF_SCHEMA = "acme.runtime"

COMMON = ("library DiabetesCommon version '1.0.0'\n"
          "using FHIR version '4.0.1'\n"
          "context Patient\n"
          "define \"Is Adult\": AgeInYears() >= 18\n")


class _Frame:
    def __init__(self, spark, rows):
        self._spark, self._rows = spark, list(rows)

    def createOrReplaceTempView(self, name):
        self._spark.views[name] = self._rows

    def collect(self):
        return [dict(zip(("kind", "name", "payload"), r)) for r in self._rows]


class _SessionSpark:
    """Just enough of a Spark session to hold the session record (CLIENT-SESSION-STATE)."""

    def __init__(self):
        self.views = {}
        self.catalog = mock.Mock()
        self.catalog.dropTempView.side_effect = lambda name: self.views.pop(name, None) is not None

    def table(self, name):
        if name not in self.views:
            raise RuntimeError(f"[TABLE_OR_VIEW_NOT_FOUND] The table or view `{name}` cannot be found.")
        return _Frame(self, self.views[name])

    def createDataFrame(self, rows, schema):
        return _Frame(self, rows)

    def sql(self, statement):
        assert statement == "SHOW VIEWS IN global_temp"
        return mock.Mock(collect=lambda: [{"viewName": name, "isTemporary": True} for name in self.views])


class _MergingProvider:
    """Stands in for the compiler: ``merge-cql`` appends, ``describe`` refuses a library that says BROKEN."""

    def __init__(self):
        self.merges = []

    def merge_cql(self, fragments):
        self.merges.append(list(fragments))
        return MergedCql(cql="\n".join(f for f in fragments if f),
                         defines=tuple(re.findall(r'define "([^"]+)"', fragments[-1])))

    def describe(self, request):
        if "BROKEN" in request.cql:
            raise CompileError("CQL translation produced errors: BROKEN")
        return LibraryGraph(defines=re.findall(r'define "([^"]+)"', request.cql), dependencies={},
                            boundaries=[], materialized_defines=[], compiler_version="test")


class SessionScopeTest(unittest.TestCase):
    def setUp(self):
        client_mod._default_client = None
        self.spark = _SessionSpark()
        self.provider = _MergingProvider()

    def tearDown(self):
        client_mod._default_client = None

    def _connect(self):
        ws = mock.Mock()
        ws.app_url.return_value = "https://cql-databricks-compiler-123.aws.databricksapps.com"
        ws.workspace_host.return_value = "https://myworkspace.cloud.databricks.com"
        ws.app_oauth_client_id.return_value = "client-abc"
        ws.ambient_subject_token.return_value = "subj-token-xyz"
        with mock.patch("cql_databricks.compiler.provider._workspace", ws):
            client = connect(self.spark, udf_schema=UDF_SCHEMA)
        client._provider = self.provider
        return client

    def _scope(self):
        return SessionRecord(self.spark).scope()

    def test_a_headerless_string_joins_the_session_library(self):
        """SVC-NOTEBOOK-SESSION-SCOPE — the session library goes to the merge ahead of the new fragment."""
        client = self._connect()
        client.cql('define "foo": 1')
        query = client.cql('define "bar": "foo" + 1')

        self.assertEqual(self.provider.merges[-1], ['define "foo": 1', 'define "bar": "foo" + 1'])
        self.assertEqual(query._lib.cql, self._scope())
        self.assertIn('define "foo": 1', query._lib.cql)

    def test_a_bare_exec_runs_only_the_fragments_own_defines(self):
        client = self._connect()
        client.cql('define "foo": 1')
        query = client.cql('define "bar": 2\ndefine "baz": 3')

        self.assertEqual(query.default_targets(), ["bar", "baz"])

    def test_a_fragment_with_populations_runs_its_populations(self):
        """BUILD-CLIENT-POPULATIONS, over only the fragment's defines."""
        client = self._connect()
        client.cql('define "Denominator": 1')
        query = client.cql('define "Numerator": 1\ndefine "Helper": 2')

        self.assertEqual(query.default_targets(), ["Numerator"])

    def test_every_client_on_the_session_shares_it(self):
        """The magic builds its own client; it must see what another client added."""
        self._connect().cql('define "foo": 1')

        self._connect().cql('define "bar": "foo"')

        self.assertEqual(self.provider.merges[-1][0], 'define "foo": 1')

    def test_a_string_with_a_header_stays_its_own_library(self):
        client = self._connect()
        query = client.cql("library Named version '1'\ndefine \"foo\": 1")

        self.assertEqual(self.provider.merges, [])
        self.assertEqual(self._scope(), "")
        self.assertIsNone(query._lib.fragment_defines)

    def test_a_fragment_that_breaks_the_library_leaves_it_unchanged(self):
        """The fragment fails in the cell that caused it, and the session keeps what it had."""
        client = self._connect()
        client.cql('define "foo": 1')

        with self.assertRaisesRegex(CompileError, "BROKEN"):
            client.cql('define "bar": BROKEN')

        self.assertEqual(self._scope(), 'define "foo": 1')

    def test_reset_clears_it(self):
        """CLIENT-SESSION-RESET."""
        self._connect().cql('define "foo": 1')

        reset(self.spark)

        self.assertEqual(self._scope(), "")

    def test_its_includes_resolve_like_a_named_librarys(self):
        """A header-less fragment can include a cell's library; the include ships with the compile."""
        session_mod.register_cell(COMMON, spark=self.spark)
        client = self._connect()

        query = client.cql("include DiabetesCommon version '1.0.0' called Common\n"
                           "define \"Adult\": Common.\"Is Adult\"")

        self.assertEqual([d.name for d in query._lib.deps], ["DiabetesCommon"])

    def test_with_no_spark_session_it_fails_before_any_round_trip(self):
        client = self._connect()
        client._session_scope._record = SessionRecord(None)

        with mock.patch.object(SessionRecord, "_session", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "CLIENT-SESSION-STATE"):
                client.cql('define "foo": 1')
        self.assertEqual(self.provider.merges, [])

    def test_no_source_is_the_session_library_as_it_stands(self):
        """SVC-NOTEBOOK-SESSION-SCOPE — ``cql()`` reaches what earlier cells declared, and joins nothing."""
        client = self._connect()
        client.cql('define "foo": 1')
        client.cql('define "bar": "foo" + 1')
        merges = len(self.provider.merges)

        query = client.cql()

        self.assertEqual(len(self.provider.merges), merges)
        self.assertEqual(query._lib.cql, self._scope())

    def test_a_bare_exec_of_the_session_library_runs_every_define(self):
        """BUILD-CLIENT-POPULATIONS over the whole session library: no populations, so every define."""
        client = self._connect()
        client.cql('define "foo": 1')
        client.cql('define "bar": 2')

        self.assertEqual(client.cql().default_targets(), ["foo", "bar"])

    def test_a_bare_exec_of_the_session_library_runs_its_populations(self):
        client = self._connect()
        client.cql('define "Denominator": 1')
        client.cql('define "Helper": 2')

        self.assertEqual(client.cql().default_targets(), ["Denominator"])

    def test_no_source_with_an_empty_session_library_fails(self):
        with self.assertRaisesRegex(ValueError, "session library is empty"):
            self._connect().cql()



class SessionExecTest(unittest.TestCase):
    def test_a_session_exec_recompiles_the_whole_library(self):
        """Every run re-creates every session view, so a view on a redefined name is never stale."""
        from dataclasses import replace
        from cql_databricks.compiler import CqlQuery, load_library
        from cql_databricks.compiler.providers import DefineShape, SqlCompileResult, SqlStatement
        provider = mock.Mock()
        provider.compile_sql.return_value = SqlCompileResult(
            statements=[SqlStatement(name="foo", kind="view", sql="CREATE OR REPLACE TEMP VIEW foo AS SELECT 1"),
                        SqlStatement(name="bar", kind="view", sql="CREATE OR REPLACE TEMP VIEW bar AS SELECT 2")],
            target="", defines=["foo", "bar"],
            shapes=[DefineShape(define="bar", obj="bar", kind="scalar", value_column="__val")])
        library = replace(load_library(cql='define "foo": 1\ndefine "bar": 2', provider=provider, conf={}),
                          fragment_defines=("bar",))
        spark = mock.Mock()

        CqlQuery(library, spark=spark).exec("bar")

        self.assertIsNone(provider.compile_sql.call_args[0][0].target)
        self.assertEqual([c.args[0] for c in spark.sql.call_args_list][-1], "SELECT * FROM `bar`")
        self.assertEqual(len(spark.sql.call_args_list), 3)


if __name__ == "__main__":
    unittest.main()
