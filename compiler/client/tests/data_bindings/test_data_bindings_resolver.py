"""KNOWLEDGE-LAKE-DIRECT — tag lookup, layout checks, and the corpus, over a fake runner."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from cql_databricks.data_bindings import (
    KNOWLEDGE_TAG, TERMINOLOGY, TERMINOLOGY_TAG, ContentResolver, LibraryId, ResolverError, TaggedSchema,
    check_layout, compare_versions, load_corpus, parse_identity, parse_includes,
    WarehouseSession, background_runner, parse_resource_types, parse_value_sets, start_statement,
    warehouse_session)
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

def test_layout_check_names_every_gap_at_once():
    schema = TaggedSchema(TERMINOLOGY_TAG, "terminology", "main", "terminology")
    run = FakeRunner([("information_schema.columns", _columns({"valueset": ["valueset_id", "code"]}))])
    with pytest.raises(ResolverError) as e:
        check_layout(run, schema, TERMINOLOGY)
    assert "valueset is missing column system" in str(e.value)
    assert "valueset is missing column version" in str(e.value)


def test_layout_check_accepts_extra_columns():
    schema = TaggedSchema(TERMINOLOGY_TAG, "terminology", "main", "terminology")
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


SOURCE = TaggedSchema(KNOWLEDGE_TAG, "measures", "main", "quality_measures")


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
    assert [lib.knowledge.name for lib in corpus.libraries if lib.name == "Helpers"] == ["measures"]


def test_corpus_sql_is_portable():
    """`resource:path` is Databricks-only SQL; every VARIANT navigation goes through `variant_get`."""
    for statement in (corpus_module._LIBRARIES, corpus_module._MEASURES, corpus_module._MEASURE_RESOURCE):
        assert "resource:" not in statement
    assert "unbase64(" in corpus_module._LIBRARIES
    assert "split_part(" in corpus_module._MEASURES and "'|'" in corpus_module._MEASURES


# --- ContentResolver ------------------------------------------------------------------------------

def test_resolver_builds_its_runner_lazily_and_caches_until_reload():
    """CLIENT-ENVIRONMENT — one environment's knowledge view, read by its standard name with no tag search."""
    built = []
    runner = _knowledge_runner()

    def factory():
        built.append(1)
        return runner

    content = ContentResolver(factory, "main", "example")
    assert built == []
    first = content.corpus()
    assert content.corpus() is first and built == [1]
    assert not any("schema_tags" in sql or "information_schema" in sql for sql, _ in runner.calls)
    assert [s.fqn for s in first.sources] == ["`main`.`knowledge`"]
    content.reload()
    assert content.corpus() is not first


# --- warehouse_session ----------------------------------------------------------------------------

class FakeConnection:
    """A SQL connector connection: each statement gets a cursor and an ID; ``fail`` makes them fail."""

    def __init__(self, rows=(), columns=(), fail=False):
        self.rows, self.columns, self.fail = list(rows), list(columns), fail
        self.executed: list[tuple[str, dict | None]] = []
        self.closed = False

    def cursor(self):
        connection = self

        class Cursor:
            query_id = None
            description = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, statement, parameters=None):
                from databricks.sql.exc import ServerOperationError

                connection.executed.append((statement, parameters))
                self.query_id = f"q{len(connection.executed)}"
                if connection.fail and statement != "SELECT 1":
                    raise ServerOperationError("boom")
                self.description = [(c, "string") for c in connection.columns] or None

            def fetchall(self):
                return connection.rows

        return Cursor()

    def close(self):
        self.closed = True


def test_warehouse_session_sends_every_statement_through_one_connection():
    """RUN-CLIENT-SESSION-REUSE — one connection for the session, opened by its first statement, not before."""
    connection = FakeConnection(rows=[(1, "x"), (2, None)], columns=["a", "b"])
    opened = []
    session = WarehouseSession(lambda: (opened.append(1), connection)[1])
    assert opened == []
    assert session("SELECT :p", {"p": "v"}) == [{"a": 1, "b": "x"}, {"a": 2, "b": None}]
    session("CREATE OR REPLACE TABLE t AS SELECT 1")
    session.observed(None)("SELECT 2")
    assert opened == [1]
    assert connection.executed == [("SELECT :p", {"p": "v"}), ("CREATE OR REPLACE TABLE t AS SELECT 1", None),
                                   ("SELECT 2", None)]


