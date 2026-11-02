"""APP-COHORT-ALGEBRA, APP-DECISIVE-COUNT — where the cohort went, and why it left.

The measure flow (`APP-MEASURE-FLOW`) draws a spine of populations and, once there is a run, a rail
of everything that departed from each one. The spine's counts are `insights/metrics.py`'s. This module
answers the harder half: of the subjects who entered a gate and did not pass it, which criterion was
responsible.

**IT IS A SET DIFFERENCE, NOT A NEGATION.** "How many were in the Initial Population and failed the
encounter criterion" is an anti-join on subject id over relations the run already materialized
(`SDP-ALL-DEFINES`). Nothing is inferred, no negative expression is authored, and no second run is paid
for. The universe is bounded — the subjects the measure was evaluated against — so the complement of a
criterion within it is a fact, not the unprovable negative it can look like.

**RESOLUTION EQUALS THE AUTHOR'S FACTORING, and that is the honest ceiling.** This can say which named
criterion a subject failed. It cannot say why the criterion was false — whether a patient was too young
or too old is below the level the CQL was written at, and a population written as one unfactored boolean
yields exactly one reason. That is a true report about an under-factored measure rather than a defect
here. The shape of the data is a different question and a different mechanism: `insights/profile.py`
already cuts a define's subjects demographically.

**THE GATES ARE SENT BY THE CLIENT.** There is no server-side ELM graph, and building one would be the
second opinion `APP-SPEC-MODEL` exists to prevent. `specDocument.ts` already derives each population's
Concepts (`SPEC-CONCEPTS` / `SPEC-PLACEMENT`); the client re-reads that determination into a request and
this module adds figures to it and nothing else. Same argument as the metrics endpoint's POST.

**SUBJECT-LEVEL DATA NEVER REACHES THE SPA** (section 10.2, property 2). The warehouse returns a crosstab
of counts; the browser receives figures. That is a PHI boundary before it is a performance one, and it is
why the arithmetic is in SQL rather than over per-member results on the client. `test_insights_flow.py`
asserts it by walking the payload, because it is the one property no reviewer can check by reading.

**FAN-OUT IS DEFEATED BEFORE ANY JOIN.** A relation is not one row per subject — one measured
denominator-exclusions table carries 57,565,315,157 rows for 2,713 distinct subjects. So every relation
is collapsed to its DISTINCT MET SUBJECTS in a CTE first; after that the joins are 1:1 over thousands of
rows. `COUNT(*)` is banned here as everywhere else on a metric path (`relation.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..relation import SUBJECT_ID, literal, met_expr, quote, value_column
from ..results import Execute, RelationRef, relation_columns, result_relation
from .evaluation import ClosureSource, MetricsUnavailable, locate
from .model import PROVENANCE_RUN, _clean

log = logging.getLogger("cql_databricks.result.insights.flow")

#: A gate's POLARITY, which is the one thing that differs between an entry conjunction and an exclusion
#: disjunction. `all-of`: a subject fails a criterion by NOT meeting it. `any-of`: by meeting it — the
#: exclusion sense, where "decisive" reads as *excluded by exactly one reason*. One boolean sense rather
#: than two code paths (`APP-DECISIVE-COUNT`, "the definition inverts correctly across polarity").
POLARITY_ALL = "all-of"
POLARITY_ANY = "any-of"


@dataclass(frozen=True)
class GateRequest:
    """One gate the client is asking about, as `specDocument.ts` derived it."""

    #: The population being entered — the stage this gate admits subjects to.
    stage: str
    stage_library: str
    #: The cohort entering. ``None`` means the evaluated cohort (the Patient binding), which is the same
    #: convention ``SpecMetric.base`` uses for a define with no enclosing population.
    base: str | None
    base_library: str | None
    polarity: str
    #: The sibling criteria this gate is made of, ``(library, define)``.
    criteria: list[tuple[str, str]]


@dataclass
class CriterionDrop:
    """One criterion's share of a gate's departures.

    ``decisive`` is the figure the rail LEADS with and it is the only one safe to add up: it counts the
    subjects for whom flipping this criterion alone would have changed the outcome. ``any_cause`` counts
    everyone who failed it at all; those overlap across criteria and overshoot the cohort, so they are
    shown beside the decisive count and never instead of it (`APP-DECISIVE-COUNT`).
    """

    library: str
    define: str
    decisive: int
    any_cause: int

    def to_dict(self) -> dict[str, Any]:
        return {"library": self.library, "define": self.define,
                "decisive": self.decisive, "anyCause": self.any_cause}


@dataclass
class FlowGate:
    """One gate's departures, decomposed.

    ``decisive + multiple + passed == entered`` BY CONSTRUCTION: every entering subject fails some
    number k of the sibling criteria, and the three buckets are k==1 (attributed to one criterion),
    k>=2, and k==0. That partition is the whole justification for leading with the decisive count, and
    `test_insights_flow.py` asserts it as arithmetic rather than trusting this sentence.

    ``reached`` and ``passed`` are BOTH reported and must not be collapsed. ``passed`` is how many met
    every sibling criterion; ``reached`` is how many the stage's own define is true of. They are equal
    only when the population is exactly the conjunction of its Concepts — and where they differ, the
    gap is a shortfall the reader should see rather than have silently attributed to a criterion.
    """

    stage: str
    library: str
    base: str | None
    polarity: str
    entered: int
    reached: int
    passed: int
    #: Failed two or more of the sibling criteria — the remainder that completes the partition.
    multiple: int
    criteria: list[CriterionDrop] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "stage": self.stage,
            "library": self.library,
            "base": self.base,
            "polarity": self.polarity,
            "entered": self.entered,
            "reached": self.reached,
            "passed": self.passed,
            "multiple": self.multiple,
            "criteria": [c.to_dict() for c in self.criteria],
        })


@dataclass
class CohortFlow:
    """Every gate's departures for one measure, in one payload — one point in time, one round trip."""

    library: str
    version: str
    provenance: str
    generated: str
    gates: list[FlowGate] = field(default_factory=list)
    #: Gates that could NOT be decomposed, by stage name. See ``cohort_flow`` for why they are omitted
    #: whole rather than partially reported.
    omitted: list[str] = field(default_factory=list)
    caveat: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "library": self.library,
            "version": self.version,
            "provenance": self.provenance,
            "generated": self.generated,
            "gates": [g.to_dict() for g in self.gates],
            "omitted": self.omitted,
            "caveat": self.caveat,
        })


