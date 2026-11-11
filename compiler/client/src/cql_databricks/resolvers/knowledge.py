"""CLIENT-KNOWLEDGE-RESOLVER: the knowledge resolvers the client ships, and the one closure walk over them.

| Resolver | Holds |
|---|---|
| :class:`CellsKnowledgeResolver` | the libraries ``%%cql`` cells declared in this Spark session (`SVC-NOTEBOOK-CELL-LIBRARY`) |
| :class:`FolderKnowledgeResolver` | every library in a ``/Workspace`` or ``/Volumes`` folder, at any depth (`RUN-ADDRESS-LOCATION`) |
| :class:`TableKnowledgeResolver` | an environment's ``knowledge`` tables (`CLIENT-ENVIRONMENT`) |

A resolver only answers lookups. The include closure is walked in one place, :func:`library_provider`, which turns a
resolver, or an ``Overlay`` chain of them (`CLIENT-RESOLVER-OVERLAY`), into the client core's
:class:`~.libraries.LibraryProvider`. The order of ``SVC-NOTEBOOK-LIBRARY-FOLDER`` is the chain
``Overlay(CellsKnowledgeResolver, Overlay(FolderKnowledgeResolver, TableKnowledgeResolver))``: a folder library's
includes resolve from its folder, sibling-first, then from the environment's knowledge; a library from the
environment's knowledge resolves its includes there alone (`CLIENT-KNOWLEDGE-INCLUDES`). Any other resolver's includes
resolve from it and the layers after it.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from cql_databricks.compiler.libraries import (FileLibraryProvider, LibraryFiles, LibraryProvider, LibraryRef,
                                               LibraryResolutionError, LibrarySource, LocalLibraryFiles, includes_of)
from .interfaces import IncludeSite, KnowledgeResolver, Overlay


class CellsKnowledgeResolver(KnowledgeResolver):
    """CLIENT-KNOWLEDGE-RESOLVER: the libraries the ``%%cql`` cells of ``spark``'s session declared (the active
    session when None), kept in the session record (`CLIENT-SESSION-STATE`). ``cells`` replaces the record with a
    plain mapping, for a caller with no Spark session."""

    def __init__(self, spark=None, *, cells: dict | None = None) -> None:
        from cql_databricks.compiler.session_libraries import CellStore, SessionRecord, SessionLibraryProvider

        self._store = cells if cells is not None else CellStore(SessionRecord(spark))
        self.provider = SessionLibraryProvider(None, cells=self._store)

    def with_fallthrough(self, fallthrough: LibraryProvider | None) -> LibraryProvider:
        """These cells ahead of ``fallthrough``, sharing one registry."""
        from cql_databricks.compiler.session_libraries import SessionLibraryProvider

        return SessionLibraryProvider(fallthrough, cells=self._store)

    def identity(self) -> str:
        return "cells"

    def library(self, name: str, version: str | None, site: IncludeSite | None = None) -> LibrarySource | None:
        source = self.provider.source(name)
        return source if source is not None and (version is None or source.version == version) else None

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        return None

    def libraries(self) -> Iterable[LibraryRef]:
        return self.provider.declared()


class FolderKnowledgeResolver(KnowledgeResolver):
    """CLIENT-KNOWLEDGE-RESOLVER: every library in a folder (`RUN-ADDRESS-LOCATION`), read through ``files``: the
    local filesystem by default, or the Workspace API as the signed-in user."""

    def __init__(self, path: str, *, files: LibraryFiles | None = None) -> None:
        self.path = path.rstrip("/")
        self.files = files or LocalLibraryFiles()
        self.provider = FileLibraryProvider([self.path], files=self.files)

    def identity(self) -> str:
        return f"folder:{self.path}"

    def library(self, name: str, version: str | None, site: IncludeSite | None = None) -> LibrarySource | None:
        try:
            return self.provider.resolve(LibraryRef(name, version))
        except LibraryResolutionError:
            return None

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        from cql_databricks.compiler.folder_content import folder_libraries

        for held in folder_libraries(self.files, self.path):
            if not held.measure_path:
                continue
            resource = json.loads(self.files.read_text(held.measure_path) or "{}")
            if resource.get("url") == url and (version is None or resource.get("version") == version):
                return resource
        return None

    def libraries(self) -> Iterable[LibraryRef]:
        return self.provider.search()


class TableKnowledgeResolver(KnowledgeResolver):
    """CLIENT-KNOWLEDGE-RESOLVER: an environment's knowledge, read through ``content`` (a
    :class:`~cql_databricks.data_bindings.ContentResolver` over the environment, `CLIENT-ENVIRONMENT`). It only reads
    (`DEC-NO-CONTENT-PUBLISHING`)."""

    def __init__(self, content: Callable[[], Any]) -> None:
        self._content_factory = content
        self._content = None

    @property
    def content(self):
        if self._content is None:
            self._content = self._content_factory()
        return self._content

    @property
    def provider(self) -> LibraryProvider:
        from cql_databricks.compiler.knowledge_libraries import KnowledgeLibraryProvider

        return KnowledgeLibraryProvider(lambda: self.content)

    def identity(self) -> str:
        return f"knowledge:{self.content.catalog}"

    def library(self, name: str, version: str | None, site: IncludeSite | None = None) -> LibrarySource | None:
        from cql_databricks.compiler.knowledge_libraries import _source

        found = self.content.corpus().library(name, version)
        return _source(found) if found is not None else None

    def measure(self, url: str, version: str | None) -> Mapping[str, Any] | None:
        corpus = self.content.corpus()
        for lib in corpus.libraries:
            if lib.measure and lib.measure[0] == url and (version is None or lib.measure[1] == version):
                return corpus.measure_resource(lib.name, lib.version)
        return None

    def libraries(self) -> Iterable[LibraryRef]:
        seen, out = set(), []
        for lib in self.content.corpus().libraries:
            if (lib.name, lib.version) not in seen:
                seen.add((lib.name, lib.version))
                out.append(LibraryRef(lib.name, lib.version))
        return out


def library_provider(source: KnowledgeResolver) -> LibraryProvider:
    """CLIENT-KNOWLEDGE-RESOLVER: ``source`` as the client core's library provider, which walks the include
    closure. The shipped order (cells, folder, the environment's knowledge) keeps its rules; any other resolver's
    includes resolve from it and the layers after it."""
    from cql_databricks.compiler.knowledge_libraries import FolderThenKnowledge

    layers = _layers(source)
    shipped = (len(layers) in (2, 3) and isinstance(layers[-1], TableKnowledgeResolver)
               and isinstance(layers[-2], FolderKnowledgeResolver)
               and (len(layers) == 2 or isinstance(layers[0], CellsKnowledgeResolver)))
    if shipped:
        chain = FolderThenKnowledge(layers[-2].provider, layers[-1].provider)
        if len(layers) == 2:
            return chain
        return layers[0].with_fallthrough(chain)
    if len(layers) == 2 and isinstance(layers[0], CellsKnowledgeResolver) \
            and isinstance(layers[1], TableKnowledgeResolver):
        return layers[0].with_fallthrough(FolderThenKnowledge(None, layers[1].provider))
    if len(layers) == 1 and hasattr(layers[0], "provider"):
        return layers[0].provider
    return _Layered(layers)


def _layers(source: KnowledgeResolver) -> list[KnowledgeResolver]:
    if isinstance(source, Overlay):
        return _layers(source.dev) + _layers(source.base)
    return [source]


class _Layered(LibraryProvider):
    """Any chain of knowledge sources: a library comes from the first layer that holds it, and its includes
    from that layer and the ones after it."""

    def __init__(self, layers: list[KnowledgeResolver]) -> None:
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
