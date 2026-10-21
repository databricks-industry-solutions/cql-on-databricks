"""CLIENT-DISCOVERY-SCOPE — discovery reads only the caller's ordered catalog scopes, in one query, earliest first.
With none configured it reads every catalog (``*``) and warns.

The fake catalog answers the tag query the way ``system.information_schema.schema_tags`` would, honouring the
query's own tag and catalog parameters, so a row outside the scopes' catalogs is never returned. Rows in a listed
catalog but outside a ``catalog.schema`` entry are returned, and the resolver must drop them.
"""
from __future__ import annotations

import json
import logging

import pytest

from cql_databricks.compiler import config
from cql_databricks.compiler.resolve import ResolutionError, resolve_data_source
from cql_databricks.data_bindings import (
    DATA_SOURCE_TAG, EVERYWHERE, KNOWLEDGE_TAG, TERMINOLOGY_TAG, CatalogScope, ContentResolver, ResolverError,
    parse_scopes)

#: Every tagged schema in the workspace: (catalog, schema, tag, value).
WORKSPACE = [
    ("acme_dev", "cql", DATA_SOURCE_TAG, "claims"),
    ("acme_dev", "cql", KNOWLEDGE_TAG, "measures"),
    ("acme_dev", "scratch", DATA_SOURCE_TAG, "scratch"),
    ("acme_shared", "fhir", DATA_SOURCE_TAG, "claims"),
    ("acme_shared", "fhir_v2", DATA_SOURCE_TAG, "fhir"),
    ("acme_shared", "vsac", TERMINOLOGY_TAG, "vsac"),
    ("acme_shared", "ecqm", KNOWLEDGE_TAG, "ecqm"),
    ("other_team", "fhir", DATA_SOURCE_TAG, "theirs"),
    ("other_team", "vsac", TERMINOLOGY_TAG, "their_vsac"),
    ("other_team", "ecqm", KNOWLEDGE_TAG, "their_ecqm"),
]

#: Both layouts, so a terminology source passes its check and a knowledge source loads (with no rows).
LAYOUT_COLUMNS = ([{"table_name": "valueset", "column_name": c} for c in ("valueset_id", "system", "code", "version")]
                  + [{"table_name": t, "column_name": c} for t in ("library", "measure")
                     for c in ("url", "version", "resource")])


class Catalog:
    """The tag query, the layout reads and empty knowledge tables, over ``tagged``; every statement is recorded."""

    def __init__(self, tagged=WORKSPACE) -> None:
        self.tagged = tagged
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, statement, params=None):
        self.calls.append((statement, params))
        if "schema_tags" in statement:
            tags, catalogs = json.loads(params["tags"]), json.loads(params["catalogs"])
            everywhere = params["everywhere"] == "true"
            return [{"catalog_name": c, "schema_name": s, "tag_name": t, "tag_value": v}
                    for c, s, t, v in self.tagged if t in tags and (everywhere or c in catalogs)]
        if "information_schema.columns" in statement:
            return LAYOUT_COLUMNS
        if "`.`library`" in statement or "`.`measure`" in statement:
            return []
        raise AssertionError(f"unexpected statement: {statement}")

    def tag_queries(self) -> int:
        return sum("schema_tags" in statement for statement, _ in self.calls)


def _resolver(scopes, catalog=None):
    catalog = catalog or Catalog()
    return ContentResolver(lambda: catalog, scopes), catalog


def _where(schemas):
    return [(s.name, f"{s.catalog}.{s.schema}") for s in schemas]


# --- The list -------------------------------------------------------------------------------------

def test_scopes_are_ordered_entries_of_a_catalog_a_catalog_schema_or_everywhere_in_lower_case():
    assert parse_scopes(["Acme_Dev.CQL", " acme_shared ", "*"]) == (
        CatalogScope("acme_dev", "cql"), CatalogScope("acme_shared"), CatalogScope(EVERYWHERE))


