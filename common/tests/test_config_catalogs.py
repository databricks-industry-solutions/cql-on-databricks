"""RUN-CLIENT-PRIVATE-PROVISIONING — the Designer's results catalog, and what it must not fall back to.

There is one results catalog (`PIPE-RESULTS-CATALOG-ISOLATION` is SUPERSEDED). These tests pin that an
unset catalog never resolves to the product catalog, or to any built-in name.
"""
from __future__ import annotations

import pytest

from cql_databricks_common import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No catalog var is set unless a test sets it (a developer's shell exports them)."""
    monkeypatch.delenv("UC_CATALOG", raising=False)
    monkeypatch.delenv("CQL_DESIGNER_RESULTS_CATALOG", raising=False)
    monkeypatch.delenv("CQL_RUNTIME_CATALOG", raising=False)
    monkeypatch.delenv("CQL_DESIGNER_CATALOG", raising=False)


def test_unset_is_an_error_not_a_default():
    """A catalog name is workspace config (SVC-LOCAL-CONFIG-BUNDLE): unset fails loud, naming the variable."""
    with pytest.raises(RuntimeError, match="CQL_DESIGNER_RESULTS_CATALOG"):
        config.designer_results_catalog()


def test_uc_catalog_is_not_a_fallback(monkeypatch):
    """The product catalog is set and the Designer's is not — it does not resolve to the product catalog."""
    monkeypatch.setenv("UC_CATALOG", "synthetic_product")
    with pytest.raises(RuntimeError, match="CQL_DESIGNER_RESULTS_CATALOG"):
        config.designer_results_catalog()


def test_empty_value_is_not_configured(monkeypatch):
    """An empty env var is "not configured", and the thing it is not configured TO is UC_CATALOG."""
    monkeypatch.setenv("UC_CATALOG", "synthetic_product")
    monkeypatch.setenv("CQL_DESIGNER_RESULTS_CATALOG", "")
    with pytest.raises(RuntimeError, match="CQL_DESIGNER_RESULTS_CATALOG"):
        config.designer_results_catalog()


def test_runtime_catalog_still_tracks_the_product_catalog(monkeypatch):
    """The `cql_*` UDFs DO live in the product catalog (SVC-RUNTIME-UDF-UC)."""
    monkeypatch.setenv("UC_CATALOG", "synthetic_product")
    monkeypatch.setenv("CQL_RUNTIME_CATALOG", "synthetic_product")
    assert config.runtime_catalog() == "synthetic_product"


@pytest.mark.parametrize("other", ["UC_CATALOG", "CQL_RUNTIME_CATALOG"])
def test_designer_catalog_may_share_a_single_catalog_deployment(monkeypatch, other):
    """RUN-CLIENT-PRIVATE-PROVISIONING — per-user schemas, not the catalog, separate private results."""
    monkeypatch.setenv(other, "synthetic_shared")
    monkeypatch.setenv("CQL_DESIGNER_RESULTS_CATALOG", "synthetic_shared")
    assert config.designer_results_catalog() == "synthetic_shared"


def test_the_run_log_catalog_follows_the_product_catalog_unless_set(monkeypatch):
    """APP-RUN-LOG, DEC-RUN-LOG-LOCATION — the shared log is not private data, so it may sit in uc_catalog."""
    with pytest.raises(RuntimeError, match="UC_CATALOG"):
        config.designer_catalog()
    monkeypatch.setenv("UC_CATALOG", "synthetic_product")
    assert config.designer_catalog() == "synthetic_product"
    monkeypatch.setenv("CQL_DESIGNER_CATALOG", "synthetic_shared")
    assert config.designer_catalog() == "synthetic_shared"
