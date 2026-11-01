"""CLIENT-DATA-PROVIDER, CLIENT-DEFAULT-RESOLVER, CLIENT-KNOWLEDGE-PROVIDER: connect() binds through providers when
it is given one, or names a data source; with neither, it binds as before (DEC-NOTEBOOK-DEFAULT-RESOLVER is open)."""
import os
import unittest
from unittest import mock

import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.compiler.data_providers import Tables
from cql_databricks.compiler.discovery import DatabricksDataBinder
from cql_databricks.compiler.provider_binder import ProviderBinder

from .test_runtime import _jvm_backed_spark


class ConnectProvidersTest(unittest.TestCase):
    def _connect(self, **kwargs):
        from .test_client import _ws

        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
             mock.patch("cql_databricks.compiler.zeroconfig.register_runtime_udfs"), \
             mock.patch("cql_databricks.compiler.zeroconfig._conf_or_empty", return_value=""):
            return client_mod.connect(spark=_jvm_backed_spark(), udf_schema="acme.runtime", **kwargs)

    def test_a_data_provider_binds_through_providers(self):
        client = self._connect(data=Tables({"Patient": "acme.fhir.patient"}))
        self.assertIsInstance(client._binder, ProviderBinder)
        self.assertEqual(client._binder.data.identity(), "tables:Patient=acme.fhir.patient")

    def test_a_named_data_source_binds_through_the_tag_resolver(self):
        """BUILD-RESOLVE: by keyword or env alike."""
        self.assertIsInstance(self._connect(data_source="synthetic-population-small")._binder, ProviderBinder)
        with mock.patch.dict(os.environ, {"CQL_DATABRICKS_DATA_SOURCE": "synthetic-population-small"}):
            self.assertIsInstance(self._connect()._binder, ProviderBinder)

    def test_no_provider_and_no_name_binds_as_before(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CQL_DATABRICKS_DATA_SOURCE", None)
            self.assertIsInstance(self._connect()._binder, DatabricksDataBinder)

    def test_providers_refuse_the_settings_they_replace(self):
        with self.assertRaises(ValueError):
            self._connect(data=Tables({"Patient": "a.b.c"}), data_source="x")
        with self.assertRaises(ValueError):
            self._connect(data=Tables({"Patient": "a.b.c"}), valueset_table="a.b.valueset")


if __name__ == "__main__":
    unittest.main()
