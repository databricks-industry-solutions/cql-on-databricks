"""CLIENT-RESULT-RESOLVER, RUN-CLIENT-RESULT-OWNERSHIP, RUN-CLIENT-TABLE-NAMES, RUN-CLIENT-DRAFT-RESULTS: kept results
found in the caller's own schemas, then the official ones, in one lookup, and written into a store's destination."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cql_databricks.resolvers.interfaces import KeptResult, ResultKey, TableRef
from cql_databricks.result._catalog_completion import Completion, TAG_STATE, result_table_name, writer_digest
from cql_databricks.result.store import DeltaResultResolver, DeltaResultStore

FP = "ab" * 32
OWNER = "someone@example.com"
W = writer_digest(OWNER)
OTHER = writer_digest("publisher@example.com")
DRAFT, RESULTS = ("env", "draft_results"), ("env", "results")
KEY = ResultKey(library="Measure", define="Numerator", fingerprint=FP)
WHEN = datetime(2026, 10, 31, tzinfo=timezone.utc)


def key(index: int) -> ResultKey:
    return ResultKey(library="Measure", define="Numerator", fingerprint=f"{index:016x}" * 4)


def name(k: ResultKey = KEY, writer: str = W) -> str:
    return result_table_name(k.library, k.define, k.fingerprint, writer)


def tags(k: ResultKey = KEY, when: datetime = WHEN, **changes) -> dict[str, str]:
    return {"cql_result_format": "cql-catalog-result-v3", "cql_result_state": "complete",
            "cql_computed_at": when.isoformat(timespec="microseconds"), "cql_fingerprint": k.fingerprint} | changes


class Catalog:
    """The store's one lookup, answered from ``own`` (``{(catalog, schema): {name: (owner, tags)}}``) and ``official``
    (``{(catalog, schema): {name: tags}}``), branch by branch, as the warehouse would."""

    def __init__(self, own=None, official=None):
        self.own, self.official, self.statements = own or {}, official or {}, []

    def __call__(self, sql, params=None):
        self.statements.append((sql, dict(params or {})))
        if sql == "SELECT current_user() AS principal":
            return [{"principal": OWNER}]
        if not sql.startswith("SELECT 'own' AS layer") and not sql.startswith("SELECT 'official' AS layer"):
            return []
        rows = []
        for branch in sql.split(" UNION ALL "):
            layer = "own" if branch.startswith("SELECT 'own'") else "official"
            catalog = branch.split("FROM `")[1].split("`")[0]
            index = branch.split(f":{layer}_")[1].split(",")[0]
            for schema in json.loads(params[f"{layer}_{index}"]):
                if layer == "own":
                    for table, (owner, held) in self.own.get((catalog, schema), {}).items():
                        if table in json.loads(params["names"]):
                            rows += [dict(layer="own", catalog_name=catalog, schema_name=schema, table_name=table,
                                          table_owner=owner, tag_name=t, tag_value=v)
                                     for t, v in (held.items() or [(None, None)])]
                else:
                    for table, held in self.official.get((catalog, schema), {}).items():
                        if table[-25:-9] in json.loads(params["prefixes"]):
                            rows += [dict(layer="official", catalog_name=catalog, schema_name=schema,
                                          table_name=table, table_owner=None, tag_name=t, tag_value=v)
                                     for t, v in held.items()]
        return rows

    @property
    def lookups(self):
        return [s for s in self.statements if s[0].startswith("SELECT '")]


def test_a_name_ends_with_the_fingerprint_and_the_writers_digest():
    """RUN-CLIENT-TABLE-NAMES: two identities computing one calculation write two tables."""
    assert len(W) == 8 and W != OTHER
    assert name() == f"measure_numerator_{FP[:16]}_{W}"
    with pytest.raises(ValueError):
        result_table_name("Measure", "Numerator", FP, "not-a-digest")


def test_one_lookup_reads_both_layers_without_describe_or_counts():
    """RUN-CLIENT-CATALOG-CHECKPOINTS: one statement over the own and official schemas, tags only."""
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}})
    found = DeltaResultResolver(run, own=[DRAFT], official=[RESULTS]).find([KEY, key(2)])
    assert found[FP].table == TableRef(catalog="env", schema="draft_results", table=name())
    assert len(run.lookups) == 1
    sql, params = run.lookups[0]
    assert sql.count("UNION ALL") == 1 and "LEFT JOIN" in sql and "information_schema`.`table_tags" in sql
    assert json.loads(params["names"]) == sorted([name(), name(key(2))])
    assert json.loads(params["prefixes"]) == sorted([FP[:16], key(2).fingerprint[:16]])
    assert not any(word in sql for word in ("DESCRIBE", "COUNT", "LAST_ALTERED", "cql_publisher"))


def test_pending_untagged_and_missing_tables_are_misses():
    pending, untagged = key(2), key(3)
    run = Catalog(own={DRAFT: {name(pending): (OWNER, tags(pending) | {TAG_STATE: "pending"}),
                               name(untagged): (OWNER, {})}})
    assert DeltaResultResolver(run, own=[DRAFT]).find([pending, untagged, key(4)]) == {}


def test_the_callers_own_result_wins_over_an_official_one():
    """RUN-CLIENT-RESULT-OWNERSHIP: own first, then official."""
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}}, official={RESULTS: {name(writer=OTHER): tags()}})
    assert DeltaResultResolver(run, own=[DRAFT], official=[RESULTS]).find([KEY])[FP].table.schema == "draft_results"


def test_any_writers_official_result_is_reused():
    """DEC-RESULT-SCOPE: the official schema is trusted by construction; another identity's table is found."""
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags()}})
    found = DeltaResultResolver(run, own=[DRAFT], official=[RESULTS]).find([KEY])
    assert found[FP] == KeptResult(table=TableRef(catalog="env", schema="results", table=name(writer=OTHER)),
                                   fingerprint=FP, computed_at=WHEN)


