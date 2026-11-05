"""CONTENT-IMPORT-MEASURES — the one import path: what it reads, what it refuses, what it writes. No Spark, no
network: the content is made up here, and Spark is a fake that records what it is asked."""
from __future__ import annotations

import base64

import pytest

from cql_databricks.data_bindings.measure_import.measures import (ImportContentError, declared_valuesets, flatten, import_measures,
                                     valueset_codes)

BASE = "http://example.org/fhir"
CQL = ("library Visits version '1.0.0'\nusing FHIR version '4.0.1'\n"
       f"valueset \"Office Visit\": '{BASE}/ValueSet/office-visit'\ncontext Patient\n"
       "define \"Initial Population\": exists [Encounter: \"Office Visit\"]\n")
LIBRARY = {"resourceType": "Library", "url": f"{BASE}/Library/Visits", "version": "1.0.0", "name": "Visits",
           "content": [{"contentType": "text/cql", "data": base64.b64encode(CQL.encode()).decode()}]}
MEASURE = {"resourceType": "Measure", "url": f"{BASE}/Measure/Visits", "version": "1.0.0",
           "library": [f"{BASE}/Library/Visits|1.0.0"]}
VALUESET = {"resourceType": "ValueSet", "url": f"{BASE}/ValueSet/office-visit", "version": "2",
            "expansion": {"timestamp": "2026-10-01", "contains": [
                {"system": "http://example.org/codes", "code": "a", "display": "A"},
                {"system": "http://example.org/codes", "code": "group", "abstract": True,
                 "contains": [{"system": "http://example.org/codes", "code": "b"}]}]}}


class _FakeSpark:
    """Tables that exist, with the rows a ``SELECT DISTINCT`` returns; records every statement."""

    def __init__(self, tables: dict[str, list[str]] | None = None):
        self.tables = tables or {}
        self.statements: list[str] = []
        self.catalog = self

    def tableExists(self, fqtn: str) -> bool:
        return fqtn in self.tables

    def sql(self, statement: str):
        self.statements.append(statement)
        fqtn = statement.rsplit(" FROM ", 1)[-1]
        return type("Rows", (), {"collect": lambda _self: [(v,) for v in self.tables.get(fqtn, [])]})()


@pytest.fixture
def writes(monkeypatch):
    """Stand in for the Spark writers; records what would be written."""
    from cql_databricks.data_bindings.measure_import import load as load_module

    seen: dict = {}

    def fake_load(spark, result, *, catalog, authority_schema, valueset_schema):
        seen["load"] = (result, catalog, authority_schema, valueset_schema)
        return {"measure": len(result.measures), "library": len(result.libraries),
                "valueset": len(result.valueset_expansions)}

    monkeypatch.setattr(load_module, "load", fake_load)
    monkeypatch.setattr(load_module, "ensure_valueset_table", lambda spark, **kw: seen.setdefault("ensure", kw))
    return seen


def test_bundles_are_flattened_at_any_depth():
    inner = {"resourceType": "Bundle", "entry": [{"resource": VALUESET}]}
    outer = {"resourceType": "Bundle", "entry": [{"resource": MEASURE}, {"resource": inner}, {"fullUrl": "x"}]}
    assert [r["resourceType"] for r in flatten([outer, LIBRARY])] == ["Measure", "ValueSet", "Library"]


def test_an_expansion_gives_its_codes_and_skips_abstract_groups():
    """LANG-VALUESET-VERSION — the rows carry the ValueSet's version, which a CQL declaration pins, not the expansion's
    timestamp."""
    rows = valueset_codes(VALUESET, source="s", now="t")
    assert [(r.code, r.display) for r in rows] == [("a", "A"), ("b", None)]
    assert {(r.valueset_id, r.version, r.source_package) for r in rows} == {(VALUESET["url"], "2", "s")}


def test_a_compose_that_lists_concepts_is_its_codes():
    vs = {"resourceType": "ValueSet", "url": f"{BASE}/ValueSet/x", "compose": {"include": [
        {"system": "http://example.org/codes", "concept": [{"code": "c"}]}]}}
    assert [r.code for r in valueset_codes(vs, source="s", now="t")] == ["c"]


