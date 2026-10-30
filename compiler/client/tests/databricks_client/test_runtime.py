"""L1 unit tests for automatic opaque-UDF catalog registration (SVC-RUNTIME-UDF). Drives
`register_runtime_udfs` against fake sessions (no Spark), and checks connect() invokes it. The key
behaviours: it fires ONLY on a JVM-backed (py4j) session, is a best-effort no-op otherwise, and never
raises.
"""
import unittest
from unittest import mock

import cql_databricks.compiler._udfs as runtime
import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.compiler._udfs import register_runtime_udfs


def _jvm_backed_spark():
    """A fake classic notebook session: a py4j `_jvm` gateway (Mock auto-vivifies the class path) plus a
    `_jsparkSession` handle. The catalog call resolves to `_jvm.databricks...CqlRuntimeUdfs.registerAll`."""
    spark = mock.Mock()
    spark._jsparkSession = mock.sentinel.jspark
    return spark


class RegisterRuntimeUdfsTest(unittest.TestCase):
    def test_none_session_is_noop(self):
        self.assertFalse(register_runtime_udfs(None))

    def test_spark_connect_session_is_noop(self):
        # A Spark Connect session (serverless / databricks-connect) has no py4j gateway.
        connect_spark = mock.Mock(spec=["sql", "createDataFrame", "range"])  # no _jvm / _jsparkSession
        self.assertFalse(register_runtime_udfs(connect_spark))

    def test_jvm_backed_session_registers_catalog(self):
        spark = _jvm_backed_spark()
        self.assertTrue(register_runtime_udfs(spark))
        catalog = spark._jvm.com.databricks.cql.runtime.udf.CqlRuntimeUdfs
        catalog.registerAll.assert_called_once_with(mock.sentinel.jspark)

    def test_registerall_error_is_swallowed(self):
        spark = _jvm_backed_spark()
        spark._jvm.com.databricks.cql.runtime.udf.CqlRuntimeUdfs.registerAll.side_effect = \
            RuntimeError("ClassNotFound: JAR not installed / cluster not restarted")
        self.assertFalse(register_runtime_udfs(spark))  # best-effort: no raise

    def test_env_disable_skips_registration(self):
        spark = _jvm_backed_spark()
        with mock.patch.dict("os.environ", {runtime._ENV_DISABLE: "1"}):
            self.assertFalse(register_runtime_udfs(spark))
        spark._jvm.com.databricks.cql.runtime.udf.CqlRuntimeUdfs.registerAll.assert_not_called()


class ConnectWiringTest(unittest.TestCase):
    def setUp(self):
        client_mod._default_client = None

    tearDown = setUp

    def test_connect_invokes_autoregistration_with_passed_spark(self):
        from .test_client import _ws  # reuse the fully-resolving fake workspace
        spark = _jvm_backed_spark()
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
             mock.patch("cql_databricks.compiler.zeroconfig.register_runtime_udfs") as reg:
            client_mod.connect(spark=spark, udf_schema="acme.runtime")
        reg.assert_called_once_with(spark)


if __name__ == "__main__":
    unittest.main()
