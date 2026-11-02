"""SPEC-READ-REF / RUN-READ-BARRIER — how much data is behind one READ.

**KEPT AS THE OVERRIDE'S SURFACE.** Per-read subject counts would come from tables ``RUN-READ-BARRIER``
materializes, but read materialization is not something a reader's evaluation does: ``CQL_MATERIALIZE_RETRIEVES``
defaults to ``none``. In ordinary operation this module answers with an EMPTY set, the client renders
absence, and a read's volume is not available — permanently rather than pending. This is the honest report:
``SPEC-CLAUSE-PROBE`` rejected clause-level probing because the DEFINE is the accountability boundary.

**THIS MODULE IS NOT DEAD CODE.** ``CQL_MATERIALIZE_RETRIEVES`` is a live authoring switch (``all`` for
single-measure diagnosis, ``costly`` as the eventual fleet setting), and this is the ONE path that reports
what it produced. Deleting it would mean rebuilding it the day the override ships; keeping it costs a
join that answers empty. What follows is the mechanism, unchanged, and it runs only when that switch is on.

**THE JOIN IS BY DESCRIPTOR, NEVER BY NAME.** A materialized read's name is content-addressed
(``scan_<type>_<hash8>``) and rebinding a value set intentionally yields a new one, so a name is not an
identity a client can hold. What identifies one is its resolved descriptor — resource type, profile,
code property, value-set id — which the orchestrator records beside the read (``pipeline_defines.read``)
at the moment it writes the barrier. The client asks in the terms it has, derived from the ELM.

**A READ NEVER MATERIALIZED IS ABSENT, NEVER ZERO** (``SPEC-METRIC``). This is now the ordinary case
rather than the edge one: with the override off nothing is materialized at all, so every read is absent
and that is the true report. Emitting 0 would say "this read matched nothing" where the truth is
"nobody counted".

**WHY IT WAS BUILT, kept as history.** The engine mounts a read as the relation ``retrieve_<Type>``,
which is per resource TYPE and whose predicates are ADVISORY hints (``DATA-RETRIEVE-VIEW``) — so it could
count the base resource type but not filtered subsets. Applying the value-set filter here would
mean re-implementing membership semantics in the client of an engine that owns them, which
``APP-DECOUPLE`` forbids. ``RUN-READ-BARRIER`` changed the shape of the problem rather than the rule:
each RESOLVED retrieve became its own table with the filter already applied by the engine's own semantics,
so counting one was counting a table. That reasoning still holds — it is the materialization it depended
on that a reader's evaluation no longer performs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..results import Execute, PinnedRelation, count_relations, relation_columns
from .evaluation import ClosureSource
from .evaluation import MetricsUnavailable
from .model import PROVENANCE_RUN

log = logging.getLogger("cql_databricks.result.insights.reads")


def read_metrics(
    *,
    library: str,
    version: str,
    data_source_id: str,
    period_start: str | None,
    period_end: str | None,
    eval_id: str,
    draft: str,
    reads: list[dict],
    closure: "ClosureSource",
    results_catalog: str,
    execute: Execute,
) -> dict:
    """Count every materialized read this evaluation produced, keyed back to the client's own reads.

    ``reads`` is ``[{"key", "type", "profiles", "valueSetId"}]`` as the CLIENT derived them from the ELM
    (``specRetrieves.ts``). It owns that determination — one model per determination,
    ``APP-SPEC-MODEL`` — and a server-side re-derivation of what a measure reads could disagree with
    the document rendering it. What this adds is the figures.

    The response is keyed by the client's ``key``, so the caller joins on the identity it already holds
    rather than on a table name it has no business knowing.
    """
    # RUN-CLIENT-RESULT-SETS: the complete selection's identity, which the closure is checked against.
    suffix = eval_id

    # Every read this evaluation MATERIALIZED, across the whole include closure. A materialized read
    # is SHARED — the engine content-addresses it so two measures issuing the same resolved retrieve
    # get one table — so it can be recorded under any library in the closure, and the search has to
    # span all of them.
    #
    # Named `materialized`, not `reads`: `reads` is this function's PARAMETER — the reads the client
    # asked about — and the whole job below is matching the asked-for set against the written one.
    materialized: list[tuple[PinnedRelation, dict]] = []
    libraries = closure.closure(data_source_id, suffix, root=library, draft=draft)
    for entry in libraries.values():
        for define in entry.defines:
            # The descriptor is the discriminator, never the name (`RUN-READ-BARRIER`).
            if define.read:
                materialized.append((entry.relation(define.mv_name), define.read))

    if not materialized:
        # NOT an error, and deliberately distinguished from one. `MetricsUnavailable` means "this
        # measure was never run here"; an evaluation that ran with materialization off has reads it
        # never wrote, and the honest report is an empty set the client renders as absence.
        if not libraries:
            raise MetricsUnavailable(
                f"no compiled libraries for data source {data_source_id!r} at evaluation {suffix} — "
                f"the measure has not been run against this data source and measurement period")
        log.info("SPEC-READ-REF: evaluation %s materialized no retrieves — reads are absent, not zero",
                 suffix)
        return _payload(library, version, [])

    columns = relation_columns(results_catalog, [ref for ref, _ in materialized])
    counts = count_relations(execute, columns, eval_id=suffix)

    out: list[dict] = []
    for read in reads:
        ref = _match(read, materialized)
        if ref is None or ref not in counts:
            continue  # never materialized — absent, not zero
        subjects, elements = counts[ref]
        entry: dict = {"key": read.get("key"), "subjects": subjects}
        if elements is not None:
            entry["elements"] = elements
        out.append(entry)

    return _payload(library, version, out)


def _match(read: dict, materialized: list[tuple[PinnedRelation, dict]]) -> PinnedRelation | None:
    """The materialized read answering one requested read, or ``None``.

    PROFILE FIRST, AND IT IS THE EXACT DISCRIMINATOR. A measure that reads Condition through two
    QICore profiles issues two retrieves against one resource type, and the profile is the only thing
    telling them apart — which is exactly the case ``SPEC-READ-REF`` exists for ("recorded on the
    problem list or as an encounter diagnosis"). The client's profiles are the ELM's ``templateId``
    last segments and the descriptor's is the same value, so this matches something the compiler emitted
    rather than a naming convention.

    TYPE ALONE IS A LAST RESORT and only where it is UNAMBIGUOUS. Falling back to "the first materialized read of
    this resource type" would silently attribute the encounter-diagnosis count to the problem-list
    phrase — a wrong number rendered with the same authority as a right one, which is worse than the
    absence mark the caller shows instead.
    """
    profiles = {p for p in (read.get("profiles") or []) if p}
    resource = read.get("type")

    if profiles:
        for ref, descriptor in materialized:
            if descriptor.get("profile") in profiles:
                return ref

    by_type = [ref for ref, descriptor in materialized if descriptor.get("resourceType") == resource]
    return by_type[0] if len(by_type) == 1 else None


def _payload(library: str, version: str, reads: list[dict]) -> dict:
    return {
        "library": library,
        "version": version,
        "provenance": PROVENANCE_RUN,
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "reads": reads,
    }
