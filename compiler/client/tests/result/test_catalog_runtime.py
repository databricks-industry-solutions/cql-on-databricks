"""RUN-CLIENT-CATALOG-CHECKPOINTS, RUN-CLIENT-RESULT-IDENTITY, RUN-CLIENT-RESULT-OWNERSHIP —
synthetic catalog execution."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re
from threading import Lock

import pytest

from cql_databricks.result._catalog_completion import writer_digest
from cql_databricks.result._catalog_runtime import execute, table_name
from cql_databricks.result.store import DeltaResultStore
from cql_databricks.result._durable import ResultsUnavailable
from cql_databricks.result._durable_plan import PreparedPlan
from test_durable_plan import identity, plan


DESTINATION = ("synthetic_catalog", "results")
#: RUN-CLIENT-RESULT-OWNERSHIP — the identity every synthetic run writes and reuses as.
OWNER = "synthetic-user"
WRITER = writer_digest(OWNER)
#: RUN-CLIENT-DRAFT-RESULTS — the synthetic environment the public calls run in, and where a private run writes.
from cql_databricks.resolvers.environment import Environment  # noqa: E402

ENVIRONMENT = Environment(name="synthetic", catalog="synthetic_catalog", version="2")
DRAFT = ("synthetic_catalog", "draft_results")


class CatalogRunner:
    """RUN-CLIENT-ASYNC-CHECKPOINTS — thread-safe synthetic catalog, not a SQL-engine substitute."""

    def __init__(self, destination=None):
        self.destination = destination or DESTINATION
        self.tables = {}
        #: Other result schemas: ``{(catalog, schema): {name: tags}}``.
        self.sources = {}
        self.calls = []
        self.fail_write = ""
        self.fail_tag = ""
        self.lock = Lock()
        self.columns = [("__val", "INT")]
        #: RUN-CLIENT-RESULT-OWNERSHIP — tables another identity owns, ``{name: owner}``.
        self.owners = {}

    def __call__(self, sql, params=None):
        with self.lock:
            self.calls.append((sql, params))
            if sql == "SELECT current_user() AS principal":
                return [dict(principal=OWNER)]
            if sql.startswith("SHOW TABLES IN"):
                return [dict(tableName=name, isTemporary=False) for name in self.tables]
            if sql.startswith("SELECT 'own' AS layer") or sql.startswith("SELECT 'official' AS layer"):
                return self._lookup(sql, params)
            if sql.startswith("SELECT table_name, column_name"):
                held = next((t for (_, schema), t in self.sources.items() if schema == params["schema"]), self.tables)
                return [dict(table_name=name, column_name=column, full_data_type=kind)
                        for name in json.loads(params["tables"]) if name in held
                        for column, kind in self.columns]
            name = re.search(r"`([a-z0-9_]+_[0-9a-f]{16}_[0-9a-f]{8})`", sql)[1]
            if sql.startswith("CREATE OR REPLACE TABLE"):
                if name == self.fail_write:
                    raise RuntimeError("synthetic compute failure")
                # CREATE OR REPLACE preserves tags; explicit invalidation is required.
                self.tables.setdefault(name, {})
                return []
            if sql.startswith("ALTER TABLE"):
                tags = dict(re.findall(r"'([^']+)' = '([^']+)'", sql))
                if name == self.fail_tag and tags.get("cql_result_state") == "complete":
                    raise RuntimeError("synthetic tag failure")
                self.tables[name].update(tags)
                return []
            pytest.fail(sql)

    def _lookup(self, sql, params):
        """CLIENT-RESULT-RESOLVER: the store's one lookup. The own layer is served from ``tables`` for the first own
        schema in the destination's catalog, with each table's owner; the official layer from ``sources``, any
        writer's, by fingerprint digits."""
        rows = []
        served_own = False
        for branch in sql.split(" UNION ALL "):
            catalog = re.search(r"FROM `([^`]+)`\.`information_schema`", branch)[1]
            layer, index = re.search(r":(own|official)_(\d+)", branch).groups()
            schemas = json.loads(params[f"{layer}_{index}"])
            if layer == "own":
                if served_own or catalog != self.destination[0]:
                    continue
                served_own = True
                for name in json.loads(params["names"]):
                    if name in self.tables:
                        rows += [dict(layer="own", catalog_name=catalog, schema_name=schemas[0], table_name=name,
                                      table_owner=self.owners.get(name, OWNER), tag_name=tag, tag_value=value)
                                 for tag, value in (self.tables[name].items() or [(None, None)])]
                continue
            prefixes = set(json.loads(params["prefixes"]))
            for schema in schemas:
                for name, tags in self.sources.get((catalog, schema), {}).items():
                    if name[-25:-9] in prefixes:
                        rows += [dict(layer="official", catalog_name=catalog, schema_name=schema, table_name=name,
                                      table_owner=None, tag_name=tag, tag_value=value) for tag, value in tags.items()]
        return rows

    @property
    def writes(self):
        return [sql for sql, _ in self.calls if sql.startswith("CREATE OR REPLACE")]


