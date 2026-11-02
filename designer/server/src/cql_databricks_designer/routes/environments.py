"""DOC-ENVIRONMENT-PICKER — the environments the Designer's picker offers.

**As the signed-in user** (`SEC-OBO`, `CLIENT-ENVIRONMENT-RESOLVER`). An environment is a tagged Unity Catalog catalog
(`CLIENT-ENVIRONMENT`), and the list is what Unity Catalog lets the caller see, read with one query on the user's
warehouse runner. It is cached per user until that user's ``POST /api/environments/reload``. The cache holds the
list, never a runner: a forwarded token expires.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from cql_databricks.compiler.environment import Environment, EnvironmentResolver
from cql_databricks_common import config
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import forwarded_identity


class EnvironmentCache:
    """One cached environment list per signed-in user, filled on first read and dropped by that user's reload."""

    def __init__(self, load: Callable[[], list[Environment]], who: Callable[[], str]) -> None:
        self._load = load
        self._who = who
        self._lock = threading.Lock()
        self._by_user: dict[str, list[Environment]] = {}
        self._generation = 0

    @property
    def generation(self) -> int:
        """Moves on every load and every reload; a cache-stamp key that never forces a load itself."""
        return self._generation

    def list(self) -> list[Environment]:
        user = self._who()
        with self._lock:
            cached = self._by_user.get(user)
        if cached is not None:
            return cached
        # Outside the lock: a lookup takes seconds, and another user's must not wait on it.
        found = self._load()
        with self._lock:
            self._by_user[user] = found
            self._generation += 1
        return found

    def reload(self) -> None:
        with self._lock:
            self._by_user.pop(self._who(), None)
            self._generation += 1


def _signed_in_user() -> str:
    """The forwarded email; locally (one person, `SEC-LOCAL-IDENTITY`) nothing is forwarded, so one entry."""
    identity = forwarded_identity()
    return identity[1] if identity else ""


def warehouse() -> str:
    warehouse = config.warehouse_id()
    if not warehouse:
        raise RuntimeError("WAREHOUSE_ID is not set: environments are read on a SQL warehouse")
    return warehouse


def user_environments() -> EnvironmentCache:
    """The Designer's cache, loading as the signed-in user."""
    return EnvironmentCache(lambda: EnvironmentResolver(user_session(warehouse())).environments(), _signed_in_user)


def _record(environment: Environment) -> dict[str, Any]:
    out: dict[str, Any] = {"name": environment.name, "catalog": environment.catalog, "version": environment.version,
                           "default": environment.default}
    if environment.description:
        out["description"] = environment.description
    return out


def router(*, environments: EnvironmentCache) -> APIRouter:
    router = APIRouter()

    @router.get("/api/environments")
    def list_environments() -> list[dict[str, Any]]:
        """DOC-ENVIRONMENT-PICKER — every environment the user can see, by name; the default is marked. Two catalogs
        with one name fail (500): the picker never chooses between them."""
        return [_record(e) for e in environments.list()]

    @router.post("/api/environments/reload")
    def reload_environments() -> dict[str, Any]:
        environments.reload()
        return {"ok": True}

    return router