def test_warehouse_session_keeps_its_connection_across_threads():
    """RUN-CLIENT-SESSION-REUSE — like a Spark session: a statement from another thread (the next request, the
    next run) uses the same warm connection, one statement at a time."""
    import threading

    connections = []
    session = WarehouseSession(lambda: (connections.append(FakeConnection()), connections[-1])[1])
    session("SELECT 1")
    other = threading.Thread(target=session, args=("SELECT 2",))
    other.start()
    other.join(5)
    assert len(connections) == 1 and [s for s, _ in connections[0].executed] == ["SELECT 1", "SELECT 2"]


def test_background_statements_use_side_connections_and_never_wait_on_the_main_one():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — a tag write runs while the run's statement is mid-flight, on a side
    connection that is reused for the next tag write and closed with the session."""
    import threading

    inside, release = threading.Event(), threading.Event()
    connections = []

    class Blocking(FakeConnection):
        def cursor(self):
            cursor = super().cursor()
            execute = cursor.execute

            def blocked(statement, parameters=None):
                if statement == "CREATE OR REPLACE TABLE t AS SELECT 1":
                    inside.set()
                    assert release.wait(5)
                execute(statement, parameters)
            cursor.execute = blocked
            return cursor

    session = WarehouseSession(lambda: (connections.append(Blocking()), connections[-1])[1])
    run = threading.Thread(target=session, args=("CREATE OR REPLACE TABLE t AS SELECT 1",))
    run.start()
    assert inside.wait(5)
    tags = background_runner(session)
    tags("ALTER TABLE t SET TAGS ('k' = 'v')")
    tags("ALTER TABLE t SET TAGS ('k' = 'w')")
    release.set()
    run.join(5)
    assert [[s for s, _ in c.executed] for c in connections] == [
        ["CREATE OR REPLACE TABLE t AS SELECT 1"],
        ["ALTER TABLE t SET TAGS ('k' = 'v')", "ALTER TABLE t SET TAGS ('k' = 'w')"]]
    session.close()
    assert all(c.closed for c in connections)


def test_background_runner_leaves_other_runners_as_they_are():
    """RUN-CLIENT-ASYNC-CHECKPOINTS — a Spark session is thread-safe, so its runner is used as it is."""
    def run(statement, params=None):
        return []
    assert background_runner(run) is run


def test_an_idle_connection_is_checked_and_replaced_only_if_its_session_ended(monkeypatch):
    """RUN-CLIENT-SESSION-REUSE — after the idle limit the main connection is checked with SELECT 1; one whose
    session ended is closed and replaced before the caller's statement, which is never retried."""
    from cql_databricks.data_bindings import runner as runner_module

    clock = [0.0]
    monkeypatch.setattr(runner_module.time, "monotonic", lambda: clock[0])
    ended = []

    class Ending(FakeConnection):
        def cursor(self):
            cursor = super().cursor()
            execute = cursor.execute

            def checked(statement, parameters=None):
                if statement == "SELECT 1" and ended:
                    raise RuntimeError("Invalid SessionHandle")
                execute(statement, parameters)
            cursor.execute = checked
            return cursor

    connections = []
    session = WarehouseSession(lambda: (connections.append(Ending()), connections[-1])[1])
    session("SELECT 2")
    clock[0] += runner_module.CHECK_AFTER_IDLE_SECONDS + 1
    session("SELECT 3")
    assert len(connections) == 1 and [s for s, _ in connections[0].executed] == ["SELECT 2", "SELECT 1", "SELECT 3"]
    clock[0] += runner_module.CHECK_AFTER_IDLE_SECONDS + 1
    ended.append(True)
    session("SELECT 4")
    assert len(connections) == 2 and connections[0].closed
    assert [s for s, _ in connections[1].executed] == ["SELECT 4"]


def test_a_failed_statement_has_the_connection_checked_before_the_next():
    """RUN-CLIENT-SESSION-REUSE — a failure may mean the session ended, so the next statement checks first."""
    connection = FakeConnection(fail=True)
    session = WarehouseSession(lambda: connection)
    with pytest.raises(ResolverError):
        session("SELECT 2")
    connection.fail = False
    session("SELECT 3")
    assert [s for s, _ in connection.executed] == ["SELECT 2", "SELECT 1", "SELECT 3"]


