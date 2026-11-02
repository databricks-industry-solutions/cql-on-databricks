"""BUILD-RESOLVE-COMPILE, `DEC-STATELESS-SERVICES` — resolve a library for a compile, once, the same way for every caller.

Every caller that compiles or evaluates a library from an environment's knowledge needs the same two answers, centralized here:

- **the closure** (:func:`resolve`) — the root from the environment's knowledge (``library``, ``version``) or as CQL text, and
  its includes from the knowledge corpus (``Corpus.resolve_closure``): compile order, includes matched only by name,
  and includes not found. A ``location`` (RUN-ADDRESS-LOCATION) narrows where the root is read and where includes
  resolve first: one knowledge store, or a folder read through a ``LibraryFiles``. A caller that cannot continue with gaps calls :meth:`Resolution.require_complete`; one that
  shows them (the Designer's editor) reads :meth:`Resolution.unresolved_messages`.
- **the ELM** (:func:`translate_root`, :func:`translate_includes`) — the root against every include, and each include
  against its own transitive includes only (PERF-CLOSURE-TRANSLATE). Every library is returned with its diagnostics;
  none is skipped.

Every read goes through the caller's resolver and SQL runner, so it runs as the caller. ``translate`` is any callable
``(cql, includes) -> {ok, elm, diagnostics}``: ``RemoteProvider.translate_result`` here, or the Apps' compiler client.

data-bindings is imported here and nowhere else in this package's import graph, so ``import cql_databricks.compiler``
does not need it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cql_databricks.data_bindings import (ContentResolver, IncludeRef, LibraryId, ResolvedLibrary, parse_identity,
                                          parse_includes)

from .libraries import FileLibraryProvider, LibraryFiles, LibraryRef, LibraryResolutionError, LibrarySource, includes_of
from .location import Location

#: ``(cql, includes) -> {"ok": bool, "elm": str | None, "diagnostics": [str]}``.
Translate = Callable[[str, Sequence[str]], Mapping[str, Any]]


class ResolutionError(RuntimeError):
    """A library, include or data source that cannot be resolved. Raised, never swallowed."""


@dataclass(frozen=True)
class Resolution:
    """A root library and its include closure, in compile order (dependencies first)."""
    name: str
    version: str
    source: str
    includes: tuple[ResolvedLibrary, ...]
    unresolved: tuple[IncludeRef, ...]
    #: RUN-ADDRESS-LOCATION — where the root was read and its includes resolved first; None is the environment's knowledge.
    location: Location | None = None

    @property
    def _where(self) -> str:
        if self.location is not None and self.location.kind == "folder":
            return f"the folder {self.location.path} or the environment's knowledge"
        return "the environment's knowledge"

    @property
    def include_sources(self) -> list[str]:
        return [r.source for r in self.includes]

    @property
    def sources(self) -> list[str]:
        """The root's source, then every include's: the closure as CQL."""
        return [self.source, *self.include_sources]

    def unresolved_messages(self) -> list[str]:
        """One diagnostic per include not found, as an editor shows them."""
        return [f'Included library "{u.name}" version {u.version} is not in {self._where}.'
                for u in self.unresolved]

    def require_complete(self) -> "Resolution":
        """This resolution, or :class:`ResolutionError` naming every include not found."""
        if self.unresolved:
            missing = ", ".join(f"{u.name} {u.version}" for u in self.unresolved)
            raise ResolutionError(f"{self.name or 'the CQL'}: includes not in {self._where}: {missing}")
        return self


def resolve(content: ContentResolver, *, library: str | None = None, version: str | None = None,
            cql: str | None = None, location: Location | str | None = None,
            files: LibraryFiles | None = None) -> Resolution:
    """The closure of ``library`` (``version``, else its newest), or of ``cql`` as given.

    With no ``location`` the root comes from the environment's knowledge and so do its includes. A store location reads the root
    from that store and prefers its copy of each include; a folder location reads through ``files`` and resolves
    includes from the folder first, sibling-first, then the environment's knowledge (RUN-ADDRESS-LOCATION).

    CQL with no ``library`` header resolves too, with an empty name: an editor validates such text."""
    if (library is None) == (cql is None):
        raise ResolutionError("give exactly one of library= (by name) or cql= (CQL text)")
    where = Location.parse(location) if isinstance(location, str) else location
    if where is not None and where.kind == "folder":
        return _resolve_in_folder(content, where, files, library=library, version=version, cql=cql)
    store = where.catalog_schema if where is not None else None
    corpus = content.corpus()
    if cql is not None:
        name, root_version = parse_identity(cql, url="", version="")
        source = cql
    else:
        found = corpus.library_in(store, library, version) if store else corpus.library(library, version)
        if found is None:
            held = f"the store {where.path}" if store else "the environment's knowledge"
            raise ResolutionError(f"{library} {version or '(newest)'} is not in {held}")
        name, root_version, source = found.name, found.version, found.source
    closure = corpus.resolve_closure(source, prefer=store)
    return Resolution(name=name, version=root_version, source=source, includes=tuple(closure.includes),
                      unresolved=tuple(closure.unresolved), location=where)


def _resolve_in_folder(content: ContentResolver, where: Location, files: LibraryFiles | None, *,
                       library: str | None, version: str | None, cql: str | None) -> Resolution:
    """RUN-ADDRESS-LOCATION — the root from the folder (or as given), its includes folder-first, then knowledge."""
    from .knowledge_libraries import FolderThenKnowledge, KnowledgeLibraryProvider
    if files is None:
        raise ResolutionError(f"{where} needs a file source to read it through")
    folder = FileLibraryProvider([where.path], files=files)
    if cql is not None:
        name, root_version = parse_identity(cql, url="", version="")
        try:
            origin = folder.resolve(LibraryRef(name, root_version or None)).origin if name else None
        except LibraryResolutionError:
            origin = None
        # A library not saved here yet resolves as though it were where Save puts it (`DOC-OPEN-SAVE`).
        root = LibrarySource(name=name, version=root_version, cql=cql,
                             origin=origin or f"{where.path}/cql/{name}-{root_version}.cql")
    else:
        try:
            root = folder.resolve(LibraryRef(library, version))
        except LibraryResolutionError as e:
            raise ResolutionError(f"{library} {version or '(newest)'} is not in the folder {where.path}") from e
        if root.cql is None:
            raise ResolutionError(f"{library} in {where.path} carries ELM only; a folder library needs its CQL")
        name, root_version = root.name, root.version or ""
    missing: list[LibraryRef] = []
    found = FolderThenKnowledge(folder, KnowledgeLibraryProvider(lambda: content)).closure(root, unresolved=missing)
    for dep in found:
        if dep.cql is None:
            raise ResolutionError(f"include {dep.name} at {dep.origin} carries ELM only; it needs its CQL")
    return Resolution(name=name, version=root_version, source=root.cql or "",
                      includes=tuple(ResolvedLibrary(id=LibraryId(d.name, d.version or ""), source=d.cql or "")
                                     for d in _dependencies_first(found)),
                      unresolved=tuple(IncludeRef(r.name, r.version or "") for r in missing), location=where)


def _dependencies_first(sources: list[LibrarySource]) -> list[LibrarySource]:
    """``sources`` in compile order, each after every source it includes (the order `Resolution` promises)."""
    by_name = {s.name: s for s in sources}
    out: list[LibrarySource] = []
    done: set[str] = set()

    def visit(src: LibrarySource) -> None:
        if src.name in done:
            return
        done.add(src.name)
        for ref in includes_of(src):
            dep = by_name.get(ref.name)
            if dep is not None:
                visit(dep)
        out.append(src)

    for src in sources:
        visit(src)
    return out


@dataclass(frozen=True)
class TranslatedLibrary:
    """One library's ELM, or its diagnostics."""
    name: str
    version: str
    source: str
    elm: str | None
    ok: bool
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "source": self.source, "elm": self.elm,
                "ok": self.ok, "diagnostics": list(self.diagnostics)}


