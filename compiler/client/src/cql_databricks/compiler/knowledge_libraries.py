"""An environment's knowledge as a library source for ``cql()`` (`CLIENT-KNOWLEDGE-INCLUDES`,
`SVC-NOTEBOOK-LIBRARY-FOLDER`).

A notebook's ``include`` and ``client.cql(library=...)`` resolve in ONE precedence order:

1. libraries declared by ``%%cql`` cells (`SVC-NOTEBOOK-CELL-LIBRARY`, :mod:`.session_libraries`);
2. the mounted folder, ``library_path`` (`SVC-NOTEBOOK-LIBRARY-FOLDER`);
3. the environment's knowledge, its ``knowledge`` views (`CLIENT-ENVIRONMENT`, `BIND-PUBLISHED-VIEW`).

An exact ``(name, version)`` match in the knowledge beats a by-name match; a by-name match takes the newest version
that holds the name. A by-name match is logged.

Knowledge is read through the same ``ContentResolver`` the durable calculations use (`BUILD-RESOLVE-COMPILE`), in
this session and as this user, and loaded once. So its ``closure()`` is one in-memory walk of the corpus, not a
round trip per include, as `SVC-NOTEBOOK-LIBRARY-FOLDER` requires of a store that can answer a whole closure.

A published library means what it was published with: the closure of a root that came from the environment's
knowledge is walked in that knowledge alone, as a folder root's is walked in its folder (sibling-first). A folder
root's include that the folder does not hold resolves from the environment's knowledge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from cql_databricks.data_bindings import ContentResolver, Corpus, Library

from .libraries import (FileLibraryProvider, LibraryProvider, LibraryRef, LibraryResolutionError,
                        LibrarySource, _name_matches, includes_of)

#: The ``origin`` of a library read from an environment's knowledge: ``uc://<catalog>.<schema>/<name>|<version>``.
KNOWLEDGE_ORIGIN = "uc://"


def from_knowledge(source: LibrarySource) -> bool:
    return bool(source.origin and source.origin.startswith(KNOWLEDGE_ORIGIN))


def _source(library: Library) -> LibrarySource:
    knowledge = library.knowledge
    return LibrarySource(name=library.name, version=library.version, cql=library.source,
                         origin=f"{KNOWLEDGE_ORIGIN}{knowledge.catalog}.{knowledge.schema}/{library.name}|{library.version}")


class KnowledgeLibraryProvider(LibraryProvider):
    """CLIENT-KNOWLEDGE-INCLUDES — libraries from the environment's knowledge.

    ``content`` builds the resolver on first use, so a session that never includes a published library never
    reads the catalog."""

    def __init__(self, content: Callable[[], ContentResolver]) -> None:
        self._content = content
        self._resolver: Optional[ContentResolver] = None

    def _corpus(self) -> Corpus:
        if self._resolver is None:
            self._resolver = self._content()
        return self._resolver.corpus()

    def _where(self) -> str:
        return f"{self._resolver.catalog}.knowledge" if self._resolver else "unknown"

    def resolve(self, ref: LibraryRef) -> LibrarySource:
        corpus = self._corpus()
        library = corpus.library(ref.name, ref.version)
        if library is None:
            raise LibraryResolutionError(f"cannot resolve CQL library '{ref}': it is not in the environment's "
                                         f"knowledge, {self._where()}")
        return _source(library)

    def closure(self, root: LibrarySource) -> List[LibrarySource]:
        """The closure of ``root`` in the environment's knowledge, dependencies first, in one walk of the loaded corpus."""
        if not root.cql:
            raise LibraryResolutionError(f"library '{root.name}' has no CQL for the environment's knowledge to walk")
        corpus = self._corpus()
        closure = corpus.resolve_closure(root.cql)
        unresolved = closure.unresolved
        if unresolved:
            missing = ", ".join(f"{ref.name} {ref.version or ''}".strip() for ref in unresolved)
            raise LibraryResolutionError(f"the include closure of '{root.name}' needs {missing}, which the "
                                         f"environment's knowledge, {self._where()}, does not hold")
        return [_source(corpus.library(item.id.name, item.id.version)) for item in closure.includes
                if item.id.name != root.name]

    def search(self, pattern: Optional[str] = None) -> List[LibraryRef]:
        refs, seen = [], set()
        for library in self._corpus().libraries:
            if (library.name, library.version) in seen or (pattern and not _name_matches(library.name, pattern)):
                continue
            seen.add((library.name, library.version))
            refs.append(LibraryRef(library.name, library.version))
        return refs


class FolderThenKnowledge(LibraryProvider):
    """CLIENT-KNOWLEDGE-INCLUDES — the mounted folder (when there is one), then the environment's knowledge: one lookup
    order."""

    def __init__(self, folder: Optional[LibraryProvider], knowledge: KnowledgeLibraryProvider) -> None:
        self._folder = folder
        self._knowledge = knowledge

    def _resolve(self, ref: LibraryRef, prefer_dir: Optional[str | Path] = None) -> LibrarySource:
        if self._folder is not None:
            try:
                if prefer_dir is not None and isinstance(self._folder, FileLibraryProvider):
                    return self._folder.resolve(ref, prefer_dir=prefer_dir)
                return self._folder.resolve(ref)
            except LibraryResolutionError:
                pass  # not in the folder: the next source in the order answers, or says it cannot
        return self._knowledge.resolve(ref)

    def resolve(self, ref: LibraryRef) -> LibrarySource:
        return self._resolve(ref)

    def closure(self, root: LibrarySource, *,
                unresolved: Optional[List[LibraryRef]] = None) -> List[LibrarySource]:
        """A knowledge root walks the environment's knowledge alone. Any other root walks the folder sibling-first, and
        an include the folder lacks comes from the environment's knowledge with its own closure.

        An include neither source holds raises, unless ``unresolved`` is given: then it is appended there and
        the walk goes on, so an editor can report every missing include at once (RUN-ADDRESS-LOCATION)."""
        if from_knowledge(root):
            return self._knowledge.closure(root)
        prefer_dir = (self._folder.home_of(root) if isinstance(self._folder, FileLibraryProvider)
                      else Path(root.origin).parent if root.origin else None)
        out: List[LibrarySource] = []
        seen = {root.name}
        queue = list(includes_of(root))
        while queue:
            ref = queue.pop(0)
            if ref.name in seen:
                continue
            seen.add(ref.name)
            try:
                source = self._resolve(ref, prefer_dir)
            except LibraryResolutionError:
                if unresolved is None:
                    raise
                unresolved.append(ref)
                continue
            out.append(source)
            if not from_knowledge(source):
                queue.extend(includes_of(source))
                continue
            for dep in self._knowledge.closure(source):
                if dep.name not in seen:
                    seen.add(dep.name)
                    out.append(dep)
        return out

    def search(self, pattern: Optional[str] = None) -> List[LibraryRef]:
        refs = list(self._folder.search(pattern)) if self._folder is not None else []
        names = {(r.name, r.version) for r in refs}
        return refs + [r for r in self._knowledge.search(pattern) if (r.name, r.version) not in names]
