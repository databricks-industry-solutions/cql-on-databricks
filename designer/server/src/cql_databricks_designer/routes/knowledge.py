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
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cql_databricks_designer.dto import TranslateRequest
from cql_databricks.compiler.resolve import resolve
from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, ContentResolver
from cql_databricks.data_bindings.bindings import bind_knowledge_views
from cql_databricks_common.workspace import user_client

from .data_sources import runtime_location

log = logging.getLogger("cql_databricks_designer")


def router(*, content: ContentResolver, warehouse: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/libraries/discover")
    def discover() -> JSONResponse:
        # Cached in the browser under revalidation by PERF-CACHE-REVISION; this handler states no
        # cache policy of its own. A failed corpus load raises and answers 500, which is not tagged
        # and so cannot stick (KNOWLEDGE-FAIL-LOUD).
        return JSONResponse(content=content.corpus().discover())

    @router.get("/api/libraries/{name}/{version}/source")
    def library_source(name: str, version: str) -> JSONResponse:
        # PERF-DISCOVER-LIGHTWEIGHT — discover omits the CQL; the SPA asks for it on open.
        library = content.corpus().library(name, version)
        if library is None:
            return JSONResponse(status_code=404, content={"error": f"{name}@{version} not found"})
        return JSONResponse(content={"source": library.source})

    @router.get("/api/libraries/{name}/{version}/data-requirements")
    def library_data_requirements(name: str, version: str) -> JSONResponse:
        # SPEC-DATA-REQUIREMENTS — what this library READS, from `Library.dataRequirement`. A separate
        # route from `/specification` and independently failable: the document stands without it.
        try:
            library = content.corpus().library(name, version)
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
            views = bind_knowledge_views(user_client(), catalog, schema, content, warehouse_id=warehouse())
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
