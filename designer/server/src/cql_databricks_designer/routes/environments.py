"""DOC-ENVIRONMENT-PICKER — the environments the Designer's picker offers.

**As the signed-in user** (`SEC-OBO`, `CLIENT-ENVIRONMENT-RESOLVER`). An environment is a tagged Unity Catalog catalog
(`CLIENT-ENVIRONMENT`), and the list is what Unity Catalog lets the caller see, read with one query on the user's
warehouse runner. It is the user's list in the process's cache (`CLIENT-ENVIRONMENT-CACHE`), the same one the
Designer's runs and the assistant's tools resolve through, so the picker and a run see one list; that user's
``POST /api/environments/reload`` drops it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from cql_databricks.resolvers.environment import Environment, EnvironmentCache
from cql_databricks_common import config
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import forwarded_identity


def warehouse() -> str:
    warehouse = config.warehouse_id()
    if not warehouse:
        raise RuntimeError("WAREHOUSE_ID is not set: environments are read on a SQL warehouse")
    return warehouse


def _signed_in_user() -> str:
    """The forwarded email, the key ``cql_databricks_common.environments`` holds a user's list under; locally (one
    person, `SEC-LOCAL-IDENTITY`) nothing is forwarded, so one entry."""
    identity = forwarded_identity()
    return identity[1] if identity else ""


def _record(environment: Environment) -> dict[str, Any]:
    out: dict[str, Any] = {"name": environment.name, "catalog": environment.catalog, "version": environment.version,
                           "default": environment.default}
    if environment.description:
        out["description"] = environment.description
    return out


def router(*, environments: EnvironmentCache, caller: Callable[[], str] = _signed_in_user,
           runner: Callable[[], Any] = lambda: user_session(warehouse())) -> APIRouter:
    """``environments`` is the process's cache; ``caller`` names the signed-in user and ``runner`` is their SQL
    runner, read only when their list is not held."""
    router = APIRouter()

    @router.get("/api/environments")
    def list_environments() -> list[dict[str, Any]]:
        """DOC-ENVIRONMENT-PICKER — every environment the user can see, by name; the default is marked. Two catalogs
        with one name fail (500): the picker never chooses between them."""
        return [_record(e) for e in environments.resolver(caller(), _Lazy(runner)).environments()]

    @router.post("/api/environments/reload")
    def reload_environments() -> dict[str, Any]:
        environments.drop(caller())
        return {"ok": True}

    return router


class _Lazy:
    """A SQL runner opened on its first statement, so a held list costs no session."""

    def __init__(self, open_runner: Callable[[], Any]) -> None:
        self._open = open_runner
        self._run = None

    def __call__(self, statement: str, params=None):
        if self._run is None:
            self._run = self._open()
        return self._run(statement, params)