def cohort_flow(
    *,
    library: str,
    version: str,
    data_source_id: str,
    period_start: str | None,
    period_end: str | None,
    eval_id: str,
    draft: str,
    gates: list[GateRequest],
    closure: "ClosureSource",
    patient_relation: str | None,
    results_catalog: str,
    execute: Execute,
) -> CohortFlow:
    """Decompose every gate's departures, in ONE crosstab over the run's result tables.

    A GATE WITH NO DECLARED BASE ENTERS FROM THE EVALUATED COHORT — the bound Patient retrieve, the
    same read `metrics.py` takes for the evaluated total and `profile.py` joins its strata against. It
    has to be: the first gate on the spine is the Initial Population and there is no population above
    it, so with no binding there is no cohort for those subjects to have departed FROM. Taking the
    stage's own relation instead would make the entering cohort the people who already passed, and the
    gate would report that nobody failed anything.

    A GATE WITH ANY UNLOCATABLE CRITERION IS OMITTED WHOLE. This is stricter than the metric set's
    "absent, not zero" (`metrics.py`) and deliberately so: there a missing relation is a missing figure,
    but here it is a WRONG one — a criterion nobody can read counts as "not failed" for every subject,
    which silently inflates every sibling's decisive count and still sums to a plausible-looking
    partition. A gate that cannot be decomposed correctly is not decomposed.
    """
    # RUN-EVAL-ID-INPUTS: the evaluation set, named by the route (`evaluation_key.EvaluationKeys`).
    suffix = eval_id
    libraries = closure.closure(data_source_id, suffix, root=library, draft=draft)
    if not libraries:
        raise MetricsUnavailable(
            f"no compiled libraries for data source {data_source_id!r} at evaluation {suffix} — "
            f"the measure has not been run against this data source and measurement period")

    # Every relation any gate mentions, resolved through the compiler's own record of what it emitted
    # (`pipeline_defines`), never by re-deriving a table name from a define name.
    wanted: dict[tuple[str, str], RelationRef | None] = {}
    for gate in gates:
        for lib, name in [(gate.stage_library, gate.stage), *gate.criteria]:
            wanted.setdefault((lib, name), locate(libraries, lib, name, results_catalog))
        if gate.base and gate.base_library:
            wanted.setdefault((gate.base_library, gate.base),
                              locate(libraries, gate.base_library, gate.base, results_catalog))

    refs = [r for r in wanted.values() if r is not None]
    columns = relation_columns(results_catalog, refs, execute=execute) if refs else {}

    def resolved(key: tuple[str, str]) -> RelationRef | None:
        ref = wanted.get(key)
        return ref if ref is not None and ref in columns else None

    usable: list[GateRequest] = []
    omitted: list[str] = []
    for gate in gates:
        needed = [(gate.stage_library, gate.stage), *gate.criteria]
        if gate.base and gate.base_library:
            needed.append((gate.base_library, gate.base))
        # A base-less gate needs the Patient binding for its entering cohort; without one there is
        # nothing to have departed from, so the gate is omitted for the same reason an unlocatable
        # criterion omits one — a figure that cannot be stated honestly is not stated.
        needs_binding = not (gate.base and gate.base_library)
        if (gate.criteria
                and all(resolved(k) is not None for k in needed)
                and not (needs_binding and not patient_relation)):
            usable.append(gate)
        else:
            omitted.append(gate.stage)

    built: list[FlowGate] = []
    if usable:
        ctes, cte_of = _subject_ctes(usable, resolved, columns, suffix)
        if any(not (g.base and g.base_library) for g in usable):
            ctes.insert(0, _evaluated_cte(patient_relation))
        sql = _crosstab(usable, cte_of, ctes)
        rows = execute(sql) or []
        built = _read_rows(usable, rows)

    return CohortFlow(
        library=library, version=version, provenance=PROVENANCE_RUN,
        generated=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        gates=built, omitted=omitted,
        caveat=_caveat(omitted),
    )


