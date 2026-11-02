"""EVALUATOR-CLIENT-EVALUATE, `DEC-ONE-VERB-ENGINE-OPTION`, `DEC-ENGINE-SESSION-OPTIONS`,
BUILD-CLIENT-SESSION-PARAMETERS — the engine setting, the session's engine settings, and the hand-off to an
installed engine. Stdlib unittest with fakes: no pyspark, no compile host, no evaluator."""
import os
import unittest
from unittest import mock

from cql_databricks.compiler import CqlClient, config
from cql_databricks.compiler.engines import (EngineNotInstalled, EngineRequest, engine_for, parse_flag,
                                             parse_period)
from cql_databricks.compiler.providers import CompileRequest

from cql_databricks.compiler.providers import CompileProvider, LibraryGraph, SqlCompileResult, SqlStatement


class _FakeSpark:
    def sql(self, text):
        return text


class _FakeProvider(CompileProvider):
    """Records the last request; compiles any target to one view. Self-contained: `scripts/test.sh` and the
    Gradle check load this directory differently, so a test module here imports no sibling."""

    def __init__(self):
        self.seen = None

    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        self.seen = request
        name = request.target or "Numerator"
        return SqlCompileResult(statements=[SqlStatement(name, "view", f"CREATE OR REPLACE TEMP VIEW `{name}` AS SELECT 1")],
                                target=name, defines=[name])

    def describe(self, request: CompileRequest) -> LibraryGraph:
        return LibraryGraph(defines=["Numerator", "Denominator"], dependencies={}, boundaries=[],
                            materialized_defines=[], compiler_version="test")

CQL = "library Probe version '1.0.0'\n\ncontext Patient\n\ndefine \"Numerator\": true\n"


class _RecordingEngine:
    def __init__(self):
        self.requests = []

    def exec(self, request: EngineRequest) -> dict:
        self.requests.append(request)
        return {t: f"frame:{t}" for t in request.targets}


def _builder(provider):
    return CqlClient.builder().provider(provider).spark(_FakeSpark())


class EngineSettingTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in (config.ENV_ENGINE, config.ENV_MEASUREMENT_PERIOD,
                    config.ENV_RECOMPUTE):
            os.environ.pop(key, None)

    def test_the_default_engine_is_local_and_runs_here(self):
        prov = _FakeProvider()
        lib = _builder(prov).build().with_library(cql=CQL)
        self.assertEqual(lib.engine, "local")

    def test_a_session_sets_every_engine_setting_once(self):
        client = (_builder(_FakeProvider()).engine("evaluator")
                  .measurement_period("2026-01-01", "2026-12-31").recompute().build())
        lib = client.with_library(cql=CQL)
        self.assertEqual((lib.engine, lib.measurement_period, lib.recompute),
                         ("evaluator", ("2026-01-01", "2026-12-31"), True))

    def test_a_call_overrides_the_session(self):
        client = _builder(_FakeProvider()).engine("evaluator").build()
        lib = client.with_library(cql=CQL, engine="local", recompute=False)
        self.assertEqual((lib.engine, lib.recompute), ("local", False))

    def test_the_environment_is_a_tier(self):
        os.environ[config.ENV_ENGINE] = "evaluator"
        lib = _builder(_FakeProvider()).build().with_library(cql=CQL)
        self.assertEqual(lib.engine, "evaluator")

    def test_the_spark_conf_reaches_a_built_client(self):
        """BUILD-RESOLVE — a built client's own settings sit over the Spark conf; they no longer replace it."""
        with mock.patch("cql_databricks.compiler.library._spark_conf",
                        return_value={config.CONF_ENGINE: "evaluator", config.CONF_RECOMPUTE: "true"}):
            lib = _builder(_FakeProvider()).recompute(False).build().with_library(cql=CQL)
        self.assertEqual((lib.engine, lib.recompute), ("evaluator", False))


