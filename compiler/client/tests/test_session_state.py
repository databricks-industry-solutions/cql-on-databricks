"""CLIENT-SESSION-STATE, CLIENT-SESSION-MOUNT — the session record, and the rules ``mount`` applies before it
touches Spark. Stdlib unittest with a fake session; the mount itself runs against Spark in the live check.
"""
import os
import tempfile
import unittest
from unittest import mock

from cql_databricks.compiler.mount import mount
from cql_databricks.compiler.session_libraries import register_cell, session_provider
from cql_databricks.compiler.session_state import SessionRecord

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


class _Model:
    def resource_schema(self, fhir_type=None):
        return {"Patient": object(), "Condition": object()} if fhir_type is None else object()


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
        record.set_mount({"types": {"Patient": "retrieve_Patient__cqlmount"}})
        register_cell(COMMON, spark=spark)
        self.assertEqual(record.version(), 2)
        self.assertEqual(record.mount()["types"], {"Patient": "retrieve_Patient__cqlmount"})
        record.drop()
        record.drop()
        self.assertIsNone(record.mount())
        self.assertEqual(session_provider(spark=spark).declared(), [])


class MountRuleTests(unittest.TestCase):
    """The string rules: never a guess, and every failure before anything is registered."""

    def test_a_fhir_string_without_a_slash_is_not_a_path(self):
        with self.assertRaisesRegex(ValueError, r"not a path.*write \./data"):
            mount(_SessionSpark(), "data", client=_Model())

    def test_a_dict_table_name_must_have_three_parts(self):
        with self.assertRaisesRegex(ValueError, "catalog.schema.table"):
            mount(_SessionSpark(), {"Patient": "silver.patient"}, client=_Model())

    def test_an_unmodeled_file_in_the_folder_fails_naming_it(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("Patient.ndjson", "Widget.ndjson"):
                open(os.path.join(d, name), "w").close()
            with self.assertRaisesRegex(ValueError, "Widget, which the compiler does not model"):
                mount(_SessionSpark(), d, client=_Model())

    def test_an_empty_folder_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "no <Type>.ndjson"):
                mount(_SessionSpark(), d, client=_Model())

    def test_something_else_is_refused(self):
        with self.assertRaisesRegex(TypeError, "cannot mount int"):
            mount(_SessionSpark(), 42, client=_Model())


if __name__ == "__main__":
    unittest.main()
