"""CLIENT-INLINE-WAREHOUSE — inline runs create prefixed views, count them, and drop them.

The warehouse is sqlite: the fake compiler emits sqlite-compatible view statements, so counts and the
score are real arithmetic, not canned rows.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from dataclasses import replace
from datetime import datetime, timezone

from cql_databricks.resolvers.environment import Environment, EnvironmentResolver
from cql_databricks.compiler.providers import LibraryGraph, DefineShape, SqlCompileResult, SqlStatement
from cql_databricks.resolvers.interfaces import KeptResult, ResultResolver, TableRef
from cql_databricks.data_bindings import Corpus
from cql_databricks.result.inline import run_inline
from test_durable_plan import plan

CQL = "library Diabetes version '1'\ncontext Patient\ndefine \"Initial Population\": true"
ROWS = {  # define -> (subject, value) rows; Numerator is true for non-diabetic p6 too
    "Initial Population": [("p1", 1), ("p2", 1), ("p3", 1), ("p6", 0)],
    "Denominator": [("p1", 1), ("p2", 1), ("p3", 1), ("p6", 0)],
    "Denominator Exclusions": [("p3", 1)],
    "Numerator": [("p1", 1), ("p6", 1)],
}


class Content:
    def corpus(self):
        return Corpus(lambda *args: [], [], [])

    generation = 1


ENVIRONMENT = Environment(name="synthetic-population", catalog="synthetic", version="2")


class Compiler:
    def __init__(self, kind="view"):
        self.requests = []
        self.described = []
        self.kind = kind

    def describe(self, request):
        raise AssertionError("BIND-CALL-SITE: an environment binds by rule, so a run asks no describe")

    def resource_schemas(self, types):
        raise AssertionError("a table relation needs no reshape")

    def compile_sql(self, request):
        self.requests.append(request)
        prefix = request.object_prefix
        statements, shapes = [], []
        for define, rows in ROWS.items():
            obj = f"{prefix}__{define.replace(' ', '_')}"
            values = " UNION ALL ".join(f"SELECT '{s}' AS __subject_id, {v} AS __val" for s, v in rows)
            statements.append(SqlStatement(obj, self.kind, f"CREATE TEMP VIEW `{obj}` AS {values}"))
            shapes.append(DefineShape(define, obj, "scalar"))
        return SqlCompileResult(statements, statements[-1].name, list(ROWS), shapes)


class Warehouse:
    """A warehouse session over sqlite: ``run`` for statements, ``rows`` for reads."""

    def __init__(self, fail_on=None):
        self.db = sqlite3.connect(":memory:")
        self.statements = []
        self.fail_on = fail_on

    def __call__(self, statement, params=None):
        self.statements.append(statement)
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError("statement failed")
        self.db.execute(statement)
        return []

    def rows(self, statement):
        self.statements.append(statement)
        described = re.match(r"DESCRIBE `(.+)`", statement)
        if described:
            # sqlite views carry no declared type for a literal; the warehouse reports boolean.
            return [(c[1], "boolean" if c[1] == "__val" else "string")
                    for c in self.db.execute(f"PRAGMA table_info(`{described[1]}`)")]
        return self.db.execute(statement).fetchall()

    def views(self):
        return [r[0] for r in self.db.execute("SELECT name FROM sqlite_temp_master WHERE type = 'view'")]


def inline(compiler=None, warehouse=None, results=None):
    warehouse = warehouse or Warehouse()
    resolvers = EnvironmentResolver(warehouse).resolvers(ENVIRONMENT, results)
    return run_inline(content=Content(), run=warehouse, compiler=compiler or Compiler(), resolvers=resolvers,
                      cql=CQL, runtime_catalog="synthetic_runtime", period=("2026-01-01", "2026-12-31"))


def test_an_inline_run_binds_the_environment_and_period_under_its_own_prefix():
    compiler = Compiler()
    inline(compiler)
    inline(compiler)
    first, second = compiler.requests
    # BIND-PUBLISHED-VIEW: the environment's data and knowledge views, by rule; one compiler call.
    assert first.data == {"rule": {"schema": "synthetic.data", "identity": "synthetic.data"}}
    assert first.terminology == {"rule": [{"table": "synthetic.knowledge.valueset",
                                            "identity": "synthetic.knowledge"}]}
    assert first.measurement_period == ("2026-01-01", "2026-12-31")
    assert first.udf_schema == "synthetic_runtime.runtime" and not first.durable
    assert first.object_prefix != second.object_prefix


def test_populations_count_as_written_and_only_the_score_is_cascaded():
    with inline() as run:
        assert run.counts() == {"Initial Population": 3, "Denominator": 3, "Denominator Exclusions": 1,
                                "Numerator": 2}
        score = run.score("proportion")
    assert (score.numerator, score.base) == (1, 2)   # p1 of p1-p2; p3 excluded; p6 never in the Denominator


def test_closing_drops_every_view_and_nothing_else_is_written():
    warehouse = Warehouse()
    with inline(warehouse=warehouse) as run:
        assert len(warehouse.views()) == 4
        assert run.cql == CQL
    assert warehouse.views() == []
    assert not any(s.lstrip().upper().startswith(("CREATE TABLE", "INSERT", "ALTER")) for s in warehouse.statements)


def test_a_failed_statement_drops_the_views_already_created():
    warehouse = Warehouse(fail_on="Numerator")
    with pytest.raises(RuntimeError):
        inline(warehouse=warehouse)
    assert warehouse.views() == []


def test_a_cache_table_barrier_is_refused_before_anything_runs():
    warehouse = Warehouse()
    with pytest.raises(ValueError, match="views only"):
        inline(Compiler(kind="cache_table"), warehouse)
    assert warehouse.views() == []


KEPT = TableRef(catalog="synthetic", schema="results", table="synthetic_base_aaaaaaaaaaaaaaaa_0badcafe")


class Durable(Compiler):
    """The durable plan of a library ``Synthetic`` with ``Base`` and ``Output``, which reads ``Base``."""

    def compile_pipeline(self, request):
        self.requests.append(request)
        return replace(plan(), datasets=[replace(d, library="Diabetes") for d in plan().datasets])


class Kept(ResultResolver):
    """A result resolver holding ``Base`` only, recording what it is asked."""

    def __init__(self, held=None):
        self.held, self.asked = held if held is not None else {"a" * 64: KEPT}, []

    def find(self, keys, *, computed_since=None):
        self.asked.append([k.fingerprint for k in keys])
        return {k.fingerprint: KeptResult(table=self.held[k.fingerprint], fingerprint=k.fingerprint,
                                          computed_at=datetime(2026, 10, 31, tzinfo=timezone.utc))
                for k in keys if k.fingerprint in self.held}

    @property
    def destination(self):
        return ("synthetic", "draft_results")

    def holds(self, table):
        return True


class Recording:
    def __init__(self):
        self.statements = []

    def __call__(self, statement, params=None):
        self.statements.append(statement)
        return []


def test_an_inline_run_reads_a_kept_result_in_place_of_its_define():
    """CLIENT-RESULT-RESOLVER — one lookup for every define; a found one is read from its table, the rest are views
    that read it, and nothing is written or tagged."""
    compiler, results, warehouse = Durable(), Kept(), Recording()
    with inline(compiler, warehouse, results) as run:
        assert compiler.requests[0].durable is True
        assert results.asked == [["a" * 64, "b" * 64]]
        assert run.defines == ("Base", "Output")
        assert run.reused == {"Base": "`synthetic`.`results`.`synthetic_base_aaaaaaaaaaaaaaaa_0badcafe`"}
        assert run.relation("Base") == run.reused["Base"]
        (view,) = warehouse.statements
        assert view.startswith("CREATE TEMPORARY VIEW `inline_") and run.relation("Output") in view
        assert "WITH `base` AS (SELECT * FROM `synthetic`.`results`.`synthetic_base_aaaaaaaaaaaaaaaa_0badcafe`)" in view
    assert warehouse.statements[-1].startswith("DROP VIEW IF EXISTS `inline_")
    assert not any(s.startswith(("CREATE TABLE", "CREATE OR REPLACE TABLE", "ALTER", "INSERT", "DROP TABLE"))
                   for s in warehouse.statements)


def test_an_inline_run_with_nothing_kept_makes_a_view_per_define_in_plan_order():
    compiler, warehouse = Durable(), Recording()
    with inline(compiler, warehouse, Kept(held={})) as run:
        assert run.reused == {}
        assert len(warehouse.statements) == 2 and run.relation("Base") in warehouse.statements[1]


def test_an_inline_run_without_a_result_resolver_compiles_the_whole_library():
    compiler = Compiler()
    with inline(compiler):
        assert not compiler.requests[0].durable