#: The CTE name for the evaluated cohort — every subject the measure ran against, before any criterion.
EVALUATED = "s_all"


def _evaluated_cte(patient_relation: str | None) -> str:
    """The evaluated cohort, from the environment's Patient view (`BIND-PUBLISHED-VIEW`).

    THE ENVIRONMENT'S DATA IS THE POPULATION: a retrieve's rows depend only on its request and the data it is bound to
    (`EMIT-ROOT-INDEPENDENT`), and a measure's population is chosen by choosing its environment (`CLIENT-ENVIRONMENT`).
    """
    return (f"{EVALUATED} AS (SELECT DISTINCT {SUBJECT_ID} "
            f"FROM {patient_relation})")


def _caveat(omitted: list[str]) -> str | None:
    """State an omission, and do not claim a cause for it.

    A gate goes missing because a criterion has no materialized relation here, and WHY that is — the
    define was never reached by this measure's pipeline, or the library was not compiled at this
    evaluation — is not something this read can tell apart. So it reports what it did not answer.
    """
    if not omitted:
        return None
    listed = ", ".join(sorted(omitted))
    return (f"{len(omitted)} gate(s) not decomposed because at least one of their criteria has no "
            f"result table at this evaluation: {listed}. A partial decomposition would attribute "
            f"those subjects to the wrong criterion, so the gate is omitted rather than approximated.")


def _subject_ctes(
    gates: list[GateRequest],
    resolved,
    columns: dict[RelationRef, dict[str, str]],
    eval_id: str,
) -> tuple[list[str], dict[tuple[str, str], str]]:
    """One CTE per DISTINCT relation, collapsing it to the subjects who met it.

    This is the step that makes the whole query safe against the fan-out: after it every operand is at
    most one row per subject, so the joins below are 1:1 over thousands of rows rather than billions.
    Deduplicated across gates — a measure's `Initial Population` is the base of several — so the 57-billion
    row relation is scanned exactly once.

    The membership predicate is `relation.met_expr`, REUSED and never restated: a boolean define
    contributes its own value (CQL's three-valued logic makes false and null both "not in") and any
    other type contributes presence. A second copy here would let this reader call a subject met that
    `results/query.py` did not.
    """
    ctes: list[str] = []
    cte_of: dict[tuple[str, str], str] = {}
    keys: list[tuple[str, str]] = []
    for gate in gates:
        keys.append((gate.stage_library, gate.stage))
        keys.extend(gate.criteria)
        if gate.base and gate.base_library:
            keys.append((gate.base_library, gate.base))

    for key in keys:
        if key in cte_of:
            continue
        ref = resolved(key)
        if ref is None:
            continue
        cols = columns[ref]
        col = value_column(cols)
        met = met_expr(col, cols.get(col) if col else None)
        name = f"s_{len(cte_of)}"
        cte_of[key] = name
        ctes.append(
            f"{name} AS (SELECT DISTINCT {met} AS {SUBJECT_ID} "
            f"FROM {result_relation(ref, eval_id)} WHERE {met} IS NOT NULL)")
    return ctes, cte_of


