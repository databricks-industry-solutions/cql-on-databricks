"""RUN-CLIENT-CATALOG-CHECKPOINTS — explicit per-table completion, discoverable through UC.

RUN-CLIENT-RESULT-IDENTITY: a result table carries four tags: its format, its state, when its computation
started and its fingerprint. Tags hold no CQL, parameters or patient values. RUN-CLIENT-TABLE-NAMES: a result
table is named ``<library>_<define>_<16 fingerprint digits>``, so one calculation has one name in every schema.
The schema says whose results they are, so the name carries no product prefix. A timestamp is recorded only
after a successful write. UC LAST_ALTERED is not consulted.
The caller serializes overwrites of each result table; this is not a distributed locking service.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from cql_databricks.data_bindings.runner import SqlRunner

from ._durable import _digest, _utc
from ._durable_delta import table_sql


log = logging.getLogger(__name__)
#: RUN-CLIENT-RESULT-IDENTITY — v2 tables carry the retired hashes and are never reused.
FORMAT = "cql-catalog-result-v3"
#: RUN-CLIENT-TABLE-NAMES — fingerprint digits in a table name. The full fingerprint is in the
#: ``cql_fingerprint`` tag and must start with these digits, so a name collision fails loudly.
NAME_DIGITS = 16
#: RUN-CLIENT-RESULT-OWNERSHIP — digits of the writer's digest that end a table name.
WRITER_DIGITS = 8
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*_[0-9a-f]{16}_[0-9a-f]{8}")
#: The same pattern for SQL ``RLIKE``.
NAME_RLIKE = "^[a-z0-9]+(_[a-z0-9]+)*_[0-9a-f]{16}_[0-9a-f]{8}$"
TAG_FORMAT = "cql_result_format"
TAG_STATE = "cql_result_state"
TAG_COMPUTED_AT = "cql_computed_at"
TAG_FINGERPRINT = "cql_fingerprint"
TAGS = (TAG_FORMAT, TAG_STATE, TAG_COMPUTED_AT, TAG_FINGERPRINT)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _slug(text: str, limit: int, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit].rstrip("_")
    if not slug:
        raise ValueError(f"{name} {text!r} has no characters usable in a table name")
    return slug


def writer_digest(principal: str) -> str:
    """RUN-CLIENT-RESULT-OWNERSHIP — the first 8 hex digits of the SHA-256 of the writer's principal name."""
    if not isinstance(principal, str) or not principal.strip():
        raise ValueError("a result's writer is a nonempty principal name")
    return hashlib.sha256(principal.encode("utf-8")).hexdigest()[:WRITER_DIGITS]


def result_table_name(library: str, define: str, fingerprint: str, writer: str) -> str:
    """RUN-CLIENT-TABLE-NAMES — ``<library>_<define>_<fingerprint digits>_<writer digits>``, at most 227 characters.

    Lowercase letters, digits and single underscores, so it needs no quoting and reads in Catalog Explorer. The
    fingerprint digits keep two calculations of one define (another period, environment or emitted SQL) in separate
    tables; the writer's digest (:func:`writer_digest`) keeps two identities' results of one calculation apart
    (`RUN-CLIENT-RESULT-OWNERSHIP`).
    """
    _digest(fingerprint, "fingerprint")
    if not re.fullmatch(r"[0-9a-f]{8}", writer or ""):
        raise ValueError("a result table name ends with the writer's 8-digit digest")
    return f"{_slug(library, 80, 'library')}_{_slug(define, 120, 'define')}_{fingerprint[:NAME_DIGITS]}_{writer}"


def _table(table: tuple[str, str, str]) -> None:
    if (not isinstance(table, tuple) or len(table) != 3
            or any(not isinstance(part, str) or not part.strip() for part in table)):
        raise ValueError("result table must be an explicit (catalog, schema, table) tuple")
    if not NAME_PATTERN.fullmatch(table[2]):
        raise ValueError("result table name does not end in calculation fingerprint and writer digits")


