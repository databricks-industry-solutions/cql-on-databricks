"""AGENT-DEFINITION-DISCOVERY, AGENT-VALUE-SET-LOOKUP, AGENT-INLINE-EXPERIMENTS, AGENT-CQL-REVIEW.

The experiment tools run real inline runs (`cql_databricks.result.run_inline`) over a sqlite warehouse,
so counts, scores and who moved are arithmetic, not canned rows. Synthetic diabetes measure: the edit
removes the exclusion criterion.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from cql_databricks.agent.service import definitions, experiments
from cql_databricks.compiler.providers import DefineShape, SqlCompileResult, SqlStatement
from cql_databricks.data_bindings import Corpus, Library, TaggedSchema
from cql_databricks.result import run_inline

KNOWLEDGE = TaggedSchema("synthetic", "synthetic", "synthetic", "knowledge")

BASELINE = """library Diabetes version '1'
valueset "Diabetes": 'urn:vs:diabetes'
valueset "Hospice": 'urn:vs:hospice'
context Patient
define "Denominator Exclusions": exists [Encounter: "Hospice"]
"""
EDITED = BASELINE.replace('exists [Encounter: "Hospice"]', "false")

#: Per CQL text, the define -> (subject, value) rows the fake compiler's views return.
ROWS = {
    BASELINE: {"Initial Population": [("p1", 1), ("p2", 1), ("p3", 1)],
               "Denominator": [("p1", 1), ("p2", 1), ("p3", 1)],
               "Denominator Exclusions": [("p3", 1)],
               "Numerator": [("p1", 1), ("p3", 1), ("p9", 1)]},
    EDITED: {"Initial Population": [("p1", 1), ("p2", 1), ("p3", 1)],
             "Denominator": [("p1", 1), ("p2", 1), ("p3", 1)],
             "Denominator Exclusions": [("p3", 0)],
             "Numerator": [("p1", 1), ("p3", 1), ("p9", 1)]},
}


def library(name, source, kind="library", title="", description=""):
    return Library(name=name, version="1", url=f"http://synthetic/{name}", fhir_version="1", source=source,
                   description=description, title=title, kind=kind, model="", data_requirements=[],
                   knowledge=KNOWLEDGE)


CORPUS = Corpus(lambda *args: [], [KNOWLEDGE], [
    library("Helper", 'library Helper version \'1\'\nvalueset "Diabetes Dx": \'urn:vs:diabetes\''),
    library("Diabetes", BASELINE, kind="measure", title="Diabetes: HbA1c Poor Control",
            description="Patients with diabetes whose most recent HbA1c is above 9%"),
])


class Content:
    def corpus(self):
        return CORPUS

    def data_source_schemas(self):
        return [TaggedSchema("population", "population", "synthetic", "source")]


class Compiler:
    def compile_sql(self, request):
        statements, shapes = [], []
        for define, rows in ROWS[request.cql].items():
            obj = f"{request.object_prefix}__{define.replace(' ', '_')}"
            values = " UNION ALL ".join(f"SELECT '{s}' AS __subject_id, {v} AS __val" for s, v in rows)
            statements.append(SqlStatement(obj, "view", f"CREATE TEMP VIEW `{obj}` AS {values}"))
            shapes.append(DefineShape(define, obj, "scalar"))
        return SqlCompileResult(statements, statements[-1].name, list(ROWS[request.cql]), shapes)


class Warehouse:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")

    def __call__(self, statement, params=None):
        if "information_schema.routines" in statement:
            return [{"routine_name": "retrieve_patient"}]
        self.db.execute(statement)
        return []

    def rows(self, statement):
        described = re.match(r"DESCRIBE `(.+)`", statement)
        if described:
            return [(c[1], "boolean" if c[1] == "__val" else "string")
                    for c in self.db.execute(f"PRAGMA table_info(`{described[1]}`)")]
        return self.db.execute(statement).fetchall()

    def views(self):
        return [r[0] for r in self.db.execute("SELECT name FROM sqlite_temp_master WHERE type = 'view'")]


@pytest.fixture
def warehouse():
    return Warehouse()


def inline_on(warehouse):
    def inline(cql, data_source, period, location=None):
        return run_inline(content=Content(), run=warehouse, compiler=Compiler(), data_source=data_source,
                          cql=cql, runtime_catalog="synthetic_runtime", period=period)
    return inline


def scoring_for(library, version, location=None):
    return "proportion" if library == "Diabetes" else None


class TestDefinitions:
    def test_the_catalog_lists_measures_first_with_their_titles(self):
        entries = definitions.list_definitions(corpus=lambda: CORPUS)
        assert [e["library"] for e in entries] == ["Diabetes", "Helper"]
        assert entries[0]["title"] == "Diabetes: HbA1c Poor Control"

    def test_a_definition_is_its_cql_as_written(self):
        found = definitions.get_definition("Diabetes", "1", corpus=lambda: CORPUS)
        assert found["cql"] == BASELINE and found["kind"] == "measure"
        assert definitions.get_definition("Missing", "1", corpus=lambda: CORPUS) is None

    def test_value_sets_carry_every_name_they_are_declared_under(self):
        by_url = {e["url"]: e for e in definitions.list_value_sets(corpus=lambda: CORPUS)}
        assert by_url["urn:vs:diabetes"]["names"] == ["Diabetes Dx", "Diabetes"]
        assert by_url["urn:vs:diabetes"]["libraries"] == ["Helper|1", "Diabetes|1"]

    def test_a_code_lookup_names_each_value_set_and_who_uses_it(self):
        seen = []

        def lookup(code, system):
            seen.append((code, system))
            return [("urn:vs:diabetes", "http://snomed.info/sct", code), ("urn:vs:undeclared", "http://snomed.info/sct", code)]

        hits = definitions.value_sets_with_code(" 44054006 ", "http://snomed.info/sct", lookup=lookup,
                                                corpus=lambda: CORPUS)
        assert seen == [("44054006", "http://snomed.info/sct")]
        assert hits[0]["names"] == ["Diabetes Dx", "Diabetes"]
        assert hits[1]["url"] == "urn:vs:undeclared" and hits[1]["names"] == []


class TestExperiments:
    def test_check_cql_returns_the_diff_to_review_and_runs_nothing(self):
        result = experiments.check_cql(EDITED, BASELINE, translate=lambda cql, location=None: {
            "library": "Diabetes", "version": "1", "ok": True, "diagnostics": []})
        assert result["ok"] and result["cql"] == EDITED
        assert '-define "Denominator Exclusions": exists [Encounter: "Hospice"]' in result["diff"]
        assert '+define "Denominator Exclusions": false' in result["diff"]

    def test_run_cql_counts_as_written_scores_and_keeps_nothing(self, warehouse):
        result = experiments.run_cql(BASELINE, "population", ("2026-01-01", "2026-12-31"), scoring=None,
                                     defines=None, inline=inline_on(warehouse), scoring_for=scoring_for)
        assert result["counts"]["Numerator"] == 3          # as written: p1, p3 and the non-diabetic p9
        assert (result["score"]["numerator"], result["score"]["base"]) == (1, 2)
        assert result["cql"] == BASELINE and result["provenance"] == "experiment"
        assert warehouse.views() == []

    def test_compare_cql_reports_who_moved_and_the_score_change(self, warehouse):
        result = experiments.compare_cql(BASELINE, EDITED, "population", ("2026-01-01", "2026-12-31"),
                                         scoring=None, defines=None, inline=inline_on(warehouse),
                                         scoring_for=scoring_for)
        changes = {c["define"]: c for c in result["changes"]}
        assert changes["Denominator Exclusions"] == {"define": "Denominator Exclusions", "before": 1, "after": 0,
                                                     "entered": 0, "left": 1}
        assert changes["Numerator"]["before"] == changes["Numerator"]["after"] == 3
        before, after = result["baseline"]["score"], result["experiment"]["score"]
        assert (before["numerator"], before["base"]) == (1, 2)
        assert (after["numerator"], after["base"]) == (2, 3)   # p3 is no longer excluded
        assert "+define" in result["experiment"]["diff"]
        assert warehouse.views() == []

    def test_an_unknown_define_is_refused_naming_what_exists(self, warehouse):
        with pytest.raises(ValueError, match="Missing"):
            experiments.run_cql(BASELINE, "population", (None, None), scoring=None, defines=["Missing"],
                                inline=inline_on(warehouse), scoring_for=scoring_for)
        assert warehouse.views() == []

    def test_without_a_declared_scoring_there_is_no_score_and_it_says_why(self, warehouse):
        result = experiments.run_cql(BASELINE, "population", (None, None), scoring=None, defines=None,
                                     inline=inline_on(warehouse), scoring_for=lambda *a: None)
        assert result["score"] is None and "scoring" in result["scoreNote"]
