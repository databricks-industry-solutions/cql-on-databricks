"""The assistant drawer's one endpoint (`APP-ASSISTANT`, `agent/README.md`).

**Takes the `FastMCP` SERVER, not its ASGI transport, and that is the point** (`SVC-MCP-TOOLS`). The
drawer generates the tool definitions it sends the model from `list_tools()` and dispatches through
`call_tool()` in process — no self-HTTP hop through our own `/mcp` mount. A second, hand-written set of
definitions is the drift that surface exists to prevent, and a second dispatch table would let an
argument name diverge with nothing failing until an agent called it.

SSE, and no JSON sibling beside it. A real answer is a SEQUENCE of tool calls rather than one round, so
a client that waited for the whole loop would show a spinner over an investigation the reader is
entitled to watch — `THEME-GLASSBOX` rather than a progress bar.
"""
from __future__ import annotations

from typing import Any

import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastmcp import FastMCP
from pydantic import BaseModel
from pydantic import Field

from cql_databricks.agent.ask import AssistantError
from cql_databricks.agent.ask import Selection
from cql_databricks.agent.ask import ask_events as run_assistant_events

log = logging.getLogger("cql_databricks_designer")


class AssistantSelectionDto(BaseModel):
    """APP-ASSISTANT-CONTEXT — what the user is looking at, as the removable context chip states it.

    Every field optional, because the chip is removable and a question broader than the selection is
    what the drawer is for. It PREFILLS tool arguments and does nothing else — the tools themselves
    still take their scope explicitly, or they would work here and break for the Genie Agent.
    """

    model_config = {"populate_by_name": True}
    library: str | None = None
    version: str | None = None
    define: str | None = None
    data_source_id: str | None = Field(default=None, alias="dataSourceId")
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    result_set: dict | None = Field(default=None, alias="resultSet")
    #: What those identifiers are CALLED on the screen. Carried separately from the arguments above
    #: and never passed to a tool — an identifier is not a name a clinician recognises, and an
    #: assistant that says `sample-patients` at someone who calls it *the sample patients*
    #: is describing the registry rather than the data.
    measure_title: str | None = Field(default=None, alias="measureTitle")
    data_source_name: str | None = Field(default=None, alias="dataSourceName")

class AssistantAskRequest(BaseModel):
    """APP-ASSISTANT — one turn of the drawer's conversation.

    **`messages` carries the conversation, and the server holds no session.** The client sends back
    what the last answer returned, for the same reason the tools carry no run id
    (`SVC-TOOL-ADDRESS`): a server-side conversation would make an answer reproducible only from
    inside the conversation that produced it. Module scope, per `MeasureMetricsRequest`'s note about
    postponed annotations.
    """

    model_config = {"populate_by_name": True}
    question: str = ""
    selection: AssistantSelectionDto | None = None
    #: The prior turns, verbatim from the previous response. Absent starts a new conversation.
    messages: list[dict[str, Any]] | None = None

def router(*, mcp_server: FastMCP) -> APIRouter:

    router = APIRouter()

    @router.post("/api/assistant/ask")
    def assistant_ask(req: AssistantAskRequest):
        # APP-ASSISTANT — one turn of the drawer's conversation, over the Foundation Model API
        # with the App's own service principal. It dispatches through `mcp_server` IN PROCESS: the
        # same object the `/mcp` mount publishes, so the tool definitions the model is given are
        # generated from one source and there is no self-HTTP hop through our own transport.
        #
        # SSE, AND THAT IS THE ONLY SHAPE — there is no second JSON endpoint beside it (one path per
        # feature). A real answer is a SEQUENCE of tool calls, not one round, so a client that waited
        # for the whole loop would show a spinner for an investigation the reader is entitled to
        # watch: each tool result is emitted AS IT LANDS, which is `THEME-GLASSBOX` rather than a
        # progress bar — the reads the answer will be built from are the same evidence it cites.
        #
        # READ-ONLY, and that is a product statement rather than a limitation of this route
        # (`agent/README.md`). Edit and Operate are the `propose_*` family and do not
        # exist yet; the standing instructions tell the model to say so plainly rather than
        # approximate them, which is `KNOWLEDGE-FAIL-LOUD` applied to a capability instead of a read.
        if not req.question.strip():
            return JSONResponse(status_code=400, content={"error": "question is required"})

        selection = Selection.from_dict(
            req.selection.model_dump(by_alias=True) if req.selection else None)

        def _sse():
            # The loop is SYNCHRONOUS and blocking (the OpenAI client and the in-process tool calls
            # both are), so this is a sync generator and Starlette runs it in a threadpool. The
            # queue-and-thread dance the run streams use is for work that must keep running while the
            # response is written; here the response IS the work.
            try:
                for event in run_assistant_events(
                        req.question, server=mcp_server, selection=selection,
                        history=req.messages):
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except AssistantError as e:
                # The loop ran out of rounds. An ERROR EVENT rather than a partial answer: a
                # half-finished investigation rendered as prose is indistinguishable from a finished
                # one. It cannot be a 502 — the status line went out with the first byte.
                log.warning("APP-ASSISTANT: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            except Exception as e:  # noqa: BLE001 — the stream is the only channel left to say it
                log.error("APP-ASSISTANT: ask failed: %s", e, exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        return StreamingResponse(_sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    return router
