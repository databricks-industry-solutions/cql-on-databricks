"""DOC-OPEN-SAVE — the canonical URL base a library created in the Designer gets: set, or a loud error."""
from __future__ import annotations

import pytest

from cql_databricks_common import config


def test_unset_is_an_error_naming_the_variable(monkeypatch):
    monkeypatch.delenv("CQL_CANONICAL_BASE", raising=False)
    with pytest.raises(RuntimeError, match="canonical_base"):
        config.canonical_base()


def test_the_base_is_an_absolute_url_without_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("CQL_CANONICAL_BASE", "https://measures.example.org/fhir/")
    assert config.canonical_base() == "https://measures.example.org/fhir"


@pytest.mark.parametrize("value", ["measures.example.org/fhir", "urn:example", "https://example.org/fhir?x=1"])
def test_a_base_that_is_not_an_http_url_is_refused(monkeypatch, value):
    monkeypatch.setenv("CQL_CANONICAL_BASE", value)
    with pytest.raises(RuntimeError, match="absolute http"):
        config.canonical_base()
