"""CLIENT-SESSION-RESULTS, CLIENT-RESOLVER-OVERLAY, CLIENT-RESULT-RESOLVER: results kept for one SQL session in
temporary tables, alone or layered with the environment's official results or the caller's own."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cql_databricks.resolvers.interfaces import Overlay, ResultKey, ResultStore, TableRef
from cql_databricks.result import OFFICIAL, SchemaResultResolver, SessionResultStore, durable
from cql_databricks.result._catalog_completion import result_table_name, writer_digest
from cql_databricks.result.session_store import RECORD, SESSION
from test_durable_plan import plan

OWNER = "someone@example.com"
W = writer_digest(OWNER)
RESULTS, DRAFT = ("env", "results"), ("env", "draft_results")
BASE, OUTPUT = "a" * 64, "b" * 64
QUERY = SimpleNamespace(compile_pipeline=lambda **options: plan())


def name(define: str, fingerprint: str, writer: str = W) -> str:
    return result_table_name("Synthetic", define, fingerprint, writer)


class Session:
    """One SQL session's temporary tables and its record, as the warehouse and serverless Spark answer them; and,
    for an overlay, the environment's ``results`` schema, any writer's tables by tag."""

    def __init__(self, official=None, own=None):
        self.own = own or {}
        self.tables: set[str] = set()
        self.record: dict[str, dict] = {}
        self.recorded = False
        self.official = official or {}
        self.statements: list[str] = []

    def __call__(self, sql, params=None):
        self.statements.append(sql)
        record = f"`{SESSION[0]}`.`{SESSION[1]}`.`{RECORD}`"
        if sql == "SELECT current_user() AS principal":
            return [{"principal": OWNER}]
        if sql.startswith(("CREATE TEMPORARY TABLE `system`", "CREATE OR REPLACE TEMPORARY TABLE `system`")):
            raise RuntimeError("[TEMP_TABLE_CREATION_REQUIRES_SINGLE_PART_NAME] a single part table name")
        if sql.startswith(f"CREATE TEMPORARY TABLE `{RECORD}` "):
            if self.recorded:
                raise RuntimeError(f"[TABLE_OR_VIEW_ALREADY_EXISTS] Cannot create table or view `session`.`{RECORD}`")
            self.recorded = True
            return []
        if record in sql:
            assert self.recorded, "the record is read before it is made"
            if sql.startswith("SELECT name, fingerprint, computed_at"):
                return [dict(name=n, fingerprint=r["fingerprint"], computed_at=r["computed_at"])
                        for n, r in self.record.items() if n in json.loads(params["names"])]
            if sql.startswith("SELECT name, columns"):
                return [dict(name=n, columns=r["columns"]) for n, r in self.record.items()
                        if n in json.loads(params["names"])]
            if sql.startswith("DELETE FROM"):
                self.record.pop(params["name"], None)
                return []
            if sql.startswith("INSERT INTO"):
                self.record[params["name"]] = dict(params)
                return []
        temp = re.search(r"(?:`system`\.`session`\.)?`([a-z0-9_]+)`", sql)
        if sql.startswith("CREATE OR REPLACE TEMPORARY TABLE"):
            self.tables.add(temp[1])
            return []
        if sql.startswith("DESCRIBE TABLE"):
            assert temp[1] in self.tables
            return [dict(col_name="__val", data_type="int", comment=None), dict(col_name="", data_type="", comment=""),
                    dict(col_name="# Detailed Table Information", data_type="", comment="")]
        if sql.startswith("SELECT 'own' AS layer"):
            names = json.loads(params["names"])
            return [dict(layer="own", catalog_name=DRAFT[0], schema_name=DRAFT[1], table_name=table,
                         table_owner=OWNER, tag_name=tag, tag_value=value)
                    for table, held in self.own.items() if table in names for tag, value in held.items()]
        if sql.startswith("SELECT 'official' AS layer"):
            prefixes = json.loads(params["prefixes"])
            return [dict(layer="official", catalog_name=RESULTS[0], schema_name=RESULTS[1], table_name=table,
                         table_owner=None, tag_name=tag, tag_value=value)
                    for table, held in self.official.items() if table[-25:-9] in prefixes for tag, value in held.items()]
        if sql.startswith("SELECT table_name, column_name"):
            return [dict(table_name=t, column_name="__val", full_data_type="INT")
                    for t in json.loads(params["tables"]) if t in self.official or t in self.own]
        pytest.fail(f"unexpected statement: {sql}")

    def computed(self):
        return [s for s in self.statements if s.startswith("CREATE OR REPLACE TEMPORARY TABLE")]


def tags(fingerprint: str, when=datetime(2026, 10, 31, tzinfo=timezone.utc)) -> dict[str, str]:
    return {"cql_result_format": "cql-catalog-result-v3", "cql_result_state": "complete",
            "cql_computed_at": when.isoformat(timespec="microseconds"), "cql_fingerprint": fingerprint}


def materialize(results, run, **options):
    return durable.materialize(QUERY, "Output", run=run, results=results, **options)


def test_a_session_keeps_what_it_computes_and_reuses_it():
    """CLIENT-SESSION-RESULTS: temporary tables, recorded with their columns; the next run reuses them."""
    run = Session()
    first = materialize(SessionResultStore(run), run)
    assert first.results["Output"].decision == "computed"
    assert run.tables == {name("Base", BASE), name("Output", OUTPUT)}
    assert json.loads(run.record[name("Output", OUTPUT)]["columns"]) == [["__val", "int"]]
    run.statements.clear()
    second = materialize(SessionResultStore(run), run)
    assert second.results["Output"].decision == "reused" and not run.computed()
    # A second store in the same session finds the record the first made.
    assert sum(s.startswith("CREATE TEMPORARY TABLE") for s in run.statements) == 1


def test_a_session_store_writes_temporary_tables_on_its_own_session_and_takes_any_calculation():
    """CLIENT-SESSION-RESULTS: addressed as system.session; nothing on a side connection; no admission check."""
    store = SessionResultStore(Session())
    assert isinstance(store, ResultStore) and store.destination == SESSION
    assert store.holds(TableRef(catalog="system", schema="session", table="x"))
    assert not store.holds(TableRef(catalog="env", schema="draft_results", table="x"))
    store.admit(SimpleNamespace())
    with store.writes([]) as writes:
        statement = writes.statement(writes.table(ResultKey(library="Synthetic", define="Base", fingerprint=BASE)),
                                     "SELECT 1")
        assert statement.startswith(f"CREATE OR REPLACE TEMPORARY TABLE `{name('Base', BASE)}` AS ")
        with pytest.raises(ValueError, match="not a temporary table"):
            writes.statement(TableRef(catalog="env", schema="draft_results", table="x"), "SELECT 1")


def test_a_recomputed_result_stops_being_complete_before_it_is_replaced():
    run = Session()
    materialize(SessionResultStore(run), run)
    run.statements.clear()
    materialize(SessionResultStore(run), run, mode="recompute")
    deletes = [i for i, s in enumerate(run.statements) if s.startswith("DELETE FROM")]
    rewrites = [i for i, s in enumerate(run.statements) if s.startswith("CREATE OR REPLACE TEMPORARY TABLE")]
    assert len(rewrites) == 2 and deletes and deletes[0] < rewrites[0]


def test_computed_since_and_another_calculation_under_the_name():
    run = Session()
    materialize(SessionResultStore(run), run)
    key = ResultKey(library="Synthetic", define="Base", fingerprint=BASE)
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert SessionResultStore(run).find([key], computed_since=later) == {}
    run.record[name("Base", BASE)]["fingerprint"] = "c" * 64
    with pytest.raises(ValueError, match="another calculation"):
        SessionResultStore(run).find([key])


def test_only_an_existing_record_is_expected_when_making_it():
    def refusing(sql, params=None):
        if sql == "SELECT current_user() AS principal":
            return [{"principal": OWNER}]
        raise RuntimeError("PERMISSION_DENIED: temporary tables are off")
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        SessionResultStore(refusing).find([ResultKey(library="Synthetic", define="Base", fingerprint=BASE)])


def test_official_results_or_the_session():
    """CLIENT-RESOLVER-OVERLAY: the official Base is reused, Output is kept for the session, and each table's
    columns come from the layer that holds it."""
    run = Session(official={name("Base", BASE, writer_digest("publisher@example.com")): tags(BASE)})
    results = Overlay(SessionResultStore(run), SchemaResultResolver(RESULTS, run, reuse=OFFICIAL))
    done = durable.materialize(QUERY, "Base", "Output", run=run, results=results)
    assert done.results["Base"].decision == "reused" and done.results["Output"].decision == "computed"
    assert run.tables == {name("Output", OUTPUT)}


def test_session_only_reuses_nothing_outside_the_session():
    run = Session(official={name("Base", BASE, writer_digest("publisher@example.com")): tags(BASE)})
    done = durable.materialize(QUERY, "Base", "Output", run=run, results=SessionResultStore(run))
    assert {done.results[d].decision for d in ("Base", "Output")} == {"computed"}


def test_official_only_reads_and_never_keeps():
    """CLIENT-RESULT-RESOLVER: an official resolver alone is read-only; a durable run needs a store."""
    run = Session(official={name("Base", BASE, writer_digest("publisher@example.com")): tags(BASE)})
    official = SchemaResultResolver(RESULTS, run, reuse=OFFICIAL)
    found = durable.resolve_results(QUERY, "Base", run=run, results=official)
    assert found.results["Base"].decision == "reused"
    with pytest.raises(TypeError, match="ResultStore"):
        durable.materialize(QUERY, "Output", run=run, results=official)


def test_own_or_the_session():
    """CLIENT-RESOLVER-OVERLAY: the caller's own draft Base is reused, never written; Output is the session's."""
    run = Session(own={name("Base", BASE): tags(BASE)})
    results = Overlay(SessionResultStore(run), SchemaResultResolver(DRAFT, run))
    assert isinstance(results, ResultStore) and results.destination == SESSION
    done = durable.materialize(QUERY, "Base", "Output", run=run, results=results)
    assert done.results["Base"].decision == "reused" and done.results["Output"].decision == "computed"
    assert run.tables == {name("Output", OUTPUT)}
    assert not any(s.startswith("CREATE OR REPLACE TABLE") for s in run.statements)