def run_on(plan, targets, *, run, destination=DESTINATION, official=(), read_only=False, **options):
    """CLIENT-RESULT-RESOLVER — ``execute`` through a Delta store over ``destination``, finding official results in
    ``official``."""
    return execute(plan, targets, results=DeltaResultStore(destination, run, official=official), run=run,
                   read_only=read_only, **options)


def prepared(compiled=None):
    return PreparedPlan(compiled or plan())


def table(key, compiled=None):
    return table_name(next(d for d in prepared(compiled).definitions if d.key == key), WRITER)


def changed(key, fingerprint, *, reusable=True):
    """The synthetic plan with ``key``'s compiler identity replaced, as another SQL, source or upstream gives."""
    compiled = plan()
    return replace(compiled, datasets=[replace(d, durable=identity(fingerprint, reusable)) if d.name == key else d
                                       for d in compiled.datasets])


def test_compute_then_reuse_has_no_metadata_tables_describes_counts_or_versions():
    """RUN-CLIENT-OPTIMISTIC — two writes for two calculations; repeat only queries catalog metadata."""
    run = CatalogRunner()
    first = run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    assert [o.decision for o in first] == ["computed", "computed"]
    assert len(run.writes) == 2
    assert all("VERSION AS OF" not in sql and "DESCRIBE" not in sql and "COUNT(" not in sql
               and "cql_completed_results" not in sql and "cql_result_sets" not in sql for sql, _ in run.calls)
    assert plan().datasets[1].query in run.writes[1]
    run.calls.clear()
    second = run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    assert [o.decision for o in second] == ["reused", "reused"]
    assert [o.result for o in second] == [o.result for o in first]
    # The caller's principal, one result lookup, one bulk descriptor lookup.
    assert len(run.calls) == 3
    assert {tag for tags in run.tables.values() for tag in tags} == {
        "cql_result_format", "cql_result_state", "cql_computed_at", "cql_fingerprint"}