@pytest.mark.parametrize("entries, message", [
    ([], "at least one catalog scope"),
    ("acme_dev,acme_shared", "a list of entries, not one string"),
    (["a.b.c"], "must be `catalog`, `catalog.schema` or `\\*`"),
    (["acme."], "must be `catalog`, `catalog.schema` or `\\*`"),
    ([""], "must be `catalog`, `catalog.schema` or `\\*`"),
    (["*.fhir"], "must be `catalog`, `catalog.schema` or `\\*`"),
    (["acme", "ACME"], "listed twice"),
])
def test_an_empty_or_malformed_list_fails_before_any_query(entries, message):
    built = []
    with pytest.raises(ResolverError, match=message):
        ContentResolver(lambda: built.append(1), entries)
    assert built == []


# --- None configured: every catalog, with a warning ----------------------------------------------------

def test_no_scopes_searches_every_catalog_and_warns(caplog):
    unique = [row for row in WORKSPACE if row[:2] != ("acme_shared", "fhir")]   # `claims` tagged once
    with caplog.at_level(logging.WARNING, logger="cql_databricks.data_bindings.resolver"):
        content, catalog = _resolver(None, Catalog(unique))
    assert "searches every catalog you can see" in caplog.text and "catalogScopes" in caplog.text
    assert [str(s) for s in content.scopes] == ["*"]
    assert _where(content.data_source_schemas()) == [
        ("claims", "acme_dev.cql"), ("fhir", "acme_shared.fhir_v2"), ("scratch", "acme_dev.scratch"),
        ("theirs", "other_team.fhir")]
    assert catalog.calls[0][1]["everywhere"] == "true"


def test_everywhere_holds_what_no_earlier_scope_claimed_and_repeats_under_it_fail():
    """``["acme_dev", "*"]`` — dev first, then every other catalog. Under ``*`` alone, `claims` is in two schemas
    at one position, so it is refused, as every lookup was before scoping."""
    content, _ = _resolver(["acme_dev", "*"])
    found = {s.name: (f"{s.catalog}.{s.schema}", s.position) for s in content.data_source_schemas()}
    assert found == {"claims": ("acme_dev.cql", 0), "scratch": ("acme_dev.scratch", 0),
                     "fhir": ("acme_shared.fhir_v2", 1), "theirs": ("other_team.fhir", 1)}
    content, _ = _resolver(None)
    with pytest.raises(ResolverError, match=r"'claims' on acme_dev.cql, acme_shared.fhir \(scope \*\)"):
        content.data_source_schemas()


# --- One query, every kind confined -----------------------------------------------------------------

def test_one_query_finds_every_kind_and_reads_only_the_scopes_catalogs():
    content, catalog = _resolver(["acme_dev.cql", "acme_shared"])
    content.data_source_schemas()
    content.terminology_sources()
    content.knowledge_sources()
    assert catalog.tag_queries() == 1
    statement, params = catalog.calls[0]
    assert json.loads(params["catalogs"]) == ["acme_dev", "acme_shared"]
    assert set(json.loads(params["tags"])) == {KNOWLEDGE_TAG, TERMINOLOGY_TAG, DATA_SOURCE_TAG}
    assert params["everywhere"] == "false"


def test_each_kind_holds_only_the_schemas_inside_a_scope():
    content, _ = _resolver(["acme_dev.cql", "acme_shared"])
    assert _where(content.data_source_schemas()) == [("claims", "acme_dev.cql"), ("fhir", "acme_shared.fhir_v2")]
    assert _where(content.terminology_sources()) == [("vsac", "acme_shared.vsac")]
    assert _where(content.knowledge_sources()) == [("measures", "acme_dev.cql"), ("ecqm", "acme_shared.ecqm")]


