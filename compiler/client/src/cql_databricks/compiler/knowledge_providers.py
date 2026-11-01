"""CLIENT-KNOWLEDGE-PROVIDER: the knowledge sources the client ships, and the one closure walk over them.

| Source | Holds | A store |
|---|---|---|
| :class:`SessionCells` | the libraries ``%%cql`` cells declared in this Spark session (`SVC-NOTEBOOK-CELL-LIBRARY`) | no |
| :class:`LibraryFolder` | every library in a ``/Workspace`` or ``/Volumes`` folder, at any depth (`RUN-ADDRESS-LOCATION`) | no |
| :class:`DeltaKnowledge` | tagged knowledge in the caller's catalog scopes, or one tagged store (`CLIENT-DEFAULT-RESOLVER`) | yes, given a store |

A source only answers lookups. The include closure is walked in one place, :func:`library_provider`, which turns
a source, or an ``Overlay`` chain of them (`CLIENT-PROVIDER-OVERLAY`), into the client core's
:class:`~.libraries.LibraryProvider`. The order of ``SVC-NOTEBOOK-LIBRARY-FOLDER`` is the chain
``Overlay(SessionCells, Overlay(LibraryFolder, DeltaKnowledge))``: a folder library's includes resolve from its
folder, sibling-first, then from tagged knowledge; a library from tagged knowledge resolves its includes there
alone. Any other source's includes resolve from it and the layers after it.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .libraries import (FileLibraryProvider, LibraryFiles, LibraryProvider, LibraryRef, LibraryResolutionError,
                        LibrarySource, LocalLibraryFiles, includes_of)
from .roles import KnowledgeSource, KnowledgeStore, Overlay


class SessionCells(KnowledgeSource):
    """CLIENT-KNOWLEDGE-PROVIDER: the libraries the ``%%cql`` cells of ``spark``'s session declared (the active
    session when None), kept in the session record (`CLIENT-SESSION-STATE`). ``cells`` replaces the record with a
    plain mapping, for a caller with no Spark session."""

    def __init__(self, spark=None, *, cells: dict | None = None) -> None:
        from .session_libraries import CellStore, SessionRecord, SessionLibraryProvider

        self._store = cells if cells is not None else CellStore(SessionRecord(spark))
        self.provider = SessionLibraryProvider(None, cells=self._store)

    def with_fallthrough(self, fallthrough: LibraryProvider | None) -> LibraryProvider:
        """These cells ahead of ``fallthrough``, sharing one registry."""
        from .session_libraries import SessionLibraryProvider

        return SessionLibraryProvider(fallthrough, cells=self._store)

    def identity(self) -> str:
        return "cells"

    def library(self, name: str, version: str | None) -> LibrarySource | None:
        source = self.provider.source(name)
        return source if source is not None and (version is None or source.version == version) else None

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        return None

    def libraries(self) -> Iterable[LibraryRef]:
        return self.provider.declared()


class LibraryFolder(KnowledgeSource):
    """CLIENT-KNOWLEDGE-PROVIDER: every library in a folder (`RUN-ADDRESS-LOCATION`), read through ``files``: the
    local filesystem by default, or the Workspace API as the signed-in user."""

    def __init__(self, path: str, *, files: LibraryFiles | None = None) -> None:
        self.path = path.rstrip("/")
        self.files = files or LocalLibraryFiles()
        self.provider = FileLibraryProvider([self.path], files=self.files)

    def identity(self) -> str:
        return f"folder:{self.path}"

    def library(self, name: str, version: str | None) -> LibrarySource | None:
        try:
            return self.provider.resolve(LibraryRef(name, version))
        except LibraryResolutionError:
            return None

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        from .folder_content import folder_libraries

        for held in folder_libraries(self.files, self.path):
            if not held.measure_path:
                continue
            resource = json.loads(self.files.read_text(held.measure_path) or "{}")
            if resource.get("url") == url and (version is None or resource.get("version") == version):
                return resource
        return None

    def libraries(self) -> Iterable[LibraryRef]:
        return self.provider.search()


@dataclass(frozen=True, kw_only=True)
class FolderClosure:
    """What :meth:`DeltaKnowledge.publish` writes: library ``name`` ``version`` in ``folder``, with the folder
    includes that changed (`CLIENT-PUBLISH`). ``translate`` compiles each; ``base`` is the canonical base for a
    library whose folder has no ``Library`` URL."""
    folder: str
    name: str
    version: str
    files: LibraryFiles
    translate: Callable
    base: str


class DeltaKnowledge(KnowledgeStore):
    """CLIENT-KNOWLEDGE-PROVIDER: tagged knowledge, read through ``content`` (a
    :class:`~cql_databricks.data_bindings.ContentResolver`), in its catalog scopes, earliest scope first. With
    ``store``, ``(catalog, schema)``, it reads and publishes that one tagged store alone, through ``run``, as
    ``run``'s identity."""

    def __init__(self, content: Callable[[], Any], *, store: tuple[str, str] | None = None, run=None) -> None:
        if store is not None and run is None:
            raise ValueError("a knowledge store is written through a SQL runner: pass run=")
        self._content_factory = content
        self._content = None
        self.store = tuple(store) if store else None
        self._run = run

    @property
    def content(self):
        if self._content is None:
            self._content = self._content_factory()
        return self._content

    @property
    def provider(self) -> LibraryProvider:
        from .knowledge_libraries import KnowledgeLibraryProvider

        return KnowledgeLibraryProvider(lambda: self.content)

    def identity(self) -> str:
        return "knowledge:" + (".".join(self.store) if self.store else ",".join(str(s) for s in self.content.scopes))

    def library(self, name: str, version: str | None) -> LibrarySource | None:
        from .knowledge_libraries import _source

        corpus = self.content.corpus()
        found = corpus.library_in(self.store, name, version) if self.store else corpus.library(name, version)
        return _source(found) if found is not None else None

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        corpus = self.content.corpus()
        for lib in corpus.libraries:
            if lib.measure and lib.measure[0] == url and (version is None or lib.measure[1] == version):
                return corpus.measure_resource(lib.name, lib.version, store=self.store)
        return None

    def libraries(self) -> Iterable[LibraryRef]:
        seen, out = set(), []
        for lib in self.content.corpus().libraries:
            if self.store and (lib.knowledge.catalog, lib.knowledge.schema) != self.store:
                continue
            if (lib.name, lib.version) not in seen:
                seen.add((lib.name, lib.version))
                out.append(LibraryRef(lib.name, lib.version))
        return out

    def publish(self, closure: FolderClosure):
        """CLIENT-PUBLISH: write ``closure``'s library, and the folder includes that changed, into this store."""
        from .library_publish import publish_folder_library

        if self.store is None:
            raise ValueError("publish needs one store: DeltaKnowledge(content, store=(catalog, schema), run=...)")
        return publish_folder_library(self.content, closure.files, closure.folder, closure.name, closure.version,
                                      store=self.store, run=self._run, translate=closure.translate,
                                      base=closure.base)


