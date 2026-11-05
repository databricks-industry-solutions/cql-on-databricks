"""Shared fixtures for the agent server suite."""
from __future__ import annotations

import pytest

from cql_databricks_common.environments import reset_environments


@pytest.fixture(autouse=True)
def _reset_environments():
    """`CLIENT-ENVIRONMENT-CACHE`: the environments are held per process, so one test's would be another's."""
    reset_environments()
    yield
    reset_environments()