@dataclass(frozen=True)
class Completion:
    """RUN-CLIENT-CATALOG-CHECKPOINTS — a successful write of one calculation, not a Delta-version reference.

    computed_at is the computation's start time, persisted only after success. Keeping this distinct
    from metadata-edit time also makes a successful empty result reusable without a row-count probe.
    """

    table: tuple[str, str, str]
    fingerprint: str
    computed_at: datetime

    def __post_init__(self) -> None:
        _table(self.table)
        _digest(self.fingerprint, "fingerprint")
        if self.table[2][-(NAME_DIGITS + WRITER_DIGITS + 2):-(WRITER_DIGITS + 1)] != "_" + self.fingerprint[:NAME_DIGITS]:
            raise ValueError("completion fingerprint does not match its result table")
        object.__setattr__(self, "computed_at", _utc(self.computed_at, "computed_at"))

    def tags(self) -> dict[str, str]:
        """RUN-CLIENT-CATALOG-CHECKPOINTS — publish state and explicit time in the same tag update."""
        return {TAG_FORMAT: FORMAT, TAG_STATE: "complete",
                TAG_COMPUTED_AT: self.computed_at.isoformat(timespec="microseconds"),
                TAG_FINGERPRINT: self.fingerprint}

    @classmethod
    def from_tags(cls, table: tuple[str, str, str], tags: Mapping[str, str]) -> Completion | None:
        """RUN-CLIENT-OPTIMISTIC — no marker/incomplete work is a miss; malformed success is an error.

        A table of another result format (an earlier release) is a miss, so it is computed again once."""
        if tags.get(TAG_FORMAT) != FORMAT or tags.get(TAG_STATE) != "complete":
            return None
        missing = set(TAGS) - tags.keys()
        if missing:
            raise ValueError(f"incomplete successful result tags: {sorted(missing)}")
        return cls(table=table, fingerprint=tags[TAG_FINGERPRINT],
                   computed_at=datetime.fromisoformat(tags[TAG_COMPUTED_AT]))


@dataclass(frozen=True)
class CatalogTable:
    """RUN-CLIENT-CATALOG-CHECKPOINTS — existence is separate from successful completion."""

    table: tuple[str, str, str]
    completion: Completion | None


def discover(run: SqlRunner, destination: tuple[str, str],
             tables: Mapping[str, str], *, owner: str) -> dict[str, CatalogTable]:
    """RUN-CLIENT-CATALOG-CHECKPOINTS — one bounded UC query for existing, pending and complete tables.

    ``tables`` maps each fingerprint to its table name (``result_table_name``). A table found under one of those names
    that ``owner`` does not own fails: its name carries ``owner``'s digest, so another identity made it, and it is
    neither reused nor written over (`RUN-CLIENT-RESULT-OWNERSHIP`).
    """
    if (not isinstance(destination, tuple) or len(destination) != 2
            or any(not isinstance(part, str) or not part.strip() for part in destination)):
        raise ValueError("destination must be an explicit (catalog, schema) tuple")
    for fingerprint, name in tables.items():
        _digest(fingerprint, "fingerprint")
        if not NAME_PATTERN.fullmatch(name) or name[-(NAME_DIGITS + WRITER_DIGITS + 2):-(WRITER_DIGITS + 1)] \
                != "_" + fingerprint[:NAME_DIGITS]:
            raise ValueError("result table name does not match its fingerprint")
    if not tables:
        return {}
    by_name = {name: fingerprint for fingerprint, name in tables.items()}
    if len(by_name) != len(tables):
        raise ValueError("two calculations map to one result table name")
    names = set(by_name)
    payload = json.dumps(sorted(names))
    if len(payload.encode("utf-8")) > 900_000:
        raise ValueError("catalog lookup exceeds the statement parameter budget")
    tables = table_sql((destination[0], "information_schema", "tables"))
    tags = table_sql((destination[0], "information_schema", "table_tags"))
    rows = run(
        "SELECT t.table_catalog, t.table_schema, t.table_name, t.table_owner, g.tag_name, g.tag_value "
        f"FROM {tables} t LEFT JOIN {tags} g ON g.catalog_name = t.table_catalog "
        "AND g.schema_name = t.table_schema AND g.table_name = t.table_name "
        f"AND g.tag_name IN ({', '.join(_literal(tag) for tag in TAGS)}) "
        "WHERE t.table_schema = :schema "
        "AND t.table_name IN (SELECT explode(from_json(:tables, 'ARRAY<STRING>')))",
        {"schema": destination[1], "tables": payload})
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        table = (row["table_catalog"], row["table_schema"], row["table_name"])
        if table[:2] != destination or table[2] not in names:
            raise ValueError("catalog lookup returned an unrequested result table")
        if row["table_owner"] != owner:
            raise PermissionError(f"{'.'.join(table)} is named for your results but owned by {row['table_owner']}; "
                                  "it is not reused or replaced (RUN-CLIENT-RESULT-OWNERSHIP)")
        values = found.setdefault(table, {})
        tag = row["tag_name"]
        if tag is not None:
            if tag not in TAGS or tag in values:
                raise ValueError("catalog lookup returned an unexpected or duplicate result tag")
            values[tag] = row["tag_value"]
    return {by_name[table[2]]: CatalogTable(table, Completion.from_tags(table, values))
            for table, values in found.items()}


