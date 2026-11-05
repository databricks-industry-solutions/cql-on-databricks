"""BUILD-RESOLVE, CLIENT-SESSION-STATE — a notebook call makes as few Spark Connect round trips as it can: the
Spark-conf tier is one config call, not one per key, and binding a `cql()` call reads the session record once."""
import unittest
from unittest import mock

from cql_databricks.compiler import config, library, session_state, zeroconfig


class _Conf:
    """A session's conf: ``getAll`` is one round trip; a per-key ``get`` must not be used."""

    def __init__(self, held):
        self.held, self.get_all_calls = held, 0

    @property
    def getAll(self):
        self.get_all_calls += 1
        return dict(self.held)

    def get(self, *_):
        raise AssertionError("the Spark-conf tier reads every key in one getAll, never one get per key")


class SparkConfTierTests(unittest.TestCase):
    def _read(self, held):
        conf = _Conf(held)
        with mock.patch("pyspark.sql.SparkSession.active", return_value=mock.Mock(conf=conf)):
            return library._spark_conf(), conf

    def test_the_tier_is_one_config_call(self):
        """BUILD-RESOLVE — one round trip, however many keys the client reads."""
        _, conf = self._read({})
        self.assertEqual(conf.get_all_calls, 1)

    def test_it_holds_the_client_keys_the_session_sets_and_nothing_else(self):
        held = {config.CONF_ENGINE: "spark", config.CONF_LIBRARY_PATH: "/Workspace/cql",
                "spark.sql.ansi.enabled": "true", "spark.cqldatabricks.unknownKey": "x"}
        settings, _ = self._read(held)
        self.assertEqual(settings, {config.CONF_ENGINE: "spark", config.CONF_LIBRARY_PATH: "/Workspace/cql"})

    def test_a_session_that_sets_none_of_them_gives_an_empty_tier(self):
        """Serverless refuses to set the client's keys, so there the tier is always empty."""
        settings, _ = self._read({"spark.sql.session.timeZone": "UTC"})
        self.assertEqual(settings, {})


class CqlCallHoldsTheRecordTests(unittest.TestCase):
    def test_binding_a_cql_call_holds_the_session_record(self):
        """CLIENT-SESSION-STATE — the client binds inside one held block, so its lookups read the record once."""
        seen = []
        client = mock.Mock()
        client.cql.side_effect = lambda *a, **kw: seen.append(session_state._held_depth) or "query"
        with mock.patch.object(zeroconfig, "_default_client", client), \
                mock.patch.object(zeroconfig, "_default_resolvers", zeroconfig.session_resolvers()):
            self.assertEqual(zeroconfig.cql('define "Two": 2'), "query")
        self.assertEqual(seen, [1])
        self.assertEqual(session_state._held_depth, 0)


if __name__ == "__main__":
    unittest.main()
