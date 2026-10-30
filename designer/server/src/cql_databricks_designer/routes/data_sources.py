"""The data sources and the knowledge-view Bind action, served by the Designer itself (`DEC-EVALUATOR-APP-REHOME`).

These were the evaluator App's routes, forwarded here through its client until EVALUATOR-REMOVE. They keep their
paths and handler names, so the SPA did not change.

**As the signed-in user** (`SEC-OBO`). A data source is a tagged UC schema (`DATA-SOURCE-UC-TAG`), and the list
is what Unity Catalog lets the caller see: ``cql_databricks.compiler.data_source`` reads the tags and the layouts
through the user's warehouse runner. Lookups take seconds, so the answer is cached per user until that user's
``POST /api/data-sources/reload``. The cache holds the computed list, never a runner: a forwarded token expires.

The Bind action (`DATA-BINDINGS`, `DATA-BIND-PROGRESS`) creates or replaces the knowledge views in the runtime
schema with the user's own client. ``CREATE OR REPLACE VIEW`` needs ownership, so a user who does not own the
views gets Unity Catalog's error in the step that failed. The deploy binds them once (`DATA-BIND-AUTO`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cql_databricks.data_bindings import ContentResolver
from cql_databricks_designer.data_bindings import user_content_resolver
from cql_databricks.data_bindings.bindings import plan_bind_steps
from cql_databricks.compiler.data_source import DataSource, list_data_sources as read_data_sources
from cql_databricks_common import config
from cql_databricks_common.warehouse import user_session
from cql_databricks_common.workspace import forwarded_identity

log = logging.getLogger("cql_databricks_designer")

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class DataSourceCache:
    """One cached data-source list per signed-in user, filled on first read and dropped by that user's reload."""

    def __init__(self, load: Callable[[], list[DataSource]], who: Callable[[], str]) -> None:
        self._load = load
        self._who = who
        self._lock = threading.Lock()
        self._by_user: dict[str, list[DataSource]] = {}
        self._generation = 0

    @property
    def generation(self) -> int:
        """Moves on every load and every reload; a cache-stamp key that never forces a load itself."""
        return self._generation

    def list(self) -> list[DataSource]:
        user = self._who()
        with self._lock:
            cached = self._by_user.get(user)
        if cached is not None:
            return cached
        # Outside the lock: a lookup takes seconds, and another user's must not wait on it.
        sources = self._load()
        with self._lock:
            self._by_user[user] = sources
            self._generation += 1
        return sources

    def get(self, source_id: str) -> DataSource | None:
        return next((s for s in self.list() if s.id == source_id), None)

    def reload(self) -> None:
        with self._lock:
            self._by_user.pop(self._who(), None)
            self._generation += 1


def _signed_in_user() -> str:
    """The forwarded email; locally (one person, `SEC-LOCAL-IDENTITY`) nothing is forwarded, so one entry."""
    identity = forwarded_identity()
    return identity[1] if identity else ""


def warehouse() -> str:
    warehouse = config.warehouse_id()
    if not warehouse:
        raise RuntimeError("WAREHOUSE_ID is not set: data sources are read on a SQL warehouse")
    return warehouse


def user_data_sources(modeled_types: Callable[[], list[str]]) -> DataSourceCache:
    """The Designer's cache, loading as the signed-in user."""
    def load() -> list[DataSource]:
        run = user_session(warehouse())
        return read_data_sources(user_content_resolver(run), run, modeled_types())
    return DataSourceCache(load, _signed_in_user)


def runtime_location() -> tuple[str, str]:
    """Where the knowledge views live: the runtime catalog and schema (`SVC-RUNTIME-UDF-UC`)."""
    catalog = config.runtime_catalog()
    if not catalog:
        raise RuntimeError("CQL_RUNTIME_CATALOG is not configured: the knowledge views live in its runtime schema")
    return catalog, config.runtime_schema()


def router(*, data_sources: DataSourceCache, content: ContentResolver) -> APIRouter:
    router = APIRouter()

    @router.get("/api/data-sources")
    def list_data_sources() -> list[dict[str, Any]]:
        # A tagged schema that fails its layout check raises and answers 500: a missing source is not
        # an empty one (fail loud, as the knowledge sources do).
        return [s.to_dict() for s in data_sources.list()]

    @router.get("/api/data-sources/{source_id}")
    def get_data_source(source_id: str) -> Any:
        source = data_sources.get(source_id)
        if source is None:
            return JSONResponse(status_code=404,
                                content={"error": f"no schema you can read is tagged cql_databricks_data_source = "
                                                  f"{source_id!r}"})
        return source.to_dict()

    @router.post("/api/data-sources/reload")
    def reload_data_sources() -> dict[str, Any]:
        data_sources.reload()
        return {"ok": True}

    @router.post("/api/data-bindings/bind")
    async def bind_all() -> Any:
        """Create or replace the knowledge views in the runtime schema, one step at a time (DATA-BIND-PROGRESS).

        An SSE stream:
          {stage: "start", total: N, steps: ["label", ...]}
          {stage: "step", index: i, label: "...", status: "ok"|"error", error?: "..."}
          {stage: "done", created: [...], errors: [...]}
        """
        try:
            warehouse_id = warehouse()
            catalog, schema = runtime_location()
            # Taken here, on the request: the worker thread below does not inherit the request's identity.
            run = user_session(warehouse_id)
        except RuntimeError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, f"data: {json.dumps(event)}\n\n")

        def bind() -> None:
            try:
                steps = plan_bind_steps(run, catalog, schema, content)
                emit({"stage": "start", "total": len(steps), "steps": [s.label for s in steps]})
                created: list[str] = []
                errors: list[str] = []
                for i, step in enumerate(steps):
                    try:
                        created.append(step.execute())
                        emit({"stage": "step", "index": i, "label": step.label, "status": "ok"})
                    except Exception as e:  # noqa: BLE001 — reported in the stream, step by step
                        log.warning("Bind step '%s' failed: %s", step.label, e)
                        errors.append(f"{step.label}: {e}")
                        emit({"stage": "step", "index": i, "label": step.label, "status": "error", "error": str(e)})
                emit({"stage": "done", "created": created, "errors": errors})
            except Exception as e:  # noqa: BLE001 — reported as the stream's result
                log.error("Bind planning failed: %s", e, exc_info=True)
                emit({"stage": "done", "created": [], "errors": [str(e)]})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=bind, daemon=True).start()

        async def sse():
            while (chunk := await queue.get()) is not None:
                yield chunk

        return StreamingResponse(sse(), media_type="text/event-stream", headers=_SSE_HEADERS)

    return router