def test_failed_downstream_retains_upstream_checkpoint_and_retries_same_cutoff():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — failed closure does not erase completed independent progress."""
    run = CatalogRunner()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=1)
    run.fail_write = table("output")
    with pytest.raises(RuntimeError, match="compute failure"):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run, mode="recompute", computed_since=cutoff)
    assert run.tables[table("base")]["cql_result_state"] == "complete"
    run.fail_write = ""
    resumed = run_on(prepared(), ("output",), destination=DESTINATION, run=run,
                      mode="recompute", computed_since=cutoff)
    assert [o.decision for o in resumed] == ["reused", "computed"]


def test_overwrite_invalidates_before_compute_and_failed_refresh_is_not_reusable():
    """RUN-CLIENT-CATALOG-CHECKPOINTS — retained old tags cannot certify a failed replacement."""
    run = CatalogRunner()
    run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    run.calls.clear()
    run.fail_write = table("base")
    with pytest.raises(RuntimeError, match="compute failure"):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run, mode="recompute")
    assert run.tables[table("base")]["cql_result_state"] == "pending"
    # RUN-CLIENT-RESULT-OWNERSHIP: one lookup, then the invalidation, then the write.
    assert run.calls[0][0] == "SELECT current_user() AS principal"
    assert run.calls[1][0].startswith("SELECT 'own' AS layer")
    assert run.calls[2][0].startswith("ALTER TABLE")
    assert "'cql_result_state' = 'pending'" in run.calls[2][0]
    assert run.calls[3][0].startswith("CREATE OR REPLACE")
    with pytest.raises(ResultsUnavailable):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run, read_only=True)


class RacingRunner(CatalogRunner):
    """RUN-CLIENT-RESULT-OWNERSHIP — another run of the same identity finishes ``key`` at the ``at``-th lookup of the
    destination (the first is the run's own lookup), writing the same table with the same tags."""

    def __init__(self, finished, key, at):
        super().__init__()
        self.finished, self.key, self.at, self.lookups = finished, key, at, 0

    def __call__(self, sql, params=None):
        if sql.startswith("SELECT 'own' AS layer"):
            self.lookups += 1
            if self.lookups == self.at:
                self.tables[table(self.key)] = dict(self.finished[table(self.key)])
        return super().__call__(sql, params)


def _finished():
    """The tags a completed run leaves on every table of the synthetic plan."""
    done = CatalogRunner()
    run_on(prepared(), ("output",), destination=DESTINATION, run=done)
    return done.tables


def test_a_run_looks_results_up_once_and_not_again_before_each_write():
    """RUN-CLIENT-RESULT-OWNERSHIP — a result another run of this identity finished after the lookup is written again,
    the same rows to the same table."""
    run = RacingRunner(_finished(), "base", at=2)
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    assert [(o.definition, o.decision) for o in outcomes] == [("base", "computed"), ("output", "computed")]
    assert run.lookups == 1


def test_a_write_that_fails_because_another_run_finished_it_reuses_that_result():
    """RUN-CLIENT-RESULT-OWNERSHIP — after a failed write the result is looked up again; a collision costs duplicate
    work, never a failed run."""
    run = RacingRunner(_finished(), "base", at=2)
    run.fail_write = table("base")
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    assert [(o.definition, o.decision) for o in outcomes] == [("base", "reused"), ("output", "computed")]
    assert "while this one wrote it" in outcomes[0].reason


def test_a_failed_write_nobody_else_finished_still_fails():
    run = RacingRunner(_finished(), "output", at=99)
    run.fail_write = table("base")
    with pytest.raises(RuntimeError, match="compute failure"):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run)


def test_recomputing_upstream_keeps_the_dependent_and_both_table_names():
    """RUN-CLIENT-RESULT-IDENTITY — a refreshed upstream is the same calculation, so the dependent is reused.
    Freshness is the data source's (DEC-RESULT-IDENTITY); no upstream generation is checked."""
    run = CatalogRunner()
    first = run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    run_on(prepared(), ("base",), destination=DESTINATION, run=run, mode="recompute")
    refreshed = run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    assert [o.decision for o in refreshed] == ["reused", "reused"]
    assert [o.result.artifact.table for o in first] == [o.result.artifact.table for o in refreshed]
    assert len(run.tables) == 2


def test_a_changed_upstream_fingerprint_is_a_new_table_for_it_and_its_dependent():
    """RUN-CLIENT-RESULT-IDENTITY — another SQL, source or upstream is another calculation under another name.
    The compiler folds the upstream fingerprint into the dependent's (DurablePlanSpec)."""
    run = CatalogRunner()
    run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    compiled = changed("output", "d" * 64)
    compiled = replace(compiled, datasets=[replace(d, durable=identity("c" * 64)) if d.name == "base" else d
                                           for d in compiled.datasets])
    second = run_on(prepared(compiled), ("output",), destination=DESTINATION, run=run)
    assert [o.decision for o in second] == ["computed", "computed"]
    assert len(run.tables) == 4
    assert {o.result.artifact.table[2] for o in second} == {table("base", compiled), table("output", compiled)}


