"""RUN-CLIENT-PRIVATE-PROVISIONING — synthetic UC policy and durable boundary checks; no live grants."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cql_databricks.result import archive_session, materialize, open_result_set
from cql_databricks.result.private import (SCHEMA_FORMAT, PrivateDestination, ensure_private_destination,
                                             find_private_destination)


def grant(action, *, principal="synthetic-users", kind="CATALOG", key="synthetic_private"):
    return {"Principal": principal, "ActionType": action, "ObjectType": kind, "ObjectKey": key}


class GrantRunner:
    """RUN-CLIENT-PRIVATE-PROVISIONING — metadata/DDL transport fake, not a two-user isolation proof."""

    def __init__(self, fallback=None):
        self.owner = "synthetic-user"
        self.catalog = "synthetic_private"
        self.catalog_owner = "synthetic-admin"
        self.create_error = None
        self.schemas = {}
        self.catalog_grants = [grant("USE CATALOG"), grant("CREATE SCHEMA")]
        self.schema_grants = []
        self.table_grants = []
        self.table_owner = self.owner
        self.table_type = "MANAGED"
        self.statements = []
        self.fallback = fallback

    def __call__(self, statement, params=None):
        self.statements.append((statement, params))
        if statement.startswith("SELECT current_user"):
            return [{"principal": self.owner}]
        if statement.startswith("CREATE SCHEMA"):
            if self.create_error is not None:
                raise self.create_error
            namespace = PrivateDestination(self.catalog, self.owner).destination
            self.schemas.setdefault(namespace[1], {"schema_owner": self.owner, "comment": SCHEMA_FORMAT})
            return []
        if statement.startswith("SELECT schema_owner"):
            record = self.schemas.get(params["schema"])
            return [record] if record else []
        if statement.startswith("SELECT schema_name"):
            return [{"schema_name": params["schema"]}] if params["schema"] in self.schemas else []
        if statement.startswith("SELECT table_owner"):
            return [{"table_owner": self.table_owner, "table_type": self.table_type}]
        if statement.startswith("SHOW GRANTS"):
            pytest.fail("per-user lifecycle must not inspect other users' grants")
        if self.fallback is not None:
            return self.fallback(statement, params)
        pytest.fail(statement)


def test_new_user_needs_no_per_user_admin_setup_and_reuses_one_schema():
    """RUN-CLIENT-PRIVATE-PROVISIONING — separate requests/sessions converge without a user setting."""
    runner = GrantRunner()
    first = ensure_private_destination(runner.catalog, run=runner)
    second = ensure_private_destination(runner.catalog, run=runner)
    assert first == second
    assert len(runner.schemas) == 1
    assert len(first.destination[1]) == len("cql_private_") + 64
    runner.owner = "another-synthetic-user"
    third = ensure_private_destination(runner.catalog, run=runner)
    assert first.destination != third.destination
    assert len(runner.schemas) == 2
    assert not any(sql.startswith(("GRANT", "ALTER", "DROP")) for sql, _ in runner.statements)


def test_find_never_creates_and_checks_an_existing_schema():
    """RUN-CLIENT-REQUEST-REOPEN — a read-only reopen finds the caller's schema or reports none."""
    runner = GrantRunner()
    assert find_private_destination(runner.catalog, run=runner) is None
    assert not any(sql.startswith("CREATE") for sql, _ in runner.statements)
    created = ensure_private_destination(runner.catalog, run=runner)
    assert find_private_destination(runner.catalog, run=runner) == created
    runner.schemas[created.destination[1]]["schema_owner"] = "another-synthetic-user"
    with pytest.raises(PermissionError, match="owned by another principal"):
        find_private_destination(runner.catalog, run=runner)


@pytest.mark.parametrize("action", ["SELECT", "MODIFY", "ALL PRIVILEGES", "MANAGE", "USE SCHEMA",
                                  "CREATE TABLE", "BROWSE", "READ METADATA", "READ VOLUME", "NEW UNKNOWN PRIVILEGE"])
