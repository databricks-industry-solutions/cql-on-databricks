"""RUN-CLIENT-PRIVATE-PROVISIONING — per-user Unity Catalog namespaces, without policing other grants."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from cql_databricks.data_bindings.runner import SqlRunner

from ._durable_delta import table_sql


SCHEMA_FORMAT = "cql-private-schema-v1"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _principal(run: SqlRunner) -> str:
    rows = run("SELECT current_user() AS principal")
    if len(rows) != 1:
        raise PermissionError("cannot verify the authenticated SQL principal")
    return _text(rows[0].get("principal"), "SQL principal")


@dataclass(frozen=True)
class PrivateDestination:
    """RUN-CLIENT-PRIVATE-PROVISIONING — per-user lifecycle scope, not a guarantee of exclusive access."""

    catalog: str
    owner: str

    @property
    def destination(self) -> tuple[str, str]:
        """RUN-CLIENT-RESULT-SETS — one stable schema per SQL principal, independent of sessions."""
        _text(self.catalog, "private catalog")
        _text(self.owner, "private owner")
        digest = sha256(self.owner.encode("utf-8")).hexdigest()
        return self.catalog, f"cql_private_{digest}"

    def verify(self, run: SqlRunner, table: tuple[str, str, str] | None = None) -> None:
        """RUN-CLIENT-PRIVATE-PROVISIONING — protect unrelated objects, not manage user exclusions.

        No grant inspection, revocation or ownership takeover. Other users' grants are accepted.
        UC authorizes each statement; ownership and format checks prevent accidental object adoption.
        """
        if _principal(run) != self.owner:
            raise PermissionError("private destination belongs to a different SQL caller")
        catalog, schema = self.destination
        rows = run(f"SELECT schema_owner, comment FROM {table_sql((catalog, 'information_schema', 'schemata'))} "
                   "WHERE catalog_name = :catalog AND schema_name = :schema",
                   {"catalog": catalog, "schema": schema})
        if len(rows) != 1 or rows[0].get("schema_owner") != self.owner:
            raise PermissionError("private schema is missing or owned by another principal")
        if rows[0].get("comment") != SCHEMA_FORMAT:
            raise PermissionError("incompatible private schema; refusing to adopt existing objects")
        if table is not None:
            if table[:2] != self.destination:
                raise PermissionError("table is outside the private destination")
            rows = run(f"SELECT table_owner, table_type FROM "
                       f"{table_sql((catalog, 'information_schema', 'tables'))} "
                       "WHERE table_catalog = :catalog AND table_schema = :schema AND table_name = :table",
                       {"catalog": catalog, "schema": schema, "table": table[2]})
            if (len(rows) != 1 or rows[0].get("table_owner") != self.owner
                    or rows[0].get("table_type") != "MANAGED"):
                raise PermissionError("private result must be a caller-owned managed table")


def ensure_private_destination(catalog: str, *, run: SqlRunner) -> PrivateDestination:
    """RUN-CLIENT-PRIVATE-PROVISIONING — automatically create/reuse this caller's per-user schema.

    The catalog is a platform prerequisite: an administrator creates it and grants Designer users
    USE CATALOG and CREATE SCHEMA. Deployment neither creates nor grants it. Users need no per-user
    configuration. Pass the returned object, not its tuple, to durable APIs so writes and cleanup
    recheck UC ownership. Creating the schema as the caller supplies ownership access without
    changing other users' grants.
    """
    _text(catalog, "private catalog")
    private = PrivateDestination(catalog, _principal(run))
    try:
        run(f"CREATE SCHEMA IF NOT EXISTS {table_sql(private.destination)} COMMENT '{SCHEMA_FORMAT}'")
    except Exception as error:
        raise PermissionError(
            f"cannot create your results schema in catalog '{catalog}'. A catalog administrator must "
            f"create the catalog and grant you USE CATALOG and CREATE SCHEMA on it ({error})") from error
    private.verify(run)
    return private


def find_private_destination(catalog: str, *, run: SqlRunner) -> PrivateDestination | None:
    """RUN-CLIENT-REQUEST-REOPEN — this caller's existing per-user schema, or None; never creates one.

    Read-only reopening must not provision storage. A missing schema means the caller has computed
    nothing in this catalog yet. A schema that exists but fails the ownership/format checks is an error.
    """
    _text(catalog, "private catalog")
    private = PrivateDestination(catalog, _principal(run))
    rows = run(f"SELECT schema_name FROM {table_sql((catalog, 'information_schema', 'schemata'))} "
               "WHERE catalog_name = :catalog AND schema_name = :schema",
               {"catalog": catalog, "schema": private.destination[1]})
    if not rows:
        return None
    private.verify(run)
    return private


def checked_destination(destination: tuple[str, str] | PrivateDestination, run: SqlRunner
                        ) -> tuple[tuple[str, str], PrivateDestination | None]:
    """RUN-CLIENT-PRIVATE-PROVISIONING — keep explicit notebook destinations platform-independent."""
    if isinstance(destination, PrivateDestination):
        destination.verify(run)
        return destination.destination, destination
    return destination, None
