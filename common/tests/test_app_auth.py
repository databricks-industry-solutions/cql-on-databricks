"""How the BFF authenticates to the Compiler Service App (`SEC-APP-TO-APP`).

The bug these cover: a deployed App sits behind the platform OAuth gate, an unauthenticated request
is redirected to the Databricks sign-in page, and a client that follows the redirect gets HTML with
a 200 — which the SPA reported as "translate failed: validation service error (502) ... non-JSON
response". Both halves are asserted: the right credential goes out, and a gate rejection comes back
as an auth error rather than a parse error.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from cql_databricks_common import app_auth
from cql_databricks_common.compiler import CompilerClient, CompilerError

APP = "https://compiler-app-0000000000000000.aws.databricksapps.com"
LOCAL = "http://127.0.0.1:8080"


def _identity(token: str = "id-tok"):
    """Patch the shared workspace client so `identity_bearer()` yields ``token``."""
    client = MagicMock()
    client.config.authenticate.return_value = {"Authorization": f"Bearer {token}"}
    return patch("cql_databricks_common.workspace.service_principal_client", return_value=client)


def test_app_endpoint_is_recognised_by_domain():
    assert app_auth.is_app_endpoint(APP)
    assert not app_auth.is_app_endpoint(LOCAL)
    assert not app_auth.is_app_endpoint("https://acme.cloud.databricks.com")


def test_app_endpoint_uses_the_process_identity():
    with _identity("sp-oauth-token"):
        assert app_auth.bearer(APP) == "sp-oauth-token"


def test_local_endpoint_uses_the_static_token_and_never_the_identity():
    with _identity() as sp_client:
        assert app_auth.bearer(LOCAL, "static") == "static"
        assert app_auth.bearer(LOCAL) is None
        sp_client.assert_not_called()  # the local host never reaches for a Databricks identity


def test_a_static_token_against_an_app_endpoint_is_a_loud_error():
    """It cannot pass the gate, so silently ignoring it would strand a deployment on a knob that
    looks set and does nothing."""
    with pytest.raises(RuntimeError, match="CQL_COMPILER_TOKEN"):
        app_auth.bearer(APP, "static")


def test_identity_bearer_fails_loud_without_a_bearer_header():
    client = MagicMock()
    client.config.authenticate.return_value = {}
    with patch("cql_databricks_common.workspace.service_principal_client", return_value=client):
        with pytest.raises(RuntimeError, match="no Bearer token"):
            app_auth.identity_bearer()


def _response(status: int, *, body: str = "", headers: dict[str, str] | None = None,
              content_type: str = "application/json") -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res._content = body.encode()
    res.headers["content-type"] = content_type
    for k, v in (headers or {}).items():
        res.headers[k] = v
    return res


def test_translate_sends_the_identity_bearer_and_does_not_follow_redirects():
    with _identity("sp-oauth-token"):
        with patch("requests.post", return_value=_response(200, body='{"ok": true}')) as post:
            CompilerClient(APP).translate("library X version '1'")

    kwargs = post.call_args.kwargs
    assert kwargs["headers"]["authorization"] == "Bearer sp-oauth-token"
    assert kwargs["allow_redirects"] is False


def test_a_gate_redirect_is_reported_as_an_auth_failure():
    """A redirect into a 200 text/html sign-in page is reported as an auth failure, not a bad response body."""
    redirect = _response(302, headers={"location": "https://acme.cloud.databricks.com/login"},
                         content_type="text/html")
    with _identity():
        with patch("requests.post", return_value=redirect):
            with pytest.raises(CompilerError, match="OAuth gate"):
                CompilerClient(APP).translate("library X version '1'")


def test_local_client_sends_the_static_bearer():
    with patch("requests.post", return_value=_response(200, body="{}")) as post:
        CompilerClient(LOCAL, "static").translate("library X version '1'")

    assert post.call_args.kwargs["headers"]["authorization"] == "Bearer static"


def test_local_client_without_a_token_sends_no_authorization():
    with patch("requests.post", return_value=_response(200, body="{}")) as post:
        CompilerClient(LOCAL).translate("library X version '1'")

    assert "authorization" not in post.call_args.kwargs["headers"]


# ── feature App endpoints — by name deployed, by URL locally (APP-PER-FEATURE) ───────────────────

def test_a_deployed_app_finds_another_feature_app_by_name(monkeypatch):
    from cql_databricks_common import config

    client = MagicMock()
    client.apps.get.return_value.url = "https://cql-databricks-compiler-1.aws.databricksapps.com/"
    monkeypatch.setenv("CQL_COMPILER_APP", "cql-databricks-compiler")
    monkeypatch.delenv("CQL_COMPILER_ENDPOINT", raising=False)
    monkeypatch.setattr(config, "_APP_URLS", {})
    with patch("cql_databricks_common.workspace.service_principal_client", return_value=client):
        assert config.compiler_endpoint() == "https://cql-databricks-compiler-1.aws.databricksapps.com"
        config.compiler_endpoint()
    client.apps.get.assert_called_once_with(name="cql-databricks-compiler")


def test_a_local_run_uses_the_endpoint_or_the_local_default(monkeypatch):
    from cql_databricks_common import config

    monkeypatch.delenv("CQL_COMPILER_APP", raising=False)
    monkeypatch.delenv("CQL_COMPILER_ENDPOINT", raising=False)
    assert config.compiler_endpoint() == "http://127.0.0.1:8080"
    monkeypatch.setenv("CQL_COMPILER_ENDPOINT", "http://127.0.0.1:4001")
    assert config.compiler_endpoint() == "http://127.0.0.1:4001"


def test_an_app_name_and_an_endpoint_together_are_refused(monkeypatch):
    from cql_databricks_common import config

    monkeypatch.setenv("CQL_COMPILER_APP", "cql-databricks-compiler")
    monkeypatch.setenv("CQL_COMPILER_ENDPOINT", "http://127.0.0.1:8080")
    with pytest.raises(RuntimeError, match="both set"):
        config.compiler_endpoint()