@pytest.mark.parametrize("kind", ["CATALOG", "SCHEMA", "TABLE", "METASTORE"])
def test_direct_and_inherited_grants_do_not_block_owned_results(action, kind):
    """RUN-CLIENT-PRIVATE-PROVISIONING — broad grants are deployment policy, not client exclusions."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    grants = {"CATALOG": runner.catalog_grants, "SCHEMA": runner.schema_grants, "TABLE": runner.table_grants,
              "METASTORE": runner.catalog_grants}
    grants[kind].append(grant(action, kind=kind))
    private.verify(runner, (*private.destination, "synthetic_result"))
    assert not any(sql.startswith(("SHOW GRANTS", "GRANT", "REVOKE")) for sql, _ in runner.statements)


def test_catalog_owner_and_other_groups_do_not_require_client_allowlists():
    """RUN-CLIENT-PRIVATE-PROVISIONING — the client does not classify privileged groups."""
    runner = GrantRunner()
    runner.catalog_grants.append(grant("ALL PRIVILEGES", principal=runner.catalog_owner))
    private = ensure_private_destination(runner.catalog, run=runner)
    runner.catalog_grants.append(grant("ALL PRIVILEGES", principal="admins"))
    private.verify(runner)


def test_schema_collision_cannot_be_adopted_or_taken_over():
    """RUN-CLIENT-PRIVATE-PROVISIONING — a deterministic name is not an authorization credential."""
    runner = GrantRunner()
    private = PrivateDestination(runner.catalog, runner.owner)
    runner.schemas[private.destination[1]] = {"schema_owner": "foreign-owner", "comment": SCHEMA_FORMAT}
    with pytest.raises(PermissionError, match="owned by another"):
        ensure_private_destination(runner.catalog, run=runner)
    assert runner.schemas[private.destination[1]]["schema_owner"] == "foreign-owner"
    assert not any(sql.startswith(("ALTER", "GRANT", "DROP")) for sql, _ in runner.statements)


def test_incompatible_schemas_are_not_adopted():
    """RUN-CLIENT-PRIVATE-PROVISIONING — existing unmarked schemas remain unchanged."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    runner.schemas[private.destination[1]]["comment"] = "foreign"
    with pytest.raises(PermissionError, match="adopt"):
        ensure_private_destination(runner.catalog, run=runner)


def test_ownership_visibility_errors_fail_without_adopting_objects():
    """RUN-CLIENT-PRIVATE-PROVISIONING — ownership checks remain mandatory, unlike grant inspection."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    def denied(statement, params=None):
        if statement.startswith("SELECT schema_owner"):
            raise PermissionError("synthetic metadata denied")
        return runner(statement, params)
    with pytest.raises(PermissionError, match="synthetic metadata denied"):
        private.verify(denied)


@pytest.mark.parametrize("field,value", [("table_owner", "foreign-owner"), ("table_type", "EXTERNAL"),
                                       ("table_type", "VIEW")])
def test_tables_require_real_ownership_and_managed_storage(field, value):
    """RUN-CLIENT-PRIVATE-PROVISIONING — properties cannot impersonate table ownership."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    setattr(runner, field, value)
    with pytest.raises(PermissionError, match="caller-owned managed"):
        private.verify(runner, (*private.destination, "synthetic_result"))


def test_changed_caller_cannot_reuse_private_destination_evidence():
    """RUN-CLIENT-PRIVATE-PROVISIONING — shared App processes cannot share user authorization."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    runner.owner = "another-synthetic-user"
    with pytest.raises(PermissionError, match="different SQL caller"):
        private.verify(runner)


def test_administrator_catalog_needs_no_marker_catalog_ddl_or_grants():
    """RUN-CLIENT-PRIVATE-PROVISIONING — the catalog is a platform prerequisite, never created or granted here."""
    runner = GrantRunner()
    ensure_private_destination(runner.catalog, run=runner)
    mutations = [sql for sql, _ in runner.statements if sql.startswith(("CREATE", "GRANT", "ALTER", "REVOKE"))]
    assert mutations == [f"CREATE SCHEMA IF NOT EXISTS {'.'.join(f'`{part}`' for part in PrivateDestination(runner.catalog, runner.owner).destination)} COMMENT '{SCHEMA_FORMAT}'"]
    assert not any("information_schema`.`catalogs" in sql for sql, _ in runner.statements)


def test_missing_catalog_privileges_fail_with_the_administrator_action():
    """RUN-CLIENT-PRIVATE-PROVISIONING — no fallback location; the caller learns which grants are missing."""
    runner = GrantRunner()
    runner.create_error = RuntimeError("PERMISSION_DENIED: synthetic")
    with pytest.raises(PermissionError, match="USE CATALOG and CREATE SCHEMA") as error:
        ensure_private_destination(runner.catalog, run=runner)
    assert "'synthetic_private'" in str(error.value) and "PERMISSION_DENIED: synthetic" in str(error.value)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert not runner.schemas


def test_public_writes_reads_and_cleanup_recheck_ownership_before_touching_data():
    """RUN-CLIENT-PRIVATE-PROVISIONING — checks are operation guards, not only first-use provisioning."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    runner.schemas[private.destination[1]]["schema_owner"] = "another-owner"
    with pytest.raises(PermissionError, match="owned by another"):
        materialize(None, "Output", destination=private, run=runner)
    with pytest.raises(ValueError, match="no server-side session"):
        archive_session("session", destination=private, run=runner)
    with pytest.raises(ValueError, match="calculation request"):
        open_result_set("session", destination=private, run=runner)


def test_private_destinations_cannot_be_used_for_job_publishing():
    """RUN-CLIENT-PRIVATE-PROVISIONING — same-user Job publishing still needs a separate destination."""
    runner = GrantRunner()
    private = ensure_private_destination(runner.catalog, run=runner)
    with pytest.raises(PermissionError, match="Job publishing"):
        materialize(SimpleNamespace(), "Output", destination=private, run=runner, purpose="published")