def test_a_non_reusable_define_is_never_reused():
    """DEC-RESULT-IDENTITY — a non-deterministic define and its readers compute every run, even when the
    destination or a result scope holds a complete table under the same name."""
    run = CatalogRunner()
    compiled = changed("output", "b" * 64, reusable=False)
    run_on(prepared(compiled), ("output",), destination=DESTINATION, run=run)
    publish(run, compiled=compiled)
    again = run_on(prepared(compiled), ("output",), destination=DESTINATION, run=run, official=[SHARED])
    assert [(o.decision, o.reason) for o in again] == [
        ("reused", "matching completed result"), ("computed", "definition is not reusable")]
    with pytest.raises(ResultsUnavailable, match="not reusable"):
        run_on(prepared(compiled), ("output",), destination=DESTINATION, run=run, read_only=True)


def test_a_table_holding_another_calculation_under_the_same_name_fails_loud():
    """RUN-CLIENT-TABLE-NAMES — a name has 16 fingerprint digits; the full tag must match."""
    run = CatalogRunner()
    run_on(prepared(), ("base",), destination=DESTINATION, run=run)
    run.tables[table("base")]["cql_fingerprint"] = "a" * 16 + "e" * 48
    with pytest.raises(ValueError, match="another calculation"):
        run_on(prepared(), ("base",), destination=DESTINATION, run=run)