def discover_in_sources(run: SqlRunner, sources: Sequence[tuple[str, str]],
                        names: Sequence[str], *, owner: str) -> dict[str, list[Completion]]:
    """RUN-CLIENT-RESULT-OWNERSHIP, RUN-CLIENT-RESULT-IDENTITY — completed results named ``names`` that ``owner``
    owns in the ``(catalog, schema)`` ``sources``, in one statement, earliest source first.

    A table's name is its calculation's identity and its writer (``result_table_name``), so the caller's result in
    the environment's other result schema is found by the same name as in the destination. Only tables tagged
    complete and owned by ``owner`` are returned: no identity reuses another's result (`DEC-RESULT-SCOPE`). Each
    catalog has its own ``information_schema``, so the statement is one branch per catalog under ``UNION ALL``.
    """
    sources = list(dict.fromkeys(sources))
    for source in sources:
        if (not isinstance(source, tuple) or len(source) != 2
                or any(not isinstance(part, str) or not part.strip() for part in source)):
            raise ValueError("result sources must be explicit (catalog, schema) tuples")
    for name in names:
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError("result table name does not end in calculation fingerprint and writer digits")
    if not sources or not names:
        return {}
    params = {"tables": json.dumps(sorted(set(names))), "owner": owner}
    branches = []
    for i, catalog in enumerate(dict.fromkeys(catalog for catalog, _ in sources)):
        params[f"schemas_{i}"] = json.dumps(sorted(schema for c, schema in sources if c == catalog))
        tags = table_sql((catalog, "information_schema", "table_tags"))
        owned = table_sql((catalog, "information_schema", "tables"))
        branches.append(
            "SELECT g.catalog_name, g.schema_name, g.table_name, g.tag_name, g.tag_value "
            f"FROM {tags} g JOIN {owned} t ON t.table_catalog = g.catalog_name AND t.table_schema = g.schema_name "
            "AND t.table_name = g.table_name AND t.table_owner = :owner "
            f"WHERE g.schema_name IN (SELECT explode(from_json(:schemas_{i}, 'ARRAY<STRING>'))) "
            "AND g.table_name IN (SELECT explode(from_json(:tables, 'ARRAY<STRING>'))) "
            f"AND g.tag_name IN ({', '.join(_literal(tag) for tag in TAGS)})")
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in run(" UNION ALL ".join(branches), params):
        table = (row["catalog_name"], row["schema_name"], row["table_name"])
        if table[:2] not in sources or table[2] not in names:
            raise ValueError("result source lookup returned a table outside the requested scopes")
        values = found.setdefault(table, {})
        if row["tag_name"] not in TAGS or row["tag_name"] in values:
            raise ValueError("result source lookup returned an unexpected or duplicate result tag")
        values[row["tag_name"]] = row["tag_value"]
    out: dict[str, list[Completion]] = {}
    for table, values in found.items():
        completion = Completion.from_tags(table, values)
        if completion is not None:
            out.setdefault(table[2], []).append(completion)
    for candidates in out.values():
        candidates.sort(key=lambda c: sources.index(c.table[:2]))
    return out


def _set_tags(run: SqlRunner, table: tuple[str, str, str], tags: Mapping[str, str]) -> None:
    _table(table)
    for key, value in tags.items():
        if key not in TAGS or not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("invalid or oversized completion tag")
    assignments = ", ".join(f"{_literal(key)} = {_literal(value)}" for key, value in sorted(tags.items()))
    run(f"ALTER TABLE {table_sql(table)} SET TAGS ({assignments})")