class SessionPeriodTests(unittest.TestCase):
    """BUILD-CLIENT-SESSION-PARAMETERS — a session period rides as a session parameter, which the compiler
    applies only where the library declares it; a query's own period is an explicit parameter."""

    def test_a_session_period_is_sent_as_a_session_parameter(self):
        prov = _FakeProvider()
        client = _builder(prov).measurement_period("2026-01-01", "2026-12-31").build()
        client.cql(cql=CQL).exec("Numerator")
        self.assertEqual(prov.seen.session_parameters,
                         {"Measurement Period": "Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]"})
        self.assertNotIn("Measurement Period", prov.seen.parameters)

    def test_a_querys_period_wins_and_is_explicit(self):
        prov = _FakeProvider()
        client = _builder(prov).measurement_period("2026-01-01", "2026-12-31").build()
        q = client.cql(cql=CQL).with_measurement_period("2025-01-01", "2025-12-31")
        q.exec("Numerator")
        self.assertIn("2025", prov.seen.parameters["Measurement Period"])
        self.assertEqual(q._lib.measurement_period, ("2025-01-01", "2025-12-31"))

    def test_the_wire_omits_session_parameters_when_there_are_none(self):
        """BUILD-CLIENT-COMPAT — the body a caller sends today is byte-identical."""
        self.assertNotIn("sessionParameters", CompileRequest(cql="x").to_json())
        body = CompileRequest(cql="x", session_parameters={"B": "2", "A": "1"}).to_json()
        self.assertEqual(list(body["sessionParameters"]), ["A", "B"])


class DispatchTests(unittest.TestCase):
    def _query(self, **builder):
        b = _builder(_FakeProvider()).engine("evaluator")
        if "period" in builder:
            b = b.measurement_period(*builder["period"])
        return b.build().cql(CQL)

    def test_exec_hands_the_query_to_the_installed_engine(self):
        engine = _RecordingEngine()
        with mock.patch("cql_databricks.compiler.engines.engine_for", return_value=engine):
            frame = self._query(period=("2026-01-01", "2026-12-31")).exec("Numerator")
        self.assertEqual(frame, "frame:Numerator")
        (request,) = engine.requests
        self.assertEqual((request.cql, list(request.targets), request.measurement_period, request.recompute),
                         (CQL, ["Numerator"], ("2026-01-01", "2026-12-31"), False))

    def test_several_targets_return_a_dict_and_recompute_rides_along(self):
        engine = _RecordingEngine()
        with mock.patch("cql_databricks.compiler.engines.engine_for", return_value=engine):
            frames = self._query().with_recompute().exec("Numerator", "Denominator")
        self.assertEqual(frames, {"Numerator": "frame:Numerator", "Denominator": "frame:Denominator"})
        self.assertTrue(engine.requests[0].recompute)

    def test_the_local_compile_knobs_are_refused_not_ignored(self):
        with mock.patch("cql_databricks.compiler.engines.engine_for", return_value=_RecordingEngine()):
            with self.assertRaises(TypeError):
                self._query().exec("Numerator", persist_as="x.y.z")

    def test_an_engine_that_returns_too_little_fails(self):
        class _Short:
            def exec(self, request):
                return {}

        with mock.patch("cql_databricks.compiler.engines.engine_for", return_value=_Short()):
            with self.assertRaises(RuntimeError):
                self._query().exec("Numerator")

    def test_an_engine_not_installed_says_so(self):
        with mock.patch("importlib.metadata.entry_points", return_value=[]):
            with self.assertRaises(EngineNotInstalled) as caught:
                engine_for("evaluator")
        self.assertIn("no CQL engine named 'evaluator' is installed", str(caught.exception))


class ParseTests(unittest.TestCase):
    def test_period(self):
        self.assertEqual(parse_period("2026-01-01/2026-12-31"), ("2026-01-01", "2026-12-31"))
        self.assertIsNone(parse_period(""))
        with self.assertRaises(ValueError):
            parse_period("2026")

    def test_flag(self):
        self.assertTrue(parse_flag("TRUE"))
        self.assertFalse(parse_flag(None))
        with self.assertRaises(ValueError):
            parse_flag("maybe")


if __name__ == "__main__":
    unittest.main()
