"""AGENT-SAVED-RESULTS — find a calculation that was already made, and summarize it.

``find_results`` · ``get_population_summary``.

**NOTICE, THEN ASK.** A population question usually has an answer already: a Designer run in the user's
private schema, or a Job's published results in a result scope (`RUN-CLIENT-PUBLISHED`). ``find_results``
says whether one exists for exactly this measure, data source and period, and when it was computed. It
never computes; the model tells the user and asks whether to reuse it.

**THE SAME FIGURES THE DESIGNER SHOWS.** ``get_population_summary`` counts each population as written and
scores the measure with the client's scorer (`RUN-CLIENT-MEASURE-SCORE`), over one complete selection.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cql_databricks.result import (ResultsUnavailable, find_private_destination, prepare_calculation,
                                   resolve_results)
from cql_databricks.result.insights.score import POPULATIONS, selection_score
from cql_databricks.result.selection_reading import (SelectionClosure, open_selection, selection_address,
                                                     selection_counts)


@dataclass(frozen=True)
class SavedResultsEnv:
    """The caller's transports and the deployment's catalogs, resolved per call (`SEC-OBO`).

    ``result_scopes`` are the ``catalog.schema`` scopes results are read from after the private schema
    (`CLIENT-DISCOVERY-SCOPE`, `DEC-RESULT-SCOPE`)."""

    run: Any
    content: Any
    compiler: Any
    private_catalog: str
    runtime_catalog: str
    runtime_schema: str
    result_scopes: tuple[str, ...]
    scoring_for: Callable[[str, str, str | None], str | None]
    #: RUN-ADDRESS-LOCATION — reads a folder location as the caller; a reopen replays through it.
    files: Any = None


def find_results(library: str, version: str, data_source: str, period: tuple[str | None, str | None], *,
                 env: SavedResultsEnv, location: str | None = None) -> dict[str, Any]:
    """RUN-CLIENT-PUBLISHED, RUN-CLIENT-REQUEST-REOPEN — a saved calculation of exactly this request, or a
    miss. Reads only: a miss is ``found: false``, never a computation."""
    prepared = prepare_calculation(content=env.content, run=env.run, compiler=env.compiler, data_source=data_source,
                                   library=library, version=version, period=period,
                                   runtime_catalog=env.runtime_catalog, runtime_schema=env.runtime_schema,
                                   location=location, files=env.files)
    # The client reads the destination first, then every catalog.schema in the configured catalog scopes, and
    # skips a scope equal to the destination (`CLIENT-DISCOVERY-SCOPE`), so the scopes are not passed again here.
    private = find_private_destination(env.private_catalog, run=env.run)
    if private is not None:
        destination, purpose = private, "private"
    elif env.result_scopes:
        destination, purpose = tuple(env.result_scopes[0].split(".", 1)), "published"
    else:
        return {"found": False, "reason": "no private results schema and no result scope to look in"}
    try:
        results = resolve_results(prepared, *prepared.targets, run=env.run, destination=destination,
                                  purpose=purpose)
    except ResultsUnavailable as error:
        return {"found": False, "reason": str(error)}
    completed = [o.result.completed_at for o in results.outcomes if o.result is not None]
    schemas = sorted({".".join(o.result.artifact.table[:2]) for o in results.outcomes if o.result is not None})
    return {"found": True, "library": prepared.library, "version": prepared.version, "dataSource": data_source,
            "period": prepared.request["period"], "computedAt": max(completed).isoformat() if completed else None,
            "schemas": schemas, "resultSet": selection_address(results)}


def get_population_summary(result_set: dict, *, env: SavedResultsEnv, scoring: str | None = None) -> dict[str, Any]:
    """RUN-CLIENT-MEASURE-SCORE — each population counted as written, and the measure's score."""
    results = open_selection(result_set, run=env.run, content=env.content, compiler=env.compiler, files=env.files)
    request = results.request or {}
    origins = _origins(results)
    root = request.get("library") or next((o["library"] for o in origins.values()), "")
    version = next((o["version"] for o in origins.values() if o["library"] == root), "")
    declared = [o["define"] for o in origins.values() if o["library"] == root]
    counted = selection_counts(results, execute=env.run.rows)
    populations = {o["define"]: counted[name][0] for name, o in origins.items()
                   if o["library"] == root and o["define"] in POPULATIONS and name in counted}
    declared_scoring = scoring or env.scoring_for(root, version, request.get("location"))
    out: dict[str, Any] = {"library": root, "version": version, "dataSource": request.get("data_source"),
                           "period": request.get("period"), "populations": populations, "provenance": "engine",
                           "resultSet": result_set}
    if declared_scoring:
        closure = SelectionClosure(results, root=root)
        out["score"] = selection_score(library=root, data_source_id=closure.data_source,
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
