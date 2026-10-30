"""L1 unit tests for the `%%cql` cell magic (SVC-NOTEBOOK-CQL-MAGIC, SVC-NOTEBOOK-CELL-LIBRARY).

IPython is a notebook-only dependency of this package (imported lazily by `load_ipython_extension`), so
these skip where it is absent rather than pulling it into the plugin's install. The execution path is
covered on serverless by the example notebooks; what is unit-testable here is the CELL REGISTRATION —
which cells become includable libraries, and that `--library` declares without compiling.
"""
import unittest
from unittest import mock

try:
    import IPython  # noqa: F401
    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

import cql_databricks.compiler.session_libraries as session_mod

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


@unittest.skipUnless(HAS_IPYTHON, "IPython is a notebook-only dependency")
class CellMagicTest(unittest.TestCase):
    def setUp(self):
        self.spark = _SessionSpark()
        from cql_databricks.compiler.magic import CqlMagics
        # Built without Magics.__init__: its `shell` is a traitlets-validated Configurable, and the magic
        # uses nothing from the base but `self.shell`. A real InteractiveShell would drag a whole IPython
        # session into a unit test to exercise four lines of argument handling.
        self.magic = CqlMagics.__new__(CqlMagics)
        self.magic.shell = mock.Mock(user_ns={"spark": self.spark})

    def test_library_declares_without_compiling(self):
        """A cell of shared definitions is not a thing to execute — no compile, no round trip."""
        with mock.patch("cql_databricks.compiler.magic._run_cql") as run:
            self.magic.cql("--library", COMMON)

        run.assert_not_called()
        self.assertEqual([str(r) for r in session_mod.session_provider(spark=self.spark).declared()],
                         ["DiabetesCommon|1.0.0"])

    def test_library_without_a_header_is_a_usage_error(self):
        from IPython.core.error import UsageError
        with self.assertRaises(UsageError):
            self.magic.cql("--library", 'define "Two": 2 + 2')

    def test_a_run_cell_is_registered_too(self):
        """Registration is unconditional, so a measure cell is both runnable and includable with no
        second syntax."""
        with mock.patch("cql_databricks.compiler.magic._run_cql") as run:
            run.return_value.default_targets.return_value = ["Denominator"]
            self.magic.cql("", COMMON)

        run.assert_called_once()
        self.assertEqual([r.name for r in session_mod.session_provider(spark=self.spark).declared()],
                         ["DiabetesCommon"])


    def test_a_headerless_cell_runs_its_own_defines(self):
        """SVC-NOTEBOOK-SESSION-SCOPE — the cell joins the session library and shows only what it declared."""
        with mock.patch("cql_databricks.compiler.magic._run_cql") as run:
            run.return_value.default_targets.return_value = ["bar"]
            self.magic.cql("--df out", 'define "bar": "foo" + 1')

        run.return_value.exec.assert_called_once_with("bar")
        self.assertIs(self.magic.shell.user_ns["out"], run.return_value.exec.return_value)

    def test_several_targets_run_in_one_exec(self):
        with mock.patch("cql_databricks.compiler.magic._run_cql") as run:
            run.return_value.exec.return_value = {"a": mock.Mock(), "b": mock.Mock()}
            self.magic.cql("--target a --target b", 'define "a": 1\ndefine "b": 2')

        run.return_value.exec.assert_called_once_with("a", "b")

    def test_a_cell_of_declarations_alone_runs_nothing(self):
        with mock.patch("cql_databricks.compiler.magic._run_cql") as run:
            run.return_value.default_targets.return_value = []
            self.magic.cql("", "valueset \"VS\": 'urn:x'")

        run.return_value.exec.assert_not_called()

    def test_df_on_a_cell_with_no_defines_is_a_usage_error(self):
        from IPython.core.error import UsageError
        with mock.patch("cql_databricks.compiler.magic._run_cql") as run:
            run.return_value.default_targets.return_value = []
            with self.assertRaises(UsageError):
                self.magic.cql("--df out", "valueset \"VS\": 'urn:x'")


if __name__ == "__main__":
    unittest.main()
