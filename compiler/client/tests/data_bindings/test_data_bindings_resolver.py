"""CONTENT-RESOLVER, KNOWLEDGE-LAKE-DIRECT — tag lookup, layout checks, and the corpus, over a fake runner.

Ported from the retired ``test_knowledge_query.py`` (parsing, closure) and ``test_knowledge_sync.py``
(identity, portable SQL); the corpus tests are new, because the derivation moved from a sync job into the
load.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from cql_databricks.data_bindings import (
    DATA_SOURCE_TAG, KNOWLEDGE_TAG, TERMINOLOGY, TERMINOLOGY_TAG, ContentResolver, LibraryId, ResolverError, TaggedSchema,
    check_layout, compare_versions, load_corpus, parse_identity, parse_includes,
    parse_resource_types, parse_value_sets, tag_statement, warehouse_runner)
from cql_databricks.data_bindings import corpus as corpus_module


class FakeRunner:
    """Answers each statement by the first pattern it matches, and records every call."""

    def __init__(self, answers: list[tuple[str, object]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, statement, params=None):
        self.calls.append((statement, params))
        for pattern, answer in self.answers:
            if re.search(pattern, statement, re.S):
                return answer(statement, params) if callable(answer) else answer
        raise AssertionError(f"unexpected statement: {statement}")


def _tags(tag, *rows):
    return [{"catalog_name": c, "schema_name": s, "tag_name": tag, "tag_value": v} for c, s, v in rows]


def _columns(layout):
    return [{"table_name": t, "column_name": c} for t, cols in layout.items() for c in cols]


KNOWLEDGE_COLUMNS = _columns({"library": ["url", "version", "resource", "source_package"],
                              "measure": ["url", "version", "resource"]})


# --- CQL parsing ------------------------------------------------------------------------------------

def test_parses_include_name_version_alias():
    inc = parse_includes("include FHIRHelpers version '4.0.1' called FH\ninclude \"Odd Name\" version '2.0'")
    assert [(i.name, i.version, i.alias) for i in inc] == [
        ("FHIRHelpers", "4.0.1", "FH"), ("Odd Name", "2.0", None)]


def test_parses_value_sets_and_resource_types():
    src = ("valueset \"Pap Test\": 'http://x/pap'\n"
           "define \"A\": exists ([Observation: \"Pap Test\"]) and exists ([Condition])")
    assert parse_value_sets(src) == ["http://x/pap"]
    assert parse_resource_types(src) == ["Observation", "Condition"]


def test_compare_versions_orders_numerically_per_segment():
    assert compare_versions("4.18", "4.6") > 0
    assert compare_versions("1.0.0", "1.0.0") == 0


def test_identity_prefers_the_cql_header_over_fhir():
    """FHIR "2026.0.0" vs CQL "5.0.000" — the header wins; it is what the compiler resolves."""
    cql = "library Encounters version '5.0.000'\nusing FHIR version '4.0.1'"
    assert parse_identity(cql, url="http://x/Library/AOEFHIR", version="2026.0.0") == (
        "Encounters", "5.0.000")


@pytest.mark.parametrize("cql", ["", None, "-- no header"])
def test_identity_falls_back_to_url_tail_and_fhir_version(cql):
    assert parse_identity(cql, url="http://x/Library/Helpers/", version="4.0.001") == ("Helpers", "4.0.001")


# --- Tags -----------------------------------------------------------------------------------------

def test_tag_statement_quotes_identifiers_and_refuses_a_quote_in_the_value():
    assert tag_statement(KNOWLEDGE_TAG, "cms", "main", "ecqm") == (
        "ALTER SCHEMA `main`.`ecqm` SET TAGS ('cql_databricks_knowledge' = 'cms')")
    with pytest.raises(ResolverError):
        tag_statement(KNOWLEDGE_TAG, "o'brien", "main", "ecqm")


# --- Layout ---------------------------------------------------------------------------------------

def test_layout_check_names_every_gap_at_once():
    schema = TaggedSchema(TERMINOLOGY_TAG, "vsac", "main", "vsac")
    run = FakeRunner([("information_schema.columns", _columns({"valueset": ["valueset_id", "code"]}))])
    with pytest.raises(ResolverError) as e:
        check_layout(run, schema, TERMINOLOGY)
    assert "valueset is missing column system" in str(e.value)
    assert "valueset is missing column version" in str(e.value)


def test_layout_check_accepts_extra_columns():
    schema = TaggedSchema(TERMINOLOGY_TAG, "vsac", "main", "vsac")
    run = FakeRunner([("information_schema.columns",
                       _columns({"valueset": ["valueset_id", "system", "code", "version", "display"]}))])
    check_layout(run, schema, TERMINOLOGY)


# --- Corpus ---------------------------------------------------------------------------------------

def _library_row(url, fhir_version, cql, **extra):
    return {"url": url, "version": fhir_version, "content_cql": cql, "title": None,
            "description": extra.get("description"), "data_requirement": extra.get("data_requirement")}


HELPERS = "library Helpers version '1.0.0'\nusing FHIR version '4.0.1'"
BASE = "library Base version '2.0.0'\ninclude Helpers version '1.0.0'"
MEASURE = "library Measure1 version '1.0.000'\ninclude Base version '2.0.0'"


def _knowledge_runner(measure_resource=None):
    libraries = [
        _library_row("http://x/Library/Helpers", "4.0.1", HELPERS, description="helpers"),
        _library_row("http://x/Library/Base", "2.0.0", BASE),
        _library_row("http://x/Library/Measure1", "2026.0.0", MEASURE,
                     data_requirement=json.dumps([{"type": "Condition"}, "junk"])),
    ]
    edges = [{"measure_url": "http://x/Measure/Measure1", "measure_version": "2026.0.0",
              "title": "Measure One", "description": "measure desc", "library_url": "http://x/Library/Measure1"}]
    return FakeRunner([
        ("information_schema.columns", KNOWLEDGE_COLUMNS),
        (r"variant_explode\(variant_get\(resource, '\$\.library'\)\)", edges),
        (r"variant_explode\(variant_get\(resource, '\$\.content'\)\)", libraries),
        (r"to_json\(resource\) AS resource FROM", [{"resource": json.dumps(measure_resource)}]
         if measure_resource else []),
    ])


SOURCE = TaggedSchema(KNOWLEDGE_TAG, "cms", "main", "ecqm")


def test_corpus_joins_measure_roots_and_keeps_library_values():
    corpus = load_corpus(_knowledge_runner(), [SOURCE])
    root = corpus.library("Measure1", "1.0.000")
    assert (root.kind, root.title, root.description) == ("measure", "Measure One", "measure desc")
    assert root.data_requirements == [{"type": "Condition"}]
    helpers = corpus.library("Helpers")
    assert (helpers.kind, helpers.description, helpers.model) == ("library", "helpers", "FHIR")


def test_discover_carries_no_source():
    records = load_corpus(_knowledge_runner(), [SOURCE]).discover()
    assert {r["id"]["name"] for r in records} == {"Helpers", "Base", "Measure1"}
    assert all("source" not in r for r in records)


def test_closure_puts_dependencies_before_dependents():
    closure = load_corpus(_knowledge_runner(), [SOURCE]).resolve_closure(MEASURE)
    assert [r.id.name for r in closure.includes] == ["Helpers", "Base"]
    assert closure.unresolved == []


def test_closure_leaves_a_pin_the_corpus_lacks_unresolved():
    """KNOWLEDGE-RESOLVE-API — a pinned version matches exactly; the 1.0.0 Helpers does not stand in for 9.9.9."""
    closure = load_corpus(_knowledge_runner(), [SOURCE]).resolve_closure(
        "library M version '1'\ninclude Helpers version '9.9.9'")
    assert closure.includes == []
    assert [(u.name, u.version) for u in closure.unresolved] == [("Helpers", "9.9.9")]


def test_closure_surfaces_an_unresolvable_include():
    closure = load_corpus(_knowledge_runner(), [SOURCE]).resolve_closure(
        "library M version '1'\ninclude Nope version '1.0.0'")
    assert [(u.name, u.version) for u in closure.unresolved] == [("Nope", "1.0.0")]


def test_measure_resource_is_a_cached_point_read_and_none_for_a_plain_library():
    runner = _knowledge_runner(measure_resource={"resourceType": "Measure", "rationale": "r"})
    corpus = load_corpus(runner, [SOURCE])
    assert corpus.measure_resource("Helpers", "1.0.0") is None
    assert corpus.measure_resource("Measure1", "1.0.000")["rationale"] == "r"
    corpus.measure_resource("Measure1", "1.0.000")
    point_reads = [p for s, p in runner.calls if "AS resource FROM" in s]
    assert point_reads == [{"url": "http://x/Measure/Measure1", "version": "2026.0.0"}]


def test_a_source_that_breaks_its_layout_fails_the_load():
    runner = FakeRunner([("information_schema.columns", _columns({"library": ["url"]}))])
    with pytest.raises(ResolverError, match="missing table measure"):
        load_corpus(runner, [SOURCE])


def test_the_first_copy_of_a_library_wins_across_sources():
    other = TaggedSchema(KNOWLEDGE_TAG, "copy", "main", "copy")
    corpus = load_corpus(_knowledge_runner(), [SOURCE, other])
    assert [lib.knowledge.name for lib in corpus.libraries if lib.name == "Helpers"] == ["cms"]


def test_corpus_sql_is_portable():
    """`resource:path` is Databricks-only SQL; every VARIANT navigation goes through `variant_get`."""
    for statement in (corpus_module._LIBRARIES, corpus_module._MEASURES, corpus_module._MEASURE_RESOURCE):
        assert "resource:" not in statement
    assert "unbase64(" in corpus_module._LIBRARIES
    assert "split_part(" in corpus_module._MEASURES and "'|'" in corpus_module._MEASURES


# --- ContentResolver ------------------------------------------------------------------------------

def test_resolver_builds_its_runner_lazily_and_caches_until_reload():
    built = []
    runner = _knowledge_runner()
    runner.answers.insert(0, ("schema_tags", _tags(KNOWLEDGE_TAG, ("main", "ecqm", "cms"))))

    def factory():
        built.append(1)
        return runner

    content = ContentResolver(factory, ["main"])
    assert built == []
    first = content.corpus()
    assert content.corpus() is first and built == [1]
    content.reload()
    assert content.corpus() is not first


def test_terminology_sources_are_layout_checked():
    runner = FakeRunner([("schema_tags", _tags(TERMINOLOGY_TAG, ("main", "vsac", "vsac"))),
                         ("information_schema.columns", _columns({"valueset": ["valueset_id"]}))])
    with pytest.raises(ResolverError, match="does not match its layout"):
        ContentResolver(lambda: runner, ["main"]).terminology_sources()


def test_data_source_schemas_are_found_by_their_own_key_and_cached_until_reload():
    # DATA-SOURCE-UC-TAG — found here, checked by the compiler; so no layout read is issued.
    runner = FakeRunner([("schema_tags", _tags(DATA_SOURCE_TAG, ("main", "fhir", "fhir_prod")))])
    content = ContentResolver(lambda: runner, ["main"])
    first = content.data_source_schemas()
    assert [(s.tag, s.name, s.fqn) for s in first] == [(DATA_SOURCE_TAG, "fhir_prod", "`main`.`fhir`")]
    assert content.data_source_schemas() is first and len(runner.calls) == 1
    content.reload()
    assert content.data_source_schemas() is not first


# --- warehouse_runner -----------------------------------------------------------------------------

def test_warehouse_runner_reads_external_links_across_chunks_with_parameters(monkeypatch):
    def status(state):
        return SimpleNamespace(state=SimpleNamespace(value=state), error=None)

    manifest = SimpleNamespace(schema=SimpleNamespace(columns=[SimpleNamespace(name="a"),
                                                               SimpleNamespace(name="b")]))
    first = SimpleNamespace(statement_id="s1", status=status("RUNNING"))
    done = SimpleNamespace(statement_id="s1", status=status("SUCCEEDED"), manifest=manifest,
                           result=SimpleNamespace(external_links=[
                               SimpleNamespace(external_link="u0", next_chunk_index=1)]))
    chunk1 = SimpleNamespace(external_links=[SimpleNamespace(external_link="u1", next_chunk_index=None)])
    api = SimpleNamespace(
        execute_statement=lambda **kw: (api.seen.append(kw), first)[1],
        get_statement=lambda sid: done,
        get_statement_result_chunk_n=lambda sid, i: chunk1,
        seen=[])
    client = SimpleNamespace(statement_execution=api)
    monkeypatch.setattr("cql_databricks.data_bindings.runner.time.sleep", lambda s: None)
    run = warehouse_runner(client, "wh", fetch=lambda url: {"u0": [["1", "x"]], "u1": [["2", None]]}[url])
    rows = run("SELECT :p", {"p": "v"})
    assert rows == [{"a": "1", "b": "x"}, {"a": "2", "b": None}]
    assert [(p.name, p.value) for p in api.seen[0]["parameters"]] == [("p", "v")]


def _statement_api(*states):
    """A Statement Execution API whose one statement, ``s1``, passes through ``states`` in order."""
    manifest = SimpleNamespace(schema=SimpleNamespace(columns=[]))
    responses = [SimpleNamespace(statement_id="s1", manifest=manifest, result=None,
                                 status=SimpleNamespace(state=SimpleNamespace(value=state),
                                                        error=SimpleNamespace(message="boom")))
                 for state in states]
    return SimpleNamespace(execute_statement=lambda **kw: responses[0],
                           get_statement=lambda sid: responses.pop(1) if len(responses) > 1 else responses[0])


def test_warehouse_runner_reports_each_statement_id_and_wall_clock_to_its_observer(monkeypatch):
    """APP-RUN-LOG — the observer sees the statement ID, its final state, and a start before its end."""
    monkeypatch.setattr("cql_databricks.data_bindings.runner.time.sleep", lambda s: None)
    seen = []
    client = SimpleNamespace(statement_execution=_statement_api("RUNNING", "SUCCEEDED"))
    run = warehouse_runner(client, "wh", fetch=lambda url: [], observer=seen.append)
    assert run("CREATE OR REPLACE TABLE t AS SELECT 1") == []
    [statement] = seen
    assert (statement.statement, statement.statement_id, statement.state) == (
        "CREATE OR REPLACE TABLE t AS SELECT 1", "s1", "SUCCEEDED")
    assert statement.started_at <= statement.ended_at


def test_warehouse_runner_observes_a_failed_statement_before_raising():
    """APP-RUN-LOG — a failed step still has a statement ID to join to system.query.history."""
    seen = []
    client = SimpleNamespace(statement_execution=_statement_api("FAILED"))
    with pytest.raises(ResolverError, match="FAILED: boom"):
        warehouse_runner(client, "wh", fetch=lambda url: [], observer=seen.append)("SELECT 1")
    assert [(s.statement_id, s.state) for s in seen] == [("s1", "FAILED")]


def test_warehouse_runner_needs_a_warehouse():
    with pytest.raises(ResolverError, match="warehouse"):
        warehouse_runner(SimpleNamespace(), "")
