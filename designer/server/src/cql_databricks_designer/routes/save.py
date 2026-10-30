"""DOC-OPEN-SAVE, DOC-AUTO-SAVE — ``POST /api/libraries/save``: write a library back to the workspace folder it was
opened from, as the signed-in user; and ``POST /api/libraries/new``: create one (New, Save As) in a workspace folder.
A tagged store is read-only in the Designer (`DOC-EDITABLE-IN-SCOPE`): both refuse a ``store:`` location. A store
changes only by ``POST /api/libraries/publish`` (`DOC-PUBLISH`), the client's publish run as the signed-in user
(`CLIENT-PUBLISH`): 200 with the summary; 400 with it when nothing was written; 502 with it when a write failed
after others.

The CQL is compiled first, with its includes resolved at the same location (RUN-ADDRESS-LOCATION), and written either
way: CQL that compiles carries the ELM compiled from it, CQL that does not carries no ELM, so no ELM written
disagrees with the CQL beside it.

Answers:

- 200 — saved; ``saved`` lists what was written, ``deleted`` what the caller agreed to delete, ``sha256`` the digest
  of the CQL now there, to send as the next save's base, ``compiled`` whether it compiled, and ``diagnostics`` why
  not.
- 409 ``conflict`` — the library changed since it was opened, or (New, Save As) is already there. Nothing was
  written.
- 409 ``stale`` — the save would leave ``paths`` out of date (a package's XML copies and HTML). Nothing was written;
  send them back in ``deleteStale`` to save and delete them.
- 400 — a location this save cannot take (a tagged store), or CQL that declares another library.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cql_databricks.compiler.folder_content import folder_library_resource, folder_measure
from cql_databricks.compiler.libraries import with_identity
from cql_databricks.compiler.library_publish import PublishFailed, PublishRefused, publish_folder_library
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.resolve import ResolutionError, resolve, translate_root
from cql_databricks.data_bindings import ContentResolver
from cql_databricks_common import config
from cql_databricks_common.compiler import CompilerClient, CompilerError
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import user_client

from ..data_bindings import files_for
from ..save import (NewLibrary, SaveConflict, SaveRefused, SaveRequest, StaleCopies, WorkspaceFolderWriter,
                    check_identity, create_in_folder, measure_copy, save_to_folder,
                    starting_cql, starting_measure)

log = logging.getLogger("cql_databricks_designer")


class SaveBody(BaseModel):
    model_config = {"populate_by_name": True, "extra": "forbid"}
    location: str
    name: str
    version: str
    cql: str
    base_sha256: str | None = Field(default=None, alias="baseSha256")
    measure: dict | None = None
    delete_stale: list[str] = Field(default_factory=list, alias="deleteStale")


class PublishBody(BaseModel):
    """DOC-PUBLISH — the folder library to publish, and the tagged store (``catalog.schema``) to publish it into."""
    model_config = {"extra": "forbid"}
    location: str
    name: str
    version: str
    store: str


class CopyOf(BaseModel):
    model_config = {"extra": "forbid"}
    location: str
    name: str
    version: str


class NewBody(BaseModel):
    """New: ``kind`` says whether it is a measure, and the CQL starts from `starting_cql`. Save As: ``copyOf`` names
    the open library and ``cql`` is its CQL as edited; the copy is a measure when the original is."""
    model_config = {"populate_by_name": True, "extra": "forbid"}
    location: str
    name: str
    version: str
    kind: Literal["measure", "library"] = "measure"
    cql: str | None = None
    copy_of: CopyOf | None = Field(default=None, alias="copyOf")


def router(*, compiler: CompilerClient, content: ContentResolver, warehouse: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    def compiled(cql: str, at: Location, location: str, name: str, version: str) -> tuple[str | None, list[str]]:
        """The ELM of ``cql``, which must declare ``name`` ``version``, with includes resolved at ``at``, and its
        problems. The ELM is None when there are any."""
        resolution = resolve(content, cql=cql, location=at, files=files_for(location))
        if (resolution.name, resolution.version) != (name, version):
            raise SaveRefused(f"the CQL declares {resolution.name or '(no name)'} "
                              f"{resolution.version or '(no version)'}, not {name} {version}; "
                              "save it as a new library")
        out = translate_root(compiler.translate, resolution)
        problems = [*resolution.unresolved_messages(), *([] if out.ok else out.diagnostics)]
        if not problems and not out.elm:
            problems = ["the compiler returned no ELM"]
        return (None if problems else out.elm), problems

    def answered(write: Callable[[], Any]) -> Any:
        try:
            return write()
        except SaveConflict as e:
            return JSONResponse(status_code=409, content={"kind": "conflict", "error": str(e)})
        except StaleCopies as e:
            return JSONResponse(status_code=409, content={"kind": "stale", "error": str(e), "paths": e.paths})
        except (SaveRefused, ResolutionError, ValueError) as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        except CompilerError as e:
            return JSONResponse(status_code=502, content={"error": str(e)})

    @router.post("/api/libraries/save")
    def save_library(body: SaveBody) -> Any:
        def write() -> Any:
            at = _folder(body.location)
            elm, problems = compiled(body.cql, at, body.location, body.name, body.version)
            request = SaveRequest(name=body.name, version=body.version, cql=body.cql, base_sha256=body.base_sha256,
                                  measure=body.measure, delete_stale=body.delete_stale)
            saved = save_to_folder(request, at.path, files=files_for(body.location),
                                   writer=WorkspaceFolderWriter(user_client()), elm=elm)
            return {**saved, "compiled": elm is not None, "diagnostics": problems}
        return answered(write)

    @router.post("/api/libraries/new")
    def new_library(body: NewBody) -> Any:
        def write() -> Any:
            check_identity(body.name, body.version)
            at = _folder(body.location)
            base = config.canonical_base()
            if body.copy_of is None:
                if body.cql is not None:
                    raise SaveRefused("a new library starts from its own CQL; send cql only with copyOf")
                new = NewLibrary(body.name, body.version, starting_cql(body.name, body.version,
                                                                       measure=body.kind == "measure"),
                                 starting_measure(body.name, body.version, base=base)
                                 if body.kind == "measure" else None)
            else:
                if body.cql is None:
                    raise SaveRefused("Save As sends the CQL it copies")
                source = body.copy_of
                original, derived_from = _original(content, source)
                new = NewLibrary(body.name, body.version, with_identity(body.cql, body.name, body.version),
                                 measure_copy(original, body.name, body.version, base=base) if original else None,
                                 derived_from=derived_from)
            elm, problems = compiled(new.cql, at, body.location, new.name, new.version)
            created = create_in_folder(new, at.path, files=files_for(body.location),
                                       writer=WorkspaceFolderWriter(user_client()), elm=elm, base=base)
            return {**created, "compiled": elm is not None, "diagnostics": problems}
        return answered(write)

    @router.post("/api/libraries/publish")
    def publish(body: PublishBody) -> Any:
        """DOC-PUBLISH — publish the folder library into a tagged store, as the signed-in user, through the client."""
        try:
            at = _folder(body.location)
            catalog, _, schema = body.store.partition(".")
            summary = publish_folder_library(content, files_for(body.location), at.path, body.name, body.version,
                                             store=(catalog, schema), run=user_session(warehouse()),
                                             translate=compiler.translate, base=config.canonical_base())
        except PublishRefused as e:
            return JSONResponse(status_code=400, content={
                "error": str(e), "summary": e.summary.to_dict() if e.summary else None})
        except PublishFailed as e:
            content.reload()   # the libraries before the failure were written
            return JSONResponse(status_code=502, content={"error": str(e), "summary": e.summary.to_dict()})
        except (SaveRefused, ResolutionError, ValueError) as e:
            return JSONResponse(status_code=400, content={"error": str(e), "summary": None})
        except CompilerError as e:
            return JSONResponse(status_code=502, content={"error": str(e), "summary": None})
        # The corpus is held in memory; what reads tagged knowledge next must see what was published.
        content.reload()
        return summary.to_dict()

    return router


def _folder(location: str) -> Location:
    """DOC-EDITABLE-IN-SCOPE — the workspace folder at ``location``. A tagged store is published and read-only here."""
    at = Location.parse(location)
    if at.kind != "folder":
        raise SaveRefused(f"{at.path} is a tagged store, which the Designer does not write; save a copy to a "
                          "workspace folder")
    return at


def _original(content: ContentResolver, source: CopyOf) -> tuple[dict | None, str | None]:
    """The ``Measure`` of the library Save As copies, if it is a measure, and that library's ``canonical|version``,
    if it has a canonical URL — each read from the location it was opened at (RUN-ADDRESS-LOCATION)."""
    at = Location.parse(source.location)
    if at.kind == "folder":
        files = files_for(source.location)
        measure = folder_measure(files, at.path, source.name, source.version)
        library = folder_library_resource(files, at.path, source.name, source.version)
        url = library[1].get("url") if library else None
        return (measure[1] if measure else None), (f"{url}|{source.version}" if url else None)
    corpus = content.corpus()
    held = corpus.library_in(at.catalog_schema, source.name, source.version)
    if held is None:
        raise SaveRefused(f"{source.name} {source.version} is not in {'.'.join(at.catalog_schema)}")
    measure = corpus.measure_resource(source.name, source.version, store=at.catalog_schema)
    return measure, (f"{held.url}|{held.fhir_version}" if held.url else None)
