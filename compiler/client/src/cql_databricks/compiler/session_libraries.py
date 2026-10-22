"""The notebook SESSION as a library source (`SVC-NOTEBOOK-CELL-LIBRARY`).

`SVC-NOTEBOOK-LIBRARY-FOLDER` makes a PUBLISHED measure runnable — mount a Git Folder, run it by name.
This module makes an UNPUBLISHED one authorable: a `%%cql` cell that declares `library X version '1.0.0'`
is registered under that identity, and a later cell's `include X` resolves it. Measures can be drafted
using session libraries, with shared definitions in one cell and populations in another::

    %%cql --library
    library SampleMeasureCommon version '1.0.0'
    ...

    %%cql
    library HypertensionBPControl version '1.0.0'
    include SampleMeasureCommon version '1.0.0' called Common
    ...

**Cells are the first root, the mounted folder is the second.** [[SessionLibraryProvider]] takes an
optional ``fallthrough`` — the engine's ``FileLibraryProvider`` — and consults cells first. This is the
engine's own ordered-search-path idiom (earlier root wins) applied one level up, not a fallback branch:
one lookup order, total. A cell therefore SHADOWS a published library of the same name while you draft
an override against a vendored content repo.

**Shadowing reaches a CELL's closure, never a published measure's.** A root that came from a file is
walked by the fallthrough alone (see [[SessionLibraryProvider.closure]]), so running
``client.cql(library="HypertensionBPControl")`` resolves exactly as a build of that content repo would — a cell cannot
silently redefine what a published measure means. Overriding is something you opt into by writing the
measure as a cell, not something that happens to a measure you merely ran.

The provider is injected through the OSS client's documented ``.library_provider(...)`` builder hook, so
no engine change and no entry-point registration is involved (`APP-DECOUPLE`). The traversal stays the
engine's: a file-backed root's closure is walked by the fallthrough itself, keeping its sibling-first
`prefer_dir` scoping. What this module owns is a lookup ORDER.

**The cells live in the Spark session** (`CLIENT-SESSION-STATE`), in the record beside the session mount, so a
Python restart loses neither and ending the session loses both. The notebook front doors build their OWN
clients: the `%%cql` magic and the module-level `cql()` share a memoized default client, and a user may hold a
third from `connect()`. All of them read one session's cells. The FALLTHROUGH is per client, so a second
`connect()` cannot silently unmount the first client's library_path.
"""
from __future__ import annotations

from cql_databricks.compiler.libraries import (LibraryProvider, LibraryRef, LibraryResolutionError, LibrarySource,
                                               identity_of, includes_of)

from .session_state import CellStore, SessionRecord


