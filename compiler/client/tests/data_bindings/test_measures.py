"""CONTENT-IMPORT-MEASURES — the one import path: what it reads, what it refuses, what it writes into the caller's
folder. No Spark, no network: the content is made up here, and the folder is a temporary one."""
from __future__ import annotations

import base64
import json

import pytest

from cql_databricks.data_bindings.measure_import.measures import (ImportContentError, check_import, declared_valuesets,
                                                                  flatten, import_measures, valueset_codes)

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


def test_an_import_writes_the_folder_layout_the_folder_resolvers_read(tmp_path):
    """CONTENT-IMPORT-MEASURES, DEC-NO-CONTENT-PUBLISHING — files in the caller's folder, never a table."""
    summary = import_measures([MEASURE, LIBRARY, VALUESET, {"resourceType": "Patient"}], folder=str(tmp_path))
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == ["libraries/Visits.cql", "resources/library-Visits-1.0.0.json",
                       "resources/measure-Visits-1.0.0.json", "valuesets/office-visit-2.json"]
    assert (tmp_path / "libraries" / "Visits.cql").read_text() == CQL
    assert json.loads((tmp_path / "resources" / "measure-Visits-1.0.0.json").read_text())["url"] == MEASURE["url"]
    valueset = json.loads((tmp_path / "valuesets" / "office-visit-2.json").read_text())
    assert (valueset["url"], valueset["version"]) == (VALUESET["url"], "2")
    assert [c["code"] for c in valueset["expansion"]["contains"]] == ["a", "b"]
    assert summary.skipped == {"Patient": 1} and len(summary.files) == 4 and summary.folder == str(tmp_path)


def test_what_the_import_writes_the_folder_resolvers_read(tmp_path):
    """RUN-ADDRESS-LOCATION, CLIENT-KNOWLEDGE-RESOLVER — the library and its measure resolve from the folder."""
    from cql_databricks.resolvers.knowledge import FolderKnowledgeResolver
    from cql_databricks.resolvers.terminology import parse_value_set

    import_measures([MEASURE, LIBRARY, VALUESET], folder=str(tmp_path))
    folder = FolderKnowledgeResolver(str(tmp_path))
    assert folder.library("Visits", "1.0.0").name == "Visits"
    assert folder.measure(MEASURE["url"], "1.0.0")["url"] == MEASURE["url"]
    path = tmp_path / "valuesets" / "office-visit-2.json"
    assert [c.code for c in parse_value_set(json.loads(path.read_text()), str(path)).codes] == ["a", "b"]


def test_codes_alone_become_a_value_set_file(tmp_path):
    codes = [{"valueset_id": f"{BASE}/ValueSet/office-visit", "system": "http://example.org/codes", "code": "a"}]
    summary = import_measures([], codes=codes, folder=str(tmp_path))
    assert summary.valuesets == [f"{BASE}/ValueSet/office-visit"]
    assert json.loads((tmp_path / "valuesets" / "office-visit.json").read_text())["expansion"]["contains"] == [
        {"system": "http://example.org/codes", "code": "a"}]


def test_a_dry_run_checks_and_writes_nothing(tmp_path):
    summary = import_measures([MEASURE, LIBRARY, VALUESET], folder=str(tmp_path), dry_run=True)
    assert summary.measures == [f"{BASE}/Measure/Visits|1.0.0"] and summary.files == []
    assert not any(tmp_path.iterdir())


def test_every_problem_is_reported_at_once_and_nothing_is_written(tmp_path):
    measure = dict(MEASURE, library=[f"{BASE}/Library/Missing"])
    bad = {"resourceType": "ValueSet", "url": f"{BASE}/ValueSet/bad", "compose": {"include": [{"valueSet": ["x"]}]}}
    with pytest.raises(ImportContentError) as e:
        import_measures([measure, LIBRARY, bad], folder=str(tmp_path))
    message = str(e.value)
    assert "Library/Missing" in message and "office-visit" in message and "ValueSet/bad" in message
    assert not any(tmp_path.iterdir())


def test_what_is_already_in_the_folder_counts(tmp_path):
    """A measure may name a library, and a library a value set, an earlier import wrote to the folder."""
    import_measures([LIBRARY, VALUESET], folder=str(tmp_path))
    summary = import_measures([MEASURE], folder=str(tmp_path))
    assert summary.measures == [f"{BASE}/Measure/Visits|1.0.0"]


def test_a_library_file_of_another_version_is_never_overwritten(tmp_path):
    """CONTENT-IMPORT-MEASURES — the folder's ``Visits.cql`` at 1.0.0 is not replaced by an import of 2.0.0."""
    import_measures([LIBRARY, VALUESET], folder=str(tmp_path))
    cql = CQL.replace("version '1.0.0'", "version '2.0.0'", 1)
    newer = dict(LIBRARY, version="2.0.0",
                 content=[{"contentType": "text/cql", "data": base64.b64encode(cql.encode()).decode()}])
    with pytest.raises(ImportContentError, match="holds Visits version 1.0.0"):
        import_measures([newer], folder=str(tmp_path))
    assert (tmp_path / "libraries" / "Visits.cql").read_text() == CQL


def test_two_versions_of_a_value_set_are_two_files(tmp_path):
    """LANG-VALUESET-VERSION — each version keeps its own codes, so a pinned declaration finds its own."""
    older = dict(VALUESET, version="1", expansion={"contains": [{"system": "http://example.org/codes", "code": "z"}]})
    import_measures([VALUESET, older], folder=str(tmp_path))
    assert sorted(p.name for p in (tmp_path / "valuesets").iterdir()) == ["office-visit-1.json", "office-visit-2.json"]


def test_check_import_is_what_the_deploy_loads_its_tables_through():
    """DEPLOY-DEFAULT-ENVIRONMENT — the check writes nothing and hands back the rows."""
    checked = check_import([MEASURE, LIBRARY, VALUESET], source="examples", lineage="example", where="main.knowledge")
    assert (len(checked.content.measures), len(checked.content.libraries)) == (1, 1)
    assert {r.source_package for r in checked.content.valueset_expansions} == {"examples"}


def test_nothing_to_import_fails(tmp_path):
    with pytest.raises(ImportContentError, match="nothing to import"):
        import_measures([{"resourceType": "Patient"}], folder=str(tmp_path))
