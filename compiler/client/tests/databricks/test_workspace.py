"""L1 unit tests for the `_workspace` App-registry lookup (SVC-DISCOVERY-PLUGIN). The `_client()` seam
is monkeypatched with a fake, proving the lookup resolves via the STABLE REST path first and falls back
to the typed SDK wrapper — resilient to Databricks SDK version drift (the serverless runtime SDK's typed
Apps wrapper calls a dead `/preview/apps/...` path). No SDK, no workspace, no network.
"""
import unittest
from unittest import mock

from cql_databricks.compiler import _workspace

_APP = {"url": "https://cql-databricks-compiler-123.aws.databricksapps.com",
        "oauth2_app_client_id": "client-abc"}


class _RestClient:
    """Current runtime: api_client.do("GET", /api/2.0/apps/{name}) returns a dict."""
    def __init__(self):
        self.api_client = mock.Mock()
        self.api_client.do.side_effect = self._do
        self.apps = mock.Mock()
        self.apps.get.side_effect = AssertionError("REST path should win; typed wrapper must not be called")

    def _do(self, method, path, **kw):
        assert method == "GET" and path == "/api/2.0/apps/cql-databricks-compiler"
        return dict(_APP)


class _TypedOnlyClient:
    """Old runtime with no usable api_client.do; the typed apps.get wrapper still works."""
    def __init__(self):
        self.api_client = mock.Mock()
        self.api_client.do.side_effect = AttributeError("no do on this SDK")
        self.apps = mock.Mock()
        self.apps.get.return_value = mock.Mock(url=_APP["url"], oauth2_app_client_id=_APP["oauth2_app_client_id"])


class GetAppResolutionTest(unittest.TestCase):
    def test_rest_path_wins(self):
        with mock.patch.object(_workspace, "_client", _RestClient):
            self.assertEqual(_workspace.app_url("cql-databricks-compiler"), _APP["url"])
            self.assertEqual(_workspace.app_oauth_client_id("cql-databricks-compiler"), "client-abc")

    def test_typed_wrapper_fallback(self):
        with mock.patch.object(_workspace, "_client", _TypedOnlyClient):
            self.assertEqual(_workspace.app_url("cql-databricks-compiler"), _APP["url"])
            self.assertEqual(_workspace.app_oauth_client_id("cql-databricks-compiler"), "client-abc")

    def test_fails_soft_when_nothing_resolves(self):
        class _Dead:
            def __init__(self):
                self.api_client = mock.Mock()
                self.api_client.do.side_effect = RuntimeError("boom")
                self.apps = mock.Mock()
                self.apps.get.side_effect = RuntimeError("boom")
        with mock.patch.object(_workspace, "_client", _Dead):
            self.assertIsNone(_workspace.app_url("cql-databricks-compiler"))
            self.assertIsNone(_workspace.app_oauth_client_id("cql-databricks-compiler"))


if __name__ == "__main__":
    unittest.main()
