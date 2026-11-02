"""CLIENT-SESSION-STATE, CLIENT-SESSION-RESET — the session record. Stdlib unittest with a fake session."""
import unittest
from unittest import mock

from cql_databricks.compiler.relations import VIEW_PREFIX
from cql_databricks.compiler.session_libraries import register_cell, session_provider
from cql_databricks.compiler.session_state import SessionRecord, reset

COMMON = "library Common version '1.0.0'\ndefine \"X\": 1\n"


class _Frame:
    def __init__(self, spark, rows):
        self._spark, self._rows = spark, list(rows)

    def createOrReplaceTempView(self, name):
        self._spark.views[name] = self._rows

    def collect(self):
        return [dict(zip(("kind", "name", "payload"), r)) for r in self._rows]


class _SessionSpark:
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


class RecordTests(unittest.TestCase):
    def test_cells_are_read_back_by_a_fresh_provider(self):
        """A Python restart keeps the Spark session, so a new provider must see the cells it holds."""
        spark = _SessionSpark()
        register_cell(COMMON, spark=spark)
        self.assertEqual([r.name for r in session_provider(spark=spark).declared()], ["Common"])

    def test_two_sessions_do_not_share_cells(self):
        one, two = _SessionSpark(), _SessionSpark()
        register_cell(COMMON, spark=one)
        self.assertEqual(session_provider(spark=two).declared(), [])

    def test_declaring_a_cell_with_no_session_raises(self):
        with mock.patch("pyspark.sql.SparkSession.getActiveSession", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "CLIENT-SESSION-STATE"):
                register_cell(COMMON)

    def test_every_write_moves_the_version_and_drop_is_safe_twice(self):
        spark = _SessionSpark()
        record = SessionRecord(spark)
        self.assertEqual(record.version(), 0)
        record.set_scope('define "Y": 2')
        register_cell(COMMON, spark=spark)
        self.assertEqual(record.version(), 2)
        record.drop()
        record.drop()
        self.assertEqual(record.scope(), "")
        self.assertEqual(session_provider(spark=spark).declared(), [])

    def test_reset_drops_the_relation_views_and_the_cells_and_nothing_else(self):
        """CLIENT-SESSION-RESET — the client's own temporary views and the record; a user's view is left alone."""
        spark = _SessionSpark()
        register_cell(COMMON, spark=spark)
        spark.views[VIEW_PREFIX + "abc"] = []
        spark.views["users_own_view"] = []
        reset(spark)
        reset(spark)
        self.assertEqual(set(spark.views), {"users_own_view"})
        self.assertEqual(session_provider(spark=spark).declared(), [])


if __name__ == "__main__":
    unittest.main()