def test_background_tag_failure_propagates_and_never_certifies_dependent():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — dependent tag success requires its upstream tag success."""
    run = CatalogRunner()
    run.fail_tag = table("base")
    with pytest.raises(RuntimeError, match="tag failure"):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    assert all(tags.get("cql_result_state") != "complete" for tags in run.tables.values())


def test_each_step_reports_its_decision_statement_and_wall_clock():
    """APP-RUN-LOG — a computed step names the exact statement it sent; a reused one sends none."""
    run, steps = CatalogRunner(), []
    run_on(prepared(), ("output",), destination=DESTINATION, run=run, on_step=steps.append)
    assert [(s.definition, s.decision, s.error) for s in steps] == [("base", "computed", None),
                                                                     ("output", "computed", None)]
    assert [s.statement for s in steps] == run.writes
    assert all(s.started_at <= s.ended_at and s.table[2] == table(s.definition) for s in steps)
    steps.clear()
    run_on(prepared(), ("output",), destination=DESTINATION, run=run, on_step=steps.append)
    assert [(s.decision, s.reason, s.statement, s.started_at) for s in steps] == [
        ("reused", "matching completed result", None, None)] * 2


def test_the_plan_is_reported_once_before_any_step_in_step_order():
    """RUN-PROGRESS-TASKS — the plan names every definition the steps will report, before the first runs."""
    run, events = CatalogRunner(), []
    run_on(prepared(), ("output",), destination=DESTINATION, run=run,
            on_plan=lambda keys: events.append(("plan", keys, len(run.writes))),
            on_step=lambda step: events.append(("step", step.definition)))
    assert events == [("plan", ("base", "output"), 0), ("step", "base"), ("step", "output")]


def test_a_failed_step_is_reported_before_the_error_is_raised():
    """APP-RUN-LOG — the failing define is logged with its error; later defines are never reported."""
    run, steps = CatalogRunner(), []
    run.fail_write = table("base")
    with pytest.raises(RuntimeError, match="compute failure"):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run, on_step=steps.append)
    [failed] = steps
    assert (failed.definition, failed.decision) == ("base", "computed")
    assert failed.error == "RuntimeError: synthetic compute failure"
    assert failed.statement == run.writes[0]


def test_published_resolution_never_writes_on_miss_or_on_hit():
    """RUN-CLIENT-PUBLISHED — read-only resolution cannot provision or compute missing work."""
    run = CatalogRunner()
    with pytest.raises(ResultsUnavailable):
        run_on(prepared(), ("output",), destination=DESTINATION, run=run, read_only=True)
    assert not run.tables
    run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    run.calls.clear()
    resolved = run_on(prepared(), ("output",), destination=DESTINATION, run=run, read_only=True)
    assert all(o.decision == "reused" for o in resolved)
    assert all(sql.startswith("SELECT") for sql, _ in run.calls)


# --- results read from other result scopes ------------------------------------------

SHARED = ("shared_catalog", "published")


def publish(run, targets=("output",), *, source=SHARED, compiled=None):
    """Compute ``targets`` into ``source``, then hold those tables in ``run`` as a result scope."""
    other = CatalogRunner(destination=source)
    run_on(prepared(compiled), targets, destination=source, run=other)
    run.sources.setdefault(source, {}).update(other.tables)
    return other


def test_one_calculation_has_one_table_name_in_every_destination_and_for_every_user():
    """RUN-CLIENT-RESULT-IDENTITY — the destination and the caller are not part of the identity. No statement
    asks who the caller is, and two destinations hold the calculation under the same names."""
    first, second = CatalogRunner(), CatalogRunner(destination=SHARED)
    run_on(prepared(), ("output",), destination=DESTINATION, run=first)
    run_on(prepared(), ("output",), destination=SHARED, run=second)
    assert set(first.tables) == set(second.tables) == {table("base"), table("output")}
    assert first.tables[table("output")]["cql_fingerprint"] == second.tables[table("output")]["cql_fingerprint"]
    # RUN-CLIENT-RESULT-OWNERSHIP: the writer's principal is read once per store, for its digest.
    assert sum("current_user" in sql for sql, _ in first.calls + second.calls) == 2


def test_a_define_the_destination_lacks_is_reused_from_a_later_result_scope_by_name():
    """Nothing is written: both defines are the shared scope's tables, found under the
    names this calculation gives them."""
    run = CatalogRunner()
    publish(run)
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED])
    assert [o.decision for o in outcomes] == ["reused", "reused"]
    assert [o.result.artifact.table for o in outcomes] == [(*SHARED, table("base")), (*SHARED, table("output"))]
    assert all(o.reason == "matching completed result in shared_catalog.published" for o in outcomes)
    assert not run.writes and not run.tables


def test_the_destination_wins_over_a_later_result_scope():
    run = CatalogRunner()
    run_on(prepared(), ("output",), destination=DESTINATION, run=run)
    publish(run)
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED])
    assert {o.result.artifact.table[:2] for o in outcomes} == {DESTINATION}


def test_a_downstream_define_is_computed_into_the_destination_over_the_shared_upstream():
    """Writes go only to the destination; the next run reuses both, each from where it is."""
    run = CatalogRunner()
    publish(run, ("base",))
    first = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED])
    assert [(o.definition, o.decision, o.result.artifact.table[:2]) for o in first] == [
        ("base", "reused", SHARED), ("output", "computed", DESTINATION)]
    assert "`shared_catalog`.`published`" in run.writes[0]
    second = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED])
    assert [o.decision for o in second] == ["reused", "reused"] and len(run.writes) == 1


def test_any_result_in_a_listed_scope_is_trusted_without_an_owner_check():
    """DEC-RESULT-SCOPE — the scope is the trust boundary: no table-owner or publisher check. The lookup never
    reads the table owner (the fake asserts it), and no tag names a publisher."""
    run = CatalogRunner()
    publish(run, ("base",))
    first = run_on(prepared(), ("base",), destination=DESTINATION, run=run, official=[SHARED])
    assert first[0].decision == "reused"
    assert not any("publisher" in tag for tags in run.sources[SHARED].values() for tag in tags)


def test_another_calculation_in_a_scope_is_not_reused():
    """RUN-CLIENT-RESULT-IDENTITY — another source, SQL or upstream is another fingerprint, so another name."""
    run = CatalogRunner()
    publish(run, compiled=changed("base", "c" * 64))
    outcomes = run_on(prepared(), ("base",), destination=DESTINATION, run=run, official=[SHARED])
    assert [o.decision for o in outcomes] == ["computed"]


def test_recompute_and_the_cutoff_skip_results_in_other_scopes():
    run = CatalogRunner()
    publish(run)
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED], mode="recompute")
    assert [o.decision for o in outcomes] == ["computed", "computed"]


def test_read_only_resolution_reads_other_scopes_and_never_writes():
    """RUN-CLIENT-PUBLISHED — a reader resolves the publisher's results through its scopes."""
    run = CatalogRunner()
    publish(run)
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED], read_only=True)
    assert [o.decision for o in outcomes] == ["reused", "reused"]
    assert all(sql.startswith("SELECT") for sql, _ in run.calls)


