"""CLIENT-PUBLISH — publish a library from a workspace folder into a tagged knowledge store, as the caller.

Authoring happens in folders (`DOC-AUTHOR-IN-FOLDERS`); the client, Jobs and the agent read tagged knowledge. This is
the one step between them, the same for a notebook, a Job and the Designer (`DEC-DESIGNER-PUBLISH`): each passes the
SQL runner and the compile it has, and the rows are written as whoever that runner is.

What is written: the library's ``Library`` resource, carrying its CQL and the ELM compiled from it (the folder's own
``Library`` JSON when it has one, else one built as New builds it), and, for a measure, its ``Measure``. Its
includes that resolve from the folder go with it, dependencies first; includes resolved from tagged knowledge are
already published and are left alone. A library is written only when it changed: the store has no row for its
``(url, version)``, or that row's CQL is not the folder's, line endings aside, or, for the measure, its ``Measure``
differs. So publishing a measure does not rewrite every shared library it includes.

Every library to be written compiles first, and if one does not, nothing is written (`PublishRefused`). Rows go
through the import's MERGE (`CONTENT-INCREMENTAL`), so a published ``(url, version)`` is replaced
(`DEC-PUBLISH-IMMUTABLE`). Writes to separate rows are not one transaction: a write that fails leaves the ones before
it written, and `PublishFailed` says which.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from cql_databricks.data_bindings import ContentResolver, SqlRunner
from cql_databricks.data_bindings.measure_import.knowledge_merge import SINGLE_ROW, knowledge_merge_sql

from .folder_content import folder_library_resource, folder_measure
from .libraries import FileLibraryProvider, LibraryFiles, LibraryRef, LibraryResolutionError
from .library_resource import cql_of, library_canonical, library_resource, with_content
from .resolve import Translate, TranslatedLibrary, resolve, translate_includes, translate_root

PUBLISHED, UNCHANGED, REFUSED, NOT_WRITTEN = "published", "unchanged", "refused", "not written"


@dataclass(frozen=True)
class PublishedLibrary:
    """What publishing did with one library. ``detail`` is the rows written, or why it was refused."""
    name: str
    version: str
    url: str
    outcome: str
    detail: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "url": self.url, "outcome": self.outcome,
                "detail": list(self.detail)}


@dataclass(frozen=True)
class PublishSummary:
    """Every library publishing considered, dependencies first, the one published last."""
    store: str
    libraries: tuple[PublishedLibrary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"store": self.store, "libraries": [lib.to_dict() for lib in self.libraries]}


class PublishRefused(RuntimeError):
    """Nothing was written. ``summary`` says, per library, why."""

    def __init__(self, message: str, summary: Optional[PublishSummary] = None) -> None:
        super().__init__(message)
        self.summary = summary


class PublishFailed(RuntimeError):
    """A write failed. ``summary`` says which libraries were written before it and which were not."""

    def __init__(self, message: str, summary: PublishSummary) -> None:
        super().__init__(message)
        self.summary = summary


@dataclass
class _Candidate:
    name: str
    version: str
    cql: str
    url: str
    library: Optional[dict[str, Any]]       # the folder's own ``Library`` JSON, if it has one
    measure: Optional[dict[str, Any]]       # the folder's ``Measure``, for the library published
    changed: bool = False


def publish_folder_library(content: ContentResolver, files: LibraryFiles, folder: str, name: str, version: str, *,
                           store: tuple[str, str], run: SqlRunner, translate: Translate, base: str,
                           lineage: str = "publish") -> PublishSummary:
    """Publish library ``name`` ``version`` from ``folder`` into the tagged knowledge ``store`` ``(catalog, schema)``.

    ``content`` resolves includes not in the folder from tagged knowledge; ``files`` reads the folder; ``run`` reads
    and writes the store, as its identity; ``translate`` compiles (`resolve.Translate`); ``base`` is the canonical
    base for a library whose folder holds no ``Library`` JSON with a URL."""
    folder = folder.rstrip("/")
    target = next((s for s in content.knowledge_sources() if (s.catalog, s.schema) == tuple(store)), None)
    if target is None:
        raise PublishRefused(f"{'.'.join(store)} is not a tagged knowledge store")
    where = f"{target.catalog}.{target.schema}"
    resolution = resolve(content, library=name, version=version, location=f"folder:{folder}", files=files)
    missing = resolution.unresolved_messages()
    if missing:
        raise PublishRefused("; ".join(missing))

    in_folder = FileLibraryProvider([folder], files=files)
    candidates: list[_Candidate] = []
    for inc in resolution.includes:
        try:
            held = in_folder.resolve(LibraryRef(inc.id.name, inc.id.version or None))
        except LibraryResolutionError:
            continue
        if held.cql == inc.source:
            candidates.append(_candidate(files, folder, inc.id.name, inc.id.version, inc.source, base, measure=False))
    candidates.append(_candidate(files, folder, resolution.name, resolution.version, resolution.source, base,
                                 measure=True))
    for c in candidates:
        c.changed = _changed(run, target, c)

    compiled = _compiled(translate, resolution, [c for c in candidates if c.changed])
    problems = {c.name: compiled[c.name].diagnostics or ("the compiler returned no ELM",)
                for c in candidates if c.changed and not (compiled[c.name].ok and compiled[c.name].elm)}
    if problems:
        summary = PublishSummary(where, tuple(
            PublishedLibrary(c.name, c.version, c.url, REFUSED if c.name in problems else NOT_WRITTEN,
                             tuple(problems.get(c.name, ()))) for c in candidates))
        raise PublishRefused(f"{', '.join(problems)} does not compile", summary)

    done: list[PublishedLibrary] = []
    for i, c in enumerate(candidates):
        if not c.changed:
            done.append(PublishedLibrary(c.name, c.version, c.url, UNCHANGED))
            continue
        try:
            rows = _write(run, target, c, compiled[c.name].elm or "", base, lineage)
        except Exception as e:  # noqa: BLE001 — reported with what was written before it, then raised
            rest = [PublishedLibrary(r.name, r.version, r.url, NOT_WRITTEN) for r in candidates[i + 1:]]
            failed = PublishedLibrary(c.name, c.version, c.url, REFUSED, (str(e),))
            summary = PublishSummary(where, (*done, failed, *rest))
            raise PublishFailed(f"{c.name} {c.version} was not published: {e}", summary) from e
        done.append(PublishedLibrary(c.name, c.version, c.url, PUBLISHED, tuple(rows)))
    return PublishSummary(where, tuple(done))


def _candidate(files: LibraryFiles, folder: str, name: str, version: str, cql: str, base: str, *,
               measure: bool) -> _Candidate:
    found = folder_library_resource(files, folder, name, version)
    library = found[1] if found else None
    url = (library or {}).get("url") or library_canonical(base, name)
    held = folder_measure(files, folder, name, version) if measure else None
    return _Candidate(name, version, cql, url, library, held[1] if held else None)


def _changed(run: SqlRunner, target, c: _Candidate) -> bool:
    """The store has no row for ``c``'s ``(url, version)``, or its CQL (or, for a measure, its ``Measure``) is not
    the folder's."""
    stored = _row(run, target.table("library"), c.url, c.version)
    if stored is None or _lines(cql_of(stored) or "") != _lines(c.cql):
        return True
    if c.measure is not None:
        return _row(run, target.table("measure"), _measure_key(c)[0], _measure_key(c)[1]) != c.measure
    return False