def translate_root(translate: Translate, resolution: Resolution) -> TranslatedLibrary:
    """The root's ELM, translated with every include of its closure available."""
    return _translated(resolution.name, resolution.version, resolution.source,
                       translate(resolution.source, resolution.include_sources))


def translate_includes(translate: Translate, resolution: Resolution) -> list[TranslatedLibrary]:
    """Each include's ELM, in compile order, each translated against only its own transitive includes.

    ``resolve_closure`` lists each library once by name, so a name-keyed walk finds each dependency's source."""
    by_name = {inc.id.name: inc for inc in resolution.includes}

    def dependencies(source: str) -> list[str]:
        collected: dict[str, str] = {}

        def walk(src: str) -> None:
            for ref in parse_includes(src):
                dep = by_name.get(ref.name)
                if dep is None or ref.name in collected:
                    continue
                collected[ref.name] = dep.source   # before recursing, so a cycle ends
                walk(dep.source)

        walk(source)
        return list(collected.values())

    return [_translated(inc.id.name, inc.id.version, inc.source, translate(inc.source, dependencies(inc.source)))
            for inc in resolution.includes]


def require_translated(libraries: Iterable[TranslatedLibrary]) -> list[TranslatedLibrary]:
    """``libraries``, or :class:`ResolutionError` naming each that did not translate and why."""
    libraries = list(libraries)
    failed = [lib for lib in libraries if not lib.ok]
    if failed:
        raise ResolutionError("did not translate: " + "; ".join(
            f"{lib.name} {lib.version}: {(lib.diagnostics or ('no ELM returned',))[0]}" for lib in failed))
    return libraries


def _translated(name: str, version: str, source: str, result: Mapping[str, Any]) -> TranslatedLibrary:
    elm = result.get("elm")
    if elm is not None and not isinstance(elm, str):
        import json
        elm = json.dumps(elm)
    return TranslatedLibrary(name=name, version=version, source=source, elm=elm,
                             ok=bool(result.get("ok")) and bool(elm),
                             diagnostics=tuple(result.get("diagnostics") or ()))
