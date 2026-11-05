"""CLIENT-RESULT-RESOLVER, CLIENT-RESOLVER-OVERLAY, RUN-CLIENT-RESULT-OWNERSHIP, RUN-CLIENT-TABLE-NAMES,
RUN-CLIENT-DRAFT-RESULTS: kept results found in one schema per resolver, the caller's own or any writer's, layered by
Overlay, and written into a store's schema."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cql_databricks.resolvers.interfaces import KeptResult, ResultKey, TableRef
from cql_databricks.result._catalog_completion import Completion, TAG_STATE, result_table_name, writer_digest
from cql_databricks.resolvers.interfaces import Overlay, ResultStore
from cql_databricks.result.store import OFFICIAL, SchemaResultResolver, SchemaResultStore

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


def own(run, schema=DRAFT) -> SchemaResultResolver:
    return SchemaResultResolver(schema, run)


def official(run, schema=RESULTS) -> SchemaResultResolver:
    return SchemaResultResolver(schema, run, reuse=OFFICIAL)


def layered(run) -> Overlay:
    """The environment's shape: the caller's own in draft_results over the official ones in results."""
    return Overlay(own(run), official(run))


class Catalog:
    """A resolver's lookup, answered from ``own`` (``{(catalog, schema): {name: (owner, tags)}}``) and ``official``
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


def test_each_layer_is_one_statement_of_tags_and_the_next_is_asked_only_for_the_rest():
    """CLIENT-RESOLVER-OVERLAY, RUN-CLIENT-CATALOG-CHECKPOINTS: the own layer reads every key's caller-named table;
    the official layer is asked only for the keys the own layer did not find. Tags only."""
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}})
    found = layered(run).find([KEY, key(2)])
    assert found[FP].table == TableRef(catalog="env", schema="draft_results", table=name())
    assert len(run.lookups) == 2
    (own_sql, own_params), (official_sql, official_params) = run.lookups
    assert own_sql.startswith("SELECT 'own'") and "LEFT JOIN" in own_sql and "UNION ALL" not in own_sql
    assert json.loads(own_params["names"]) == sorted([name(), name(key(2))])
    assert official_sql.startswith("SELECT 'official'") and "information_schema`.`table_tags" in official_sql
    assert json.loads(official_params["prefixes"]) == [key(2).fingerprint[:16]]
    for sql, _ in run.lookups:
        assert not any(word in sql for word in ("DESCRIBE", "COUNT", "LAST_ALTERED", "cql_publisher"))


def test_the_official_layer_is_not_asked_when_the_own_layer_finds_every_key():
    """CLIENT-RESOLVER-OVERLAY: nothing is left for the base layer."""
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}})
    assert set(layered(run).find([KEY])) == {FP}
    assert [sql.split(" AS layer")[0] for sql, _ in run.lookups] == ["SELECT 'own'"]


def test_pending_untagged_and_missing_tables_are_misses():
    pending, untagged = key(2), key(3)
    run = Catalog(own={DRAFT: {name(pending): (OWNER, tags(pending) | {TAG_STATE: "pending"}),
                               name(untagged): (OWNER, {})}})
    assert own(run).find([pending, untagged, key(4)]) == {}


def test_the_callers_own_result_wins_over_an_official_one():
    """RUN-CLIENT-RESULT-OWNERSHIP: own first, then official."""
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}}, official={RESULTS: {name(writer=OTHER): tags()}})
    assert layered(run).find([KEY])[FP].table.schema == "draft_results"


def test_any_writers_official_result_is_reused():
    """DEC-RESULT-SCOPE: the official schema is trusted by construction; another identity's table is found."""
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags()}})
    found = layered(run).find([KEY])
    assert found[FP] == KeptResult(table=TableRef(catalog="env", schema="results", table=name(writer=OTHER)),
                                   fingerprint=FP, computed_at=WHEN)


def test_another_identitys_table_in_an_own_schema_is_never_read():
    """RUN-CLIENT-RESULT-OWNERSHIP: the own layer asks only for names carrying the caller's digest."""
    run = Catalog(own={DRAFT: {name(writer=OTHER): ("publisher@example.com", tags())}})
    assert own(run).find([KEY]) == {}


