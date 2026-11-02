"""RUN-CLIENT-MAINTENANCE, RUN-CLIENT-DRAFT-RESULTS — synthetic expiry, scope and optimistic-refresh behavior over an
environment's ``draft_results``."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from cql_databricks.result._catalog_completion import FORMAT, Completion, result_table_name, writer_digest
from cql_databricks.result.maintenance import maintain_draft_results


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
CATALOG = "synthetic_environment"


def completion(owner="synthetic-alice", index=1, age=8):
    fingerprint = f"{index:016x}" * 4
    return Completion(table=(CATALOG, "draft_results",
                             result_table_name("Synthetic", "Output", fingerprint, writer_digest(owner))),
                      fingerprint=fingerprint, computed_at=NOW - timedelta(days=age))


def row(item, owner="synthetic-alice"):
    return dict(table_catalog=item.table[0], table_schema=item.table[1], table_name=item.table[2],
                tags_json=json.dumps(item.tags()))


class Catalog:
    """Synthetic SQL boundary; query predicates are exercised separately against live UC."""

    def __init__(self, *items):
        self.rows = [row(item, owner) for item, owner in items]
        self.calls = []
        self.owner = self.principal = "synthetic-deployer"
        self.managers = set()
        self.visible = True
        self.before_recheck = lambda: None
        self.drop_error = None

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT schema_owner"):
            assert params == {"schema": "draft_results"}
            return [{"schema_owner": self.owner}] if self.visible else []
        if sql.startswith("SELECT privilege_type"):
            assert "information_schema`.`schema_privileges" in sql and "privilege_type = 'MANAGE'" in sql
            return [{"privilege_type": "MANAGE"}] if params["principal"] in self.managers else []
        if sql == "SELECT current_user() AS principal":
            return [{"principal": self.principal}]
        if sql.startswith("WITH candidates"):
            if "selected" in params:
                self.before_recheck()
                requested = {(part["schema"], part["table"]) for part in json.loads(params["selected"])}
                return [r for r in self.rows if (r["table_schema"], r["table_name"]) in requested]
            return self.rows
        if sql.startswith("DROP TABLE IF EXISTS"):
            if self.drop_error:
                raise self.drop_error
            return []
        pytest.fail(sql)


def maintain(runner, **changes):
    return maintain_draft_results(**dict(run=runner, catalog=CATALOG,
        now=NOW) | changes)


def test_preview_finds_expired_results_across_users_without_data_scans():
    """RUN-CLIENT-MAINTENANCE — one discovery, no per-user archive and no age inferred from UC edits."""
    alice, bob = completion(), completion("synthetic-bob", 2, 7)
    catalog = Catalog((alice, "synthetic-alice"), (bob, "synthetic-bob"))
    report = maintain(catalog)
    assert report.candidates == (alice, bob)
    assert report.dry_run and not report.deleted
    assert report.cutoff == NOW - timedelta(days=7)
    assert len(catalog.calls) == 3
    sql, params = catalog.calls[-1]
    for fragment in ("information_schema`.`table_tags", "t.table_schema = :schema", "t.table_type = 'MANAGED'",
                     "metadata['cql_result_state'] = 'complete'", "CAST(:cutoff AS TIMESTAMP)"):
        assert fragment in sql
    assert params["cutoff"] == report.cutoff.isoformat(timespec="microseconds")
    assert not any(word in sql for word in ("DESCRIBE", "COUNT", "LAST_ALTERED", "cql_result_sets"))


def test_expiry_drops_only_rechecked_results_and_does_not_change_grants_or_vacuum(caplog):
    """RUN-CLIENT-MAINTENANCE — deletion needs no per-user action or quiet-window assertion."""
    item = completion()
    catalog = Catalog((item, "synthetic-alice"))
    with caplog.at_level("INFO"):
        report = maintain(catalog, dry_run=False)
    assert report.deleted == (item.table,) and not report.dry_run
    assert not report.changed
    assert "Expired draft result" in caplog.text
    mutations = [sql for sql, _ in catalog.calls if not sql.startswith(("SELECT", "WITH"))]
    assert mutations == ["DROP TABLE IF EXISTS " + ".".join(f"`{part}`" for part in item.table)]


@pytest.mark.parametrize("change", ["pending", "removed", "fresh", "recomputed", "ownership"])
def test_refresh_or_ownership_change_observed_before_deletion_is_skipped(change):
    """RUN-CLIENT-MAINTENANCE — rechecks are optimistic and do not claim atomic compare-and-drop."""
    item = completion()
    catalog = Catalog((item, "synthetic-alice"))
    def changed():
        # Pending/fresh/missing/foreign rows no longer pass the SQL eligibility predicate.
        # A recompute of the same table moves its start time even when it is still past retention.
        catalog.rows = ([row(replace(item, computed_at=item.computed_at + timedelta(seconds=1)))]
                        if change == "recomputed" else [])
    catalog.before_recheck = changed
    report = maintain(catalog, dry_run=False)
    assert report.changed == (item.table,) and not report.deleted
    assert not any(sql.startswith("DROP") for sql, _ in catalog.calls)


def test_batch_limit_reports_backlog_without_introducing_a_storage_cap():
    """RUN-CLIENT-MAINTENANCE — bounded sweep work leaves excess candidates for the next run."""
    first, second = completion(), completion(index=2)
    catalog = Catalog((first, "synthetic-alice"), (second, "synthetic-alice"))
    report = maintain(catalog, limit=1)
    assert report.candidates == (first,) and report.has_more
    assert catalog.calls[-1][0].endswith("LIMIT 2")


def test_an_environment_never_loses_tables_outside_draft_results():
    """RUN-CLIENT-DRAFT-RESULTS — maintenance scopes by the draft schema, not the catalog."""
    item = completion()
    published = replace(item, table=(CATALOG, "published_measure", item.table[2]))
    catalog = Catalog((published, "synthetic-alice"))
    with pytest.raises(PermissionError, match="out-of-scope"):
        maintain(catalog, dry_run=False)
    assert not any(sql.startswith("DROP") for sql, _ in catalog.calls)


def test_owner_or_direct_manage_grant_is_required_for_cross_user_coverage():
    """RUN-CLIENT-MAINTENANCE — refuse a partial-visibility scan of the draft schema."""
    catalog = Catalog()
    catalog.principal = "synthetic-alice"
    with pytest.raises(PermissionError, match="needs MANAGE"):
        maintain(catalog)
    catalog.visible = False
    with pytest.raises(PermissionError, match="missing or not visible"):
        maintain(catalog)
    assert not any(sql.startswith("WITH") for sql, _ in catalog.calls)


def test_deployment_granted_maintenance_principal_sweeps_without_owning_the_schema():
    """RUN-CLIENT-MAINTENANCE — the deploy owns the draft schema; our principal holds MANAGE on it."""
    item = completion()
    catalog = Catalog((item, "synthetic-alice"))
    catalog.owner = "synthetic-admin"
    catalog.principal = "synthetic-maintenance"
    catalog.managers.add("synthetic-maintenance")
    report = maintain(catalog, dry_run=False)
    assert report.deleted == (item.table,)


@pytest.mark.parametrize("changes", [
    {"retention_days": 0}, {"retention_days": True}, {"limit": 0}, {"limit": 1001},
    {"dry_run": "false"}, {"catalog": ""}, {"now": datetime(2026, 1, 1)},
])
def test_invalid_policy_fails_before_sql(changes):
    """RUN-CLIENT-MAINTENANCE — no guessed destination, timezone or retention."""
    catalog = Catalog()
    with pytest.raises(ValueError):
        maintain(catalog, **changes)
    assert not catalog.calls


def test_malformed_success_or_unexpected_schema_never_becomes_a_deletion_candidate():
    """RUN-CLIENT-MAINTENANCE — corrupt success metadata is an error, never destructive evidence."""
    catalog = Catalog((completion(), "synthetic-alice"))
    catalog.rows[0]["table_schema"] = "published"
    with pytest.raises(PermissionError, match="out-of-scope"):
        maintain(catalog, dry_run=False)
    catalog.rows[0] = row(completion())
    catalog.rows[0]["tags_json"] = json.dumps({"cql_result_format": FORMAT,
                                            "cql_result_state": "complete"})
    with pytest.raises(ValueError, match="incomplete successful"):
        maintain(catalog, dry_run=False)
    assert not any(sql.startswith("DROP") for sql, _ in catalog.calls)


def test_custom_retention_and_drop_failures_are_visible():
    """RUN-CLIENT-MAINTENANCE — configurable age and fail-loud deletion errors."""
    catalog = Catalog((completion(age=31), "synthetic-alice"))
    assert maintain(catalog, retention_days=30).cutoff == NOW - timedelta(days=30)
    catalog.drop_error = RuntimeError("synthetic UC denial")
    with pytest.raises(RuntimeError, match="synthetic UC denial"):
        maintain(catalog, dry_run=False)