def test_rows_are_positional_keep_repeated_columns_and_are_none_when_empty():
    """RUN-CLIENT-WAREHOUSE-CONNECTOR, CORE-NO-SILENT-MISLOWER — the results' ``Execute``: tuples by position."""
    session = WarehouseSession(lambda: FakeConnection(rows=[("d", 3, 3)], columns=["d", "n", "n"]))
    assert session.rows("SELECT 'd' AS d, 3 AS n, 3 AS n") == [("d", 3, 3)]
    assert WarehouseSession(lambda: FakeConnection(columns=["n"])).rows("SELECT 1 WHERE false") is None


def test_warehouse_session_closes_at_the_end_and_refuses_statements_after():
    """RUN-CLIENT-SESSION-REUSE — the holder ends the session; a late statement fails, never reconnects."""
    connection = FakeConnection()
    with WarehouseSession(lambda: connection) as session:
        session("SELECT 1")
    assert connection.closed
    with pytest.raises(ResolverError, match="closed"):
        session("SELECT 1")
    with pytest.raises(ResolverError, match="closed"):
        session.background("SELECT 1")
    session.close()


def test_warehouse_session_reports_each_statement_to_its_observer():
    """APP-RUN-LOG — the statement ID, its final state, start before end; ``observed`` gives a run its own."""
    seen, run_seen = [], []
    session = WarehouseSession(lambda: FakeConnection(), observer=seen.append)
    session("CREATE OR REPLACE TABLE t AS SELECT 1")
    session.observed(run_seen.append)("SELECT 2")
    [statement] = seen
    assert (statement.statement, statement.statement_id, statement.state) == (
        "CREATE OR REPLACE TABLE t AS SELECT 1", "q1", "SUCCEEDED")
    assert statement.started_at <= statement.ended_at
    assert [(s.statement, s.statement_id) for s in run_seen] == [("SELECT 2", "q2")]


def test_warehouse_session_observes_a_failed_statement_before_raising():
    """APP-RUN-LOG — a failed step still has a statement ID to join to system.query.history."""
    seen = []
    with pytest.raises(ResolverError, match="FAILED: boom: SELECT 2"):
        WarehouseSession(lambda: FakeConnection(fail=True), observer=seen.append)("SELECT 2")
    assert [(s.statement_id, s.state) for s in seen] == [("q1", "FAILED")]


def test_every_failure_is_a_resolver_error_with_the_statement():
    """RUN-CLIENT-WAREHOUSE-CONNECTOR — a failed connect or download is reported like a failed statement."""
    def refused():
        raise RuntimeError("HTTP 500: storage")
    with pytest.raises(ResolverError, match="HTTP 500: storage: SELECT 2"):
        WarehouseSession(refused)("SELECT 2")


def test_warehouse_session_connects_to_the_warehouse_as_the_client(monkeypatch):
    """RUN-CLIENT-WAREHOUSE-CONNECTOR — the warehouse's HTTP path, the client's host, a 30 min statement limit,
    no cloud fetch and complex values as JSON text. The credentials read the client again on every request, so
    a holder that swaps in a newer client (a fresh forwarded token) moves the session to it."""
    from databricks import sql

    seen = {}
    monkeypatch.setattr(sql, "connect", lambda **kw: (seen.update(kw), FakeConnection())[1])
    client = SimpleNamespace(config=SimpleNamespace(host="https://example.cloud.databricks.com",
                                                    authenticate=lambda: {"Authorization": "Bearer old"}))
    warehouse_session(client, "wh")("SELECT 1")
    assert (seen["server_hostname"], seen["http_path"]) == ("example.cloud.databricks.com", "/sql/1.0/warehouses/wh")
    assert seen["session_configuration"] == {"STATEMENT_TIMEOUT": "1800"}
    assert seen["use_cloud_fetch"] is False and seen["_use_arrow_native_complex_types"] is False
    headers = seen["credentials_provider"]()
    assert headers() == {"Authorization": "Bearer old"}
    client.config = SimpleNamespace(host=client.config.host, authenticate=lambda: {"Authorization": "Bearer new"})
    assert headers() == {"Authorization": "Bearer new"}


def test_warehouse_session_needs_a_warehouse():
    with pytest.raises(ResolverError, match="warehouse"):
        warehouse_session(SimpleNamespace(), "")


# --- start_statement (RUN-CLIENT-PARALLEL-DEFINES) ---------------------------------------------------