def _crosstab(gates: list[GateRequest], cte_of: dict[tuple[str, str], str], ctes: list[str]) -> str:
    """ONE statement for every gate, whatever the gate or criterion count.

    Each gate gets a per-subject CTE carrying one failure flag per sibling criterion plus their sum
    ``k``, and then a fixed set of branches over it. Every branch projects the SAME four columns so the
    ``UNION ALL`` is shape-safe — the discipline `results/query.py._count_branch` already follows.

    Criteria are addressed POSITIONALLY (``c0``, ``c1``) rather than by name. Define names are
    author-given text, they collide across libraries routinely (`Denominator` in two variants of one
    measure, `SPEC-ANCHOR`), and the server holds the index → (library, define) map anyway.
    """
    gate_ctes: list[str] = []
    branches: list[str] = []

    for gi, gate in enumerate(gates):
        base_key = (gate.base_library, gate.base) if gate.base and gate.base_library else None
        base_cte = cte_of[base_key] if base_key else None
        stage_cte = cte_of[(gate.stage_library, gate.stage)]

        # An `all-of` gate fails a criterion by NOT meeting it; an `any-of` (the exclusion disjunction)
        # by meeting it. One operator, flipped — 10.3's polarity inversion.
        miss = "IS NULL" if gate.polarity == POLARITY_ALL else "IS NOT NULL"

        flags = []
        joins = []
        for ci, key in enumerate(gate.criteria):
            cte = cte_of[key]
            joins.append(f"LEFT JOIN {cte} c{ci} ON c{ci}.{SUBJECT_ID} = b.{SUBJECT_ID}")
            flags.append(f"CASE WHEN c{ci}.{SUBJECT_ID} {miss} THEN 1 ELSE 0 END AS f{ci}")
        joins.append(f"LEFT JOIN {stage_cte} st ON st.{SUBJECT_ID} = b.{SUBJECT_ID}")

        # With no declared base the entering cohort is the EVALUATED cohort — see `cohort_flow`. Not
        # the stage's own relation: that is the population being entered, so the gate would be asking
        # which criterion the people who already passed had failed, and the answer is none of them.
        entering = base_cte or EVALUATED
        k = " + ".join(f"f{ci}" for ci in range(len(gate.criteria)))
        g = f"g_{gi}"
        gate_ctes.append(
            f"{g} AS (SELECT b.{SUBJECT_ID}, "
            + ", ".join(flags)
            + f", CASE WHEN st.{SUBJECT_ID} IS NULL THEN 0 ELSE 1 END AS reached "
            f"FROM {entering} b " + " ".join(joins) + ")")
        gate_ctes.append(f"k_{gi} AS (SELECT *, {k} AS k FROM {g})")

        kt = f"k_{gi}"
        branches.append(
            f"SELECT {literal(str(gi))} AS g, {literal('__entered')} AS c, "
            f"COUNT(DISTINCT {SUBJECT_ID}) AS n, CAST(NULL AS BIGINT) AS d FROM {kt}")
        branches.append(
            f"SELECT {literal(str(gi))}, {literal('__reached')}, "
            f"COUNT(DISTINCT CASE WHEN reached = 1 THEN {SUBJECT_ID} END), CAST(NULL AS BIGINT) FROM {kt}")
        branches.append(
            f"SELECT {literal(str(gi))}, {literal('__passed')}, "
            f"COUNT(DISTINCT CASE WHEN k = 0 THEN {SUBJECT_ID} END), CAST(NULL AS BIGINT) FROM {kt}")
        branches.append(
            f"SELECT {literal(str(gi))}, {literal('__multiple')}, "
            f"COUNT(DISTINCT CASE WHEN k >= 2 THEN {SUBJECT_ID} END), CAST(NULL AS BIGINT) FROM {kt}")
        for ci in range(len(gate.criteria)):
            branches.append(
                f"SELECT {literal(str(gi))}, {literal(f'c{ci}')}, "
                f"COUNT(DISTINCT CASE WHEN f{ci} = 1 THEN {SUBJECT_ID} END), "
                f"COUNT(DISTINCT CASE WHEN f{ci} = 1 AND k = 1 THEN {SUBJECT_ID} END) FROM {kt}")

    return "WITH " + ",\n".join(ctes + gate_ctes) + "\n" + "\nUNION ALL\n".join(branches)


def _read_rows(gates: list[GateRequest], rows: list[tuple]) -> list[FlowGate]:
    """Fold the crosstab back into one `FlowGate` per gate."""
    by_gate: dict[int, dict[str, tuple[int, int | None]]] = {}
    for row in rows:
        gi = int(row[0])
        by_gate.setdefault(gi, {})[str(row[1])] = (
            int(row[2] or 0), int(row[3]) if row[3] is not None else None)

    out: list[FlowGate] = []
    for gi, gate in enumerate(gates):
        cells = by_gate.get(gi)
        if not cells:
            continue
        criteria = []
        for ci, (lib, name) in enumerate(gate.criteria):
            any_cause, decisive = cells.get(f"c{ci}", (0, 0))
            criteria.append(CriterionDrop(library=lib, define=name,
                                          decisive=decisive or 0, any_cause=any_cause))
        out.append(FlowGate(
            stage=gate.stage, library=gate.stage_library, base=gate.base, polarity=gate.polarity,
            entered=cells.get("__entered", (0, None))[0],
            reached=cells.get("__reached", (0, None))[0],
            passed=cells.get("__passed", (0, None))[0],
            multiple=cells.get("__multiple", (0, None))[0],
            criteria=criteria,
        ))
    return out
