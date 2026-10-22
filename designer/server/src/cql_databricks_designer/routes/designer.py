"""Designer Specification document (`SPEC-*`, `APP-*`): its prose and the synthetic deck.

The result derivations this module used to serve — the metric set, a define's profile, the cohort flow
and the read accounting — moved to the evaluator (`MOVE-DERIVATIONS`), and then into its client
(`DERIVATIONS-TO-LIBRARY`, `cql_databricks.result.insights`); `routes/runs.py` serves them.
The Designer renders them; the agent needs them as much as it does. The Data Readiness read is gone
with its module (DESIGNER-ONE-MODULE).

**Every derivation here is scoped to ONE evaluation, and that is not an implementation detail.** A
profile and a metric set are facts about a data source over a measurement period, so the same define
has different true distributions per scope and the server cannot locate the result relation without it
(`PIPE-PER-LIBRARY-SCHEMA`). Required, never defaulted: a request without a scope has no answer rather
than a stale one, and a figure silently read from a different evaluation than the one above it on the
page is the defect `APP-SPEC-MODEL` names.

The handler names here deliberately shadow the builders they call, which is why `app.py` imported those
builders ALIASED (`build_measure_metrics`, `build_define_profile`, ...). Unaliased, a handler shadowed
the module-level name of the same spelling and the route called ITSELF with the builder's keyword
arguments, on every request. The aliases move with the handlers for the same reason.
"""
from __future__ import annotations

from typing import Any

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..assistant_tools import specification_document

from .. import sample_deck
from cql_databricks.data_bindings import ContentResolver

from cql_databricks_designer.dto import TranslateRequest
from cql_databricks_designer.explain.narrations import NarrationStore

log = logging.getLogger("cql_databricks_designer")


def router(*, content: ContentResolver, narrations: NarrationStore) -> APIRouter:


    router = APIRouter()

    @router.get("/api/libraries/{name}/{version}/specification")
    def library_specification(name: str, version: str) -> JSONResponse:
        # The Specification's PROSE for a library: the Summary, and the what/why/how-to-read triple
        # per define, keyed library-qualified so the client can join it onto the structure it derives
        # from the ELM (SPEC-DOCUMENT). Structure, metrics and the Appendix are NOT here — they are
        # derived client-side, and a second copy could disagree with the first.
        #
        # AUTHORED PROSE (SPEC-MEASURE-METADATA). The measure's own FHIR resource is quoted verbatim and
        # attributed to its steward. The signed-in user's cached narration of this exact CQL
        # (APP-NARRATIVE-OVERLAY) is composed under it; authored prose wins the slot. A failed read of
        # either is logged, and its absence is visible on the page as prose that is not there.
        #
        # THE COMPOSITION ITSELF LIVES IN `assistant_tools` (`SVC-MCP-TOOLS`, `AGENT-TOOL-REGISTRY`), and this
        # route delegates to it. It is not a helper extracted for tidiness: the MCP tool
        # `get_specification` must return exactly what this route returns, or one define reads one
        # way to a person and another way to an agent — which is the drift the whole tool surface
        # exists to prevent. The independent-failure behaviour above moved with it, unchanged.
        document = specification_document(name, version, content=content, narrations=narrations)
        if document is None:
            return JSONResponse(status_code=404,
                                content={"error": f"No Specification for {name}@{version}"})
        return JSONResponse(content=document)

    @router.post("/api/sample-run")
    def sample_run(req: TranslateRequest) -> dict[str, Any]:
        # APP-NO-UI-MOCK / APP-EXEC-MODE: the synthetic Test Deck funnel, computed on the BFF (no
        # embedded data under src/). Offline — no compiler, no Spark session; deterministic + clearly
        # synthetic (deck-NNN subjects). Same RunResult shape as /api/run so the SPA renders it identically.
        return sample_deck.sample_run(req.cql or "")

    return router