class AsyncConnection:
    """A connection whose cursors take ``execute_async``; a status request reports ``states`` in turn, then
    ``last``."""

    def __init__(self, states=("RUNNING",), last="SUCCEEDED", error=None):
        self.states, self.last, self.error = list(states), last, error
        self.submitted, self.cancelled, self.watched = [], [], []
        self.closed_operations = 0

    def cursor(self):
        from databricks.sql.backend.types import CommandState
        from databricks.sql.exc import ServerOperationError

        connection = self

        class Cursor:
            query_id = None
            active_command_id = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

            def execute_async(self, statement):
                connection.submitted.append(statement)
                self.query_id = self.active_command_id = f"q{len(connection.submitted)}"

            def get_query_state(self):
                connection.watched.append(self.active_command_id)
                name = connection.states.pop(0) if connection.states else connection.last
                if name == "FAILED":
                    raise ServerOperationError(connection.error)
                return getattr(CommandState, name)

            def cancel(self):
                connection.cancelled.append(self.query_id)
                connection.last = "CANCELLED"

            def close(self):
                if self.active_command_id is not None:
                    connection.closed_operations += 1

        return Cursor()

    def close(self):
        pass


def settled(running):
    """Poll ``running`` until it settles; its result, or the error it raised."""
    for _ in range(10):
        if running.poll():
            return True
    raise AssertionError("the statement never settled")


def test_a_started_statement_runs_without_waiting_and_is_polled_to_its_end():
    """RUN-CLIENT-PARALLEL-DEFINES — submitted with execute_async on the main connection, and nothing else: a poll
    asks its state on the same connection; the statement is closed and observed once it succeeds."""
    connection, seen = AsyncConnection(states=("PENDING", "RUNNING")), []
    running = WarehouseSession(lambda: connection, observer=seen.append).start("CREATE TABLE t AS SELECT 1")
    assert connection.submitted == ["CREATE TABLE t AS SELECT 1"] and connection.watched == [] and seen == []
    assert [running.poll(), running.poll(), running.poll()] == [False, False, True]
    assert connection.watched == ["q1"] * 3
    assert [(s.statement_id, s.state) for s in seen] == [("q1", "SUCCEEDED")] and connection.closed_operations == 1


def test_starting_statements_never_waits_on_one_already_running():
    """RUN-CLIENT-PARALLEL-DEFINES — a start only submits; it asks no state, so it never meets the warehouse's
    hold on a status request for a running statement."""
    connection = AsyncConnection()
    session = WarehouseSession(lambda: connection)
    session.start("CREATE TABLE a AS SELECT 1")
    session.start("CREATE TABLE b AS SELECT 2")
    assert connection.submitted == ["CREATE TABLE a AS SELECT 1", "CREATE TABLE b AS SELECT 2"]
    assert connection.watched == []


def test_a_started_statement_that_fails_raises_the_warehouses_message():
    """RUN-CLIENT-PARALLEL-DEFINES — the status call carries the warehouse's error, so the run log keeps it."""
    connection, seen = AsyncConnection(states=("RUNNING",), last="FAILED", error="TABLE_OR_VIEW_NOT_FOUND"), []
    running = WarehouseSession(lambda: connection, observer=seen.append).start("CREATE TABLE t AS SELECT 1")
    with pytest.raises(ResolverError, match="TABLE_OR_VIEW_NOT_FOUND: CREATE TABLE t"):
        settled(running)
    assert [(s.statement_id, s.state) for s in seen] == [("q1", "FAILED")]


def test_a_statement_that_ends_without_succeeding_raises():
    """RUN-CLIENT-PARALLEL-DEFINES — a statement the warehouse cancelled (its time limit) is a failure."""
    running = WarehouseSession(lambda: AsyncConnection(states=(), last="CANCELLED")).start("SELECT 1")
    with pytest.raises(ResolverError, match="CANCELLED"):
        settled(running)


def test_cancelling_a_started_statement_stops_it_and_reports_it_once():
    """RUN-CLIENT-PARALLEL-DEFINES — cancel stops the statement on the warehouse; the observer sees CANCELLED."""
    connection, seen = AsyncConnection(), []
    running = WarehouseSession(lambda: connection, observer=seen.append).start("SELECT 1")
    running.cancel()
    running.cancel()
    assert connection.cancelled == ["q1"] and [s.state for s in seen] == ["CANCELLED"]


def test_a_runner_that_cannot_start_a_statement_runs_it_before_returning():
    """RUN-CLIENT-PARALLEL-DEFINES — a Spark runner's statements run one at a time."""
    ran = []
    running = start_statement(lambda statement, params=None: ran.append(statement) or [], "SELECT 1")
    assert ran == ["SELECT 1"] and running.poll() is True
