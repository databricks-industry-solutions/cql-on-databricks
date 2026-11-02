"""DOC-OPEN-SAVE, DOC-AUTO-SAVE — write a library back to the workspace folder it was opened from, as the signed-in user.
Every edit is written, whether or not it compiles. Folders are the only place the Designer edits: a tagged store is
read-only here (`DOC-EDITABLE-IN-SCOPE`), so nothing the Designer edits reaches tagged knowledge.

A folder mirrors a measure package (`folder_content.py`), which holds one library's logic several times: the
``.cql`` file, the ``Library`` resource's encoded CQL and compiled ELM, the bundle, an XML copy of each resource and
an HTML rendering. A save rewrites the ``.cql`` and every JSON copy, with ELM compiled from the saved CQL, or with no
ELM when it does not compile, so no ELM in the folder disagrees with the CQL beside it. The XML copies and the HTML cannot be regenerated here; the first save that would
leave them out of date names them and asks, and they are deleted only when the caller confirms each one.

A save never creates a library, and refuses when the library changed since it was opened or last saved: the caller
sends the digest of the CQL it last knew to be there, and the save compares it with the CQL there now.

New and Save As create one in a folder (`create_in_folder`): its CQL, a ``Library`` resource and, for a measure, a
``Measure``. Each gets a canonical URL under the deployment's one base (`config.canonical_base`). A create writes
nothing over anything: a folder that already holds one of its files refuses it.

A tagged store changes only by publishing (`CLIENT-PUBLISH`, `DOC-PUBLISH`), which the client does.
The ``Library`` resource every write carries is the client's (`library_resource.py`).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from cql_databricks.compiler.folder_content import folder_library_resource, folder_measure
from cql_databricks.compiler.libraries import FileLibraryProvider, LibraryFiles, LibraryRef, LibraryResolutionError
from cql_databricks.compiler.library_resource import library_canonical, library_resource, with_content


class SaveConflict(Exception):
    """The library changed since it was opened. Nothing was written."""


class StaleCopies(Exception):
    """Files this save would leave out of date, which only the caller may agree to delete. Nothing was written."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"{len(paths)} file(s) would be left out of date")
        self.paths = paths


class SaveRefused(Exception):
    """A save this location cannot take. Nothing was written."""


@dataclass
class SaveRequest:
    name: str
    version: str
    cql: str
    #: The digest of the CQL as it was opened (`cql_sha256`); None for a library that was never saved.
    base_sha256: str | None
    #: The edited ``Measure`` resource, whole, when the Overview was edited; None leaves it as it is.
    measure: dict[str, Any] | None = None
    #: Out-of-date copies the caller agreed to delete (`StaleCopies.paths`).
    delete_stale: list[str] = field(default_factory=list)


