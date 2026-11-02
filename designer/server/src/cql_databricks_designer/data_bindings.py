"""DOC-ENVIRONMENT-PICKER, KNOWLEDGE-LAKE-DIRECT — the environments' content an App process holds.

One :class:`~cql_databricks.data_bindings.ContentResolver` per environment, read as the App's service principal and
shared by every request: each environment's corpus is loaded once and held in memory, so it is read with the App's own
identity rather than any one user's. The environments it holds content for are found once, as the same principal
(`CLIENT-ENVIRONMENT-RESOLVER`), and found again on ``reload()``. The SQL warehouse is the App's configured warehouse;
a read with none configured fails loud, naming it.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from cql_databricks.compiler.environment import Environment, EnvironmentResolver
from cql_databricks.compiler.libraries import LibraryFiles
from cql_databricks.data_bindings import ContentResolver, SqlRunner

from cql_databricks_common import config


def app_sql_runner() -> SqlRunner:
    """The App's held session on its warehouse, as its service principal. Opened on first use, not at startup.

    RUN-CLIENT-SESSION-REUSE — one session for the process, closed at shutdown (``cql_databricks_common.warehouse``).
    """
    from cql_databricks_common.warehouse import service_principal_session
    return service_principal_session(config.warehouse_id() or "")


class EnvironmentContents:
    """KNOWLEDGE-LAKE-DIRECT — each environment's content, held for the process; nothing is read until first asked.

    ``get(None)`` is the default environment's (`CLIENT-ENVIRONMENT`)."""

    def __init__(self, runner: Callable[[], SqlRunner]) -> None:
        self._runner = runner
        self._lock = threading.Lock()
        self._environments: EnvironmentResolver | None = None
        self._held: dict[str, ContentResolver] = {}
        self._generation = 0

    @property
    def generation(self) -> tuple:
        """Changes on every reload and with every held environment's corpus; a cache key that never forces a load."""
        with self._lock:
            return (self._generation, *sorted((catalog, content.generation) for catalog, content in self._held.items()))

    def environment(self, name: str | None) -> Environment:
        """The environment ``name`` as the App's principal sees it; None is the default."""
        with self._lock:
            if self._environments is None:
                run = self._runner()
                self._environments = EnvironmentResolver(run)
            environments = self._environments
        return environments.resolve(name)

    def get(self, name: str | None) -> ContentResolver:
        """The held content of the environment ``name``; None is the default's."""
        environment = self.environment(name)
        with self._lock:
            held = self._held.get(environment.catalog)
            if held is None:
                held = ContentResolver(self._runner, environment.catalog, environment.name)
                self._held[environment.catalog] = held
            return held

    def reload(self) -> None:
        """Forget every environment and every corpus; the next read finds and loads them again."""
        with self._lock:
            self._environments = None
            self._held = {}
            self._generation += 1


def app_contents() -> EnvironmentContents:
    """The one :class:`EnvironmentContents` of an App process."""
    return EnvironmentContents(app_sql_runner)


def user_workspace_files() -> LibraryFiles:
    """RUN-ADDRESS-LOCATION — folder locations, read through the Workspace API as the signed-in user (`SEC-OBO`)."""
    from cql_databricks.compiler.workspace_files import WorkspaceLibraryFiles
    from cql_databricks_common.workspace import user_client
    return WorkspaceLibraryFiles(user_client)


def files_for(location: str | None) -> LibraryFiles | None:
    """RUN-ADDRESS-LOCATION — the signed-in user's file source when ``location`` is a folder, else None."""
    return user_workspace_files() if location and location.startswith("folder:") else None
