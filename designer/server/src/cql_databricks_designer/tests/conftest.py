"""Shared fixtures for the backend suite."""

from __future__ import annotations

import pytest

from cql_databricks_common.workspace import reset_client


@pytest.fixture(autouse=True)
def _reset_workspace_client():
    """`SEC-WS-CLIENT-SHARED`: the workspace client is process-wide, so a test that builds one —
    real or mocked — would otherwise hand it to every test that runs after it."""
    reset_client()
    yield
    reset_client()


@pytest.fixture
def catalog_scopes(monkeypatch):
    """CLIENT-DISCOVERY-SCOPE — the deployment's catalog scopes, which the App needs before it builds its resolver."""
    monkeypatch.setenv("CQL_DATABRICKS_CATALOG_SCOPES", "synthetic_dev.cql,synthetic_shared")
