"""The measure repository as the Designer reads it: libraries, what they read, and where they come from.

Every handler reads the knowledge corpus of one environment (`DOC-ENVIRONMENT-PICKER`, `KNOWLEDGE-LAKE-DIRECT`): its
``knowledge`` views, loaded once into memory. Each takes ``environment``, the one the Designer has picked; None is
the default (`CLIENT-ENVIRONMENT`). None of them touches a result table, an
orchestrator or a state layer.

`KNOWLEDGE-FAIL-LOUD` governs every read here: `discover` raises rather than answering 200 with `[]`,
because a 200 is cacheable and a plain reload re-served the empty list. `library_data_requirements` answers
500 with the reason, so the section says why it is missing and the document around it still renders.

**Knowledge is read-only here**, except through Publish (`DOC-PUBLISH`). An environment is declared by tagging its
catalog, not by a record this App stores, so there is nothing to create, edit or delete.

RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — every library read takes an optional ``location``: the environment's knowledge
(``store:<catalog>.knowledge``) or a folder (``folder:<path>``). A folder is read through the Workspace API as the
signed-in user, laid out as a measure package is (`folder_content.py`). With no location a read is the environment's
knowledge.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cql_databricks_designer.data_bindings import files_for, user_workspace_files
from cql_databricks_designer.dto import TranslateRequest
from cql_databricks.compiler.folder_content import folder_libraries, folder_library_resource
from cql_databricks.compiler.libraries import FileLibraryProvider, LibraryRef, LibraryResolutionError
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.resolve import resolve
from cql_databricks_common.workspace import forwarded_identity, user_client

from ..data_bindings import EnvironmentContents

log = logging.getLogger("cql_databricks_designer")


def router(*, contents: EnvironmentContents) -> APIRouter:
    router = APIRouter()

    def where(location: str | None) -> Location | None:
        return Location.parse(location) if location else None

    @router.get("/api/locations")
    def locations(environment: str | None = None) -> dict[str, Any]:
        """DOC-OPEN-SAVE — where a library can be opened from: the environment's knowledge, and the signed-in user's
        own and the shared workspace folders to browse from."""
        content = contents.get(environment)
        identity = forwarded_identity()
        email = identity[1] if identity and identity[1] else user_client().current_user.me().user_name
        return {
            "stores": [{"location": f"store:{s.catalog}.{s.schema}", "catalog": s.catalog, "schema": s.schema}
                       for s in content.knowledge_sources()],
            "folders": [{"location": f"folder:/Workspace/Users/{email}", "label": "My folder"},
                        {"location": "folder:/Workspace/Shared", "label": "Shared"}],
        }

    @router.get("/api/folders")
    def folder(path: str) -> JSONResponse:
        """DOC-OPEN-SAVE — one workspace folder, as the signed-in user sees it: its subfolders, and the libraries it
        holds when it holds any (its own CQL, or a package's ``cql/``). A folder's libraries are read only when it has
        CQL, so browsing a large home folder lists one level at a time."""
        from databricks.sdk.errors import NotFound, PermissionDenied
        from databricks.sdk.service.workspace import ObjectType
        try:
            children = list(user_client().workspace.list(path))
        except (NotFound, PermissionDenied) as e:
            return JSONResponse(status_code=404, content={"error": f"{path}: {e}"})
        folders = sorted((c for c in children if c.object_type in (ObjectType.DIRECTORY, ObjectType.REPO)),
                         key=lambda c: c.path or "")
        holds_cql = any((c.path or "").endswith(".cql") for c in children) or any(
            (c.path or "").rsplit("/", 1)[-1] in ("cql", "input") for c in folders)
        libraries = [_folder_record(f, f"folder:{path.rstrip('/')}")
                     for f in folder_libraries(user_workspace_files(), path)] if holds_cql else []
        return JSONResponse(content={
            "path": path, "location": f"folder:{path.rstrip('/')}",
            "folders": [{"name": (c.path or "").rsplit("/", 1)[-1], "path": c.path} for c in folders],
            "libraries": libraries,
        })

    @router.get("/api/libraries/discover")
    def discover(location: str | None = None, environment: str | None = None) -> JSONResponse:
        # Cached in the browser under revalidation by PERF-CACHE-REVISION; this handler states no
        # cache policy of its own. A failed corpus load raises and answers 500, which is not tagged
        # and so cannot stick (KNOWLEDGE-FAIL-LOUD).
        at = where(location)
        if at is not None and at.kind == "folder":
            return JSONResponse(content=[_folder_record(f, str(at))
                                         for f in folder_libraries(user_workspace_files(), at.path)])
        records = contents.get(environment).corpus().discover()
        return JSONResponse(content=records if at is None else [r for r in records if r["location"] == str(at)])

    @router.get("/api/libraries/{name}/{version}/source")
    def library_source(name: str, version: str, location: str | None = None,
                       environment: str | None = None) -> JSONResponse:
        # PERF-DISCOVER-LIGHTWEIGHT — discover omits the CQL; the SPA asks for it on open.
        at = where(location)
        if at is not None and at.kind == "folder":
            try:
                found = FileLibraryProvider([at.path], files=user_workspace_files()).resolve(LibraryRef(name, version))
            except LibraryResolutionError:
                return JSONResponse(status_code=404, content={"error": f"{name}@{version} not found in {at.path}"})
            return JSONResponse(content={"source": found.cql or "", "path": found.origin})
        corpus = contents.get(environment).corpus()
        library = corpus.library_in(at.catalog_schema, name, version) if at else corpus.library(name, version)
        if library is None:
            return JSONResponse(status_code=404, content={"error": f"{name}@{version} not found"})
        return JSONResponse(content={"source": library.source})

    @router.get("/api/libraries/{name}/{version}/data-requirements")
    def library_data_requirements(name: str, version: str, location: str | None = None,
                                  environment: str | None = None) -> JSONResponse:
        # SPEC-DATA-REQUIREMENTS — what this library READS, from `Library.dataRequirement`. A separate
        # route from `/specification` and independently failable: a failure answers 500 with its reason,
        # and the rest of the document stands without it (KNOWLEDGE-FAIL-LOUD).
        at = where(location)
        try:
            found = (folder_library_resource(files_for(location), at.path, name, version)
                     if at is not None and at.kind == "folder" else None)
            if found is not None:
                requirements = found[1].get("dataRequirement") or []
            elif at is not None and at.kind == "folder":
                # An include the folder does not hold resolved from the environment's knowledge (RUN-ADDRESS-LOCATION),
                # so its reads are read there too.
                library = contents.get(environment).corpus().library(name, version)
                requirements = library.data_requirements if library else []
            else:
                corpus = contents.get(environment).corpus()
                library = corpus.library_in(at.catalog_schema, name, version) if at else corpus.library(name, version)
                requirements = library.data_requirements if library else []
        except Exception as e:  # noqa: BLE001 — every failure is one answer: this section cannot be read, and why
            log.warning("Data requirements unavailable for %s@%s: %s", name, version, e)
            return JSONResponse(status_code=500,
                                content={"error": f"{name}@{version}: {e}"})
        return JSONResponse(content={"dataRequirements": requirements})

    @router.post("/api/knowledge/reload")
    def reload_knowledge() -> dict[str, Any]:
        """KNOWLEDGE-LAKE-DIRECT — forget every held environment and corpus: newly published or tagged content
        appears after this (or a restart), never silently."""
        contents.reload()
        return {"ok": True}

    @router.post("/api/libraries/resolve")
    def resolve_libraries(req: TranslateRequest) -> dict[str, Any]:
        # KNOWLEDGE-RESOLVE-API: the include closure in compile-safe order (deps before dependents), and
        # unresolved includes.
        # BUILD-RESOLVE-COMPILE: the resolution every compiler caller uses.
        resolution = resolve(contents.get(req.environment), cql=req.cql or "")
        return {
            "includes": [{"name": r.id.name, "version": r.id.version, "source": r.source}
                         for r in resolution.includes],
            "unresolved": [{"name": u.name, "version": u.version} for u in resolution.unresolved],
        }

    return router


def _folder_record(found, location: str) -> dict[str, Any]:
    """A folder's library as the catalog lists it — the shape ``Corpus.discover`` returns for a store."""
    return {"id": {"name": found.name, "version": found.version}, "location": location, "kind": found.kind,
            "status": "draft", "model": "", "title": found.title, "path": found.cql_path,
            "metadata": {"description": found.description, "tags": [], "author": ""}, "updated": ""}
