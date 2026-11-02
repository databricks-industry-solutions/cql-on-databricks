"""AGENT-SAVED-RESULTS — find a calculation that was already made, and summarize it.

``find_results`` · ``get_population_summary``.

**NOTICE, THEN ASK.** A population question usually has an answer already: a Designer run in the environment's
``draft_results``, or a Job's published results in its ``results`` (`RUN-CLIENT-DRAFT-RESULTS`). ``find_results``
says whether the caller's own, or an official one, exists for exactly this measure, environment and period, and when
it was computed (`RUN-CLIENT-RESULT-OWNERSHIP`). It never computes; the model tells the user and asks whether to reuse it.

**THE SAME FIGURES THE DESIGNER SHOWS.** ``get_population_summary`` counts each population as written and
scores the measure with the client's scorer (`RUN-CLIENT-MEASURE-SCORE`), over one complete selection.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cql_databricks.resolvers.interfaces import Resolvers
from cql_databricks.result import ResultsUnavailable, prepare_calculation, resolve_results
from cql_databricks.result.insights.score import POPULATIONS, selection_score
from cql_databricks.result.selection_reading import (SelectionClosure, open_selection, selection_address,
                                                     selection_counts)


@dataclass(frozen=True)
class SavedResultsEnv:
    """The caller's transports and the resolvers of the environment the tools run in, resolved per call (`SEC-OBO`,
    `AGENT-DEFAULT-ENVIRONMENT`). ``resolvers.results`` finds the caller's own results, then the official ones;
    ``readers`` gives the resolvers a result-set address reopens through, in the environment it names."""

    run: Any
    content: Any
    compiler: Any
    resolvers: Resolvers
    readers: Callable[[dict], Resolvers]
    runtime_catalog: str
    runtime_schema: str
    scoring_for: Callable[[str, str, str | None], str | None]
    #: RUN-ADDRESS-LOCATION — reads a folder location as the caller; a reopen replays through it.
    files: Any = None


def find_results(library: str, version: str, period: tuple[str | None, str | None], *,
                 env: SavedResultsEnv, location: str | None = None) -> dict[str, Any]:
    """AGENT-SAVED-RESULTS, RUN-CLIENT-REQUEST-REOPEN — a saved calculation of exactly this request in the
    environment: the caller's own in its ``draft_results``, else the official one in its ``results``
    (`RUN-CLIENT-RESULT-OWNERSHIP`), or a miss. Reads only: a miss is ``found: false``, never a computation."""
    prepared = prepare_calculation(content=env.content, run=env.run, compiler=env.compiler,
                                   resolvers=env.resolvers, library=library, version=version, period=period,
                                   runtime_catalog=env.runtime_catalog, runtime_schema=env.runtime_schema,
                                   location=location, files=env.files)
    try:
        results = resolve_results(prepared, *prepared.targets, results=env.resolvers.results, run=env.run)
    except ResultsUnavailable as error:
        return {"found": False, "reason": str(error)}
    completed = [o.result.completed_at for o in results.outcomes if o.result is not None]
    schemas = sorted({".".join(o.result.artifact.table[:2]) for o in results.outcomes if o.result is not None})
    return {"found": True, "library": prepared.library, "version": prepared.version,
            "environment": env.resolvers.environment, "period": prepared.request["period"],
            "computedAt": max(completed).isoformat() if completed else None,
            "schemas": schemas, "resultSet": selection_address(results)}


def get_population_summary(result_set: dict, *, env: SavedResultsEnv, scoring: str | None = None) -> dict[str, Any]:
    """RUN-CLIENT-MEASURE-SCORE — each population counted as written, and the measure's score."""
    results = open_selection(result_set, run=env.run, resolvers=env.readers(result_set), content=env.content,
                             compiler=env.compiler, files=env.files)
    request = results.request or {}
    origins = _origins(results)
    root = request.get("library") or next((o["library"] for o in origins.values()), "")
    version = next((o["version"] for o in origins.values() if o["library"] == root), "")
    declared = [o["define"] for o in origins.values() if o["library"] == root]
    counted = selection_counts(results, execute=env.run.rows)
    populations = {o["define"]: counted[name][0] for name, o in origins.items()
                   if o["library"] == root and o["define"] in POPULATIONS and name in counted}
    declared_scoring = scoring or env.scoring_for(root, version, request.get("location"))
    out: dict[str, Any] = {"library": root, "version": version, "environment": request.get("environment"),
                           "period": request.get("period"), "populations": populations, "provenance": "engine",
                           "resultSet": result_set}
    if declared_scoring:
        closure = SelectionClosure(results, root=root)
        out["score"] = selection_score(library=root, data_source_id=closure.environment,
                                       eval_id=results.selection.selection_id, draft="", closure=closure,
                                       results_catalog=results.selection.destination[0], execute=env.run.rows,
                                       scoring=declared_scoring, declared=declared).to_dict()
    else:
        out["score"] = None
        out["scoreNote"] = "the measure declares no scoring; pass scoring to score it"
    return out


def _origins(results) -> dict[str, dict]:
    """Each named output's compiler origin: library, version and define."""
    import json

    by_key = {d.key: json.loads(d.origin_json) for d in results.selection.definitions}
    return {name: by_key[key] for name, key in results.selection.targets.items()}
