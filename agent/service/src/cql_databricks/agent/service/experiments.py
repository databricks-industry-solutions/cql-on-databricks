"""AGENT-INLINE-EXPERIMENTS, AGENT-CQL-REVIEW — run CQL the agent wrote, and keep nothing.

``check_cql`` · ``run_cql`` · ``compare_cql``.

**AN EXPERIMENT IS CQL TEXT, NOT A REFERENCE.** Each tool takes the whole library as written. It runs
inline on the caller's warehouse session (`CLIENT-INLINE-WAREHOUSE`), from the data source as it is now,
and its views are dropped before the tool returns. Nothing is saved, tagged or reused, so nothing
official can change.

**THE CQL THAT RAN COMES BACK** (`AGENT-CQL-REVIEW`). Every answer carries the exact CQL, and its diff
against ``baseline_cql`` when one is given, so the user reviews what executed rather than the model's
account of it.

**POPULATIONS ARE COUNTED AS WRITTEN; ONLY THE SCORE IS CASCADED** (`RUN-CLIENT-MEASURE-SCORE`).
``compare_cql`` recomputes the baseline in the same call, so every difference it reports comes from the
logic change alone, not from data that changed since an official result was saved.
"""
from __future__ import annotations

import difflib
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any

from cql_databricks.result import InlineRun
from cql_databricks.result.insights.score import POPULATIONS

#: ``(cql, data_source, period, location) -> InlineRun`` — an inline run as the caller (`SEC-OBO`). The location
#: (RUN-ADDRESS-LOCATION) is where includes resolve first; None is all of tagged knowledge.
InlineRunner = Callable[[str, str, tuple[str | None, str | None], str | None], InlineRun]

#: ``(library, version, location) -> scoring or None`` — the declared scoring of a measure in scope.
ScoringLookup = Callable[[str, str, str | None], str | None]

#: ``(cql, location) -> {"library", "version", "ok", "diagnostics"}`` — translation with the include closure.
Translator = Callable[[str, str | None], dict[str, Any]]

PROVENANCE = "experiment"


def check_cql(cql: str, baseline_cql: str | None = None, *, translate: Translator,
              location: str | None = None) -> dict[str, Any]:
    """Translate ``cql`` with its includes and report every diagnostic. Runs nothing."""
    result = translate(cql, location)
    out = {"library": result.get("library") or "", "version": result.get("version") or "",
           "ok": bool(result.get("ok")), "diagnostics": list(result.get("diagnostics") or []), "cql": cql}
    if baseline_cql is not None:
        out["diff"] = diff(baseline_cql, cql)
    return out


def run_cql(cql: str, data_source: str, period: tuple[str | None, str | None], *, scoring: str | None,
            defines: list[str] | None, inline: InlineRunner, scoring_for: ScoringLookup,
            baseline_cql: str | None = None, location: str | None = None) -> dict[str, Any]:
    """Run ``cql`` inline: each define's subjects as written, and the score when one is declared."""
    with inline(cql, data_source, period, location) as run:
        return _summary(run, scoring, defines, scoring_for, baseline_cql, location)


def compare_cql(baseline_cql: str, cql: str, data_source: str, period: tuple[str | None, str | None], *,
                scoring: str | None, defines: list[str] | None, inline: InlineRunner,
                scoring_for: ScoringLookup, location: str | None = None) -> dict[str, Any]:
    """Run both inline, then compare: counts, score, and who moved in or out of each define."""
    with ExitStack() as views:
        before = views.enter_context(inline(baseline_cql, data_source, period, location))
        after = views.enter_context(inline(cql, data_source, period, location))
        baseline = _summary(before, scoring, defines, scoring_for, None, location)
        experiment = _summary(after, scoring, defines, scoring_for, baseline_cql, location)
        shared = [d for d in baseline["counts"] if d in experiment["counts"]]
        moved = _moved(before, after, shared)
    return {"baseline": baseline, "experiment": experiment,
            "changes": [{"define": d, "before": baseline["counts"][d], "after": experiment["counts"][d],
                         "entered": moved[d][0], "left": moved[d][1]} for d in shared],
            "provenance": PROVENANCE}


def diff(before: str, after: str) -> str:
    """A unified diff of two CQL texts, for the user to review."""
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile="baseline.cql", tofile="experiment.cql"))


def _summary(run: InlineRun, scoring: str | None, defines: list[str] | None, scoring_for: ScoringLookup,
             baseline_cql: str | None, location: str | None = None) -> dict[str, Any]:
    wanted = tuple(defines) if defines else (tuple(d for d in run.defines if d in POPULATIONS) or run.defines)
    unknown = [d for d in wanted if d not in run.defines]
    if unknown:
        raise ValueError(f"{unknown} are not defines of {run.library or 'this CQL'}; it declares {list(run.defines)}")
    declared = scoring or (scoring_for(run.library, run.version, location) if run.library else None)
    out: dict[str, Any] = {"library": run.library, "version": run.version, "dataSource": run.data_source,
                           "period": list(run.period), "counts": run.counts(wanted), "cql": run.cql,
                           "provenance": PROVENANCE}
    if declared:
        out["score"] = run.score(declared).to_dict()
    else:
        out["score"] = None
        out["scoreNote"] = "no scoring declared for this library; pass scoring to score it"
    if baseline_cql is not None:
        out["diff"] = diff(baseline_cql, run.cql)
    return out


def _moved(before: InlineRun, after: InlineRun, defines: list[str]) -> dict[str, tuple[int, int]]:
    """Per define, how many subjects entered (met it only after) and left (met it only before). Counts
    only: no subject id leaves the warehouse."""
    if not defines:
        return {}
    branches = []
    for i, d in enumerate(defines):
        b, a = before.subjects(d), after.subjects(d)
        branches.append(f"SELECT {i} AS d, (SELECT COUNT(*) FROM ({a} EXCEPT {b})) AS entered, "
                        f"(SELECT COUNT(*) FROM ({b} EXCEPT {a})) AS left_")
    rows = before.rows("\nUNION ALL\n".join(branches))
    return {defines[int(r[0])]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}