def _row(run: SqlRunner, table: str, url: str, version: str) -> Optional[dict[str, Any]]:
    rows = run(f"SELECT to_json(resource) AS resource FROM {table} WHERE url = :url AND version = :version",
               {"url": url, "version": version})
    return json.loads(rows[0]["resource"]) if rows else None


def _compiled(translate: Translate, resolution, changed: list[_Candidate]) -> dict[str, TranslatedLibrary]:
    """The ELM of each library to write: the root against its closure, an include against its own includes."""
    names = {c.name for c in changed}
    out: dict[str, TranslatedLibrary] = {}
    if names - {resolution.name}:
        out.update({t.name: t for t in translate_includes(translate, resolution) if t.name in names})
    if resolution.name in names:
        out[resolution.name] = translate_root(translate, resolution)
    return out


def _write(run: SqlRunner, target, c: _Candidate, elm: str, base: str, lineage: str) -> list[str]:
    library = (with_content(c.library, c.cql, elm) if c.library is not None
               else library_resource(c.name, c.version, c.cql, elm, base=base))
    rows = [_merge(run, target.table("library"), {**library, "url": c.url, "version": c.version}, lineage)]
    if c.measure is not None:
        rows.append(_merge(run, target.table("measure"), c.measure, lineage))
    return rows


def _merge(run: SqlRunner, table: str, resource: dict[str, Any], lineage: str) -> str:
    merged = run(knowledge_merge_sql(table, SINGLE_ROW), {
        "resource_json": json.dumps(resource), "source_package": None, "source_year": None,
        "source_lineage": lineage, "ingested_at": datetime.now(timezone.utc).isoformat()})
    if not merged or int(merged[0].get("num_affected_rows") or 0) != 1:
        raise RuntimeError(f"{table} {resource['url']}|{resource['version']} was not written")
    return f"{table} {resource['url']}|{resource['version']}"


def _measure_key(c: _Candidate) -> tuple[str, str]:
    m = c.measure or {}
    if not m.get("url") or not m.get("version"):
        raise PublishRefused(f"the Measure for {c.name} has no url and version to publish it under")
    return str(m["url"]), str(m["version"])


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
