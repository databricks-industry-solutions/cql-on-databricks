"""AGENT-DEFINITION-DISCOVERY, AGENT-VALUE-SET-LOOKUP — what definitions and value sets exist.

``list_definitions`` · ``get_definition`` · ``list_value_sets`` · ``value_sets_with_code``.

**THE MODEL MATCHES, THE TOOL LISTS.** A user asks about "the diabetes population"; which definition that
means is a semantic judgement over titles and descriptions, and the model makes it, then asks the user to
pick one or write a new one. So the catalog tools return compact entries and do no ranking of their own:
a keyword filter here would be a second, worse matcher in front of the one that reads the words.

**THE CQL IS THE DEFINITION.** ``get_definition`` returns the source as written, so an explanation is of
the logic that runs (`APP-CQL-AUTHORITATIVE`) and the user can check it (`AGENT-CQL-REVIEW`).

Collaborators are injected, as in ``data.py``: the corpus of tagged knowledge, and a terminology reader.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cql_databricks.compiler.folder_content import folder_measure
from cql_databricks.compiler.libraries import FileLibraryProvider, LibraryRef, LibraryResolutionError
from cql_databricks.compiler.location import Location
from cql_databricks.data_bindings import Corpus, parse_value_set_declarations
from cql_databricks.result.insights import parse_scoring

#: Supplies the corpus of the environment's knowledge (`AGENT-DEFAULT-ENVIRONMENT`).
CorpusSource = Callable[[], Corpus]

#: ``(code, system or None) -> [(valueset_id, system, code), ...]`` from the terminology view.
CodeLookup = Callable[[str, str | None], list[tuple[str, str, str]]]


def list_definitions(*, corpus: CorpusSource) -> list[dict[str, Any]]:
    """AGENT-DEFINITION-DISCOVERY: every library in the environment's knowledge, measures first."""
    entries = []
    for lib in corpus().libraries:
        entry: dict[str, Any] = {"library": lib.name, "version": lib.version, "kind": lib.kind,
                                 "location": f"store:{lib.knowledge.catalog}.{lib.knowledge.schema}"}
        if lib.title:
            entry["title"] = lib.title
        if lib.description:
            entry["description"] = lib.description
        entries.append(entry)
    return sorted(entries, key=lambda e: (e["kind"] != "measure", e["library"], e["version"]))


def get_definition(library: str, version: str, *, corpus: CorpusSource, location: str | None = None,
                   files=None) -> dict[str, Any] | None:
    """One library's CQL as written, with its measure's title, description and declared scoring, read from
    ``location`` when given (RUN-ADDRESS-LOCATION); a folder is read through ``files``.

    ``None`` when no library of that name and version is there, which is an answer, not a failure."""
    at = Location.parse(location) if location else None
    if at is not None and at.kind == "folder":
        return _folder_definition(library, version, at, files)
    source = corpus()
    store = at.catalog_schema if at else None
    lib = source.library_in(store, library, version) if store else source.library(library, version)
    if lib is None:
        return None
    out: dict[str, Any] = {"library": lib.name, "version": lib.version, "kind": lib.kind, "cql": lib.source}
    if lib.title:
        out["title"] = lib.title
    if lib.description:
        out["description"] = lib.description
    if lib.kind == "measure":
        scoring = parse_scoring(source.measure_resource(lib.name, lib.version, store=store))
        if scoring:
            out["scoring"] = scoring
    if at is not None:
        out["location"] = str(at)
    return out


def _folder_definition(library: str, version: str, at: Location, files) -> dict[str, Any] | None:
    """A folder library: its CQL from the folder, and its kind, title and scoring from the package's ``Measure``."""
    try:
        found = FileLibraryProvider([at.path], files=files).resolve(LibraryRef(library, version))
    except LibraryResolutionError:
        return None
    out: dict[str, Any] = {"library": found.name, "version": found.version or "", "kind": "library",
                           "cql": found.cql or "", "location": str(at)}
    measure = folder_measure(files, at.path, found.name, found.version or "")
    if measure is not None:
        resource = measure[1]
        out["kind"] = "measure"
        for key in ("title", "description"):
            if resource.get(key):
                out[key] = resource[key]
        scoring = parse_scoring(resource)
        if scoring:
            out["scoring"] = scoring
    return out


def folder_scoring(files, folder: str, library: str, version: str) -> str | None:
    """RUN-ADDRESS-LOCATION — the declared scoring of a folder measure, or None."""
    measure = folder_measure(files, folder, library, version)
    return parse_scoring(measure[1]) if measure else None


def list_value_sets(*, corpus: CorpusSource) -> list[dict[str, Any]]:
    """Every value set the libraries in scope declare: its URL, the names authors gave it, and who uses it."""
    found: dict[str, dict[str, Any]] = {}
    for lib in corpus().libraries:
        for name, url in parse_value_set_declarations(lib.source):
            entry = found.setdefault(url, {"url": url, "names": [], "libraries": []})
            if name not in entry["names"]:
                entry["names"].append(name)
            used = f"{lib.name}|{lib.version}"
            if used not in entry["libraries"]:
                entry["libraries"].append(used)
    return sorted(found.values(), key=lambda e: e["url"])


def value_sets_with_code(code: str, system: str | None = None, *, lookup: CodeLookup,
                         corpus: CorpusSource) -> list[dict[str, Any]]:
    """Every value set in the terminology that contains ``code`` (in ``system``, when given).

    Each carries the names libraries in scope declare it under, and those libraries; a value set no
    library declares has none, which is a true answer about the corpus."""
    if not code.strip():
        raise ValueError("value_sets_with_code needs a code")
    declared = {entry["url"]: entry for entry in list_value_sets(corpus=corpus)}
    hits: dict[str, dict[str, Any]] = {}
    for valueset_id, hit_system, hit_code in lookup(code.strip(), system.strip() if system else None):
        entry = hits.setdefault(valueset_id, {"url": valueset_id, "matches": [],
                                              "names": declared.get(valueset_id, {}).get("names", []),
                                              "libraries": declared.get(valueset_id, {}).get("libraries", [])})
        match = {"system": hit_system, "code": hit_code}
        if match not in entry["matches"]:
            entry["matches"].append(match)
    return sorted(hits.values(), key=lambda e: e["url"])