def test_the_earlier_official_schema_wins_then_the_latest_result_in_it():
    later = ("env", "later_results")
    newer = WHEN + timedelta(days=1)
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags(), name(): tags(when=newer)},
                            later: {name(writer=OTHER): tags(when=newer + timedelta(days=1))}})
    found = Overlay(official(run), official(run, later)).find([KEY])[FP]
    assert (found.table.schema, found.table.table, found.computed_at) == ("results", name(), newer)


def test_computed_since_passes_over_older_results_in_both_layers():
    cutoff = WHEN + timedelta(hours=1)
    run = Catalog(own={DRAFT: {name(): (OWNER, tags())}},
                  official={RESULTS: {name(writer=OTHER): tags(when=cutoff + timedelta(hours=1))}})
    found = layered(run).find([KEY], computed_since=cutoff)
    assert found[FP].table.schema == "results"
    assert own(Catalog(own={DRAFT: {name(): (OWNER, tags())}})).find(
        [KEY], computed_since=cutoff) == {}


def test_a_table_named_for_the_caller_but_owned_by_another_identity_fails():
    """RUN-CLIENT-RESULT-OWNERSHIP: it is neither reused nor written over."""
    run = Catalog(own={DRAFT: {name(): ("someone-else", tags())}})
    with pytest.raises(PermissionError, match="owned by someone-else"):
        own(run).find([KEY])


def test_a_table_holding_another_calculation_under_the_name_fails_in_either_layer():
    """RUN-CLIENT-OPTIMISTIC: the name shares 16 fingerprint digits; the full tag must match, or it fails loud."""
    other = FP[:16] + "cd" * 24
    for resolver in (own(Catalog(own={DRAFT: {name(): (OWNER, tags(cql_fingerprint=other))}})),
                     official(Catalog(official={RESULTS: {name(writer=OTHER): tags(cql_fingerprint=other)}}))):
        with pytest.raises(ValueError, match="another calculation"):
            resolver.find([KEY])


def test_an_earlier_result_format_is_a_miss():
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags(cql_result_format="cql-catalog-result-v2")}})
    assert official(run).find([KEY]) == {}


def test_a_lookup_rejects_tables_and_tags_it_did_not_ask_for():
    def answering(rows):
        return lambda sql, params=None: [{"principal": OWNER}] if "current_user" in sql else rows
    row = dict(layer="official", catalog_name="env", schema_name="elsewhere", table_name=name(), table_owner=None,
               tag_name="cql_result_state", tag_value="complete")
    with pytest.raises(ValueError, match="outside the official"):
        official(answering([row])).find([KEY])
    mine = dict(row, layer="own", schema_name="draft_results", table_name=name(key(9)), table_owner=OWNER)
    with pytest.raises(ValueError, match="unrequested"):
        own(answering([mine])).find([KEY])
    duplicate = dict(mine, table_name=name())
    with pytest.raises(ValueError, match="duplicate"):
        own(answering([duplicate, duplicate])).find([KEY])
    assert layered(lambda *a: pytest.fail("issued SQL")).find([]) == {}


def test_a_resolver_reads_one_schema_and_an_overlay_is_addressed_by_its_first_layer():
    """CLIENT-RESULT-RESOLVER, RUN-CLIENT-RESULT-SETS: a resolver holds only its schema; an overlay holds either
    layer's and is addressed by the first."""
    with pytest.raises(ValueError, match="reuse is 'own' or 'official'"):
        SchemaResultResolver(DRAFT, Catalog(), reuse="anyone")
    with pytest.raises(ValueError, match="explicit"):
        SchemaResultResolver("env.draft_results", Catalog())
    assert own(Catalog()).destination == DRAFT
    assert not own(Catalog()).holds(TableRef(catalog="env", schema="results", table=name()))
    resolver = layered(Catalog())
    assert resolver.destination == DRAFT and not isinstance(resolver, ResultStore)
    assert resolver.holds(TableRef(catalog="env", schema="results", table=name()))
    assert not resolver.holds(TableRef(catalog="env", schema="elsewhere", table=name()))


