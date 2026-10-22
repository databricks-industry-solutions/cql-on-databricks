"""APP-NARRATIVE-OVERLAY — generate a library's narration on request, check it, and cache it for the user.

``POST /api/libraries/{name}/{version}/narration``. SSE, as the assistant drawer is: a generation takes
minutes and is a sequence of stages (compile, write, check, perhaps write and check again), and a reader
waiting on it is shown which one is running.

The library is compiled HERE, from its CQL in knowledge, and its reference set is derived from that ELM
(`SPEC-EXPLAIN-INPUTS`). The browser sends nothing but the library's name and version, so the denominator
the narration is checked against is never the client's.

Each POST generates. A cached narration is read through ``/specification``; this route writes a newer one,
which is what "Regenerate" means (`SPEC-EXPLAIN-AGAIN` stays the assistant drawer's re-explanation).

Terminal events, exactly one per stream:

- ``narration`` — passed, cached, and the composed Specification (``specification_document``) attached.
- ``rejected`` — failed its checks on every attempt; carries the last attempt's problems. Nothing cached.
- ``error`` — anything else: the compile, the model call, the cache write.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cql_databricks.agent.ask import serving_client
from cql_databricks.compiler.resolve import resolve, translate_root
from cql_databricks.data_bindings import ContentResolver
from cql_databricks_common import config
from cql_databricks_common.compiler import CompilerClient

from ..assistant_tools import specification_document
from ..explain.generate import NarrationRejected, generate
from ..explain.narrations import NarrationKey, NarrationStore, contract_digest, signed_in_user, source_digest
from ..explain.reference_set import build_reference_set

log = logging.getLogger("cql_databricks_designer")


def _frame(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def router(*, compiler: CompilerClient, content: ContentResolver, narrations: NarrationStore) -> APIRouter:

    router = APIRouter()

    @router.post("/api/libraries/{name}/{version}/narration")
    def generate_narration(name: str, version: str):
        library = content.corpus().library(name, version)
        if library is None:
            return JSONResponse(status_code=404, content={"error": f"{name}@{version} not found"})
        # Who, which model and the model client are settled on the request, before the stream opens: each
        # is a refusal the caller can be told with a status, and the client carries the signed-in user's
        # token (`SEC-OBO`), so the call is made as the reader.
        try:
            user = signed_in_user()
            endpoint = config.assistant_endpoint()
            client = serving_client()
        except RuntimeError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})

        def _sse():
            try:
                yield _frame({"type": "stage", "stage": "compiling"})
                translated = translate_root(compiler.translate, resolve(content, cql=library.source))
                if not translated.ok or not translated.elm:
                    raise RuntimeError(f"{name}@{version} did not compile: " + "; ".join(translated.diagnostics))
                reference_set = build_reference_set(json.loads(translated.elm)).to_dict()

                result = None
                for event in generate(library=name, version=version, source=library.source,
                                      reference_set=reference_set, client=client, endpoint=endpoint):
                    if event["type"] == "generated":
                        result = event["result"]
                    else:
                        yield _frame(event)

                yield _frame({"type": "stage", "stage": "saving"})
                key = NarrationKey(user=user, library=name, version=version,
                                   source_sha256=source_digest(library.source), contract_sha256=contract_digest())
                narrations.save(key, instance=result.instance, endpoint=endpoint, attempts=result.attempts,
                                app_version=config.app_version())
                yield _frame({"type": "narration", "attempts": result.attempts,
                              "specification": specification_document(name, version, content=content,
                                                                      narrations=narrations)})
            except NarrationRejected as e:
                log.warning("APP-NARRATIVE-OVERLAY: %s@%s rejected after %d attempts: %d problems",
                            name, version, e.attempts, len(e.problems))
                yield _frame({"type": "rejected", "attempts": e.attempts,
                              "problems": [p.to_dict() for p in e.problems]})
            except Exception as e:  # noqa: BLE001 — the stream is the only channel left to say it
                log.error("APP-NARRATIVE-OVERLAY: %s@%s failed: %s", name, version, e, exc_info=True)
                yield _frame({"type": "error", "error": str(e)})

        return StreamingResponse(_sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