def search(run: SqlRunner, destination: tuple[str, str], *, fingerprint: str | None,
           computed_since: datetime | None, limit: int) -> tuple[Completion, ...]:
    """RUN-CLIENT-RESULT-IDENTITY — one bounded tag query; candidates are not resolved results."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("search limit must be an integer between 1 and 1000")
    tags = table_sql((destination[0], "information_schema", "table_tags"))
    params = {"schema": destination[1], "format": FORMAT, "state": "complete"}
    predicates = ["metadata['cql_result_format'] = :format", "metadata['cql_result_state'] = :state"]
    if fingerprint is not None:
        _digest(fingerprint, "fingerprint")
        params["fingerprint"] = fingerprint
        predicates.append(f"metadata['{TAG_FINGERPRINT}'] = :fingerprint")
    if computed_since is not None:
        params["computed_since"] = _utc(computed_since, "computed_since").isoformat(timespec="microseconds")
        predicates.append("metadata['cql_computed_at'] >= :computed_since")
    rows = run(
        "WITH candidates AS (SELECT catalog_name, schema_name, table_name, "
        "map_from_entries(collect_list(named_struct('key', tag_name, 'value', tag_value))) AS metadata "
        f"FROM {tags} WHERE schema_name = :schema "
        f"AND tag_name IN ({', '.join(_literal(tag) for tag in TAGS)}) "
        "GROUP BY catalog_name, schema_name, table_name) "
        "SELECT catalog_name, schema_name, table_name, to_json(metadata) AS tags_json FROM candidates WHERE "
        + " AND ".join(predicates)
        + f" ORDER BY metadata['cql_computed_at'] DESC, table_name LIMIT {limit}", params)
    found = []
    for row in rows:
        table = (row["catalog_name"], row["schema_name"], row["table_name"])
        if table[:2] != destination:
            raise ValueError("catalog search returned a table outside the requested destination")
        completion = Completion.from_tags(table, json.loads(row["tags_json"]))
        if completion is None or (fingerprint is not None and completion.fingerprint != fingerprint):
            raise ValueError("catalog search returned inconsistent completion metadata")
        found.append(completion)
    return tuple(found)


class CompletionWriter:
    """RUN-CLIENT-ASYNC-CHECKPOINTS — bounded tag I/O, drained on success and foreground failure.

    Pass a runner safe for concurrent independent statements. Warehouse runners have no mutable SQL
    session state. Data computation stays on the caller thread; only tag updates use these workers.
    """

    def __init__(self, run: SqlRunner, *, workers: int = 2, pending_limit: int = 4) -> None:
        if type(workers) is not int or workers < 1 or type(pending_limit) is not int or pending_limit < workers:
            raise ValueError("checkpoint workers and queue limit must be positive, with limit >= workers")
        self.run = run
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cql-checkpoint")
        self._limit = pending_limit
        self._pending: deque[Future] = deque()
        self._futures: dict[tuple[str, str, str], Future] = {}
        self._closed = False

    def __enter__(self) -> CompletionWriter:
        return self

    def check(self) -> None:
        """RUN-CLIENT-ASYNC-CHECKPOINTS — surface failed tag writes before scheduling further work."""
        if self._closed:
            raise RuntimeError("completion writer is closed")
        for future in self._futures.values():
            if future.done():
                future.result()

    def invalidate(self, table: tuple[str, str, str]) -> None:
        """RUN-CLIENT-CATALOG-CHECKPOINTS — await invalidation before overwriting an existing table."""
        self.check()
        _table(table)
        if table in self._futures:
            raise ValueError("cannot overwrite a result with pending completion work")
        _set_tags(self.run, table, {TAG_FORMAT: FORMAT, TAG_STATE: "pending"})

    def submit(self, completion: Completion, *, after: Sequence[tuple[str, str, str]] = ()) -> Future:
        """RUN-CLIENT-ASYNC-CHECKPOINTS — tag success only after upstream completion tags succeeded.

        ``after`` contains the upstream result tables submitted earlier in this operation. Reused upstream
        results already have durable success tags and do not need queue entries.
        """
        self.check()
        if completion.table in self._futures:
            raise ValueError("duplicate result completion in one operation")
        if any(table not in self._futures for table in after):
            raise ValueError("completion depends on a result not yet submitted")
        dependencies = tuple(self._futures[table] for table in after)
        while self._pending and self._pending[0].done():
            self._pending.popleft().result()
        if len(self._pending) >= self._limit:
            self._pending.popleft().result()

        def publish() -> None:
            for future in dependencies:
                future.result()
            _set_tags(self.run, completion.table, completion.tags())

        future = self._pool.submit(publish)
        self._futures[completion.table] = future
        self._pending.append(future)
        return future

    def finish(self) -> None:
        """RUN-CLIENT-ASYNC-CHECKPOINTS — do not report success or leak work after the caller exits."""
        self._closed = True
        self._pool.shutdown(wait=True)
        for future in self._futures.values():
            future.result()

    def __exit__(self, kind, error, traceback) -> bool:
        try:
            self.finish()
        except BaseException as checkpoint_error:
            if error is None:
                raise
            message = f"Completion tag publication also failed: {checkpoint_error}"
            if hasattr(error, "add_note"):
                error.add_note(message)
            log.error(message)
        return False
