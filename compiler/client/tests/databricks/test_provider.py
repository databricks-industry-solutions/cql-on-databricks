"""L1 unit tests for the databricks-app provider factory (SVC-DISCOVERY-PLUGIN): App-registry endpoint
discovery + OAuth token-exchange assembly. The `_workspace` seams are monkeypatched, so this drives the
orchestration (default-name, loud fails, correct RemoteProvider + OAuth2TokenExchange construction)
without the SDK. Needs cql-databricks on the path (RemoteProvider/OAuth2TokenExchange construct without
requests/pyspark — those are lazy).
"""
import unittest
from unittest import mock

from cql_databricks.compiler import provider


def _ws(**over):
    """A fake _workspace with all seams resolving; override per test."""
    m = mock.Mock()
    m.app_url.return_value = "https://cql-databricks-compiler-123.aws.databricksapps.com"
    m.workspace_host.return_value = "https://myworkspace.cloud.databricks.com"
    m.app_oauth_client_id.return_value = "client-abc"
    m.ambient_subject_token.return_value = "subj-token-xyz"
    for k, v in over.items():
        getattr(m, k).return_value = v
    return m


class MakeProviderTest(unittest.TestCase):
    def test_builds_remote_provider_with_token_exchange(self):
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()):
            prov = provider.make_provider({})
        from cql_databricks.compiler import RemoteProvider
        self.assertIsInstance(prov, RemoteProvider)
        # endpoint discovered from the App registry
        self.assertEqual(prov._base, "https://cql-databricks-compiler-123.aws.databricksapps.com")
        # OAuth token-exchange wired from workspace OIDC + app client id + ambient token
        creds = prov._credentials
        self.assertEqual(creds._token_url, "https://myworkspace.cloud.databricks.com/oidc/v1/token")
        self.assertEqual(creds._audience, "client-abc")
        self.assertEqual(creds._subject_token, "subj-token-xyz")
        # The ambient runtime token is an opaque Databricks token, so the exchange must use the
        # Databricks PAT subject-token type (a generic JWT type is rejected 400 by /oidc/v1/token).
        self.assertEqual(creds._subject_token_type, provider.DATABRICKS_PAT_TOKEN_TYPE)
        # SVC-EXCHANGE-RETRY — the Databricks plugin opts in to retrying invalid_grant, and nothing else.
        self.assertEqual(creds._retry_errors, frozenset({"invalid_grant"}))
        self.assertEqual(creds._retry_seconds, provider.EXCHANGE_RETRY_SECONDS)

    def test_default_app_name_used_when_unset(self):
        ws = _ws()
        with mock.patch("cql_databricks.compiler.provider._workspace", ws):
            provider.make_provider({})
        ws.app_url.assert_called_once_with(provider.DEFAULT_APP_NAME)

    def test_explicit_app_name_override(self):
        ws = _ws()
        with mock.patch("cql_databricks.compiler.provider._workspace", ws):
            provider.make_provider({provider.CONF_APP_NAME: "custom-app"})
        ws.app_url.assert_called_once_with("custom-app")

    def test_retired_m2m_settings_are_loud(self):
        # SVC-COMPILER-M2M is SUPERSEDED: a Job still configured for it fails instead of silently exchanging.
        for key in provider.RETIRED_M2M_CONFS:
            ws = _ws()
            with mock.patch("cql_databricks.compiler.provider._workspace", ws):
                with self.assertRaises(ValueError) as e:
                    provider.make_provider({key: "oauth-m2m"})
            self.assertIn(key, str(e.exception))
            self.assertIn("SVC-EXCHANGE-RETRY", str(e.exception))
            ws.app_url.assert_not_called()

    def test_missing_app_is_loud(self):
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws(app_url=None)):
            with self.assertRaises(ValueError) as e:
                provider.make_provider({})
        self.assertIn("App registry", str(e.exception))

    def test_missing_auth_inputs_are_loud_and_named(self):
        with mock.patch("cql_databricks.compiler.provider._workspace",
                        _ws(ambient_subject_token=None, app_oauth_client_id=None)):
            with self.assertRaises(ValueError) as e:
                provider.make_provider({})
        msg = str(e.exception)
        self.assertIn("ambient subject token", msg)
        self.assertIn("app OAuth client id", msg)


if __name__ == "__main__":
    unittest.main()