def test_the_earlier_result_scope_wins():
    run = CatalogRunner()
    later = ("shared_catalog", "later")
    publish(run, source=later)
    publish(run)
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[SHARED, later])
    assert {o.result.artifact.table[:2] for o in outcomes} == {SHARED}
    outcomes = run_on(prepared(), ("output",), destination=DESTINATION, run=run, official=[later, SHARED])
    assert {o.result.artifact.table[:2] for o in outcomes} == {later}


def test_a_shared_result_whose_fingerprint_disagrees_with_its_table_name_fails():
    """RUN-CLIENT-OPTIMISTIC — malformed success is an error, never silently recomputed over."""
    run = CatalogRunner()
    publish(run, ("base",))
    run.sources[SHARED][table("base")]["cql_fingerprint"] = "a" * 16 + "e" * 48
    with pytest.raises(ValueError, match="another calculation"):
        run_on(prepared(), ("base",), destination=DESTINATION, run=run, official=[SHARED])


def recipe(name, reads, n):
    from cql_databricks.compiler.providers import DatasetRecipe
    return DatasetRecipe(name=name, target=name.title(), reads=list(reads), statements=[], query=f"SELECT {n} AS __val",
                         library="Synthetic", library_version="1", define=name.title(), durable=identity(f"{n:x}" * 64))


def fan(*dependents):
    """``base``, then each of ``dependents`` reading only it, then ``out`` reading all of them."""
    from cql_databricks.compiler.providers import PipelinePlan
    return PreparedPlan(PipelinePlan(compiler_version="synthetic-compiler", datasets=[
        recipe("base", [], 1), *[recipe(name, ["base"], 2 + i) for i, name in enumerate(dependents)],
        recipe("out", dependents, 9)]))


class Started:
    """A started statement that finishes on its ``polls``-th poll, or fails there if ``fails``."""

    def __init__(self, world, key, polls, fails):
        self.world, self.key, self.polls, self.fails = world, key, polls, fails
        world.active.add(key)
        world.events.append(("start", key, frozenset(world.active)))

    def poll(self):
        self.polls -= 1
        if self.polls > 0:
            return False
        self.world.active.discard(self.key)
        if self.fails:
            raise RuntimeError(f"synthetic failure of {self.key}")
        self.world.events.append(("done", self.key))
        return True

    def cancel(self):
        self.world.active.discard(self.key)
        self.world.events.append(("cancel", self.key))


@pytest.fixture
def world(monkeypatch):
    """Statements that run side by side: ``polls[key]`` polls each, ``fail`` the key that fails."""
    from types import SimpleNamespace

    from cql_databricks.result import _catalog_runtime

    state = SimpleNamespace(active=set(), events=[], polls={}, fail=None)

    def start(run, statement):
        key = re.match(r"CREATE OR REPLACE TABLE \S*`synthetic_([a-z_]+)_[0-9a-f]{16}_[0-9a-f]{8}`", statement)[1]
        run(statement)  # the synthetic catalog records the table
        return Started(state, key, state.polls.get(key, 1), key == state.fail)
    monkeypatch.setattr(_catalog_runtime, "start_statement", start)
    return state