def test_a_whole_catalog_scope_holds_every_schema_in_it():
    content, _ = _resolver(["acme_dev"])
    assert _where(content.data_source_schemas()) == [("claims", "acme_dev.cql"), ("scratch", "acme_dev.scratch")]


def test_reload_runs_the_one_query_again():
    content, catalog = _resolver(["acme_shared"])
    content.data_source_schemas()
    content.reload()
    content.data_source_schemas()
    assert catalog.tag_queries() == 2


# --- Priority ---------------------------------------------------------------------------------------

def test_a_name_in_two_scopes_resolves_to_the_earlier_scope():
    dev_first, _ = _resolver(["acme_dev", "acme_shared"])
    shared_first, _ = _resolver(["acme_shared", "acme_dev"])
    claims = {s.name: s for s in dev_first.data_source_schemas()}["claims"]
    assert (claims.schema, claims.position) == ("cql", 0)
    claims = {s.name: s for s in shared_first.data_source_schemas()}["claims"]
    assert (claims.schema, claims.position) == ("fhir", 0)


def test_the_list_is_in_scope_order_not_name_order():
    content, _ = _resolver(["acme_shared", "acme_dev.scratch"])
    assert [s.name for s in content.data_source_schemas()] == ["claims", "fhir", "scratch"]
    content, _ = _resolver(["acme_dev.scratch", "acme_shared"])
    assert [s.name for s in content.data_source_schemas()] == ["scratch", "claims", "fhir"]


def test_a_schema_inside_two_entries_takes_the_earlier_position():
    content, _ = _resolver(["acme_dev.cql", "acme_shared", "acme_dev"])
    positions = {s.name: s.position for s in content.data_source_schemas()}
    assert positions == {"claims": 0, "fhir": 1, "scratch": 2}


def test_a_name_twice_in_one_scope_fails_naming_both_schemas():
    tagged = [*WORKSPACE, ("acme_shared", "fhir_copy", DATA_SOURCE_TAG, "fhir")]
    content, _ = _resolver(["acme_shared"], Catalog(tagged))
    with pytest.raises(ResolverError, match=r"cql_databricks_data_source = 'fhir' on "
                                            r"acme_shared.fhir_copy, acme_shared.fhir_v2 \(scope acme_shared\)"):
        content.data_source_schemas()


def test_a_name_twice_in_a_later_scope_is_shadowed_not_refused():
    """The earlier scope answers the name, so the later scope's two schemas are never a guess."""
    tagged = [*WORKSPACE, ("acme_shared", "claims_copy", DATA_SOURCE_TAG, "claims")]
    content, _ = _resolver(["acme_dev.cql", "acme_shared"], Catalog(tagged))
    assert ("claims", "acme_dev.cql") in _where(content.data_source_schemas())


def test_a_data_source_outside_the_scopes_fails_naming_them():
    content, catalog = _resolver(["acme_dev.cql", "acme_shared"])
    with pytest.raises(ResolutionError, match="no data source is tagged 'theirs' in catalog scopes "
                                              "acme_dev.cql, acme_shared; known: claims, fhir"):
        resolve_data_source(content, catalog, "theirs", [])


# --- The session setting ------------------------------------------------------------------------------

def test_the_session_setting_resolves_explicit_then_spark_conf_then_env(monkeypatch):
    conf = {config.CONF_CATALOG_SCOPES: "acme_dev.cql, acme_shared"}
    monkeypatch.setenv(config.ENV_CATALOG_SCOPES, "from_env")
    assert config.resolve_catalog_scopes(["explicit"], conf=conf) == ["explicit"]
    assert config.resolve_catalog_scopes(conf=conf) == ["acme_dev.cql", "acme_shared"]
    assert config.resolve_catalog_scopes(conf={}) == ["from_env"]


def test_an_unset_session_setting_is_none_configured(monkeypatch):
    monkeypatch.delenv(config.ENV_CATALOG_SCOPES, raising=False)
    assert config.resolve_catalog_scopes(conf={}) is None
