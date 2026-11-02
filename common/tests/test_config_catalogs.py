"""SVC-CATALOG-LAYOUT, APP-RUN-LOG — the catalogs the Apps are configured with, and what they fall back to.

There is no results catalog: results are in each environment's own schemas (`RUN-CLIENT-DRAFT-RESULTS`).
"""
from __future__ import annotations

import pytest

from cql_databricks_common import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No catalog var is set unless a test sets it (a developer's shell exports them)."""
    monkeypatch.delenv("UC_CATALOG", raising=False)
    monkeypatch.delenv("CQL_RUNTIME_CATALOG", raising=False)
    monkeypatch.delenv("CQL_DESIGNER_CATALOG", raising=False)


def test_runtime_catalog_still_tracks_the_product_catalog(monkeypatch):
    """The `cql_*` UDFs DO live in the product catalog (SVC-RUNTIME-UDF-UC)."""
    monkeypatch.setenv("UC_CATALOG", "synthetic_product")
    monkeypatch.setenv("CQL_RUNTIME_CATALOG", "synthetic_product")
    assert config.runtime_catalog() == "synthetic_product"


def test_the_run_log_catalog_follows_the_product_catalog_unless_set(monkeypatch):
    """APP-RUN-LOG, DEC-RUN-LOG-LOCATION — the shared log is not private data, so it may sit in uc_catalog."""
    with pytest.raises(RuntimeError, match="UC_CATALOG"):
        config.designer_catalog()
    monkeypatch.setenv("UC_CATALOG", "synthetic_product")
    assert config.designer_catalog() == "synthetic_product"
    monkeypatch.setenv("CQL_DESIGNER_CATALOG", "synthetic_shared")
    assert config.designer_catalog() == "synthetic_shared"
