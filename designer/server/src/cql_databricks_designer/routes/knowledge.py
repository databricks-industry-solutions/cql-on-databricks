"""The measure repository as the Designer reads it: libraries, what they read, and where they come from.

Every handler reads the knowledge corpus through the resolver (`CONTENT-RESOLVER`, `KNOWLEDGE-LAKE-DIRECT`):
the tagged knowledge tables, loaded once into memory. None of them touches a result table, an
orchestrator or a state layer.

`KNOWLEDGE-FAIL-LOUD` governs the reads whose result IS the answer: `discover` raises rather than
answering 200 with `[]`, because a 200 is cacheable and a plain reload re-served the empty list.
`library_data_requirements` is the deliberate exception — an enrichment of a document that stands
without it, so a failure is logged and the section is absent.

**Knowledge sources are read-only here** (`DEC-CONTENT-LOADERS-AND-RESOLVER`). A source is a UC schema
tagged ``cql_databricks_knowledge`` or ``cql_databricks_terminology``; it is declared by tagging the
schema, not by a record this App stores, so there is nothing to create, edit or delete.

RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — every library read takes an optional ``location``: a tagged store
(``store:<catalog>.<schema>``) or a folder (``folder:<path>``). A folder is read through the Workspace API as the
signed-in user, laid out as a measure package is (`folder_content.py`). With no location a read is all of tagged
knowledge, as before.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cql_databricks_designer.data_bindings import files_for, user_workspace_files
from cql_databricks_designer.dto import TranslateRequest
from cql_databricks.compiler.folder_content import folder_libraries, folder_library_resource
from cql_databricks.compiler.libraries import FileLibraryProvider, LibraryRef, LibraryResolutionError
from cql_databricks.compiler.location import Location
from cql_databricks.compiler.resolve import resolve
from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, ContentResolver, ResolverError
from cql_databricks.data_bindings.bindings import bind_knowledge_views
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import forwarded_identity, user_client

from ..store_access import store_refusal
from .data_sources import runtime_location

log = logging.getLogger("cql_databricks_designer")


def router(*, content: ContentResolver, warehouse: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    def where(location: str | None) -> Location | None:
        return Location.parse(location) if location else None

    @router.get("/api/locations")
    def locations() -> dict[str, Any]:
        """DOC-OPEN-SAVE — where a library can be opened from: every tagged store, and the signed-in user's own and
        the shared workspace folders to browse from."""
        identity = forwarded_identity()
        email = identity[1] if identity and identity[1] else user_client().current_user.me().user_name
        return {
            "stores": [{"location": f"store:{s.catalog}.{s.schema}", "catalog": s.catalog, "schema": s.schema}
                       for s in content.knowledge_sources()],
            "folders": [{"location": f"folder:/Workspace/Users/{email}", "label": "My folder"},
                        {"location": "folder:/Workspace/Shared", "label": "Shared"}],
        }

    @router.get("/api/locations/access")
    def location_access(location: str) -> JSONResponse:
        """DOC-EDITABLE-IN-SCOPE — whether the signed-in user can write the tagged store ``location``, asked as them
        through the warehouse before a library there opens. A folder is not asked: no call states a user's own level
        on one, so its libraries open editable and a save the user cannot make is refused by Databricks."""
        at = Location.parse(location)
        if at.kind != "store":
            return JSONResponse(status_code=400, content={
                "error": f"{location}: only a tagged store's access is checked before a library opens"})
        catalog, schema = at.catalog_schema
        try:
            reason = store_refusal(user_session(warehouse()), catalog, schema)
        except ResolverError as e:
            log.warning("store access check failed for %s: %s", location, e)
            return JSONResponse(status_code=502, content={"error": f"Could not check access to {catalog}.{schema}: {e}"})
        return JSONResponse(content={"writable": reason is None, "reason": reason})

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
    def discover(location: str | None = None) -> JSONResponse:
        # Cached in the browser under revalidation by PERF-CACHE-REVISION; this handler states no
        # cache policy of its own. A failed corpus load raises and answers 500, which is not tagged
        # and so cannot stick (KNOWLEDGE-FAIL-LOUD).
        at = where(location)
        if at is not None and at.kind == "folder":
            return JSONResponse(content=[_folder_record(f, str(at))
                                         for f in folder_libraries(user_workspace_files(), at.path)])
        records = content.corpus().discover()
        return JSONResponse(content=records if at is None else [r for r in records if r["location"] == str(at)])

    @router.get("/api/libraries/{name}/{version}/source")
    def library_source(name: str, version: str, location: str | None = None) -> JSONResponse:
        # PERF-DISCOVER-LIGHTWEIGHT — discover omits the CQL; the SPA asks for it on open.
        at = where(location)
        if at is not None and at.kind == "folder":
            try:
                found = FileLibraryProvider([at.path], files=user_workspace_files()).resolve(LibraryRef(name, version))
            except LibraryResolutionError:
                return JSONResponse(status_code=404, content={"error": f"{name}@{version} not found in {at.path}"})
            return JSONResponse(content={"source": found.cql or "", "path": found.origin})
        corpus = content.corpus()
        library = corpus.library_in(at.catalog_schema, name, version) if at else corpus.library(name, version)
        if library is None:
            return JSONResponse(status_code=404, content={"error": f"{name}@{version} not found"})
        return JSONResponse(content={"source": library.source})

    @router.get("/api/libraries/{name}/{version}/data-requirements")
    def library_data_requirements(name: str, version: str, location: str | None = None) -> JSONResponse:
        # SPEC-DATA-REQUIREMENTS — what this library READS, from `Library.dataRequirement`. A separate
        # route from `/specification` and independently failable: the document stands without it.
        at = where(location)
        try:
            found = (folder_library_resource(files_for(location), at.path, name, version)
                     if at is not None and at.kind == "folder" else None)
            if found is not None:
                requirements = found[1].get("dataRequirement") or []
            elif at is not None and at.kind == "folder":
                # An include the folder does not hold resolved from tagged knowledge (RUN-ADDRESS-LOCATION), so its
                # reads are read there too.
                library = content.corpus().library(name, version)
                requirements = library.data_requirements if library else []
            else:
                corpus = content.corpus()
                library = corpus.library_in(at.catalog_schema, name, version) if at else corpus.library(name, version)
                requirements = library.data_requirements if library else []
        except Exception as e:
            log.warning("Data requirements unavailable for %s@%s: %s", name, version, e)
            requirements = []
        return JSONResponse(content={"dataRequirements": requirements})

    @router.get("/api/knowledge-sources")
    def list_knowledge_sources() -> dict[str, Any]:
        # The tagged schemas the corpus and the terminology binding read, in lookup order.
        return {
            "sources": [s.to_dict() for s in content.knowledge_sources()]
                       + [s.to_dict() for s in content.terminology_sources()],
            "tags": [KNOWLEDGE_TAG, TERMINOLOGY_TAG],
        }

    @router.post("/api/knowledge-sources/reload")
    def reload_knowledge_sources() -> Any:
        # The visible refresh that `DATA-SOURCE-UC-TAG` needs: tag lookups are cached, so
        # a newly tagged schema appears after this (or a restart), never silently.
        # DATA-BIND-AUTO — the knowledge views union the tagged schemas that existed when they were made, so
        # they are rebound too, as the signed-in user (`DEC-EVALUATOR-APP-REHOME`): a newly imported
        # terminology schema joins `runtime.valuesets` without a manual Bind. A failure to bind is this
        # reload's failure, reported to the caller (the views need their owner to replace them).
        content.reload()
        try:
            catalog, schema = runtime_location()
            views = bind_knowledge_views(user_session(warehouse()), catalog, schema, content)
        except Exception as e:  # noqa: BLE001 — returned to the caller, never swallowed
            log.exception("DATA-BIND-AUTO: rebinding the knowledge views failed")
            return JSONResponse(status_code=502, content={"ok": False, "error": f"DATA-BIND-AUTO: {e}"})
        return {"ok": True, "views": views}

    @router.post("/api/libraries/resolve")
    def resolve_libraries(req: TranslateRequest) -> dict[str, Any]:
        # KNOWLEDGE-RESOLVE-API: the include closure in compile-safe order (deps before dependents), and
        # unresolved includes.
        # BUILD-RESOLVE-COMPILE: the resolution every compiler caller uses.
        resolution = resolve(content, cql=req.cql or "")
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