def library_provider(source: KnowledgeSource) -> LibraryProvider:
    """CLIENT-KNOWLEDGE-PROVIDER: ``source`` as the client core's library provider, which walks the include
    closure. The shipped order (cells, folder, tagged knowledge) keeps its rules; any other source's includes
    resolve from it and the layers after it."""
    from .knowledge_libraries import FolderThenKnowledge

    layers = _layers(source)
    shipped = (len(layers) in (2, 3) and isinstance(layers[-1], DeltaKnowledge) and layers[-1].store is None
               and isinstance(layers[-2], LibraryFolder) and (len(layers) == 2 or isinstance(layers[0], SessionCells)))
    if shipped:
        chain = FolderThenKnowledge(layers[-2].provider, layers[-1].provider)
        if len(layers) == 2:
            return chain
        return layers[0].with_fallthrough(chain)
    if len(layers) == 2 and isinstance(layers[0], SessionCells) and isinstance(layers[1], DeltaKnowledge) \
            and layers[1].store is None:
        return layers[0].with_fallthrough(FolderThenKnowledge(None, layers[1].provider))
    if len(layers) == 1 and hasattr(layers[0], "provider"):
        return layers[0].provider
    return _Layered(layers)


def _layers(source: KnowledgeSource) -> list[KnowledgeSource]:
    if isinstance(source, Overlay):
        return _layers(source.dev) + _layers(source.base)
    return [source]


class _Layered(LibraryProvider):
    """Any chain of knowledge sources: a library comes from the first layer that holds it, and its includes
    from that layer and the ones after it."""

    def __init__(self, layers: list[KnowledgeSource]) -> None:
        self._layers = layers

    def _find(self, ref: LibraryRef, start: int = 0) -> tuple[int, LibrarySource]:
        for i in range(start, len(self._layers)):
            found = self._layers[i].library(ref.name, ref.version)
            if found is not None:
                return i, found
        raise LibraryResolutionError(f"cannot resolve CQL library '{ref}': no knowledge source holds it "
                                     f"({', '.join(layer.identity() for layer in self._layers[start:])})")

    def resolve(self, ref: LibraryRef) -> LibrarySource:
        return self._find(ref)[1]

    def closure(self, root: LibrarySource) -> list[LibrarySource]:
        start = next((i for i, layer in enumerate(self._layers)
                      if layer.library(root.name, root.version) is not None), 0)
        out: list[LibrarySource] = []
        seen = {root.name}
        queue = [(ref, start) for ref in includes_of(root)]
        while queue:
            ref, at = queue.pop(0)
            if ref.name in seen:
                continue
            seen.add(ref.name)
            layer, found = self._find(ref, at)
            out.append(found)
            queue.extend((inc, layer) for inc in includes_of(found))
        return out

    def search(self, pattern: str | None = None) -> list[LibraryRef]:
        seen, out = set(), []
        for layer in self._layers:
            for ref in layer.libraries():
                if (ref.name, ref.version) not in seen and (pattern is None or pattern.lower() in ref.name.lower()):
                    seen.add((ref.name, ref.version))
                    out.append(ref)
        return out
