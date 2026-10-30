"""SEC-OBO — every deployed App binds the signed-in user, outermost of its middleware.

Asserted on the assembled Apps (the Designer's and the agent's), because the defect is a MISSING call and only
the installed stack can show it. The seam itself (`workspace.user_client()`) is tested in `common/tests/test_obo.py`.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
import pytest

#: CLIENT-DISCOVERY-SCOPE — building the App resolves the deployment's catalog scopes.
pytestmark = pytest.mark.usefixtures("catalog_scopes")


def test_every_deployment_binds_the_identity():
    """SEC-OBO — each feature's App binds the signed-in user (`APP-PER-FEATURE`).

    Each App has its own entrypoint and its own service principal, so a middleware installed on only
    one would leave the others running with the App's authority. Asserted on the installed stack rather
    than by reading the source, because the defect is a MISSING call and only the assembled app can show
    it.
    """
    from cql_databricks_common.workspace import install_forwarded_identity

    def bound(app: FastAPI) -> bool:
        return any("_bind_signed_in_user" in getattr(m.kwargs.get("dispatch"), "__name__", "")
                   for m in app.user_middleware if m.kwargs.get("dispatch"))

    probe = FastAPI()
    install_forwarded_identity(probe)
    assert bound(probe), "the installer under test does not register what this looks for"

    with patch("cql_databricks.agent.server.app.assistant_server"), \
         patch("cql_databricks.agent.server.app.mcp_http_app"):
        from cql_databricks.agent.server.app import create_mcp_app
        assert bound(create_mcp_app())
    from cql_databricks_designer.app import create_app
    assert bound(create_app())


def test_the_identity_is_bound_before_the_cache_stamp_runs():
    """PERF-CACHE-REVISION, SEC-OBO — the stamp is per user, and its readers call as the user.

    Starlette runs the last-registered middleware outermost. Registered before the stamp, the binding
    ran INSIDE it, so the stamp saw no user: one stamp for everyone, and a reader acting as the user
    raised. Asserted by order on the stack: the binding must be the outermost of the two.
    """
    def order(app: FastAPI) -> list[str]:
        return [getattr(m.kwargs.get("dispatch"), "__name__", "") for m in app.user_middleware
                if m.kwargs.get("dispatch")]

    from cql_databricks_designer.app import create_app
    names = order(create_app())
    assert names.index("_bind_signed_in_user") < names.index("cache_revision"), names
