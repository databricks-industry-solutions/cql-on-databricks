"""L1 unit tests for the `%%cql` cell magic (SVC-NOTEBOOK-CQL-MAGIC, SVC-NOTEBOOK-CELL-LIBRARY,
SVC-NOTEBOOK-SESSION-SCOPE).

IPython is a notebook-only dependency of this package (imported lazily by `load_ipython_extension`), so
these skip where it is absent rather than pulling it into the plugin's install. What is tested: a cell is
compiled and declared and never run, a cell that fails to compile declares nothing, and the magic refuses
options.
"""
import unittest
from unittest import mock

try:
    import IPython  # noqa: F401
    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

import cql_databricks.compiler.session_libraries as session_mod
from cql_databricks.compiler.providers import CompileError

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
        # session into a unit test to exercise a few lines of cell handling.
        self.magic = CqlMagics.__new__(CqlMagics)
        self.magic.shell = mock.Mock(user_ns={"spark": self.spark})

    def _declared(self):
        return [str(r) for r in session_mod.session_provider(spark=self.spark).declared()]

    def test_a_library_cell_compiles_and_declares_and_runs_nothing(self):
        """SVC-NOTEBOOK-CQL-MAGIC, SVC-NOTEBOOK-CELL-LIBRARY — a cell is a declaration, like a Python def."""
        with mock.patch("cql_databricks.compiler.magic._bind_cql") as bind:
            self.magic.cql("", COMMON)

        bind.assert_called_once_with(COMMON, spark=self.spark)
        bind.return_value.describe.assert_called_once_with()
        bind.return_value.exec.assert_not_called()
        self.assertEqual(self._declared(), ["DiabetesCommon|1.0.0"])

    def test_a_library_cell_that_fails_to_compile_declares_nothing(self):
        """The cell fails where the error is, and leaves the session as it was."""
        with mock.patch("cql_databricks.compiler.magic._bind_cql") as bind:
            bind.return_value.describe.side_effect = CompileError("CQL translation produced errors")
            with self.assertRaises(CompileError):
                self.magic.cql("", COMMON)

        self.assertEqual(self._declared(), [])

    def test_a_headerless_cell_joins_the_session_library_and_runs_nothing(self):
        """SVC-NOTEBOOK-SESSION-SCOPE — binding the fragment joins it (and compiles it); nothing runs."""
        with mock.patch("cql_databricks.compiler.magic._bind_cql") as bind:
            self.magic.cql("", 'define "bar": "foo" + 1')

        bind.assert_called_once_with('define "bar": "foo" + 1', spark=self.spark)
        bind.return_value.exec.assert_not_called()
        self.assertEqual(self._declared(), [])

    def test_options_are_a_usage_error(self):
        """SVC-NOTEBOOK-CQL-MAGIC — the magic takes no options; Python chooses and binds what runs."""
        from IPython.core.error import UsageError
        for line in ("--library", "--df out", "--target a"):
            with self.subTest(line=line), mock.patch("cql_databricks.compiler.magic._bind_cql") as bind:
                with self.assertRaisesRegex(UsageError, "takes no options"):
                    self.magic.cql(line, COMMON)
                bind.assert_not_called()
        self.assertEqual(self._declared(), [])


if __name__ == "__main__":
    unittest.main()
