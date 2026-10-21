"""RUN-CLIENT-MAINTENANCE — expire completed private results across users, using UC tags.

The deploy owns the daily Job that calls this (`databricks.yml`, `scripts/jobs/maintenance.py`) and the
principal it runs as (`scripts/deploy/maintenance_principal.py`).

There is no archive store or session index. DROP removes derived tables, never source/published
data. Completion tags are rechecked before deletion; this optimistic check is not a lock.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cql_databricks.data_bindings.runner import SqlRunner

from ._catalog_completion import Completion, FORMAT, NAME_RLIKE, TAGS, _literal
from ._durable import _utc
from ._durable_delta import table_sql
from .private import PrivateDestination, SCHEMA_FORMAT, _principal, _text


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceReport:
    """RUN-CLIENT-MAINTENANCE — report exact candidates and completed deletions, without data reads."""

    catalog: str
    cutoff: datetime
    candidates: tuple[Completion, ...]
    deleted: tuple[tuple[str, str, str], ...] = ()
    changed: tuple[tuple[str, str, str], ...] = ()
    has_more: bool = False
    dry_run: bool = True


def _scope(run: SqlRunner, catalog: str) -> None:
    """RUN-CLIENT-PRIVATE-PROVISIONING — the catalog may be shared with source and published data.

    Scope comes from the private schema marker, the owner-derived schema name and caller-owned
    managed result tables, never from the catalog alone.
    """
    _text(catalog, "private catalog")
    _authority(run, catalog)


def _authority(run: SqlRunner, catalog: str) -> None:
    """RUN-CLIENT-MAINTENANCE — ownership or a direct MANAGE grant covers every user's schema.

    MANAGE on a catalog is inherited by its schemas and tables and allows deleting objects the
    principal does not own. Deployment grants it to the maintenance principal. Never claim a
    complete sweep when information_schema may hide other users' tables.
    """
    principal = _principal(run)
    rows = run(f"SELECT catalog_owner FROM {table_sql((catalog, 'information_schema', 'catalogs'))} "
               "WHERE catalog_name = :catalog", {"catalog": catalog})
    if len(rows) != 1:
        raise PermissionError(f"private catalog '{catalog}' is missing or not visible to the maintenance principal")
    if rows[0].get("catalog_owner") == principal:
        return
    rows = run(f"SELECT privilege_type FROM {table_sql((catalog, 'information_schema', 'catalog_privileges'))} "
               "WHERE catalog_name = :catalog AND grantee = :principal AND privilege_type = 'MANAGE'",
               {"catalog": catalog, "principal": principal})
    if not rows:
        raise PermissionError("maintenance principal needs MANAGE on the private catalog for cross-user coverage")


def _expired(run: SqlRunner, catalog: str, cutoff: datetime, limit: int,
             selected: tuple[Completion, ...] | None = None) -> tuple[Completion, ...]:
    """RUN-CLIENT-MAINTENANCE — one bounded catalog query, including ownership and completion tags."""
    tags = table_sql((catalog, "information_schema", "table_tags"))
    tables = table_sql((catalog, "information_schema", "tables"))
    schemas = table_sql((catalog, "information_schema", "schemata"))
    params = {"catalog": catalog, "schema_format": SCHEMA_FORMAT, "format": FORMAT,
              "cutoff": cutoff.isoformat(timespec="microseconds")}
    selection = ""
    if selected is not None:
        params["selected"] = json.dumps([{"schema": item.table[1], "table": item.table[2]} for item in selected])
        selection = (" AND named_struct('schema', t.table_schema, 'table', t.table_name) IN "
                     "(SELECT explode(from_json(:selected, 'ARRAY<STRUCT<schema:STRING,table:STRING>>')))")
    rows = run(
        "WITH candidates AS (SELECT t.table_catalog, t.table_schema, t.table_name, s.schema_owner, "
        "map_from_entries(collect_list(named_struct('key', g.tag_name, 'value', g.tag_value))) AS metadata "
        f"FROM {tables} t JOIN {schemas} s ON s.catalog_name = t.table_catalog "
        "AND s.schema_name = t.table_schema "
        f"JOIN {tags} g ON g.catalog_name = t.table_catalog AND g.schema_name = t.table_schema "
        "AND g.table_name = t.table_name "
        "WHERE t.table_catalog = :catalog AND s.comment = :schema_format "
        "AND t.table_schema = concat('cql_private_', sha2(s.schema_owner, 256)) "
        "AND t.table_owner = s.schema_owner AND t.table_type = 'MANAGED' "
        "AND lower(t.data_source_format) = 'delta' "
        f"AND t.table_name RLIKE '{NAME_RLIKE}' "
        f"AND g.tag_name IN ({', '.join(_literal(tag) for tag in TAGS)})" + selection +
        " GROUP BY t.table_catalog, t.table_schema, t.table_name, s.schema_owner) "
        "SELECT table_catalog, table_schema, table_name, schema_owner, to_json(metadata) AS tags_json "
        "FROM candidates WHERE metadata['cql_result_format'] = :format "
        "AND metadata['cql_result_state'] = 'complete' "
        "AND CAST(metadata['cql_computed_at'] AS TIMESTAMP) <= CAST(:cutoff AS TIMESTAMP) "
        "ORDER BY CAST(metadata['cql_computed_at'] AS TIMESTAMP), table_schema, table_name "
        f"LIMIT {limit}", params)
    found, seen = [], set()
    requested = {item.table for item in selected} if selected is not None else None
    for row in rows:
        table = (row["table_catalog"], row["table_schema"], row["table_name"])
        if (table[:2] != PrivateDestination(catalog, row["schema_owner"]).destination
                or table in seen or (requested is not None and table not in requested)):
            raise PermissionError("maintenance discovery returned duplicate or out-of-scope tables")
        item = Completion.from_tags(table, json.loads(row["tags_json"]))
        if item is None or item.computed_at > cutoff:
            raise ValueError("maintenance discovery returned inconsistent expiry metadata")
        found.append(item)
        seen.add(table)
    return tuple(found)


def maintain_private_results(*, run: SqlRunner, catalog: str, retention_days: int = 7, limit: int = 1000, dry_run: bool = True,
                             now: datetime | None = None) -> MaintenanceReport:
    """RUN-CLIENT-MAINTENANCE — seven-day computation-age expiry, configurable by deployment.

    Only marked, successfully completed, user-owned managed Delta results qualify. Pending/untagged
    writes, foreign objects and published results are excluded. Reopening a result does not extend
    its retention. No VACUUM, reference index, DESCRIBE, row count or ownership/grant change.

    No quiet window or session pin is required. A final tag check skips observed refreshes, but UC
    has no atomic compare-and-drop: a concurrent read/recompute may fail and need retry. Existing
    readers are not pins; once expired, they may need to compute missing results again.
    """
    if type(retention_days) is not int or retention_days < 1:
        raise ValueError("retention_days must be a positive integer")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("maintenance batch limit must be between 1 and 1000")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")
    cutoff = _utc(now or datetime.now(timezone.utc), "maintenance time") - timedelta(days=retention_days)
    _scope(run, catalog)
    found = _expired(run, catalog, cutoff, limit + 1)
    candidates, deleted, changed = found[:limit], [], []
    if not dry_run and candidates:
        _scope(run, catalog)
        for item in candidates:
            if _expired(run, catalog, cutoff, 1, (item,)) != (item,):
                changed.append(item.table)
                continue
            run(f"DROP TABLE IF EXISTS {table_sql(item.table)}")
            deleted.append(item.table)
            log.info("Expired private result %s fingerprint=%s computed_at=%s",
                     table_sql(item.table), item.fingerprint, item.computed_at.isoformat())
    return MaintenanceReport(catalog, cutoff, candidates, tuple(deleted), tuple(changed),
                             len(found) > limit, dry_run)
