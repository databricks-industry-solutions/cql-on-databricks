"""CLIENT-ENVIRONMENT, CLIENT-RESOLVERS, CLIENT-ENVIRONMENT-RESOLVER, CLIENT-DISCOVERY-PARALLEL: connect() runs in an environment, the default unless
one is named, and a resolver given replaces the environment's for its role."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.resolvers.data import TableDataResolver
from cql_databricks.resolvers.environment import Discovery, EnvironmentLookupError
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

    def test_the_environment_is_chosen_on_first_compile_never_at_connect(self):
        """CLIENT-ENVIRONMENT, CLIENT-DISCOVERY-PARALLEL: connect() only starts finding the environments; none is
        chosen, and nothing found can fail, until a run needs one."""
        with mock.patch("cql_databricks.resolvers.environment.EnvironmentResolver") as resolver, \
             mock.patch("cql_databricks.resolvers.environment.discover_environments",
                        side_effect=EnvironmentLookupError("synthetic: no default")):
            client = self._connect()
            self.assertIsInstance(client._binder, ResolverBinder)
            resolver.assert_not_called()

    def test_connect_finds_the_runtime_and_environments_while_it_resolves_the_compiler(self):
        """CLIENT-DISCOVERY-PARALLEL: the compiler App's endpoint, the runtime's location and the environments are
        looked up at once. Each waits for the other two to start; one after another, none would, and the barrier
        breaks."""
        from .test_client import _ws

        all_three = threading.Barrier(3, timeout=5)
        started = threading.Event()

        def endpoint(conf):
            all_three.wait()
            return mock.MagicMock()

        def runtime(spark):
            all_three.wait()
            return "acme.runtime"

        def environments(run):
            started.set()
            all_three.wait()
            return Discovery(())

        with mock.patch.object(client_mod, "make_provider", side_effect=endpoint), \
             mock.patch.object(client_mod, "discover_udf_schema", side_effect=runtime), \
             mock.patch("cql_databricks.resolvers.environment.discover_environments", side_effect=environments), \
             mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
             mock.patch("cql_databricks.compiler.zeroconfig.register_runtime_udfs"):
            client = client_mod.connect(spark=_jvm_backed_spark())
        self.assertTrue(started.is_set())
        from cql_databricks.compiler.config import CONF_UDF_SCHEMA
        self.assertEqual(client._conf[CONF_UDF_SCHEMA], "acme.runtime")

    def test_a_failed_discovery_fails_at_first_use_on_the_callers_thread(self):
        """CLIENT-DISCOVERY-PARALLEL: what the background discovery raised is raised where the environment is needed."""
        failure = EnvironmentLookupError("no catalog you can see is tagged cql_databricks_environment_default")
        env = client_mod._SessionEnvironment(None, None)
        with ThreadPoolExecutor(max_workers=1) as pool, \
             mock.patch("cql_databricks.resolvers.environment.discover_environments", side_effect=failure):
            env.prefetch(pool, _jvm_backed_spark())
        with self.assertRaises(EnvironmentLookupError) as raised:
            env.get()
        self.assertIs(raised.exception, failure)

    def test_no_discovery_starts_when_every_role_is_given(self):
        """CLIENT-DISCOVERY-PARALLEL: a client that names a resolver for every role never reads the environments."""
        with mock.patch("cql_databricks.resolvers.environment.discover_environments") as discover:
            self._connect(data=mock.MagicMock(), terminology=mock.MagicMock(), knowledge=mock.MagicMock(),
                          results=mock.MagicMock())
        discover.assert_not_called()

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
