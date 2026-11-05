"""CONTENT-IMPORT-MEASURES — import quality measures into a folder of the caller's own. The one import path.

The caller hands over FHIR resources it has already read: ``Measure``, ``Library`` and ``ValueSet`` resources, or
``Bundle``s of them, plus any value-set codes it holds as rows. :func:`import_measures` writes them as files into a
``/Volumes`` or ``/Workspace`` folder the caller names and may write, laid out the way the folder resolvers read a
folder (`RUN-ADDRESS-LOCATION`, `CLIENT-KNOWLEDGE-RESOLVER`, `CLIENT-TERMINOLOGY-RESOLVER`):

- ``libraries/<Name>.cql`` — each library's CQL;
- ``resources/library-<Name>-<version>.json`` and ``resources/measure-<Name>-<version>.json`` — each ``Library``, and
  the ``Measure`` whose primary library is ``<Name>``, as given, where a measure package keeps them;
- ``valuesets/<name>[-<version>].json`` — one FHIR ``ValueSet`` per value set and version, its codes as its
  ``expansion``.

It writes nothing else and never an environment's ``knowledge``, which only the deploy and the implementer write
(`DEC-NO-CONTENT-PUBLISHING`). Where the content comes from is the caller's business: nothing here fetches anything.
The skill ``import-quality-measures`` uses it (``CONTENT-IMPORT-SKILL``).

Everything is checked before anything is written, and every problem is reported at once (:func:`check_import`, which
the deploy's loader of the example tables shares):

- a ``Measure`` must name a ``Library`` that is in this import or already in the folder
  (``CONTENT-MEASURE-LIBRARY-LINK``);
- every value set a library declares must have codes, in this import or already in the folder. A measure run against
  a missing value set fails (``LANG-VALUESET-VERSION``);
- a ``ValueSet`` resource must carry its codes: an ``expansion``, or a ``compose`` that only lists concepts. One
  defined by filters or by other value sets has to be expanded first.

A file the import writes replaces the file of the same library or value set and version. A library file in the folder
whose CQL declares another version is refused, never overwritten.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .transform import (_CQL_HEADER_RE, TransformResult, ValueSetExpansionRow, _decode_content,
                        transform_resources)

log = logging.getLogger("cql_databricks.data_bindings.measure_import.measures")

#: A CQL value-set declaration: ``valueset "Name": 'url'`` (CQL 1.5, section 5.4).
_CQL_VALUESET_RE = re.compile(r"""^\s*(?:public\s+|private\s+)?valueset\s+"[^"]*"\s*:\s*'([^']+)'""", re.M)

#: How many names an error lists before it says how many more there are.
_LISTED = 20

#: The folder's subfolders (see the module docstring).
LIBRARIES, RESOURCES, VALUESETS = "libraries", "resources", "valuesets"


class ImportContentError(ValueError):
    """The content cannot be imported as given. Nothing was written."""


@dataclass(frozen=True)
class CheckedImport:
    """Content :func:`check_import` accepted: its rows, and the ``ValueSet`` resources it was given by url|version."""
    content: TransformResult
    valuesets: list[str]                                   # each value set given codes
    skipped: dict[str, int] = field(default_factory=dict)  # resourceType -> resources not imported
    given: dict[tuple[str, str | None], dict] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportSummary:
    """What an import wrote, or would write on a dry run."""
    folder: str                        # the folder written
    measures: list[str]                # canonical url|version of each Measure
    libraries: list[str]               # canonical url|version of each Library
    valuesets: list[str]               # each value set given codes
    skipped: dict[str, int] = field(default_factory=dict)  # resourceType -> resources not imported
    files: list[str] = field(default_factory=list)         # every file written, in the folder; empty on a dry run