def test_another_identitys_table_in_an_own_schema_is_never_read():
    """RUN-CLIENT-RESULT-OWNERSHIP: the own layer asks only for names carrying the caller's digest."""
    run = Catalog(own={DRAFT: {name(writer=OTHER): ("publisher@example.com", tags())}})
    assert DeltaResultResolver(run, own=[DRAFT]).find([KEY]) == {}


def test_the_earlier_official_schema_wins_then_the_latest_result_in_it():
    later = ("env", "later_results")
    newer = WHEN + timedelta(days=1)
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags(), name(): tags(when=newer)},
                            later: {name(writer=OTHER): tags(when=newer + timedelta(days=1))}})
    found = DeltaResultResolver(run, official=[RESULTS, later]).find([KEY])[FP]
    assert (found.table.schema, found.table.table, found.computed_at) == ("results", name(), newer)


def test_computed_since_passes_over_older_results_in_both_layers():
    cutoff = WHEN + timedelta(hours=1)
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}},
                  official={RESULTS: {name(writer=OTHER): tags(when=cutoff + timedelta(hours=1))}})
    found = DeltaResultResolver(run, own=[DRAFT], official=[RESULTS]).find([KEY], computed_since=cutoff)
    assert found[FP].table.schema == "results"
    assert DeltaResultResolver(Catalog(own={DRAFT: {name(): (OWNER, tags())}}), own=[DRAFT]).find(
        [KEY], computed_since=cutoff) == {}


def test_a_table_named_for_the_caller_but_owned_by_another_identity_fails():
    """RUN-CLIENT-RESULT-OWNERSHIP: it is neither reused nor written over."""
    run = Catalog(own={DRAFT: {name(): ("someone-else", tags())}})
    with pytest.raises(PermissionError, match="owned by someone-else"):
        DeltaResultResolver(run, own=[DRAFT]).find([KEY])


def test_a_table_holding_another_calculation_under_the_name_fails_in_either_layer():
    """RUN-CLIENT-OPTIMISTIC: the name shares 16 fingerprint digits; the full tag must match, or it fails loud."""
    other = FP[:16] + "cd" * 24
    for run, layers in ((Catalog(own={DRAFT: {name(): (OWNER, tags(cql_fingerprint=other))}}), {"own": [DRAFT]}),
                        (Catalog(official={RESULTS: {name(writer=OTHER): tags(cql_fingerprint=other)}}),
                         {"official": [RESULTS]})):
        with pytest.raises(ValueError, match="another calculation"):
            DeltaResultResolver(run, **layers).find([KEY])


def test_an_earlier_result_format_is_a_miss():
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags(cql_result_format="cql-catalog-result-v2")}})
    assert DeltaResultResolver(run, official=[RESULTS]).find([KEY]) == {}


def test_a_lookup_rejects_tables_and_tags_it_did_not_ask_for():
    def answering(rows):
        return lambda sql, params=None: [{"principal": OWNER}] if "current_user" in sql else rows
    row = dict(layer="official", catalog_name="env", schema_name="elsewhere", table_name=name(), table_owner=None,
               tag_name="cql_result_state", tag_value="complete")
    with pytest.raises(ValueError, match="outside the official"):
        DeltaResultResolver(answering([row]), official=[RESULTS]).find([KEY])
    own = dict(row, layer="own", schema_name="draft_results", table_name=name(key(9)), table_owner=OWNER)
    with pytest.raises(ValueError, match="unrequested"):
        DeltaResultResolver(answering([own]), own=[DRAFT]).find([KEY])
    duplicate = dict(own, table_name=name())
    with pytest.raises(ValueError, match="duplicate"):
        DeltaResultResolver(answering([duplicate, duplicate]), own=[DRAFT]).find([KEY])
    assert DeltaResultResolver(lambda *a: pytest.fail("issued SQL"), own=[DRAFT]).find([]) == {}