@pytest.mark.parametrize("include", [{"system": "s", "filter": [{"property": "concept", "op": "is-a", "value": "x"}]},
                                     {"valueSet": [f"{BASE}/ValueSet/other"]}])
def test_a_value_set_that_needs_expanding_fails(include):
    vs = {"resourceType": "ValueSet", "url": f"{BASE}/ValueSet/x", "compose": {"include": [include]}}
    with pytest.raises(ImportContentError, match="expand it first"):
        valueset_codes(vs, source="s", now="t")


def test_a_library_declares_its_value_sets_in_cql_requirements_and_dependencies():
    lib = dict(LIBRARY, dataRequirement=[{"codeFilter": [{"valueSet": f"{BASE}/ValueSet/lab|3"}]}],
               relatedArtifact=[{"type": "depends-on", "resource": f"{BASE}/ValueSet/dep"},
                                {"type": "depends-on", "resource": f"{BASE}/Library/Other"}])
    assert declared_valuesets(lib) == {f"{BASE}/ValueSet/office-visit", f"{BASE}/ValueSet/lab",
                                       f"{BASE}/ValueSet/dep"}


def test_an_import_writes_the_environments_knowledge(writes):
    """CONTENT-IMPORT-MEASURES, DATA-ENVIRONMENT-LAYOUT — knowledge, value sets too; nothing is tagged."""
    summary = import_measures(_FakeSpark(), [MEASURE, LIBRARY, VALUESET, {"resourceType": "Patient"}],
                              catalog="main", source="pkg")
    result, catalog, schema, valuesets = writes["load"]
    assert (catalog, schema, valuesets) == ("main", "knowledge", "knowledge")
    assert summary.rows == {"measure": 1, "library": 1, "valueset": 2}
    assert summary.skipped == {"Patient": 1}
    assert summary.knowledge == "main.knowledge"


def test_codes_alone_go_to_the_knowledge(writes):
    codes = [{"valueset_id": f"{BASE}/ValueSet/office-visit", "system": "http://example.org/codes", "code": "a"}]
    summary = import_measures(_FakeSpark(), [], codes=codes, catalog="main", source="pkg")
    assert summary.knowledge == "main.knowledge" and summary.valuesets == [f"{BASE}/ValueSet/office-visit"]


def test_a_dry_run_checks_and_writes_nothing(writes):
    summary = import_measures(_FakeSpark(), [MEASURE, LIBRARY, VALUESET], catalog="main", source="pkg", dry_run=True)
    assert summary.measures == [f"{BASE}/Measure/Visits|1.0.0"] and summary.rows == {}
    assert "load" not in writes


def test_every_problem_is_reported_at_once_and_nothing_is_written(writes):
    measure = dict(MEASURE, library=[f"{BASE}/Library/Missing"])
    bad = {"resourceType": "ValueSet", "url": f"{BASE}/ValueSet/bad", "compose": {"include": [{"valueSet": ["x"]}]}}
    with pytest.raises(ImportContentError) as e:
        import_measures(_FakeSpark(), [measure, LIBRARY, bad], catalog="main", source="pkg")
    message = str(e.value)
    assert "Library/Missing" in message and "office-visit" in message and "ValueSet/bad" in message
    assert "load" not in writes


def test_what_is_already_in_the_target_counts(writes):
    """A measure may name a library, and a library a value set, loaded by an earlier import."""
    spark = _FakeSpark({"`main`.`knowledge`.`library`": [f"{BASE}/Library/Visits"],
                        "`main`.`knowledge`.`valueset`": [f"{BASE}/ValueSet/office-visit"]})
    summary = import_measures(spark, [MEASURE, LIBRARY], catalog="main", source="pkg")
    assert summary.rows["measure"] == 1


def test_nothing_to_import_fails():
    with pytest.raises(ImportContentError, match="nothing to import"):
        import_measures(_FakeSpark(), [{"resourceType": "Patient"}], catalog="main", source="pkg")


def test_no_catalog_fails():
    with pytest.raises(ImportContentError, match="no catalog"):
        import_measures(_FakeSpark(), [MEASURE], catalog="", source="pkg")

