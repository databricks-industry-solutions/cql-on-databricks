"""APP-NARRATIVE-OVERLAY, APP-AI-CACHE — generated narrations, cached per user in one Delta table.

``<designer_catalog>.designer.narrations``, beside the run log. The deploy creates it from ``COLUMNS``
(``scripts/deploy/provision-narrations.py``) and grants the Designer App's service principal SELECT and
MODIFY on it, and no one else: a narration holds no patient data, but nothing outside the App reads it.
The App only selects and inserts.

ONE ROW PER GENERATION, never updated. A row is found by its key: the signed-in user, the library and
version, a digest of the library's CQL, and a digest of the generation contract (the writing rules and the
schema). The newest matching row is the narration. A changed CQL source or contract is a miss, so a stale
narration is never shown against logic it was not checked against. "Regenerate" inserts a newer row.

Only checked narrations are written (`explain/checks.py`); a rejected one is never cached.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock

from cql_databricks.data_bindings import SqlRunner

from cql_databricks_designer import run_log

from .checks import SCHEMA_PATH

log = logging.getLogger(__name__)

SCHEMA = run_log.SCHEMA
TABLE = "narrations"

#: The one definition of the table's columns, in order: the deploy's DDL and the App's insert read it.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("user", "STRING"), ("library", "STRING"), ("version", "STRING"), ("source_sha256", "STRING"),
    ("contract_sha256", "STRING"), ("endpoint", "STRING"), ("attempts", "INT"), ("instance", "STRING"),
    ("generated_at", "TIMESTAMP"), ("app_version", "STRING"),
)
_REQUIRED = {"user", "library", "version", "source_sha256", "contract_sha256", "instance"}


def table_sql(catalog: str) -> str:
    return f"`{catalog}`.`{SCHEMA}`.`{TABLE}`"


def create_statement(catalog: str) -> str:
    """APP-NARRATIVE-OVERLAY — the table's DDL. Run by the deploy, as the deployer; never by the App."""
    cols = ", ".join(f"`{name}` {kind}{' NOT NULL' if name in _REQUIRED else ''}" for name, kind in COLUMNS)
    return (f"CREATE TABLE IF NOT EXISTS {table_sql(catalog)} ({cols}) USING DELTA "
            "COMMENT 'APP-NARRATIVE-OVERLAY: generated narrations, one row per generation, per user'")


def source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def contract_digest() -> str:
    """A digest of what a narration is generated and checked against: the writing rules and the schema.

    Editing either is a cache miss for every library, because a narration checked against the old contract
    has not been checked against the new one."""
    from .generate import skill_text
    digest = hashlib.sha256(skill_text().encode("utf-8"))
    digest.update(SCHEMA_PATH.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class NarrationKey:
    """What a cached narration is the narration OF, and for whom."""

    user: str
    library: str
    version: str
    source_sha256: str
    contract_sha256: str


def signed_in_user() -> str:
    """The signed-in user the cache is partitioned by.

    A partition key, not an authorization decision: a narration holds no data, and every read it was
    generated from was made as the user. The same reading of identity as the cache stamp's (`cache_stamp.py`).
    Target detection, as in `user_client()`, not a fallback chain:

    - Deployed (the App's service principal): the email the Apps platform forwards. Required.
    - Locally (a person's OAuth): the profile's own user, there being exactly one (`SEC-LOCAL-IDENTITY`).
    """
    from cql_databricks_common.workspace import acting_as_service_principal, forwarded_identity

    if acting_as_service_principal():
        identity = forwarded_identity()
        if not identity or not identity[1]:
            raise RuntimeError("APP-NARRATIVE-OVERLAY: no x-forwarded-email on this request, so there is no "
                               "signed-in user to cache a narration for")
        return identity[1]
    return _local_user()


@lru_cache(maxsize=1)
def _local_user() -> str:
    from cql_databricks_common.workspace import service_principal_client
    name = service_principal_client().current_user.me().user_name
    if not name:
        raise RuntimeError("APP-NARRATIVE-OVERLAY: the local profile's user has no user name")
    return name


class NarrationStore:
    """APP-AI-CACHE — read and write cached narrations, as the App's service principal.

    ``run`` builds the service principal's runner (``app_sql_runner``) and ``catalog`` names the table's
    catalog; both are read per call, so a missing setting fails that call. Lookups are remembered in this
    process, including misses, and a save replaces the remembered value. ``generation`` moves on every save
    so the cache stamp (`PERF-CACHE-REVISION`) stops validating Specification bodies cached before it.
    """

    def __init__(self, run: Callable[[], SqlRunner], catalog: Callable[[], str]) -> None:
        self._run, self._catalog = run, catalog
        self._memo: dict[NarrationKey, str | None] = {}
        self._lock = Lock()
        self.generation = 0

    def latest(self, key: NarrationKey) -> str | None:
        """The newest cached instance for ``key``, or None when it has none."""
        with self._lock:
            if key in self._memo:
                return self._memo[key]
        rows = self._run()(
            f"SELECT `instance` FROM {table_sql(self._catalog())} WHERE `user` = :user AND `library` = :library "
            "AND `version` = :version AND `source_sha256` = :source AND `contract_sha256` = :contract "
            "ORDER BY `generated_at` DESC LIMIT 1",
            {"user": key.user, "library": key.library, "version": key.version,
             "source": key.source_sha256, "contract": key.contract_sha256})
        instance = rows[0]["instance"] if rows else None
        with self._lock:
            self._memo[key] = instance
        return instance

    def save(self, key: NarrationKey, *, instance: str, endpoint: str, attempts: int,
             app_version: str | None) -> None:
        """Insert one generation. Raises on failure: an uncached narration is reported, not shown."""
        names = ", ".join(f"`{name}`" for name, _ in COLUMNS)
        self._run()(
            f"INSERT INTO {table_sql(self._catalog())} ({names}) VALUES (:user, :library, :version, :source, "
            ":contract, :endpoint, CAST(:attempts AS INT), :instance, current_timestamp(), NULLIF(:app_version, ''))",
            {"user": key.user, "library": key.library, "version": key.version, "source": key.source_sha256,
             "contract": key.contract_sha256, "endpoint": endpoint, "attempts": str(attempts),
             "instance": instance, "app_version": app_version or ""})
        with self._lock:
            self._memo[key] = instance
            self.generation += 1