def flatten(resources: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The resources, with every ``Bundle`` replaced by its entries' resources, at any depth."""
    out: list[dict] = []
    for resource in resources:
        if resource.get("resourceType") == "Bundle":
            out.extend(flatten(entry["resource"] for entry in resource.get("entry") or [] if "resource" in entry))
        else:
            out.append(dict(resource))
    return out


def valueset_codes(valueset: Mapping[str, Any], *, source: str, now: str) -> list[ValueSetExpansionRow]:
    """The codes of one ``ValueSet`` resource: its ``expansion``, else a ``compose`` that only lists concepts."""
    url = valueset.get("url")
    if not url:
        raise ImportContentError(f"ValueSet {valueset.get('id', '(no id)')} has no url")
    # LANG-VALUESET-VERSION: the version a CQL declaration pins is the ValueSet's own, never its expansion's timestamp.
    version = valueset.get("version")
    codes: list[tuple[str, str, str | None]] = []

    def walk(contains: list[dict]) -> None:
        for c in contains:
            if c.get("system") and c.get("code") and not c.get("abstract"):
                codes.append((c["system"], c["code"], c.get("display")))
            walk(c.get("contains") or [])

    if valueset.get("expansion"):
        walk(valueset["expansion"].get("contains") or [])
    else:
        compose = valueset.get("compose") or {}
        for include in compose.get("include") or []:
            if include.get("filter") or include.get("valueSet") or not include.get("concept"):
                raise ImportContentError(
                    f"ValueSet {url} is defined by filters or other value sets and carries no expansion: "
                    "expand it first, then import the expanded ValueSet or its codes")
            codes.extend((include["system"], c["code"], c.get("display")) for c in include["concept"])
        if compose.get("exclude"):
            raise ImportContentError(f"ValueSet {url} excludes codes and carries no expansion: expand it first")
    if not codes:
        raise ImportContentError(f"ValueSet {url} has no codes")
    return [ValueSetExpansionRow(valueset_id=url, system=s, code=c, display=d, version=version, source_url=url,
                                 source_package=source, ingested_at=now) for s, c, d in codes]


def declared_valuesets(library: Mapping[str, Any]) -> set[str]:
    """The value sets one ``Library`` declares: in its CQL, its data requirements and its dependencies."""
    urls = set(_CQL_VALUESET_RE.findall(_decode_content(dict(library), "text/cql") or ""))
    for requirement in library.get("dataRequirement") or []:
        for code_filter in requirement.get("codeFilter") or []:
            if code_filter.get("valueSet"):
                urls.add(code_filter["valueSet"])
    for artifact in library.get("relatedArtifact") or []:
        ref = artifact.get("resource") or ""
        if artifact.get("type") == "depends-on" and "/ValueSet/" in ref:
            urls.add(ref)
    return {url.split("|", 1)[0] for url in urls}


def _listed(names: Iterable[str]) -> str:
    names = sorted(names)
    more = f", and {len(names) - _LISTED} more" if len(names) > _LISTED else ""
    return ", ".join(names[:_LISTED]) + more


def check_import(resources: Iterable[Mapping[str, Any]], *, codes: Iterable[Mapping[str, Any]] = (),
                 source: str = "import", lineage: str = "import", known_libraries: Iterable[str] = (),
                 known_valuesets: Iterable[str] = (), where: str) -> CheckedImport:
    """CONTENT-IMPORT-MEASURES — read and check what an import would write (see the module docstring), writing
    nothing. ``known_libraries`` and ``known_valuesets`` are the canonical URLs ``where`` already holds, so a
    ``Measure`` may name a library, and a library declare a value set, held there. ``source`` and ``lineage`` are
    the provenance the rows carry. Raises :class:`ImportContentError` listing every problem."""
    now = datetime.now(timezone.utc).isoformat()
    flat = flatten(resources)

    skipped: dict[str, int] = {}
    for r in flat:
        if r.get("resourceType") not in ("Measure", "Library", "ValueSet"):
            skipped[str(r.get("resourceType"))] = skipped.get(str(r.get("resourceType")), 0) + 1
    knowledge = transform_resources([r for r in flat if r.get("resourceType") in ("Measure", "Library")],
                                    source_package=source, source_year=0, source_lineage=lineage)
    not_cql = sum(r.get("resourceType") == "Library" for r in flat) - len(knowledge.libraries)
    if not_cql:
        skipped["Library (no CQL or ELM content)"] = not_cql
    no_library = sum(r.get("resourceType") == "Measure" for r in flat) - len(knowledge.measures)
    if no_library:
        skipped["Measure (names no Library)"] = no_library

    problems: list[str] = []
    expansions: list[ValueSetExpansionRow] = []
    given: dict[tuple[str, str | None], dict] = {}
    for vs in (r for r in flat if r.get("resourceType") == "ValueSet"):
        try:
            expansions.extend(valueset_codes(vs, source=source, now=now))
            given[(vs["url"], vs.get("version"))] = vs
        except ImportContentError as e:
            problems.append(str(e))
    for i, row in enumerate(codes):
        missing = [k for k in ("valueset_id", "system", "code") if not row.get(k)]
        if missing:
            problems.append(f"code row {i} has no {', '.join(missing)}")
            continue
        expansions.append(ValueSetExpansionRow(
            valueset_id=row["valueset_id"], system=row["system"], code=row["code"], display=row.get("display"),
            version=row.get("version"), source_url=row["valueset_id"], source_package=source, ingested_at=now))
    if not (knowledge.measures or knowledge.libraries or expansions or problems):
        raise ImportContentError("nothing to import: no Measure, no Library with CQL or ELM, and no value-set codes")

    libraries = {r.url for r in knowledge.libraries} | set(known_libraries)
    for m in knowledge.measures:
        unknown = [ref for ref in json.loads(m.resource_json).get("library") or []
                   if ref.split("|", 1)[0] not in libraries]
        if unknown:
            problems.append(f"Measure {m.url}|{m.version} names {_listed(unknown)}, which is not in this import "
                            f"or in {where}")
    declared = set()
    for lib in knowledge.libraries:
        declared |= declared_valuesets(json.loads(lib.resource_json))
    loaded = {row.valueset_id.split("|", 1)[0] for row in expansions}
    without_codes = declared - loaded - set(known_valuesets)
    if without_codes:
        problems.append(f"{len(without_codes)} value set(s) the libraries declare have no codes in this import or "
                        f"in {where}: {_listed(without_codes)}")
    if problems:
        raise ImportContentError("cannot import:\n- " + "\n- ".join(problems))
    return CheckedImport(content=TransformResult(measures=knowledge.measures, libraries=knowledge.libraries,
                                                 valueset_expansions=expansions),
                         valuesets=sorted(loaded), skipped=skipped, given=given)


def import_measures(resources: Iterable[Mapping[str, Any]], *, folder: str, codes: Iterable[Mapping[str, Any]] = (),
                    dry_run: bool = False) -> ImportSummary:
    """CONTENT-IMPORT-MEASURES — write measures, libraries and value-set codes as files into ``folder``, a
    ``/Volumes`` or ``/Workspace`` folder of the caller's own, read and written as this process's user (see the module
    docstring for the layout).

    ``resources`` are FHIR resources (``Measure``, ``Library``, ``ValueSet``, or ``Bundle``s of them); any other
    type is counted in ``skipped`` and not imported. ``codes`` are value-set codes already in rows:
    ``{valueset_id, system, code}``, with ``display`` and ``version`` optional. ``dry_run`` checks everything and
    writes nothing. The folder is then read with ``FolderKnowledgeResolver(folder)`` and
    ``FolderTerminologyResolver(f"{folder}/valuesets")``.
    """
    if not folder:
        raise ImportContentError("no folder: an import writes files into a folder of your own")
    root = Path(folder)
    checked = check_import(resources, codes=codes, known_libraries=_folder_libraries(root),
                           known_valuesets=_folder_valuesets(root), where=folder)
    files = _files(checked, root)
    summary = dict(folder=folder,
                   measures=[f"{m.url}|{m.version}" for m in checked.content.measures],
                   libraries=[f"{lib.url}|{lib.version}" for lib in checked.content.libraries],
                   valuesets=checked.valuesets, skipped=checked.skipped)
    if dry_run:
        return ImportSummary(**summary)
    for path, body in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    log.info("imported into %s: %d files", folder, len(files))
    return ImportSummary(**summary, files=sorted(str(p) for p in files))


def _files(checked: CheckedImport, root: Path) -> dict[Path, str]:
    """Every file the import writes under ``root``, path to text. Refuses a library file holding another version and
    two measures of one library."""
    out: dict[Path, str] = {}
    problems: list[str] = []
    names: dict[str, str] = {}
    by_url: dict[str, str] = {}
    for lib in checked.content.libraries:
        resource = json.loads(lib.resource_json)
        cql = _decode_content(resource, "text/cql")
        header = _CQL_HEADER_RE.search(cql or "")
        if header is None:
            problems.append(f"Library {lib.url}|{lib.version} carries no CQL with a library header to write")
            continue
        name, version = header.group(1).strip('"'), header.group(2)
        cql_path = root / LIBRARIES / f"{name}.cql"
        held = _CQL_HEADER_RE.search(cql_path.read_text(encoding="utf-8")) if cql_path.is_file() else None
        if held is not None and held.group(2) != version:
            problems.append(f"{cql_path} holds {name} version {held.group(2)}; this import has version {version}")
        if names.get(name, version) != version:
            problems.append(f"this import has two versions of library {name}: {names[name]} and {version}")
        names[name] = version
        by_url[lib.url] = name
        out[cql_path] = cql
        out[root / RESOURCES / f"library-{name}-{lib.version}.json"] = json.dumps(resource, indent=1) + "\n"
    for m in checked.content.measures:
        resource = json.loads(m.resource_json)
        primary = (resource.get("library") or [""])[0].split("|", 1)[0]
        name = by_url.get(primary) or primary.rstrip("/").rsplit("/", 1)[-1]
        path = root / RESOURCES / f"measure-{name}-{m.version}.json"
        if path in out:
            problems.append(f"this import has two measures of library {name} at version {m.version}")
        out[path] = json.dumps(resource, indent=1) + "\n"
    rows: dict[tuple[str, str | None], list[ValueSetExpansionRow]] = {}
    for row in checked.content.valueset_expansions:
        rows.setdefault((row.valueset_id, row.version), []).append(row)
    for (url, version), codes in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rstrip("/").rsplit("/", 1)[-1])
        resource = dict(checked.given.get((url, version)) or {"resourceType": "ValueSet", "url": url})
        if version is not None:
            resource["version"] = version
        resource["expansion"] = {"contains": [
            {"system": r.system, "code": r.code, **({"display": r.display} if r.display else {})} for r in codes]}
        name = f"{stem}-{version}.json" if version else f"{stem}.json"
        out[root / VALUESETS / name] = json.dumps(resource, indent=1) + "\n"
    if problems:
        raise ImportContentError("cannot import:\n- " + "\n- ".join(problems))
    return out


def _folder_libraries(root: Path) -> set[str]:
    """The canonical URLs of the ``Library`` resources the folder already holds."""
    return {r["url"] for r in _resources(root / RESOURCES, "Library") if r.get("url")}


def _folder_valuesets(root: Path) -> set[str]:
    """The canonical URLs of the ``ValueSet`` resources the folder already holds."""
    return {r["url"] for r in _resources(root / VALUESETS, "ValueSet") if r.get("url")}


def _resources(folder: Path, kind: str) -> list[dict]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            resource = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(resource, dict) and resource.get("resourceType") == kind:
            out.append(resource)
    return out
