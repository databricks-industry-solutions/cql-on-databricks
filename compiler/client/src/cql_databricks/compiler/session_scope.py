"""The session library that header-less CQL joins (`SVC-NOTEBOOK-SESSION-SCOPE`).

A ``%%cql`` cell or a ``cql("...")`` string with no ``library`` header is a FRAGMENT of one library per Spark
session, the way a SQL cell's view lands in the session's one namespace. A later fragment sees an earlier one's
defines unqualified; redeclaring a name replaces it, as a REPL rebinds a name::

    %%cql
    define "foo": 'bar'

    %%cql
    define "shout": Upper("foo")

    cql('define "foo": \\'baz\\'').exec("foo")      # replaces foo; shout now reads 'BAZ'
    cql().exec("shout")                             # the session library as it stands

A fragment with a header stays its own library (`SVC-NOTEBOOK-CELL-LIBRARY`), reached by ``include``.

**The merge is the compiler's** (``merge-cql``): splitting CQL into declarations needs a parser, and the client
has none. `using` follows the translator, as for a file on disk: an identical repeat is accepted, a second
version of a model fails.

**A fragment joins only if the whole library still compiles.** The merged library is described before it is
kept, so a fragment that breaks it — a define that no longer type-checks against a redefined name, a second
FHIR version — fails in the cell that caused it and leaves the session as it was.

**It lives in the Spark session** (`CLIENT-SESSION-STATE`), in the record beside the cells, so every client on
the session shares it and ``reset(spark)`` (`CLIENT-SESSION-RESET`) clears it.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .cql import describe
from .library import Library
from .providers import CompileProvider
from .session_state import SessionRecord


class SessionScope:
    """The session library of one Spark session (`SVC-NOTEBOOK-SESSION-SCOPE`). Holds no state of its own:
    the library is read from and written to the session record at each use."""

    def __init__(self, record: SessionRecord) -> None:
        self._record = record

    def join(self, fragment: str, provider: CompileProvider, load: Callable[[str], Library]) -> Library:
        """Add ``fragment`` to the session library and return the result, bound by ``load``. Raises, and keeps
        the session library unchanged, when the merged library does not compile."""
        self._record.spark  # noqa: B018 — fail before any round trip when there is no session to keep it in
        merged = provider.merge_cql([self._record.scope(), fragment])
        library = replace(load(merged.cql), fragment_defines=merged.defines)
        describe(library)
        self._record.set_scope(merged.cql)
        return library

    def library(self, load: Callable[[str], Library]) -> Library:
        """The session library as it stands, bound by ``load``: what ``cql()`` with no source returns. Its
        ``exec()`` with no targets runs the populations, else every define. Raises when nothing has joined."""
        text = self._record.scope()
        if not text:
            raise ValueError("the session library is empty: no %%cql cell or cql(\"...\") string without a "
                             "`library` line has run in this Spark session (SVC-NOTEBOOK-SESSION-SCOPE)")
        library = load(text)
        return replace(library, fragment_defines=tuple(describe(library).defines))

    def cql(self) -> str:
        """The session library's CQL so far, ``""`` before any fragment has joined."""
        return self._record.scope()
