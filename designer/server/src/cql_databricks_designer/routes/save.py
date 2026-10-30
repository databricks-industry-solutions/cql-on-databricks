"""DOC-OPEN-SAVE — ``POST /api/libraries/save``: save a library where it was opened, as the signed-in user; and
``POST /api/libraries/new``: create one (New, Save As) at a location.

The CQL is compiled first, with its includes resolved at the same location (RUN-ADDRESS-LOCATION). CQL that does not
compile is not saved: the ELM a package carries is compiled from the CQL beside it, and a save that wrote CQL with no
ELM to match would leave the package disagreeing with itself.

Answers:

- 200 — saved; ``saved`` lists what was written, ``deleted`` what the caller agreed to delete, ``sha256`` the digest
  of the CQL now there, to send as the next save's base.
- 409 ``conflict`` — the library changed since it was opened, or (New, Save As) is already there. Nothing was
  written.
- 409 ``stale`` — the save would leave ``paths`` out of date (a package's XML copies and HTML). Nothing was written;
  send them back in ``deleteStale`` to save and delete them.
- 422 — the CQL does not compile; ``diagnostics`` says why. 400 — a location this save cannot take.
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
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.resolve import ResolutionError, resolve, translate_root
from cql_databricks.data_bindings import ContentResolver
from cql_databricks_common import config
from cql_databricks_common.compiler import CompilerClient, CompilerError
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import user_client

from ..data_bindings import files_for
from ..save import (NewLibrary, SaveConflict, SaveRefused, SaveRequest, StaleCopies, WorkspaceFolderWriter,
                    check_identity, create_in_folder, create_in_store, measure_copy, save_to_folder, save_to_store,
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


class _NotCompiled(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("the CQL does not compile")
        self.problems = problems


def router(*, compiler: CompilerClient, content: ContentResolver, warehouse: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    def compiled(cql: str, at: Location, location: str, name: str, version: str) -> str:
        """The ELM of ``cql``, which must declare ``name`` ``version``, with includes resolved at ``at``."""
        resolution = resolve(content, cql=cql, location=at, files=files_for(location))
        if (resolution.name, resolution.version) != (name, version):
            raise SaveRefused(f"the CQL declares {resolution.name or '(no name)'} "
                              f"{resolution.version or '(no version)'}, not {name} {version}; "
                              "save it as a new library")
        out = translate_root(compiler.translate, resolution)
        problems = [*resolution.unresolved_messages(), *([] if out.ok else out.diagnostics)]
        if problems or not out.elm:
            raise _NotCompiled(problems)
        return out.elm

    def answered(write: Callable[[], Any]) -> Any:
        try:
            return write()
        except _NotCompiled as e:
            return JSONResponse(status_code=422, content={"error": str(e), "diagnostics": e.problems})
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
            at = Location.parse(body.location)
            elm = compiled(body.cql, at, body.location, body.name, body.version)
            request = SaveRequest(name=body.name, version=body.version, cql=body.cql, base_sha256=body.base_sha256,
                                  measure=body.measure, delete_stale=body.delete_stale)
            if at.kind == "folder":
                return save_to_folder(request, at.path, files=files_for(body.location),
                                      writer=WorkspaceFolderWriter(user_client()), elm=elm)
            saved = save_to_store(request, at.catalog_schema, corpus=content.corpus(),
                                  run=user_session(warehouse()), elm=elm)
            # The corpus is held in memory; the next read must see the row this save wrote.
            content.reload()
            return saved
        return answered(write)

    @router.post("/api/libraries/new")
    def new_library(body: NewBody) -> Any:
        def write() -> Any:
            check_identity(body.name, body.version)
            at = Location.parse(body.location)
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
            elm = compiled(new.cql, at, body.location, new.name, new.version)
            if at.kind == "folder":
                return create_in_folder(new, at.path, files=files_for(body.location),
                                        writer=WorkspaceFolderWriter(user_client()), elm=elm, base=base)
            created = create_in_store(new, at.catalog_schema, corpus=content.corpus(),
                                      run=user_session(warehouse()), elm=elm, base=base)
            content.reload()
            return created
        return answered(write)

    return router


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