def starts(world):
    return [(event[1], set(event[2])) for event in world.events if event[0] == "start"]


def test_defines_that_read_only_the_shared_upstream_run_together_after_it(world):
    """RUN-CLIENT-PARALLEL-DEFINES — base alone, then every define that reads only base at once, then out."""
    world.polls = {"a": 3, "b": 2, "c": 4}
    steps = []
    run_on(fan("a", "b", "c"), ("out",), destination=DESTINATION, run=CatalogRunner(),
            on_step=steps.append)
    assert starts(world) == [("base", {"base"}), ("a", {"a"}), ("b", {"a", "b"}), ("c", {"a", "b", "c"}),
                             ("out", {"out"})]
    assert [s.definition for s in steps] == ["base", "b", "a", "c", "out"]  # in the order they finished


def test_a_long_define_delays_only_the_defines_that_read_it(world):
    """RUN-CLIENT-PARALLEL-DEFINES — a define reading the short branch starts while the long one still runs."""
    from cql_databricks.compiler.providers import PipelinePlan

    compiled = PipelinePlan(compiler_version="synthetic-compiler", datasets=[
        recipe("base", [], 1), recipe("long", ["base"], 2), recipe("short", ["base"], 3),
        recipe("after_short", ["short"], 4), recipe("out", ["long", "after_short"], 5)])
    world.polls = {"long": 10}
    run_on(PreparedPlan(compiled), ("out",), destination=DESTINATION, run=CatalogRunner())
    assert ("after_short", {"long", "after_short"}) in starts(world)
    assert world.events.index(("done", "after_short")) < world.events.index(("done", "long"))


def test_no_more_than_parallelism_statements_run_at_once(world):
    """RUN-CLIENT-PARALLEL-DEFINES — the cap holds; the next define starts as one finishes."""
    world.polls = {"a": 2, "b": 2, "c": 2}
    run_on(fan("a", "b", "c"), ("out",), destination=DESTINATION, run=CatalogRunner(),
            parallelism=2)
    assert max(len(active) for _, active in starts(world)) == 2


def test_the_first_failure_cancels_every_running_statement_and_starts_nothing_more(world):
    """RUN-CLIENT-PARALLEL-DEFINES, APP-RUN-LOG — the failed define, then each cancelled one, are reported."""
    world.polls, world.fail = {"a": 3, "b": 5, "c": 5}, "a"
    run, steps = CatalogRunner(), []
    with pytest.raises(RuntimeError, match="synthetic failure of a"):
        run_on(fan("a", "b", "c"), ("out",), destination=DESTINATION, run=run,
                on_step=steps.append)
    assert {("cancel", "b"), ("cancel", "c")} <= set(world.events)
    assert "out" not in {key for key, _ in starts(world)}
    assert [(s.definition, s.decision, s.error) for s in steps] == [
        ("base", "computed", None), ("a", "computed", "RuntimeError: synthetic failure of a"),
        ("b", "cancelled", "cancelled: a failed"), ("c", "cancelled", "cancelled: a failed")]
    assert not any(tags.get("cql_result_state") == "complete" for name, tags in run.tables.items()
                   if "synthetic_b_" in name or "synthetic_c_" in name)


def test_each_start_is_reported_as_it_happens(world):
    """RUN-PROGRESS-TASKS — ``on_start`` names each define when its statement starts."""
    events = []
    world.polls = {"a": 3}
    run_on(fan("a", "b"), ("out",), destination=DESTINATION, run=CatalogRunner(),
            on_start=lambda key: events.append(("start", key)), on_step=lambda s: events.append(("step", s.definition)))
    assert events[:2] == [("start", "base"), ("step", "base")]
    assert events.index(("start", "b")) < events.index(("step", "a"))


def test_parallelism_must_be_a_positive_count():
    with pytest.raises(ValueError, match="parallelism"):
        run_on(prepared(), ("output",), destination=DESTINATION, run=CatalogRunner(), parallelism=0)