def cql_sha256(text: str) -> str:
    """The digest a save compares: the CQL's UTF-8 bytes, line endings as written."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FolderWriter(Protocol):
    def write_text(self, path: str, text: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def mkdirs(self, path: str) -> None: ...


class WorkspaceFolderWriter:
    """Writes workspace files through the Workspace API, as whichever identity ``client`` is."""

    def __init__(self, client) -> None:
        self._workspace = client.workspace

    def write_text(self, path: str, text: str) -> None:
        from databricks.sdk.service.workspace import ImportFormat
        self._workspace.import_(path, content=base64.b64encode(text.encode("utf-8")).decode("ascii"),
                                format=ImportFormat.AUTO, overwrite=True)

    def delete(self, path: str) -> None:
        self._workspace.delete(path)

    def mkdirs(self, path: str) -> None:
        self._workspace.mkdirs(path)


def save_to_folder(req: SaveRequest, folder: str, *, files: LibraryFiles, writer: FolderWriter,
                   elm: str | None) -> dict[str, Any]:
    """Save ``req`` into the package folder ``folder``. ``elm`` is the saved CQL, compiled; None when it does not
    compile."""
    folder = folder.rstrip("/")
    try:
        existing = FileLibraryProvider([folder], files=files).resolve(LibraryRef(req.name, req.version))
    except LibraryResolutionError:
        existing = None
    if existing is not None:
        if req.base_sha256 is None or cql_sha256(existing.cql or "") != req.base_sha256:
            raise SaveConflict(f"{existing.origin} changed since it was opened or last saved")
        cql_path = existing.origin or ""
    elif req.base_sha256 is not None:
        raise SaveConflict(f"{req.name} {req.version} is no longer in {folder}")
    else:
        cql_path = f"{folder}/cql/{req.name}-{req.version}.cql"

    rewrites: dict[str, str] = {cql_path: req.cql}
    library = folder_library_resource(files, folder, req.name, req.version)
    updated_library = with_content(library[1], req.cql, elm) if library else None
    if library:
        rewrites[library[0]] = _json(updated_library)
    if req.measure is not None:
        measure = folder_measure(files, folder, req.name, req.version)
        rewrites[measure[0] if measure else f"{folder}/resources/measure-{req.name}-{req.version}.json"] = (
            _json(req.measure))
    present = set(files.glob(folder, "**/*"))
    for path in sorted(p for p in present if p.endswith(".json") and "/" not in p[len(folder) + 1:]):
        bundle = _bundle_with(files.read_text(path), req, updated_library, elm)
        if bundle is not None:
            rewrites[path] = _json(bundle)

    stale = sorted({path[:-len(".json")] + ext for path in rewrites if path.endswith(".json")
                    for ext in (".xml", ".html")} & present)
    unconfirmed = [p for p in stale if p not in req.delete_stale]
    if unconfirmed:
        raise StaleCopies(unconfirmed)

    if cql_path.rsplit("/", 1)[0] not in {p.rsplit("/", 1)[0] for p in present}:
        writer.mkdirs(cql_path.rsplit("/", 1)[0])
    for path, text in rewrites.items():
        writer.write_text(path, text)
    for path in stale:
        writer.delete(path)
    return {"location": f"folder:{folder}", "saved": sorted(rewrites), "deleted": stale,
            "sha256": cql_sha256(req.cql)}


# ── New and Save As ───────────────────────────────────────────────────────────────────────────────

#: What a new library's name and version must be: each becomes part of a file name and of a canonical URL.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")

_PROPORTION = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/measure-scoring", "code": "proportion"}]}
_STARTING_POPULATIONS = (("initial-population", "Initial Population"), ("denominator", "Denominator"),
                         ("numerator", "Numerator"))


@dataclass
class NewLibrary:
    """A library to create. ``cql`` already declares ``name`` and ``version``; ``measure`` is its whole ``Measure``
    resource, or None for a library that is not a measure."""
    name: str
    version: str
    cql: str
    measure: dict[str, Any] | None
    #: The ``canonical|version`` of the library this one was saved as a copy of (`APP-DERIVE-MEASURE`).
    derived_from: str | None = None


def check_identity(name: str, version: str) -> None:
    """Refuse a name or version that cannot be a file name and a canonical URL segment."""
    if not _NAME.fullmatch(name):
        raise SaveRefused(f"{name!r} is not a library name: a letter, then letters, digits or _")
    if not _VERSION.fullmatch(version):
        raise SaveRefused(f"{version!r} is not a version: letters, digits, '.', '_' or '-'")


def starting_cql(name: str, version: str, *, measure: bool) -> str:
    """The CQL a New library starts from: QI-Core and FHIRHelpers, as the example measures use, and for a measure
    the three populations its starting ``Measure`` names, each ``true`` until the author writes them."""
    cql = (f"library {name} version '{version}'\n\nusing QICore version '6.0.0'\n\n"
           "include FHIRHelpers version '4.0.1' called FHIRHelpers\n\ncontext Patient\n")
    if measure:
        cql += "".join(f'\ndefine "{expression}":\n  true\n' for _, expression in _STARTING_POPULATIONS)
    return cql


def starting_measure(name: str, version: str, *, base: str) -> dict[str, Any]:
    """The ``Measure`` a New measure starts with: its identity, its library, and one proportion group over the
    starting populations (`starting_cql`)."""
    population = [{"code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/measure-population",
                                        "code": code}]},
                   "criteria": {"language": "text/cql-identifier", "expression": expression}}
                  for code, expression in _STARTING_POPULATIONS]
    return {"resourceType": "Measure", "id": name, "url": f"{base}/Measure/{name}", "version": version, "name": name,
            "title": name, "status": "draft", "library": [f"{library_canonical(base, name)}|{version}"],
            "scoring": _PROPORTION, "group": [{"population": population}]}


def measure_copy(source: dict[str, Any], name: str, version: str, *, base: str) -> dict[str, Any]:
    """``source`` saved as a copy (`APP-DERIVE-MEASURE`): a draft under this deployment's base and the new name and
    version, naming the new library, linked back to ``source``. The original's business identifiers and its
    rendered narrative belong to the original and are left out; everything else is kept."""
    out = {k: v for k, v in copy.deepcopy(source).items() if k not in ("identifier", "text", "meta")}
    out.update({"id": name, "url": f"{base}/Measure/{name}", "version": version, "name": name, "status": "draft",
                "library": [f"{library_canonical(base, name)}|{version}"]})
    if source.get("url"):
        original = f"{source['url']}|{source['version']}" if source.get("version") else source["url"]
        out["relatedArtifact"] = [*(out.get("relatedArtifact") or []),
                                  {"type": "derived-from", "resource": original}]
    return out


def create_in_folder(new: NewLibrary, folder: str, *, files: LibraryFiles, writer: FolderWriter, elm: str | None,
                     base: str) -> dict[str, Any]:
    """Create ``new`` in the folder ``folder``, laid out as a measure package: ``cql/<Name>-<version>.cql``,
    ``resources/library-…json`` and, for a measure, ``resources/measure-…json``."""
    folder = folder.rstrip("/")
    stem = f"{new.name}-{new.version}"
    writes = {f"{folder}/cql/{stem}.cql": new.cql,
              f"{folder}/resources/library-{stem}.json": _json(library_resource(
                  new.name, new.version, new.cql, elm, base=base, derived_from=new.derived_from))}
    if new.measure is not None:
        writes[f"{folder}/resources/measure-{stem}.json"] = _json(new.measure)
    try:
        held = FileLibraryProvider([folder], files=files).resolve(LibraryRef(new.name, new.version))
    except LibraryResolutionError:
        held = None
    if held is not None:
        raise SaveConflict(f"{new.name} {new.version} is already in {folder}")
    taken = [path for path in writes if files.read_text(path) is not None]
    if taken:
        raise SaveConflict(f"{', '.join(taken)} already exists")
    for directory in sorted({path.rsplit("/", 1)[0] for path in writes}):
        writer.mkdirs(directory)
    for path, text in writes.items():
        writer.write_text(path, text)
    return {"location": f"folder:{folder}", "saved": sorted(writes), "deleted": [], "sha256": cql_sha256(new.cql)}


def _bundle_with(text: str | None, req: SaveRequest, library: dict[str, Any] | None,
                 elm: str | None) -> dict[str, Any] | None:
    """A bundle carrying this library (and measure) as saved, or None when ``text`` is no bundle holding them."""
    try:
        bundle = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        return None
    changed = False
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") or {}
        kind = resource.get("resourceType")
        if (kind == "Library" and resource.get("name") == req.name
                and str(resource.get("version") or "") == req.version):
            entry["resource"] = library if library is not None else with_content(resource, req.cql, elm)
            changed = True
        elif kind == "Measure" and req.measure is not None and req.name in _named(resource):
            entry["resource"] = req.measure
            changed = True
    return bundle if changed else None


def _named(measure: dict[str, Any]) -> list[str]:
    return [c.split("|", 1)[0].rstrip("/").rsplit("/", 1)[-1] for c in measure.get("library") or [] if isinstance(c, str)]


def _json(resource: dict[str, Any]) -> str:
    return json.dumps(resource, indent=2, ensure_ascii=False) + "\n"
