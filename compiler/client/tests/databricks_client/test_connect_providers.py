"""CLIENT-ENVIRONMENT, CLIENT-RESOLVERS, CLIENT-ENVIRONMENT-RESOLVER: connect() runs in an environment, the default unless
one is named, and a resolver given replaces the environment's for its role."""
import os
import unittest
from unittest import mock

import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.compiler.data_resolvers import TableDataResolver
from cql_databricks.compiler.resolver_binder import ResolverBinder

from .test_runtime import _jvm_backed_spark


class ConnectResolversTest(unittest.TestCase):
    def _connect(self, **kwargs):
        from .test_client import _ws

        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
             mock.patch("cql_databricks.compiler.zeroconfig.register_runtime_udfs"):
            return client_mod.connect(spark=_jvm_backed_spark(), udf_schema="acme.runtime", **kwargs)

    def test_a_data_resolver_given_replaces_the_environments(self):
        client = self._connect(data=TableDataResolver({"Patient": "acme.fhir.patient"}))
        self.assertIsInstance(client._binder, ResolverBinder)
        self.assertEqual(client._binder.data.identity(), "tables:Patient=acme.fhir.patient")

    def test_the_environment_is_found_on_first_compile_never_at_connect(self):
        """CLIENT-ENVIRONMENT: building a client touches no workspace; the default, unless one is named."""
        with mock.patch("cql_databricks.compiler.environment.EnvironmentResolver") as resolver:
            client = self._connect()
            self.assertIsInstance(client._binder, ResolverBinder)
            resolver.assert_not_called()

    def test_an_environment_is_named_by_keyword_or_env(self):
        """BUILD-RESOLVE: a keyword, else CQL_DATABRICKS_ENVIRONMENT, else the default."""
        self.assertEqual(client_mod._environment(None, "large").name, "large")
        with mock.patch.dict(os.environ, {"CQL_DATABRICKS_ENVIRONMENT": "small"}):
            self.assertEqual(client_mod._environment(None, None).name, "small")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CQL_DATABRICKS_ENVIRONMENT", None)
            self.assertIsNone(client_mod._environment(None, None).name)

    def test_the_retired_settings_are_gone(self):
        """CLIENT-ENVIRONMENT: no data source, catalog scopes or value-set table to name."""
        for retired in ("data_source", "catalog_scopes", "data_catalog", "valueset_table"):
            with self.assertRaises(TypeError):
                self._connect(**{retired: "x"})


if __name__ == "__main__":
    unittest.main()