class SessionLibraryProvider(LibraryProvider):
    """Libraries declared by `%%cql` cells, ahead of an optional ``fallthrough`` provider
    (`SVC-NOTEBOOK-CELL-LIBRARY`).

    ``cells`` is the registry to read and write: a mapping of name to source. The default is a PRIVATE dict,
    so constructing a provider directly (a test, a second independent session) shares nothing.
    [[session_provider]] is the one that binds to the Spark session's record (`CLIENT-SESSION-STATE`)."""

    def __init__(self, fallthrough: LibraryProvider | None = None, *,
                 cells: dict[str, LibrarySource] | None = None) -> None:
        self._cells = {} if cells is None else cells
        self._fallthrough = fallthrough

    @property
    def fallthrough(self) -> LibraryProvider | None:
        """The provider consulted for anything no cell declares (the mounted folder), or ``None``."""
        return self._fallthrough

    # -- registration ----------------------------------------------------------------------------
    def register(self, cql: str) -> LibrarySource | None:
        """Register ``cql`` under the identity its `library` header DECLARES, replacing any earlier cell
        of that name. Returns the registered source, or ``None`` for a headerless snippet — which declares
        no library, so there is nothing for a later cell to include.

        Identity is the header and never the cell order, so moving a cell around the notebook cannot
        change what an `include` resolves to. Re-registration is a REPLACE: the definition the next
        compile sees is the one you last ran, which is why a stale cell nobody re-ran is the hazard worth
        naming rather than one the code can detect."""
        name, version = identity_of(cql)
        if not name:
            return None
        source = LibrarySource(name=name, version=version, cql=cql)
        self._cells[name] = source
        return source

    def declared(self) -> list[LibraryRef]:
        """Every library declared by a cell so far, in registration order."""
        return [s.ref for s in self._cells.values()]

    def source(self, name: str) -> LibrarySource | None:
        """The cell-declared library called ``name``, or ``None`` — the draft's text, for publishing it to
        a folder or re-registering an edited variant of it."""
        return self._cells.get(name)

    def clear(self) -> None:
        """Forget every cell-declared library, so names resolve from the mounted folder again. What a
        notebook does when it has published its drafts and wants the published copies to be what answers."""
        self._cells.clear()

    # -- LibraryProvider -------------------------------------------------------------------------
    def resolve(self, ref: LibraryRef) -> LibrarySource:
        """The cell that declares ``ref.name``, else the fallthrough's answer.

        The version is a HINT here for the same reason it is one in the engine's file provider: a cell you
        are drafting is the only definition of that name in the session, and failing a compile because the
        including cell pins `1.0.0` while the drafted cell says `1.0.1` would be a paper cut with no
        upside."""
        cell = self._cells.get(ref.name)
        if cell is not None:
            return cell
        if self._fallthrough is None:
            raise LibraryResolutionError(
                f"cannot resolve CQL library '{ref}' — no `%%cql` cell in this session declares it "
                f"(declared: {[str(r) for r in self.declared()] or 'none'}), and no library folder is "
                f"mounted. Run the cell that declares it, or mount a folder with "
                f"connect(spark, library_path=...).")
        return self._fallthrough.resolve(ref)

    def closure(self, root: LibrarySource) -> list[LibrarySource]:
        """The transitive `include` closure of ``root``, cells first.

        A root carrying an ``origin`` came from a directory, so its whole closure is the FALLTHROUGH's walk
        — that keeps `FileLibraryProvider`'s sibling-first `prefer_dir` scoping, which a content repo's
        per-measure copies of a shared helper library depend on. A root with no origin is a cell (or a pasted body),
        which has no sibling directory for that rule to apply to: its walk resolves cells here and hands
        every file-backed dependency BACK to the fallthrough's own `closure()`, so a cell that includes a
        published measure still gets that measure's own copies. Cycle-safe, like the walk it delegates to."""
        if root.origin is not None:
            if self._fallthrough is None:
                raise LibraryResolutionError(
                    f"library '{root.name}' came from {root.origin} but no library folder is mounted to "
                    f"resolve its includes — mount one with connect(spark, library_path=...).")
            return self._fallthrough.closure(root)

        cells = self._snapshot()
        out: list[LibrarySource] = []
        seen = {root.name}
        queue = list(includes_of(root))
        while queue:
            ref = queue.pop(0)
            if ref.name in seen:
                continue
            seen.add(ref.name)
            cell = cells.get(ref.name)
            if cell is not None:
                out.append(cell)
                queue.extend(includes_of(cell))
                continue
            source = self.resolve(ref)
            out.append(source)
            # A file-backed dependency's OWN closure is the fallthrough's to walk (sibling-first).
            for dep in self._fallthrough.closure(source):
                if dep.name not in seen:
                    seen.add(dep.name)
                    out.append(dep)
        return out

    def _snapshot(self) -> dict[str, LibrarySource]:
        """The cells read once, for a walk that looks up many (one record read, not one per include)."""
        snapshot = getattr(self._cells, "snapshot", None)
        return snapshot() if snapshot is not None else dict(self._cells)

    def search(self, pattern: str | None = None) -> list[LibraryRef]:
        """Cell-declared libraries plus whatever the fallthrough offers, cells first."""
        refs = [r for r in self.declared() if not pattern or _matches(r.name, pattern)]
        if self._fallthrough is not None:
            names = {r.name for r in refs}
            refs += [r for r in self._fallthrough.search(pattern) if r.name not in names]
        return refs


def _matches(name: str, pattern: str) -> bool:
    """The engine's documented `search()` rule, applied to cell names: a `*`/`?`/`[` pattern is a glob,
    anything else is a case-insensitive substring. Restated rather than imported — the engine's copy is
    private, and a search filter silently changing meaning under us is worse than four lines."""
    from fnmatch import fnmatch

    if any(c in pattern for c in "*?["):
        return fnmatch(name.lower(), pattern.lower())
    return pattern.lower() in name.lower()


def session_provider(fallthrough: LibraryProvider | None = None, spark=None) -> SessionLibraryProvider:
    """A provider over the cells of ``spark``'s session (the active one when ``None``), with ``fallthrough``
    as its second root (`CLIENT-SESSION-STATE`).

    One per client by design: every client on a session sees the same declared cells, and each keeps its own
    mounted folder, so building a second client cannot change what the first one resolves against."""
    return SessionLibraryProvider(fallthrough, cells=CellStore(SessionRecord(spark)))


def register_cell(cql: str, spark=None) -> LibrarySource | None:
    """Register a `%%cql` cell body as a library of ``spark``'s session (the active one when ``None``). With no
    Spark session it raises: a cell exists to be run. See [[SessionLibraryProvider.register]]."""
    return session_provider(spark=spark).register(cql)
