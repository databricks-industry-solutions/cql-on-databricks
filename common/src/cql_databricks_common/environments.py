"""CLIENT-ENVIRONMENT-CACHE, SEC-OBO — the environments each signed-in user can see, kept for the App's process.

An App builds its resolvers per request or tool call, so the environments a resolver finds would be found again on
every call. The App process holds one :class:`~cql_databricks.resolvers.EnvironmentCache` instead, keyed by the email
the Apps platform forwards, as the user's held warehouse session is. One user's list is never handed to another. The
runner given is the user's held session (``warehouse.user_session``), which refuses a deployed request with no
forwarded email, so no list is kept under no one. Locally nothing is forwarded and the one developer's list is kept
under ``""`` (`SEC-LOCAL-IDENTITY`). A restart or another replica finds them once more.
"""
from __future__ import annotations

from cql_databricks.resolvers.environment import EnvironmentCache, EnvironmentResolver

from .workspace import forwarded_identity

#: CLIENT-ENVIRONMENT-CACHE — this process's one cache.
_cache = EnvironmentCache()


def process_environments() -> EnvironmentCache:
    """The process's cache, for a holder that lists, reloads or reads its generation."""
    return _cache


def user_environments(run) -> EnvironmentResolver:
    """The signed-in user's :class:`EnvironmentResolver` on ``run``, its list held for the process. Call it on the
    request's thread."""
    identity = forwarded_identity()
    return process_environments().resolver(identity[1] if identity else "", run)


def reset_environments() -> None:
    """Start the process's cache empty, as ``workspace.reset_client`` does the client, for tests."""
    global _cache
    _cache = EnvironmentCache()
