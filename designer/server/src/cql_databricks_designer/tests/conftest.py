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