def test_an_overlay_over_a_store_writes_admits_and_lists_through_the_store():
    """CLIENT-RESOLVER-OVERLAY, CLIENT-RESOLVER-READ-WRITE: the base layer is only read."""
    run = Catalog(official={RESULTS: {name(writer=OTHER): tags()}})
    requires = {"data": frozenset({"env.data"})}
    layers = Overlay(SchemaResultStore(DRAFT, run, requires=requires), official(run))
    assert isinstance(layers, ResultStore) and layers.destination == DRAFT
    assert layers.find([KEY])[FP].table.schema == "results"
    with pytest.raises(PermissionError, match="data is unbound"):
        layers.admit(SimpleNamespace(identities={}))
    with layers.writes([KEY]) as writes:
        assert writes.table(KEY) == TableRef(catalog="env", schema="draft_results", table=name())
    with pytest.raises(TypeError, match="two resolvers of one role"):
        Overlay(own(run), SimpleNamespace())


def test_a_store_writes_the_callers_table_in_its_destination_and_marks_it_complete():
    run = Catalog()
    store = SchemaResultStore(DRAFT, run)
    assert store.destination == DRAFT and store.reuse == "own"
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
    store = SchemaResultStore(DRAFT, run)
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
        SchemaResultStore(DRAFT, run, capacity=1).writes([KEY])
    SchemaResultStore(DRAFT, run, capacity=2).writes([KEY]).close()


def test_an_official_store_takes_only_the_environments_own_content():
    """RUN-CLIENT-DRAFT-RESULTS: refused before anything computes, naming what differs."""
    requires = {"data": frozenset({"env.data"}), "terminology": frozenset({"env.terminology"}),
                "knowledge": frozenset({"env.knowledge"})}
    store = SchemaResultStore(RESULTS, Catalog(), requires=requires)
    own = {"data": "env.data", "terminology": "env.terminology", "knowledge": "env.knowledge"}
    store.admit(SimpleNamespace(identities=own))
    with pytest.raises(PermissionError, match="knowledge is cql, not env.knowledge"):
        store.admit(SimpleNamespace(identities=own | {"knowledge": "cql"}))
    with pytest.raises(PermissionError, match="cells, folders"):
        store.admit(SimpleNamespace())
    SchemaResultStore(DRAFT, Catalog()).admit(SimpleNamespace())


def test_search_lists_completed_results_in_the_destination():
    """RUN-CLIENT-RESULT-IDENTITY: candidates, latest first."""
    held = Completion(table=(*DRAFT, name()), fingerprint=FP, computed_at=WHEN)

    def run(sql, params=None):
        assert sql.startswith("WITH candidates") and params["schema"] == "draft_results"
        return [{"catalog_name": "env", "schema_name": "draft_results", "table_name": name(),
                 "tags_json": json.dumps(held.tags())}]
    assert SchemaResultStore(DRAFT, run).search(fingerprint=FP) == (
        KeptResult(table=TableRef(catalog="env", schema="draft_results", table=name()), fingerprint=FP,
                   computed_at=WHEN),)


def test_an_environment_builds_its_result_resolvers_as_overlays_of_one_schema_each():
    """CLIENT-ENVIRONMENT-RESOLVER, RUN-CLIENT-DRAFT-RESULTS, RUN-CLIENT-REQUEST-REOPEN: the caller's own results over
    the official ones, a store writing draft_results or results, and the reader each address reopens through."""
    from cql_databricks.resolvers.environment import Environment, EnvironmentResolver

    env = Environment(name="synthetic-population", catalog="env", version="3")
    environments = EnvironmentResolver(Catalog())

    def layers(resolver):
        return [(layer.schema, layer.reuse, isinstance(layer, ResultStore)) for layer in (resolver.dev, resolver.base)]

    read = environments.results(env)
    assert not isinstance(read, ResultStore)
    assert layers(read) == [(DRAFT, "own", False), (RESULTS, "official", False)]
    draft = environments.draft_store(env, capacity=3)
    assert isinstance(draft, ResultStore) and draft.destination == DRAFT and draft.dev.capacity == 3
    assert layers(draft) == [(DRAFT, "own", True), (RESULTS, "official", False)]
    draft.admit(SimpleNamespace())
    published = environments.official_store(env)
    assert published.destination == RESULTS
    assert layers(published) == [(RESULTS, "own", True), (RESULTS, "official", False)]
    with pytest.raises(PermissionError, match="draft_results"):
        published.admit(SimpleNamespace())
    assert layers(environments.reader(env, DRAFT)) == layers(read)
    official_reader = environments.reader(env, RESULTS)
    assert (official_reader.schema, official_reader.reuse) == (RESULTS, "official")
    with pytest.raises(ValueError, match="not a result schema"):
        environments.reader(env, ("env", "elsewhere"))
