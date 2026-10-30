"""CLIENT-DISCOVERY-SCOPE — a notebook session with no catalog scopes set searches every catalog.

Serverless raises CONFIG_NOT_AVAILABLE when an unset key is read, even with a default, which failed the
example notebooks before any content was found.
"""
from types import SimpleNamespace

from cql_databricks.compiler import zeroconfig
from cql_databricks.data_bindings.tags import EVERYWHERE


class _ServerlessConf:
    """Synthetic stand-in for serverless `spark.conf`: an unset key raises, whatever the default."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        if key not in self._values:
            raise RuntimeError(f"[CONFIG_NOT_AVAILABLE.WITHOUT_SUGGESTION] Configuration {key} is not available.")
        return self._values[key]


def _spark(values):
    return SimpleNamespace(conf=_ServerlessConf(values))


def test_unreadable_scopes_conf_searches_every_catalog(monkeypatch):
    """CLIENT-DISCOVERY-SCOPE — unset (here: unreadable) is "none configured", which means `*`."""
    monkeypatch.delenv("CQL_DATABRICKS_CATALOG_SCOPES", raising=False)
    resolver = zeroconfig._session_content(_spark({}))
    assert [str(scope) for scope in resolver.scopes] == [EVERYWHERE]


def test_scopes_conf_is_still_read_when_set(monkeypatch):
    """CLIENT-DISCOVERY-SCOPE — the conf tier wins over the env tier when it is set."""
    monkeypatch.setenv("CQL_DATABRICKS_CATALOG_SCOPES", "from_env")
    resolver = zeroconfig._session_content(_spark({"spark.cqldatabricks.catalogScopes": "cat_a, cat_b.schema_b"}))
    assert [str(scope) for scope in resolver.scopes] == ["cat_a", "cat_b.schema_b"]


def test_env_scopes_apply_when_the_conf_is_unreadable(monkeypatch):
    """CLIENT-DISCOVERY-SCOPE — an unreadable conf falls to the env tier, by the BUILD-RESOLVE order."""
    monkeypatch.setenv("CQL_DATABRICKS_CATALOG_SCOPES", "from_env")
    resolver = zeroconfig._session_content(_spark({}))
    assert [str(scope) for scope in resolver.scopes] == ["from_env"]
