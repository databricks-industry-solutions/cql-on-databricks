"""BIND-CALL-SITE: the binding a compile carries, chosen at compile time.

A library's binder (:class:`~.resolver_binder.ResolverBinder`) binds each compile's call sites through the run's
resolvers (`CLIENT-RESOLVERS`). A library with no binder binds nothing: CQL that retrieves nothing compiles, and a
retrieve or a value-set membership fails at the compiler, naming its site.

Platform-free: nothing here imports Spark or a platform SDK.
"""
from __future__ import annotations

from .providers import CompileRequest


def bound_request(library, request: CompileRequest, *, durable: bool = False) -> CompileRequest:
    """``request`` bound through ``library``'s binder, or unchanged when it has none. The binder asks ``describe``,
    when it must, with this very request, so the site ids match (`BIND-CALL-SITE`)."""
    binder = getattr(library, "binder", None)
    if binder is None:
        return request
    return binder.bind_request(library, request, lambda: library.provider.describe(request), durable=durable)
