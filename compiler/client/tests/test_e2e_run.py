"""The E2E driver's SQL-substrate flow (scripts/e2e_run.py, PIPE-RUNTIME-COMPILE) — pyspark-free.

The live gate (RuntimeCompileE2ESpec) needs a Connect server + pyspask; this unit test covers the driver's
LOGIC with fakes: the compiled SQL statements run, then the per-(subject, define) result is assembled. No pyspark import fires (the SQL path never touches it).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import e2e_run  # noqa: E402

from cql_databricks.compiler.providers import (  # noqa: E402
    CompileProvider,
    SqlCompileResult,
    SqlStatement,
)


class _FakeDF:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSpark:
    def __init__(self, rows):
        self.calls = []
        self._rows = rows

    def sql(self, text):
        self.calls.append(text)
        return _FakeDF(self._rows)


class _FakeProvider(CompileProvider):
    def __init__(self, result):
        self._result = result

    def compile(self, request):
        raise AssertionError("proto compile() must not be called on the SQL substrate")

    def compile_sql(self, request):
        return self._result


class RunE2eSqlTests(unittest.TestCase):
    def _run(self, spark):
        provider = _FakeProvider(SqlCompileResult(
            statements=[SqlStatement("Numerator", "view",
                                     "CREATE OR REPLACE TEMP VIEW Numerator AS SELECT 1")],
            target="Numerator", defines=["Numerator"]))
        return e2e_run.run_e2e(spark, provider, elm="{}", targets=["Numerator"],
                               context="Unfiltered", conf={})

    def test_the_script_runs_then_the_target_select(self):
        spark = _FakeSpark(rows=[{"__subject_id": "*", "Numerator": 2}])
        self._run(spark)
        self.assertEqual(spark.calls, ["CREATE OR REPLACE TEMP VIEW Numerator AS SELECT 1",
                                       "SELECT * FROM `Numerator`"])

    def test_result_assembled_by_subject_and_define(self):
        spark = _FakeSpark(rows=[{"__subject_id": "p1", "Numerator": True},
                                 {"__subject_id": "p2", "Numerator": False}])
        out = self._run(spark)
        self.assertEqual(out, {"p1": {"Numerator": True}, "p2": {"Numerator": False}})


if __name__ == "__main__":
    unittest.main()
