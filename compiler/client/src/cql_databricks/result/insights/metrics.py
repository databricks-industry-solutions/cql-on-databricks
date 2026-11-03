"""SPEC-METRIC, SPEC-ATTRITION — the measure's real metric set.

Every figure the Specification renders, read from the tables the pipeline already wrote: the same route and
wire shape as the sample metrics, with real numbers.

**A MEASURE'S DEFINES SPAN ITS INCLUDE CLOSURE.** A measure reads defines of itself and of each helper library it
includes, each in its own selected table. So the builder counts across all of them in one query, which is what makes
the figures comparable: `SPEC-ATTRITION` puts each figure beside its base, and a base read
a minute later than the figure it scales can contradict it.

**THE DEFINE→RELATION MAP COMES FROM THE SELECTION, NOT FROM GUESSING THE NAME.** The selection's metadata
names each define's library and its pinned table (``selection_reading.SelectionClosure``), so the map is what the
compiler actually emitted. Re-deriving a table name from a define name would be a second naming rule that
could disagree with the compiler's.

**A DEFINE THE PIPELINE DID NOT MATERIALIZE IS ABSENT, NEVER ZERO.** Only the defines a measure actually
reaches are compiled into its pipeline — a helper library may contribute 4 of its defines, not all
of them — so most library entries in the document have no relation to count. Emitting 0 for those would
state "no subject met this" where the truth is "this was never evaluated here"; `SPEC-METRIC` already
specifies a not-computed state for exactly that, and the client renders an absence mark when a define is
missing from the set.

**EVERY DEFINE IS COUNTED INSIDE A COHORT, AND THE CLIENT NAMES IT** (`SPEC-POPULATION-CASCADE`). A
measure's Numerator is written without its Denominator — HL7 CQF Measures makes each population a subset
of the one before it, and leaves that to the evaluator — so a define counted across every subject can
exceed the population it is scored against. The cascade depends on the declared scoring, which the client
owns (`specScoring.ts`); it sends each define's cohort as populations the subject must be in and must not
be in, and this module only counts. Same split as the flow's gates (`flow.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ..relation import SUBJECT_ID, met_expr, patient_rows, value_column
from ..results import Execute, PinnedRelation, count_relations, relation_columns, result_relation
from .evaluation import ClosureSource, MetricsUnavailable, locate
from .model import PROVENANCE_RUN, DefineMetric, MeasureMetrics

log = logging.getLogger("cql_databricks.result.insights.metrics")

#: A define, library-qualified: two libraries in one closure routinely declare the same name.
DefineKey = tuple[str, str]


@dataclass(frozen=True)
class Cohort:
    """SPEC-POPULATION-CASCADE — the subjects a define is counted among: in every ``all_of`` define and in
    no ``none_of`` define. Both empty is every evaluated subject.

    The client derives it from the declared scoring (`specScoring.ts`); for the Numerator of a proportion
    measure it is the Initial Population and the Denominator, less the Denominator Exclusions.
    """

    all_of: tuple[DefineKey, ...] = ()
    none_of: tuple[DefineKey, ...] = ()

    def __post_init__(self) -> None:
        if self.none_of and not self.all_of:
            raise ValueError("a cohort that removes subjects must name the cohort it removes them from")

    @property
    def everyone(self) -> bool:
        return not self.all_of


def measure_metrics(
    *,
    library: str,
    version: str,
    data_source_id: str,
    period_start: str | None,
    period_end: str | None,
    eval_id: str,
    draft: str,
    defines: list[tuple[str, str, str | None]],
    cohorts: dict[DefineKey, Cohort],
    closure: "ClosureSource",
    patient_relation: str | None,
    results_catalog: str,
    execute: Execute,
) -> MeasureMetrics:
    """Build the whole measure's metric set from the post-pipeline result tables.

    ``defines`` is ``(library, name, result_type)`` per define, as the CLIENT derived them from the ELM —
    it owns that determination (`APP-SPEC-MODEL`) and a server-side re-derivation could disagree.
    ``cohorts`` is each define's cohort, keyed ``(library, name)``, and every define must have one.
    What this function adds is the figures.

    ``result_type`` is deliberately NOT used to decide the element grain here. The relation's own schema
    decides it (`relation.element_count_expr`), because the relation is the thing being counted; a `List<…>`
    return whose relation carries no usable ordinal has no element count to give, and the result type cannot
    know that.
    """
    missing_cohort = [(lib, name) for lib, name, _ in defines if (lib, name) not in cohorts]
    if missing_cohort:
        raise ValueError(f"every define needs a cohort to be counted in (SPEC-POPULATION-CASCADE); "
                         f"none given for {missing_cohort}")

    # RUN-CLIENT-RESULT-SETS: the complete selection's identity, which the closure is checked against.
    suffix = eval_id
    libraries = closure.closure(data_source_id, suffix, root=library, draft=draft)
    if not libraries:
        raise MetricsUnavailable(
            f"no compiled libraries for data source {data_source_id!r} at evaluation {suffix} — "
            f"the measure has not been run against this data source and measurement period")

    # (library, define) -> the relation that holds it, from the compiler's own record. The cohorts'
    # populations are located with the defines: a population is normally one of them, but need not be.
    wanted = [(lib, name) for lib, name, _ in defines]
    wanted += [key for c in cohorts.values() for key in (*c.all_of, *c.none_of)]
    located: dict[DefineKey, PinnedRelation] = {}
    for define_library, name in dict.fromkeys(wanted):
        ref = locate(libraries, define_library, name)
        if ref is not None:
            located[(define_library, name)] = ref

    columns = relation_columns(results_catalog, located.values())
    counts = count_relations(execute, columns, eval_id=suffix)
    evaluated = _evaluated_subjects(patient_relation, execute=execute)

    present = [(lib, name) for lib, name, _ in defines
               if (lib, name) in located and located[(lib, name)] in counts]
    within = _counted_within(present, cohorts, located, columns, suffix, execute=execute)

    out: list[DefineMetric] = []
    for define_library, name, _ in defines:
        key = (define_library, name)
        ref = located.get(key)
        if ref is None or ref not in counts:
            continue  # never materialized here — absent, not zero
        subjects, elements = counts[ref]
        counted, cohort_subjects = (subjects, evaluated) if cohorts[key].everyone else within[key]
        out.append(DefineMetric(define=name, library=define_library, subjects=subjects,
                                counted=counted, cohort_subjects=cohort_subjects, elements=elements))

    return MeasureMetrics(
        library=library, version=version, provenance=PROVENANCE_RUN,
        generated=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        subjects=evaluated,
        caveat=_caveat(defines, out), defines=out,
    )


def _counted_within(
    present: list[DefineKey],
    cohorts: dict[DefineKey, Cohort],
    located: dict[DefineKey, PinnedRelation],
    columns: dict[PinnedRelation, dict[str, str]],
    eval_id: str,
    *,
    execute: Execute,
) -> dict[DefineKey, tuple[int, int]]:
    """SPEC-POPULATION-CASCADE — ``{define: (counted, cohort size)}`` for every present define whose cohort
    is not everyone, in ONE statement.

    A cohort naming a population with no result relation RAISES. Counting a Numerator without the
    Denominator Exclusions it must leave out is the wrong number, not a missing one, so it is not
    reported at all.
    """
    scoped = [key for key in present if not cohorts[key].everyone]
    if not scoped:
        return {}
    distinct = list(dict.fromkeys(cohorts[key] for key in scoped))
    members = [m for c in distinct for m in (*c.all_of, *c.none_of)]
    unread = sorted({m for m in members if m not in located})
    if unread:
        raise MetricsUnavailable(
            f"the population(s) {unread} have no result relation at this evaluation, so the defines "
            f"scored within them cannot be counted (SPEC-POPULATION-CASCADE)")

    subject_sql: dict[DefineKey, str] = {}
    for key in dict.fromkeys([*scoped, *members]):
        ref = located[key]
        cols = columns[ref]
        col = value_column(cols)
        met = met_expr(col, cols.get(col) if col else None)
        subject_sql[key] = (f"SELECT DISTINCT {met} AS {SUBJECT_ID} "
                            f"FROM {result_relation(ref, eval_id)} WHERE {met} IS NOT NULL")
    index = {key: i for i, key in enumerate(subject_sql)}
    targets = [(distinct.index(cohorts[key]), index[key]) for key in scoped]
    rows = execute(cohort_count_sql(
        list(subject_sql.values()),
        [([index[k] for k in c.all_of], [index[k] for k in c.none_of]) for c in distinct],
        targets)) or []

    sizes: dict[int, int] = {}
    counted: dict[tuple[int, int], int] = {}
    for row in rows:
        cohort, define, n = int(row[0]), int(row[1]), int(row[2] or 0)
        if define < 0:
            sizes[cohort] = n
        else:
            counted[(cohort, define)] = n
    return {key: (counted.get(target, 0), sizes.get(target[0], 0)) for key, target in zip(scoped, targets)}


def cohort_count_sql(subjects: list[str], cohorts: list[tuple[list[int], list[int]]],
                     targets: list[tuple[int, int]]) -> str:
    """SPEC-POPULATION-CASCADE — the counting statement, over SQL that selects each define's met subjects.

    ``subjects[i]`` selects the distinct subjects who met define ``i``. A cohort is ``(all_of, none_of)``
    as indexes into ``subjects``, with ``all_of`` never empty. ``targets`` are ``(cohort, define)`` pairs
    to count. Each row is ``(cohort, define, n)``: ``define`` -1 is the cohort's own size.

    Every operand is distinct subjects BEFORE any join, so the joins are 1:1 however many rows a result
    relation carries, and ``COUNT(DISTINCT …)`` keeps it so (`relation.py`: no ``COUNT(*)`` on a
    metric path). Public so a test can run it on a real SQL engine.
    """
    ctes = [f"s_{i} AS ({sql})" for i, sql in enumerate(subjects)]
    branches: list[str] = []
    for j, (all_of, none_of) in enumerate(cohorts):
        first, *rest = all_of
        joins = [f"JOIN s_{i} a_{n} ON a_{n}.{SUBJECT_ID} = b.{SUBJECT_ID}" for n, i in enumerate(rest)]
        joins += [f"LEFT JOIN s_{i} x_{n} ON x_{n}.{SUBJECT_ID} = b.{SUBJECT_ID}" for n, i in enumerate(none_of)]
        where = " AND ".join(f"x_{n}.{SUBJECT_ID} IS NULL" for n in range(len(none_of)))
        ctes.append(f"k_{j} AS (SELECT b.{SUBJECT_ID} FROM s_{first} b"
                    + "".join(f" {join}" for join in joins)
                    + (f" WHERE {where}" if where else "") + ")")
        branches.append(f"SELECT {j} AS k, -1 AS d, COUNT(DISTINCT {SUBJECT_ID}) AS n FROM k_{j}")
    for j, i in targets:
        branches.append(f"SELECT {j}, {i}, COUNT(DISTINCT m.{SUBJECT_ID}) "
                        f"FROM s_{i} m JOIN k_{j} c ON c.{SUBJECT_ID} = m.{SUBJECT_ID}")
    return "WITH " + ",\n".join(ctes) + "\n" + "\nUNION ALL\n".join(branches)


def _evaluated_subjects(patient_relation: str | None, *, execute: Execute) -> int:
    """The EVALUATED total — every subject the measure ran against, before any criterion (`SPEC-METRIC`).

    **Read from the environment's Patient view, and only from there** (`BIND-PUBLISHED-VIEW`). This is the
    decision the metric set waited on, and the reason it is the binding rather than a result table: a
    result relation's subject set is whatever its joins LEFT — the numerator relation mentions 2,713
    subjects, but that excludes every patient with no reading at all, so it is a lower
    bound on the cohort and not the cohort. The binding is also already the authority for "what a patient
    is" when the strata read demographics, so the cohort and its breakdowns come from one definition
    rather than two that can disagree.

    **THE ENVIRONMENT'S DATA IS THE POPULATION.** The view takes no argument: a retrieve's rows depend only on its
    request and the data it is bound to (`EMIT-ROOT-INDEPENDENT`), and a measure's population is chosen by
    choosing its environment (`CLIENT-ENVIRONMENT`).

    An environment with no Patient view has no cohort to state, and 0 is not the answer — it reports
    absence upward by raising, since `SPEC-METRIC` requires a share's base be STATED. A binding that
    resolves but yields no subjects is the same absence for the same reason: a document cannot take
    shares against an empty cohort, and rendering "0 → 67" is a base that is not a base.
    """
    if not patient_relation:
        raise MetricsUnavailable(
            "the environment has no Patient view, so the evaluated-subject total the shares "
            "are taken against cannot be stated (SPEC-METRIC requires the base be stated)")

    sql = (f"SELECT COUNT(DISTINCT {SUBJECT_ID}) AS subjects "
           f"FROM {patient_rows(patient_relation, execute)}")
    rows = execute(sql)
    if not rows or not rows[0]:
        raise MetricsUnavailable(
            f"the Patient binding {patient_relation} returned no count for the evaluated-subject total")
    subjects = int(rows[0][0] or 0)
    if subjects == 0:
        raise MetricsUnavailable(
            f"the Patient binding {patient_relation} holds no subjects, so the "
            f"evaluated-subject total the shares are taken against cannot be stated (SPEC-METRIC)")
    return subjects


def _caveat(requested: list[tuple[str, str, str | None]], produced: list[DefineMetric]) -> str | None:
    """Say how many of the asked-about defines this evaluation has no relation for.

    Stated rather than left to be noticed: a document whose library sections show absence marks is
    reading a real evaluation, which is a different thing from a broken read, and the reader deserves the
    distinction on the page.

    **It does not claim WHY**, because there is more than one true reason and this layer cannot tell them
    apart: a define the measure never reaches is not compiled into its pipeline at all, and a library
    whose pipeline has not materialized yet has the define compiled but no table — both observed on the
    same evaluation. Naming one cause would be a guess dressed as an explanation.
    """
    missing = len(requested) - len(produced)
    if missing <= 0:
        return None
    return (f"{missing} of {len(requested)} defines have no result relation in this evaluation, so their "
            f"figures are absent rather than zero.")
