"""RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — what a folder location holds, read the way a measure package lays it out.

An eCQI measure package keeps each library's CQL in ``cql/<Name>-<version>.cql`` and its FHIR resources in
``resources/``: ``measure-<Name>-<version>.json`` and ``library-<Name>-<version>.json``, each also as ``.xml``, plus a
bundle of all of them and an HTML rendering at the top. A folder location mirrors that layout. Its libraries are
whatever [[FileLibraryProvider]] finds (the client's folder rules); its ``Measure`` is the JSON resource naming the
library. A folder laid out flat — ``X.cql`` beside ``X.measure.json``, as ``examples/measures`` is — reads the same.

Nothing here is FHIR validation. A ``Measure`` is read as the package shipped it and handed on whole.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .libraries import FileLibraryProvider, LibraryFiles

#: Where a folder keeps its ``Measure`` resources: the package's ``resources/``, or flat beside the CQL.
MEASURE_GLOBS: Tuple[str, ...] = ("resources/measure-*.json", "measure-*.json", "*.measure.json")
#: Where a folder keeps its ``Library`` resources.
LIBRARY_GLOBS: Tuple[str, ...] = ("resources/library-*.json", "library-*.json", "Library-*.json")


@dataclass(frozen=True)
class FolderLibrary:
    """One library a folder holds: where its CQL is, and the ``Measure`` that names it, if any."""
    name: str
    version: str
    cql_path: str
    measure_path: Optional[str]
    title: str
    description: str

    @property
    def kind(self) -> str:
        return "measure" if self.measure_path else "library"


def folder_libraries(files: LibraryFiles, folder: str) -> List[FolderLibrary]:
    """Every library in ``folder``, in the order the client's folder rules find them."""
    provider = FileLibraryProvider([folder], files=files)
    measures = _resources(files, folder, MEASURE_GLOBS, "Measure")
    out: List[FolderLibrary] = []
    for ref in provider.search():
        source = provider.resolve(ref)
        found = _naming(measures, source.name, source.version)
        path, measure = found if found else (None, {})
        out.append(FolderLibrary(name=source.name, version=source.version or "", cql_path=source.origin or "",
                                 measure_path=path, title=str(measure.get("title") or ""),
                                 description=str(measure.get("description") or "")))
    return out


def folder_measure(files: LibraryFiles, folder: str, name: str, version: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The ``(path, Measure)`` in ``folder`` whose root library is ``name`` (``version`` preferred), or ``None``."""
    return _naming(_resources(files, folder, MEASURE_GLOBS, "Measure"), name, version)


def folder_library_resource(files: LibraryFiles, folder: str, name: str,
                            version: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The ``(path, Library)`` resource in ``folder`` for ``name@version``, or ``None``."""
    for path, resource in _resources(files, folder, LIBRARY_GLOBS, "Library"):
        if resource.get("name") == name and str(resource.get("version") or "") == version:
            return path, resource
    return None


def _resources(files: LibraryFiles, folder: str, globs: Tuple[str, ...],
               kind: str) -> List[Tuple[str, Dict[str, Any]]]:
    out, seen = [], set()
    for pattern in globs:
        for path in files.glob(folder, pattern):
            if path in seen:
                continue
            seen.add(path)
            text = files.read_text(path)
            try:
                resource = json.loads(text) if text else None
            except ValueError:
                continue
            if isinstance(resource, dict) and resource.get("resourceType") == kind:
                out.append((path, resource))
    return out


def _naming(measures: List[Tuple[str, Dict[str, Any]]], name: str,
            version: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The measure whose ``library`` canonical names ``name``; one at ``version`` wins over another version."""
    named = [(path, m) for path, m in measures if name in _library_names(m)]
    exact = [(path, m) for path, m in named if version and str(m.get("version") or "") == version]
    return (exact or named or [None])[0]


def _library_names(measure: Dict[str, Any]) -> List[str]:
    """The library names a ``Measure``'s ``library`` canonicals end in (``…/Library/<Name>[|version]``)."""
    names = []
    for canonical in measure.get("library") or []:
        if isinstance(canonical, str) and canonical:
            names.append(canonical.split("|", 1)[0].rstrip("/").rsplit("/", 1)[-1])
    return names
