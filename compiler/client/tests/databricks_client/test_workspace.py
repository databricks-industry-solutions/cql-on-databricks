"""L1 unit tests for the `_workspace` App-registry lookup (SVC-DISCOVERY-PLUGIN). The `_client()` seam is
replaced by a fake whose REST client answers like the workspace does: a record for a known App, and the SDK's
own `NotFound` or `PermissionDenied` for the other two cases. No workspace, no network.
"""
import unittest
from unittest import mock

from databricks.sdk.errors import NotFound, PermissionDenied

from cql_databricks.compiler import _workspace
from cql_databricks.compiler import provider

APP = "cql-databricks-compiler"
_RECORD = {"url": "https://cql-databricks-compiler-123.aws.databricksapps.com",
           "oauth2_app_client_id": "client-abc"}


class _Workspace:
    """A workspace whose `api_client.do` returns `answer` (a record) or raises it (an SDK error)."""
    def __init__(self, answer):
        self.api_client = mock.Mock()
        self.api_client.do.side_effect = self._do
        self._answer = answer

    def _do(self, method, path, **kwargs):
        if isinstance(self._answer, Exception):
            raise self._answer
        return dict(self._answer)


def _patched(workspace):
    return mock.patch.object(_workspace, "_client", lambda: workspace)


class GetAppTest(unittest.TestCase):
    def test_one_rest_call_reads_the_app_record(self):
        """SVC-DISCOVERY-PLUGIN — the App is read from the stable REST path, once per seam."""
        ws = _Workspace(_RECORD)
        with _patched(ws):
            self.assertEqual(_workspace.app_url(APP), _RECORD["url"])
            self.assertEqual(_workspace.app_oauth_client_id(APP), "client-abc")
        self.assertEqual([c.args for c in ws.api_client.do.call_args_list],
                         [("GET", f"/api/2.0/apps/{APP}"), ("GET", f"/api/2.0/apps/{APP}")])

    def test_an_app_without_a_url_yet_has_none(self):
        """SVC-DISCOVERY-PLUGIN — an App that exists but has no URL yet is a `None`, which the caller reports."""
        with _patched(_Workspace({"oauth2_app_client_id": "client-abc"})):
            self.assertIsNone(_workspace.app_url(APP))

    def test_a_missing_app_raises_naming_the_app_and_the_cause(self):
        """SVC-DISCOVERY-PLUGIN — not found is a typed error, not a `None` the caller has to guess about."""
        with _patched(_Workspace(NotFound("no such app"))):
            with self.assertRaises(_workspace.AppLookupError) as caught:
                _workspace.app_url(APP)
        self.assertIn(f"App '{APP}' was not found", str(caught.exception))
        self.assertIn("no such app", str(caught.exception))

    def test_an_unreadable_app_raises_naming_the_permission(self):
        """SVC-DISCOVERY-PLUGIN — a permission error names the App and says the identity may not read it."""
        with _patched(_Workspace(PermissionDenied("not allowed"))):
            with self.assertRaises(_workspace.AppLookupError) as caught:
                _workspace.app_oauth_client_id(APP)
        self.assertIn(f"App '{APP}' cannot be read by this identity", str(caught.exception))
        self.assertIn("permission denied", str(caught.exception))


class MakeProviderSurfacesTheLookupErrorTest(unittest.TestCase):
    def test_a_missing_app_reaches_the_caller_of_make_provider(self):
        """SVC-DISCOVERY-PLUGIN — the lookup error is not swallowed on the way to the user."""
        with _patched(_Workspace(NotFound("no such app"))):
            with self.assertRaises(_workspace.AppLookupError) as caught:
                provider.make_provider({})
        self.assertIn(f"App '{provider.DEFAULT_APP_NAME}' was not found", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
