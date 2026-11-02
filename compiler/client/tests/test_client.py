"""The ``CqlClient`` session + the lazy ``CqlQuery`` handle (BUILD-CLIENT-SESSION / BUILD-CLIENT-QUERY):
handle vs terminal ``exec``, artifact egress (elm/dag), population defaults, context resolution, and the
bare-verb option knobs.

Stdlib unittest with FAKES (no pyspark, no compile host): a fake provider records the ``CompileRequest`` it
is handed and returns a canned ``SqlCompileResult`` / ``LibraryGraph`` (single-target vs whole-library); a
fake session records ``spark.sql`` calls.
"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from cql_databricks.compiler import CqlClient, CqlQuery, config, cql as run_cql
from cql_databricks.compiler.config import ConfigResolutionError
from cql_databricks.compiler.library import Library, load_library, _cql_literal
from cql_databricks.compiler.providers import (
    SCALAR,
    CompileProvider,
    CompileRequest,
    DefineShape,
    LibraryGraph,
    SqlCompileResult,
    SqlStatement,
)


class _FakeDataFrame:
    def __init__(self, sql):
        self.sql = sql


class _FakeSpark:
    def __init__(self):
        self.calls = []

    def sql(self, text):
        self.calls.append(text)
        return _FakeDataFrame(text)

    def table(self, name):
        return _FakeDataFrame(f"table:{name}")


class _FakeProvider(CompileProvider):
    """Records the last request; compiles a single target to one named view, or the whole library (target
    None) to a view per define. describe() reports populations + a materialized barrier."""

    def __init__(self, defines=("Initial Population", "Denominator", "Numerator", "ConditionList", "DemographicAttribute"),
                 materialized=("ConditionList",)):
        self.seen = None
        self.described = None
        self._defines = list(defines)
        self._materialized = list(materialized)

    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        self.seen = request
        if request.target is not None:
            n = request.target
            return SqlCompileResult(
                statements=[SqlStatement(n, "view", f"CREATE OR REPLACE TEMP VIEW `{n}` AS SELECT 1")],
                target=n, defines=[n])
        stmts = [SqlStatement("__frame_base", "view", "CREATE OR REPLACE TEMP VIEW __frame_base AS SELECT 1")]
        stmts += [SqlStatement(d, "view", f"CREATE OR REPLACE TEMP VIEW `{d}` AS SELECT 1")
                  for d in self._defines]
        shapes = [DefineShape(define=d, obj=d, kind=SCALAR) for d in self._defines]
        return SqlCompileResult(statements=stmts, target="__frame_base", defines=list(self._defines), shapes=shapes)

    def describe(self, request: CompileRequest) -> LibraryGraph:
        self.described = request
        return LibraryGraph(defines=list(self._defines), dependencies={},
                            boundaries=list(self._materialized),
                            materialized_defines=list(self._materialized), compiler_version="test")


def _client(provider):
    return (CqlClient.builder().provider(provider).spark(_FakeSpark()).build())


class HandleTests(unittest.TestCase):
    def test_cql_returns_a_lazy_handle_not_a_dataframe(self):
        q = _client(_FakeProvider()).cql(cql='define "x": 1 + 1')
        self.assertIsInstance(q, CqlQuery)

    def test_raw_cql_sets_cql_and_empty_elm(self):
        lib = _client(_FakeProvider()).with_library(cql='define "x": 1 + 1')
        self.assertEqual(lib.cql, 'define "x": 1 + 1')
        self.assertEqual(lib.elm, "")

    def test_source_string_is_classified_cql_vs_elm_vs_resource(self):
        """BUILD-CLIENT-COMPAT: the source-STRING classification is frozen — a released string form can
        never change meaning. A `.cql`/`.elm.json` path is a resource, a leading `{` is ELM, anything else
        is raw CQL. A bare library NAME is deliberately NOT a fourth case (see `library=` below)."""
        client = _client(_FakeProvider())
        self.assertEqual(client.cql('define "x": 1')._lib.cql, 'define "x": 1')      # raw CQL text
        self.assertEqual(client.cql('{"library": {}}')._lib.elm, '{"library": {}}')  # ELM JSON
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "Sample.cql").write_text("library Sample version '1.0.0'\n")
            (root / "Sample.elm.json").write_text('{"library": {"identifier": {"id": "Sample"}}}')
            # A resource path: the sibling ELM is PREFERRED when present (the interchange form).
            self.assertEqual(client.cql(str(root / "Sample.cql"))._lib.elm,
                             '{"library": {"identifier": {"id": "Sample"}}}')

    def test_cql_resource_without_sibling_elm_falls_back_to_cql_text(self):
        """A `.cql` resource with NO pre-translated sibling binds the CQL TEXT and lets the service translate.
        Content repos ship CQL, not ELM, so `client.cql("measures/Foo.cql")` works against any checkout of
        published content."""
        client = _client(_FakeProvider())
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "Solo.cql"
            path.write_text("library Solo version '2.0.0'\ndefine \"x\": 1\n")
            lib = client.cql(str(path))._lib
            self.assertEqual(lib.elm, "")
            self.assertIn('define "x": 1', lib.cql)

    def test_library_name_is_never_inferred_from_a_bare_string(self):
        """A bare string stays raw CQL forever (`BUILD-CLIENT-COMPAT`); a NAME is addressed by `library=`.
        Guards the one-way door: were `cql("ScreeningMeasure")` ever re-read as a name, every caller whose CQL text
        happens to lack punctuation would silently change meaning."""
        client = _client(_FakeProvider())
        self.assertEqual(client.cql("ScreeningMeasure")._lib.cql, "ScreeningMeasure")
        self.assertIsNone(client.cql("ScreeningMeasure")._lib.elm or None)

    def test_a_source_and_library_together_is_a_loud_error(self):
        """Two bodies is a mistake, not a precedence question. Silently honoring one (compiling
        `library=` and dropping the source with no warning) is the quiet-wrong-answer class this surface
        exists to avoid."""
        client = _client(_FakeProvider())
        with self.assertRaises(ValueError) as ctx:
            client.cql('define "x": 1', library="ScreeningMeasure")
        self.assertIn("library=", str(ctx.exception))

    def test_udf_schema_knob_reaches_the_request(self):
        """EMIT-UDF-QUALIFY was reachable on the wire but had no way to SET it — the field existed and
        nothing ever populated it."""
        prov = _FakeProvider()
        client = (CqlClient.builder().provider(prov).spark(_FakeSpark())
                  .udf_schema("acme.runtime").build())
        client.cql(cql='define "x": 1').exec("Numerator")
        self.assertEqual(prov.seen.udf_schema, "acme.runtime")
        self.assertEqual(prov.seen.to_json()["runtime"], {"schema": "acme.runtime"})

    def test_udf_jar_is_shipped_once_per_session_not_once_per_exec(self):
        """The upload hook runs on EVERY exec, and Spark Connect rejects re-adding an artifact it already
        holds — so an unguarded (and now loud) upload would make the SECOND query in a session fail."""
        class _CountingSpark(_FakeSpark):
            def __init__(self):
                super().__init__()
                self.artifacts = []

            def addArtifact(self, jar):
                if jar in self.artifacts:
                    raise RuntimeError("artifact already exists")
                self.artifacts.append(jar)

        spark = _CountingSpark()
        client = (CqlClient.builder().provider(_FakeProvider()).spark(spark)
                  .conf(config.CONF_UDF_JAR, "/tmp/cql-udfs.jar").build())
        q = client.cql(cql='define "x": 1')
        q.exec("Numerator")
        q.exec("Denominator")          # would raise before the once-per-session guard
        self.assertEqual(spark.artifacts, ["/tmp/cql-udfs.jar"])

    def test_measurement_period_and_parameter_carried_to_request(self):
        prov = _FakeProvider()
        q = (_client(prov).cql(cql='define "x": 1')
             .with_measurement_period("2026-01-01", "2026-12-31")
             .with_parameter("Threshold", 40))
        q.exec("Numerator")
        self.assertEqual(q._lib.cql, 'define "x": 1')
        # LANG-PARAM-INSTANT: a bare DATE widens to the INSTANT it denotes — the measures declare
        # `Interval<DateTime>` and precision is ISO-string LENGTH (see the boundary test below).
        self.assertEqual(prov.seen.parameters["Measurement Period"],
                         "Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]")
        self.assertEqual(prov.seen.parameters["Threshold"], "40")

    def test_measurement_period_bounds_are_millisecond_precision(self):
        """`with_measurement_period` must not emit bare Date literals (`LANG-PARAM-INSTANT`).

        A parameter override is CQL expression TEXT translated through the front end, so a bare
        `@2026-01-01` stays a 10-char day-precision value even against an `Interval<DateTime>` parameter.
        Compared to a 23-char resource instant that shares its prefix, the result is UNCERTAIN -> null ->
        the row fails its WHERE. Regression: measurement period boundaries must be millisecond-precision to
        match FHIR instant resources, or boundary-day rows are silently dropped from the result.
        """
        prov = _FakeProvider()
        (_client(prov).cql(cql='define "x": 1')
            .with_measurement_period("2026-01-01", "2026-12-31")).exec("Numerator")
        lo, hi = prov.seen.parameters["Measurement Period"].strip("Interval[]").split(", ")
        self.assertEqual(len(lo.lstrip("@")), 23, f"low bound must be ms-precision, got {lo}")
        self.assertEqual(len(hi.lstrip("@")), 23, f"high bound must be ms-precision, got {hi}")

    def test_measurement_period_passes_through_an_explicit_instant(self):
        """An operand that already carries a time is used VERBATIM — the caller pinned the instant."""
        prov = _FakeProvider()
        (_client(prov).cql(cql='define "x": 1')
            .with_measurement_period("2026-01-01T00:00:00.0", "2026-12-31T23:59:59.999")).exec("Numerator")
        self.assertEqual(prov.seen.parameters["Measurement Period"],
                         "Interval[@2026-01-01T00:00:00.0, @2026-12-31T23:59:59.999]")

    def test_measurement_period_bounds_follow_the_declared_point_type(self):
        """LANG-PARAM-INSTANT — a DateTime period widens a bare date, a Date period truncates a datetime, anything
        else is refused."""
        from cql_databricks.compiler import measurement_period_bounds, measurement_period_literal
        self.assertEqual(measurement_period_bounds("2026-01-01", "2026-12-31"),
                         ("2026-01-01T00:00:00.000", "2026-12-31T23:59:59.999"))
        self.assertEqual(measurement_period_bounds("2026-01-01T00:00:00.000", "2026-12-31T23:59:59.999", "Date"),
                         ("2026-01-01", "2026-12-31"))
        self.assertEqual(measurement_period_literal("2026-01-01", "2026-12-31", "Date"),
                         "Interval[@2026-01-01, @2026-12-31]")
        with self.assertRaises(ValueError):
            measurement_period_bounds("2026", "2027", "Integer")

    def test_bare_verb_fluent_aliases_are_immutable(self):
        base = _client(_FakeProvider()).cql(cql='define "x": 1')
        refined = base.parameter("A", 1).measurement_period("2026-01-01", "2026-12-31")
        self.assertEqual(base._lib.parameters, {})                 # original untouched
        self.assertEqual(refined._lib.parameters["A"], "1")


class ExecTests(unittest.TestCase):
    def test_exec_single_target_returns_a_dataframe(self):
        df = _client(_FakeProvider()).cql(cql='define "x": 1').exec("Numerator")
        self.assertEqual(df.sql, "SELECT * FROM `Numerator`")

    def test_exec_default_evaluates_populations_as_a_dict(self):
        res = _client(_FakeProvider()).cql(cql='define "x": 1').exec()
        self.assertIsInstance(res, dict)
        # the well-known populations present, in canonical order, each its own DataFrame
        self.assertEqual(list(res), ["Initial Population", "Denominator", "Numerator"])
        self.assertEqual(res["Numerator"].sql, "SELECT * FROM `Numerator`")

    def test_exec_multiple_named_targets_returns_a_dict(self):
        res = _client(_FakeProvider()).cql(cql='define "x": 1').exec("Numerator", "ConditionList")
        self.assertEqual(set(res), {"Numerator", "ConditionList"})

    def test_exec_arity_follows_the_CALL_not_the_librarys_contents(self):
        """BUILD-CLIENT-QUERY. A library with exactly ONE population must still give `exec()` a dict.

        Arity follows the CALL, not the library's contents: `exec()` returns a dict whatever the number of
        populations the library resolves, so `for name, df in q.exec().items()` works on every measure. A planned
        switch to authoritative populations metadata (a FHIR `Measure`) cannot change the RETURN TYPE of caller
        code. Arity depends only on how many targets the CALLER named.
        """
        one_pop = _FakeProvider(defines=("Numerator", "DemographicAttribute"), materialized=())
        q = _client(one_pop).cql(cql='define "x": 1')
        self.assertEqual(q.populations(), ["Numerator"])          # exactly one population resolved
        self.assertIsInstance(q.exec(), dict)                     # ...still a dict
        self.assertEqual(list(q.exec()), ["Numerator"])
        # An explicitly named single target is the ONLY DataFrame-returning form.
        self.assertNotIsInstance(q.exec("Numerator"), dict)

    def test_exec_no_targets_on_a_library_with_one_define_is_still_a_dict(self):
        solo = _FakeProvider(defines=("OnlyDefine",), materialized=())
        res = _client(solo).cql(cql='define "x": 1').exec()
        self.assertIsInstance(res, dict)
        self.assertEqual(list(res), ["OnlyDefine"])

    def test_persist_as_single_target_creates_managed_table(self):
        prov = _FakeProvider()
        spark = _FakeSpark()
        client = CqlClient.builder().provider(prov).spark(spark).build()
        df = client.cql(cql='define "x": 1').exec("Numerator", persist_as="cat.sch.numerator")
        self.assertIn("CREATE TABLE `cat`.`sch`.`numerator` AS SELECT * FROM `Numerator`", spark.calls)
        self.assertEqual(df.sql, "table:cat.sch.numerator")

    def test_persist_as_rejected_for_multi_target(self):
        q = _client(_FakeProvider()).cql(cql='define "x": 1')
        with self.assertRaises(TypeError):
            q.exec("Numerator", "Denominator", persist_as="a.b.c")


class ArtifactTests(unittest.TestCase):
    def test_dag_exposes_defines_and_materialized_barriers(self):
        q = _client(_FakeProvider()).cql(cql='define "x": 1')
        dag = q.dag()                                              # alias of describe()
        self.assertIn("Numerator", dag.defines)
        self.assertEqual(dag.materialized_defines, ["ConditionList"])
        self.assertIs(q.describe(), dag)                           # memoized

    def test_elm_returns_bound_elm(self):
        q = _client(_FakeProvider()).cql(elm='{"library": {"x": 1}}')
        self.assertEqual(q.elm(), '{"library": {"x": 1}}')

    def test_populations_helper_intersects_well_known_names(self):
        q = _client(_FakeProvider()).cql(cql='define "x": 1')
        self.assertEqual(q.populations(), ["Initial Population", "Denominator", "Numerator"])

    def test_sql_script_is_not_exposed(self):
        # The compiled SQL script stays internal (BUILD-CLIENT-QUERY): no sql()/script surface on the handle.
        q = _client(_FakeProvider()).cql(cql='define "x": 1')
        self.assertFalse(hasattr(q, "sql"))
        self.assertFalse(hasattr(q, "script"))


class ContextAndOptionTests(unittest.TestCase):
    def test_no_context_is_sent_UNSET_not_forced_patient(self):
        prov = _FakeProvider()
        with mock.patch.dict(os.environ, {}, clear=True):
            _client(prov).cql(cql='define "One": 1 + 1').exec("One")
        self.assertIsNone(prov.seen.context)
        self.assertIsNone(prov.seen.to_json()["context"])

    def test_explicit_context_is_carried(self):
        prov = _FakeProvider()
        _client(prov).cql(cql='define "x": 1', context="Population").exec("x")
        self.assertEqual(prov.seen.context, "Population")

    def test_option_alias_and_data_model_knobs(self):
        prov = _FakeProvider()
        client = (CqlClient.builder().provider(prov).spark(_FakeSpark())
                  .option(config.CONF_CONTEXT, "Practitioner")
                  .data_model("OMOP").build())
        lib = client.with_library(cql='define "x": 1')
        self.assertEqual(lib.context, "Practitioner")
        self.assertEqual(lib.overrides[config.OVERRIDE_DATA_MODEL], "OMOP")


class StringFormTests(unittest.TestCase):
    def test_cql_string_compiles_against_a_passed_client(self):
        prov = _FakeProvider()
        df = run_cql('define "Two": 2 + 2', client=_client(prov)).exec("Numerator")
        self.assertEqual(prov.seen.cql, 'define "Two": 2 + 2')
        self.assertIsNone(prov.seen.context)                       # string form imposes no context
        self.assertEqual(df.sql, "SELECT * FROM `Numerator`")

    def test_cql_library_source_wraps_directly_without_a_client(self):
        lib = _client(_FakeProvider()).with_library(cql='define "x": 1')
        q = run_cql(lib)                                           # a bound Library needs no client
        self.assertIsInstance(q, CqlQuery)
        self.assertIs(q._lib, lib)

    def test_cql_lazy_client_fails_loudly_without_endpoint(self):
        # The contract under test is LOUDNESS: with nothing wired, `cql()` must refuse and say what to
        # set, never quietly return a handle that fails later somewhere less legible.
        #
        # The exception CLASS is deliberately not pinned. This package resolves the zero-config
        # Databricks tier before the config tier (decision 10), so with no endpoint the first thing to
        # fail is App-registry discovery (AppLookupError naming the App) rather than config resolution
        # (ConfigResolutionError). Both are loud and both name the fix; asserting one class would be
        # asserting which tier ran, which is not the behavior a caller depends on.
        from cql_databricks.compiler._workspace import AppLookupError
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises((ConfigResolutionError, AppLookupError, ValueError)) as caught:
                run_cql('define "x": 1')                           # no client, no endpoint → loud FAIL
        self.assertRegex(str(caught.exception), r"endpoint|appName|provider\.",
                         "the failure must name the setting that would fix it")

    def test_cql_with_explicit_endpoint_skips_discovery(self):
        """The explicit path must not touch discovery or the Databricks SDK (decision 10). This is the
        lane the OSS end-to-end conformance suite drives against a locally hosted compile service, so a
        regression here would silently make the platform-free path unreachable."""
        import cql_databricks.compiler.zeroconfig as zc
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(zc, "connect", side_effect=AssertionError("discovery must not run")):
                q = run_cql('define "Two": 2 + 2', client=_client(_FakeProvider()))
        self.assertIsInstance(q, CqlQuery)

    def test_context_with_a_bound_library_raises(self):
        lib = _client(_FakeProvider()).with_library(cql='define "x": 1')
        with self.assertRaises(TypeError):
            run_cql(lib, context="Patient")


class CqlLiteralTests(unittest.TestCase):
    def test_literal_rendering(self):
        self.assertEqual(_cql_literal(True), "true")
        self.assertEqual(_cql_literal(False), "false")
        self.assertEqual(_cql_literal(40), "40")
        self.assertEqual(_cql_literal(None), "null")
        self.assertEqual(_cql_literal("@2026-01-01"), "@2026-01-01")   # str is verbatim CQL
        self.assertEqual(_cql_literal("'active'"), "'active'")


class LoadLibraryGuardTests(unittest.TestCase):
    def test_needs_exactly_one_body_source(self):
        with self.assertRaises(ValueError):
            load_library(conf={}, provider=_FakeProvider())            # none of resource/elm/cql
        lib = load_library(elm="{}", conf={}, provider=_FakeProvider())
        self.assertIsInstance(lib, Library)
        self.assertEqual(lib.elm, "{}")


if __name__ == "__main__":
    unittest.main()
