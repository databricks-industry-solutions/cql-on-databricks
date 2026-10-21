"""The compiler proxy (`SVC-PROTOCOL`, `BUILD-COMPILE-LINK`).

Everything here reaches the CQL Compiler Service over HTTP and holds no state of its own. It is a
PROXY and not a compiler: `APP-DECOUPLE` is what lets the BFF have no engine dependency at all, and the
one thing this layer adds is the include closure — resolved from knowledge, then sent as `deps` so the
engine can link a multi-library measure's cross-library `FunctionRef`s.

The sample data source is provisioned by the deploy (`scripts/deploy/provision-samples.py`, CONTENT-SAMPLE-PRELOAD); a
rerun of the deploy step repairs it.
"""
from __future__ import annotations

from typing import Any

import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cql_databricks_common.compiler import CompilerClient
from cql_databricks_common.compiler import CompilerError
from cql_databricks_designer.explain.reference_set import build_reference_set
from cql_databricks.compiler.resolve import resolve, translate_includes, translate_root
from cql_databricks.data_bindings import ContentResolver

from cql_databricks_designer.dto import TranslateRequest

log = logging.getLogger("cql_databricks_designer")


def router(*, compiler: CompilerClient, content: ContentResolver) -> APIRouter:

    router = APIRouter()

    @router.get("/api/modeled-types")
    def modeled_types() -> dict[str, Any]:
        # DATA-SOURCE-PROFILE: the FHIR resource types the engine actually models (SVC-RESOURCE-SCHEMA), so
        # the Data Sources editor offers only bindable entities (APP-NO-UI-MOCK). Best-effort — if the
        # compiler is unreachable the editor falls back to its static list, so a degraded compiler yields
        # an empty set + a flag rather than a hard error.
        try:
            return {"types": compiler.list_modeled_types(), "fallback": False}
        except CompilerError as e:
            log.warning("modeled-types unavailable, editor will use its static fallback: %s", e)
            return {"types": [], "fallback": True}

    @router.post("/api/translate")
    def translate(req: TranslateRequest) -> dict[str, Any]:
        # KNOWLEDGE-LAKE-DIRECT, BUILD-RESOLVE-COMPILE: when the caller doesn't pass explicit includes, resolve the
        # ``include`` closure from the knowledge corpus with the resolution every compiler caller uses, so
        # multi-library measures validate as authored. A truly-unresolvable include is surfaced as a diagnostic.
        resolution = resolve(content, cql=req.cql or "")
        has_explicit_includes = bool(req.includes)
        try:
            result = (compiler.translate(req.cql or "", req.includes) if has_explicit_includes
                      else translate_root(compiler.translate, resolution).to_dict())
        except CompilerError as e:
            return JSONResponse(status_code=502, content={"error": str(e)})
        extra = [] if has_explicit_includes else resolution.unresolved_messages()
        diagnostics = [*extra, *result.get("diagnostics", [])]
        response: dict[str, Any] = {
            "elm": result.get("elm"), "diagnostics": diagnostics,
            "ok": bool(result.get("ok")) and len(extra) == 0,
        }
        if req.include_elm and not has_explicit_includes:
            response["includes"] = [t.to_dict() for t in translate_includes(_reported(compiler), resolution)]
            # SPEC-EXPLAIN-INPUTS — the resolved reference set rides back WITH the ELM it was derived
            # from, rather than being fetched from a route of its own.
            #
            # WHY HERE AND NOT A `GET .../reference-set`. A separate route would have to compile the
            # closure a SECOND time to answer, and the two compiles could disagree — the client would
            # then hold an ELM-derived model and a reference set built from a different ELM, which is
            # precisely the two-determinations failure this is meant to END. Deriving it from the ELM
            # already in hand costs one walk, guarantees they describe the same compilation, and is the
            # only shape in which "one determination" is a property of the system rather than a hope.
            #
            # The BATCH case keeps its own path: `reference_set.py` has a CLI. That is not a second
            # derivation — it is the same function, run from a different entry point.
            response["referenceSets"] = _reference_sets(response)
        return response

    def _reported(client: CompilerClient):
        """APP-SPEC-MODEL — an include the compiler cannot reach is that include's diagnostic, not the request's 502:
        the editor still shows the root and every include that did translate."""
        def translate(cql: str, includes) -> dict[str, Any]:
            try:
                return client.translate(cql, list(includes))
            except CompilerError as e:
                return {"elm": None, "diagnostics": [str(e)], "ok": False}
        return translate

    def _reference_sets(translated: dict[str, Any]) -> list[dict[str, Any]]:
        """SPEC-EXPLAIN-INPUTS — one reference set per library in the closure, root first.

        Derived from the ELM in this very response, so the client's document model and the reference set
        it resolves references against cannot describe two different compilations (`APP-SPEC-MODEL`).

        A library whose ELM is absent or unparseable is SKIPPED rather than faked: it means that library
        failed to compile, its diagnostics are already on the response, and an empty reference set for it
        would read as "this library reads nothing".
        """
        out: list[dict[str, Any]] = []
        for elm_json in [translated.get("elm"), *(i.get("elm") for i in translated.get("includes", []))]:
            if not elm_json:
                continue
            try:
                out.append(build_reference_set(json.loads(elm_json)).to_dict())
            except (ValueError, TypeError, AttributeError) as e:
                # Non-fatal by design: the document stands without it (references degrade to prose),
                # and taking a whole compile down over one unparseable library would be the wrong trade.
                log.warning("Reference set not derivable for one library in the closure: %s", e)
        return out

    return router