def test_a_resolver_reads_at_least_one_schema_and_holds_only_its_own():
    with pytest.raises(ValueError, match="at least one schema"):
        DeltaResultResolver(Catalog())
    resolver = DeltaResultResolver(Catalog(), own=[DRAFT], official=[RESULTS])
    assert resolver.destination == DRAFT
    assert resolver.holds(TableRef(catalog="env", schema="results", table=name()))
    assert not resolver.holds(TableRef(catalog="env", schema="elsewhere", table=name()))


def test_a_store_writes_the_callers_table_in_its_destination_and_marks_it_complete():
    run = Catalog()
    store = DeltaResultStore(DRAFT, run, official=[RESULTS])
    assert store.destination == DRAFT and store.own == (DRAFT,)
    with store.writes([KEY]) as writes:
        table = writes.table(KEY)
        assert table == TableRef(catalog="env", schema="draft_results", table=name())
        statement = writes.statement(table, "SELECT 1 AS __val")
        assert statement.startswith("CREATE OR REPLACE TABLE `env`.`draft_results`.") and "cql.durable.format" in statement
        writes.starting(table)
        writes.completed(KeptResult(table=table, fingerprint=FP, computed_at=WHEN))
    tagged = [s for s, _ in run.statements if s.startswith("ALTER TABLE")]
    assert len(tagged) == 1 and "'cql_result_state' = 'complete'" in tagged[0]
    with store.writes([KEY]) as writes, pytest.raises(ValueError, match="not in this store's destination"):
        writes.statement(TableRef(catalog="env", schema="results", table=name()), "SELECT 1")


def test_a_store_marks_a_table_its_lookup_saw_pending_before_replacing_it():
    """RUN-CLIENT-CATALOG-CHECKPOINTS."""
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}})
    store = DeltaResultStore(DRAFT, run)
    store.find([KEY], computed_since=WHEN + timedelta(days=1))
    with store.writes([KEY]) as writes:
        writes.starting(writes.table(KEY))
    assert any("'cql_result_state' = 'pending'" in s for s, _ in run.statements)


def test_a_store_refuses_more_tables_than_its_capacity():
    """RUN-CLIENT-LIFECYCLE."""
    def run(sql, params=None):
        if "current_user" in sql:
            return [{"principal": OWNER}]
        return [{"tableName": name(key(5)), "isTemporary": False}] if sql.startswith("SHOW TABLES") else []
    with pytest.raises(RuntimeError, match="capacity exceeded"):
        DeltaResultStore(DRAFT, run, capacity=1).writes([KEY])
    DeltaResultStore(DRAFT, run, capacity=2).writes([KEY]).close()


def test_an_official_store_takes_only_the_environments_own_content():
    """RUN-CLIENT-DRAFT-RESULTS: refused before anything computes, naming what differs."""
    requires = {"data": frozenset({"env.data"}), "terminology": frozenset({"env.terminology"}),
                "knowledge": frozenset({"env.knowledge"})}
    store = DeltaResultStore(RESULTS, Catalog(), requires=requires)
    own = {"data": "env.data", "terminology": "env.terminology", "knowledge": "env.knowledge"}
    store.admit(SimpleNamespace(identities=own))
    with pytest.raises(PermissionError, match="knowledge is cql, not env.knowledge"):
        store.admit(SimpleNamespace(identities=own | {"knowledge": "cql"}))
    with pytest.raises(PermissionError, match="cells, folders"):
        store.admit(SimpleNamespace())
    DeltaResultStore(DRAFT, Catalog()).admit(SimpleNamespace())


def test_search_lists_completed_results_in_the_destination():
    """RUN-CLIENT-RESULT-IDENTITY: candidates, latest first."""
    held = Completion(table=(*DRAFT, name()), fingerprint=FP, computed_at=WHEN)

    def run(sql, params=None):
        assert sql.startswith("WITH candidates") and params["schema"] == "draft_results"
        return [{"catalog_name": "env", "schema_name": "draft_results", "table_name": name(),
                 "tags_json": json.dumps(held.tags())}]
    assert DeltaResultStore(DRAFT, run).search(fingerprint=FP) == (
        KeptResult(table=TableRef(catalog="env", schema="draft_results", table=name()), fingerprint=FP,
                   computed_at=WHEN),)
