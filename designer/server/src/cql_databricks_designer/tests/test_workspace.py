"""The shared workspace-client seam (`SEC-WS-CLIENT-SHARED`)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import cql_databricks_designer
from cql_databricks_common.workspace import reset_client, service_principal_client


def test_client_is_built_once_and_reused():
    with patch("databricks.sdk.WorkspaceClient", return_value=MagicMock()) as ctor:
        first = service_principal_client()
        second = service_principal_client()

    assert first is second
    assert ctor.call_count == 1


def test_concurrent_callers_share_one_client():
    """The failure this seam exists to prevent: parallel callers each minting their own token."""
    with patch("databricks.sdk.WorkspaceClient", side_effect=lambda: MagicMock()) as ctor:
        with ThreadPoolExecutor(max_workers=8) as pool:
            clients = list(pool.map(lambda _: service_principal_client(), range(64)))

    assert ctor.call_count == 1
    assert all(c is clients[0] for c in clients)


def test_no_call_site_builds_its_own_client():
    """The seam is the only constructor — a second one reintroduces the per-Config token cache."""
    package = Path(cql_databricks_designer.__file__).parent
    offenders = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if path.name != "workspace.py"
        and path.parent.name != "tests"
        and "WorkspaceClient(" in path.read_text()
    ]

    assert offenders == [], (
        "build the client through cql_databricks_common.workspace.service_principal_client() "
        f"(SEC-WS-CLIENT-SHARED); direct constructions in: {offenders}")


def test_reset_client_forces_a_rebuild():
    with patch("databricks.sdk.WorkspaceClient", side_effect=lambda: MagicMock()) as ctor:
        first = service_principal_client()
        reset_client()
        second = service_principal_client()

    assert first is not second
    assert ctor.call_count == 2
