"""The SQL run path of ``cql()`` (EMIT-SQL-EXECUTE).

Stdlib unittest with FAKES (no pyspark, no compile host), mirroring the rest of the client suite: a fake
provider returns a ``SqlCompileResult``, a fake session records the ``spark.sql`` calls, and we assert
the statements run in order and the target is read last. The live end-to-end diff is the conformance gate.
"""
import unittest

from cql_databricks.compiler.providers import (
    CompileProvider,
    CompileRequest,
    SqlCompileResult,
    SqlStatement,
)
from cql_databricks.compiler.cql import _run_statements, _select, cql


class _FakeDataFrame:
    def __init__(self, sql):
        self.sql = sql


class _FakeSpark:
    """Records every ``sql(text)`` call and returns a marker DataFrame carrying the text."""

    def __init__(self):
        self.calls = []

    def sql(self, text):
        self.calls.append(text)
        return _FakeDataFrame(text)


class _FakeProvider(CompileProvider):
    def __init__(self, result: SqlCompileResult):
        self._result = result
        self.seen = None

    def compile_sql(self, request: CompileRequest) -> SqlCompileResult:
        self.seen = request
        return self._result


class _FakeLibrary:
    def __init__(self, provider):
        self.elm = "{}"
        self.context = "Patient"
        self.overrides = {}
        self.provider = provider
        self.cql = None
        self.parameters = {}


def _result():
    return SqlCompileResult(
        statements=[
            SqlStatement("__frame_base", "view", "CREATE OR REPLACE TEMP VIEW __frame_base AS SELECT 1"),
            SqlStatement("AllEncounters", "view", "CREATE OR REPLACE TEMP VIEW AllEncounters AS SELECT 2"),
            SqlStatement("Numerator", "view", "CREATE OR REPLACE TEMP VIEW Numerator AS SELECT 3"),
        ],
        target="Numerator",
        defines=["AllEncounters", "Numerator"],
    )


class SqlRunTests(unittest.TestCase):
    def test_run_statements_executes_in_order_then_select_reads_target(self):
        spark = _FakeSpark()
        _run_statements(spark, _result())
        df = _select(spark, _result().target)
        # Every statement ran, in order, then a final SELECT over the (quoted) target.
        self.assertEqual(spark.calls[:3], [s.sql for s in _result().statements])
        self.assertEqual(spark.calls[-1], "SELECT * FROM `Numerator`")
        self.assertEqual(df.sql, "SELECT * FROM `Numerator`")

    def test_select_identifier_is_backtick_quoted(self):
        spark = _FakeSpark()
        _select(spark, "Initial Population")
        self.assertEqual(spark.calls[-1], "SELECT * FROM `Initial Population`")

    def test_exec_single_target_compiles_and_selects(self):
        provider = _FakeProvider(_result())
        spark = _FakeSpark()
        df = cql(_FakeLibrary(provider), spark=spark).exec("Numerator")
        self.assertIsNotNone(provider.seen)                 # the SQL provider path was taken
        self.assertEqual(provider.seen.target, "Numerator")
        self.assertEqual(df.sql, "SELECT * FROM `Numerator`")

    def test_script_property_joins_statements(self):
        script = _result().script
        self.assertIn("CREATE OR REPLACE TEMP VIEW Numerator AS SELECT 3;", script)
        self.assertEqual(script.count(";"), 3)


if __name__ == "__main__":
    unittest.main()
