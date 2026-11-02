"""L1 unit tests for the notebook compile shim (SVC-NOTEBOOK-RUNTIME-BIND).

Drives the one adaptation that still belongs to the accelerator — the `CACHE TABLE` -> temp view barrier
rewrite (SVC-RUN-SERVERLESS-BARRIER) — plus UC-backed discovery of the udf schema. The `sources.udfs.schema`
injection is not done here: the OSS client owns that knob (`.udf_schema()`,
EMIT-UDF-QUALIFY), and `test_the_request_body_is_the_oss_providers_own` is what keeps a second copy of it
from growing back here. No workspace and no Spark: the Spark session is a stub and `requests.post` is
captured.
"""
import unittest
from unittest import mock

from cql_databricks.compiler.providers import CompileRequest, RemoteProvider

from cql_databricks.compiler.compile_shim import CqlDatabricksCompileProvider, discover_udf_schema


class _Row(dict):
    """A stand-in for a Spark Row — indexable by column name, which is all the discovery reads."""


class _Spark:
    """A Spark session stub returning fixed rows for the discovery query."""

    def __init__(self, rows):
        self._rows = rows
        self.sql_seen = None

    def sql(self, text):
        self.sql_seen = text
        return mock.Mock(collect=lambda: self._rows)


class DiscoverUdfSchemaTest(unittest.TestCase):
    """The `cql_*` catalog's location is READ FROM UC, so a notebook names no catalog
    (SVC-ZERO-CONFIG-NOTEBOOK)."""

    def test_resolves_catalog_and_schema_from_information_schema(self):
        spark = _Spark([_Row(routine_catalog="acme", routine_schema="runtime")])
        self.assertEqual(discover_udf_schema(spark), "acme.runtime")
        self.assertIn("system.information_schema.routines", spark.sql_seen)
        self.assertIn("cql_age_at", spark.sql_seen)

    def test_unregistered_catalog_is_loud(self):
        """An absent catalog fails HERE with the fix, not later as an opaque UNRESOLVED_ROUTINE
        (CORE-NO-SILENT-MISLOWER)."""
        with self.assertRaises(RuntimeError) as ctx:
            discover_udf_schema(_Spark([]))
        self.assertIn("register-uc-udfs.sh", str(ctx.exception))

    def test_no_session_is_loud(self):
        with self.assertRaises(RuntimeError) as ctx:
            discover_udf_schema(None)
        self.assertIn("Spark session", str(ctx.exception))


class CompileProviderTest(unittest.TestCase):
    def setUp(self):
        base = RemoteProvider("https://compiler.example", mock.Mock(auth_headers=lambda: {}))
        self.provider = CqlDatabricksCompileProvider(base)

    def _post(self, response_body, path="/compile-sql"):
        """Run one `_post` through a captured `requests.post`; return (request_body, result)."""
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None, allow_redirects=True):
            self.assertFalse(allow_redirects)  # SVC-DISCOVERY-PLUGIN — preserve the auth refusal.
            captured["url"] = url
            captured["body"] = json
            return mock.Mock(status_code=200, json=lambda: response_body)

        with mock.patch("requests.post", fake_post):
            out = self.provider._post(path, CompileRequest(elm="{}", target="Numerator"))
        return captured, out

    def test_the_request_body_is_the_oss_providers_own(self):
        """The shim post-processes the RESPONSE only. The request body must be exactly what the OSS
        provider serialized — `sources.udfs.schema` now rides there as ordinary config (`.udf_schema()`),
        so a second copy written on the body here would be one wire with two authors."""
        captured, _ = self._post({"statements": []})
        self.assertEqual(captured["url"], "https://compiler.example/compile-sql")
        self.assertEqual(captured["body"], CompileRequest(elm="{}", target="Numerator").to_json())

    def test_rewrites_cache_table_barriers_to_temp_views(self):
        """SVC-RUN-SERVERLESS-BARRIER — serverless rejects CACHE TABLE AS SELECT; the body is unchanged,
        so the rewrite is result-identical."""
        _, out = self._post({"statements": [
            {"kind": "cache_table", "sql": "CACHE TABLE Numerator AS\nSELECT 1"},
            {"kind": "view", "sql": "CREATE OR REPLACE TEMP VIEW IsAdult AS\nSELECT 2"},
        ]})
        self.assertEqual([s["kind"] for s in out["statements"]], ["view", "view"])
        self.assertEqual(out["statements"][0]["sql"],
                         "CREATE OR REPLACE TEMP VIEW Numerator AS\nSELECT 1")
        self.assertEqual(out["statements"][1]["sql"],
                         "CREATE OR REPLACE TEMP VIEW IsAdult AS\nSELECT 2")

    def test_barriers_are_left_alone_off_the_sql_compile_path(self):
        """Only `/compile-sql` carries executable statements; `/describe` etc. pass through untouched."""
        _, out = self._post({"statements": [{"kind": "cache_table", "sql": "CACHE TABLE X AS SELECT 1"}]},
                            path="/describe")
        self.assertEqual(out["statements"][0]["sql"], "CACHE TABLE X AS SELECT 1")


if __name__ == "__main__":
    unittest.main()
