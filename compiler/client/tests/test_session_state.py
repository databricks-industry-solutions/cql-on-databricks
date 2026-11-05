"""CLIENT-SESSION-STATE, CLIENT-SESSION-RESET — the session record. Stdlib unittest with a fake session."""
import unittest
from unittest import mock

from cql_databricks.compiler.relations import VIEW_PREFIX
from cql_databricks.compiler.session_libraries import register_cell, session_provider
from cql_databricks.compiler.session_state import SessionRecord, held_record, reset

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


class _CountingSpark(_SessionSpark):
    """Counts reads of the record: each is a Spark Connect round trip on a real session."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def table(self, name):
        self.reads += 1
        return super().table(name)


class HeldRecordTests(unittest.TestCase):
    """CLIENT-SESSION-STATE — one notebook operation reads the record at most once."""

    def test_within_a_block_the_record_is_read_once_and_sees_its_own_writes(self):
        spark = _CountingSpark()
        with held_record():
            self.assertEqual(session_provider(spark=spark).declared(), [])
            register_cell(COMMON, spark=spark)
            self.assertEqual([r.name for r in session_provider(spark=spark).declared()], ["Common"])
            self.assertEqual(SessionRecord(spark).version(), 1)
        self.assertEqual(spark.reads, 1)

    def test_outside_a_block_every_read_goes_to_the_session(self):
        spark = _CountingSpark()
        session_provider(spark=spark).declared()
        session_provider(spark=spark).declared()
        self.assertEqual(spark.reads, 2)

    def test_the_next_block_reads_the_session_again(self):
        """Nothing is kept between operations: a write by anything else is seen by the next cell."""
        spark = _CountingSpark()
        with held_record():
            session_provider(spark=spark).declared()
        spark.views.clear()
        register_cell(COMMON, spark=spark)
        with held_record():
            self.assertEqual([r.name for r in session_provider(spark=spark).declared()], ["Common"])
        self.assertEqual(spark.reads, 3)

    def test_blocks_nest_and_hold_until_the_outermost_ends(self):
        spark = _CountingSpark()
        with held_record():
            with held_record():
                session_provider(spark=spark).declared()
            session_provider(spark=spark).declared()
        self.assertEqual(spark.reads, 1)

    def test_a_drop_within_a_block_leaves_nothing_to_read(self):
        spark = _CountingSpark()
        register_cell(COMMON, spark=spark)
        with held_record():
            SessionRecord(spark).drop()
            self.assertEqual(session_provider(spark=spark).declared(), [])
        self.assertEqual(spark.reads, 1)

    def test_a_write_through_one_handle_is_what_another_handle_on_the_session_reads(self):
        """Two Python objects for one Spark session: a write forgets what either held."""
        spark = _CountingSpark()
        other = mock.Mock(wraps=spark, views=spark.views)
        with held_record():
            self.assertEqual(session_provider(spark=other).declared(), [])
            register_cell(COMMON, spark=spark)
            self.assertEqual([r.name for r in session_provider(spark=other).declared()], ["Common"])

    def test_a_failed_read_is_not_held(self):
        spark = _CountingSpark()
        spark.table = mock.Mock(side_effect=RuntimeError("[PERMISSION_DENIED] no"))
        with held_record():
            with self.assertRaisesRegex(RuntimeError, "PERMISSION_DENIED"):
                SessionRecord(spark).rows()
            with self.assertRaisesRegex(RuntimeError, "PERMISSION_DENIED"):
                SessionRecord(spark).rows()


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
