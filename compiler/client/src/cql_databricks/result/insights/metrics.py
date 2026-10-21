"""APP-SAMPLE-METRICS (retiring), SPEC-METRIC, SPEC-ATTRITION — the measure's REAL metric set.

Every figure the Specification renders, read from the tables the pipeline already wrote. This is what
retires ``insights/sample.py``'s metric half: same route, same wire shape, same client, real numbers.

**A MEASURE'S DEFINES LIVE IN SEVERAL SCHEMAS.** ``PIPE-PER-LIBRARY-SCHEMA`` gives each library its own
results schema, and a measure reads its include closure — a multi-library measure spans one schema per
library (itself plus each shared helper library it includes). So the builder resolves a schema PER LIBRARY and counts across all of them in one query, which
is what makes the figures comparable: `SPEC-ATTRITION` puts each figure beside its base, and a base read
a minute later than the figure it scales can contradict it.

**THE DEFINE→TABLE MAP COMES FROM THE STATE LAYER, NOT FROM GUESSING THE NAME.** ``pipeline_defines``
records ``(define_name, mv_name)`` per compiled library, so the map is what the compiler actually emitted.
Re-deriving a table name from a define name would be a second naming rule able to disagree with
``PIPE-RESULT-NAMING`` — and it would be wrong today, because a dependency library's defines are recorded
LIBRARY-PREFIXED (``CareVisits_Visits_During_Period``) while the client asks about
``Visits During Period`` in library ``Encounters``.

**A DEFINE THE PIPELINE DID NOT MATERIALIZE IS ABSENT, NEVER ZERO.** Only the defines a measure actually
reaches are compiled into its pipeline — a helper library may contribute 4 of its defines, not all
of them — so most library entries in the document have no relation to count. Emitting 0 for those would
state "no subject met this" where the truth is "this was never evaluated here"; `SPEC-METRIC` already
specifies a not-computed state for exactly that, and the client renders an absence mark when a define is
missing from the set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..relation import SUBJECT_ID
from ..results import Execute, RelationRef, count_relations, relation_columns
from .evaluation import ClosureSource, MetricsUnavailable, locate
from .model import PROVENANCE_RUN, DefineMetric, MeasureMetrics

log = logging.getLogger("cql_databricks.result.insights.metrics")


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
    closure: "ClosureSource",
    patient_function: str | None,
    results_catalog: str,
    execute: Execute,
) -> MeasureMetrics:
    """Build the whole measure's metric set from the post-pipeline result tables.

    ``defines`` is ``(library, name, result_type)`` per define, as the CLIENT derived them from the ELM —
    it owns that determination (`APP-SPEC-MODEL`) and a server-side re-derivation could disagree.
    What this function adds is the figures.

    ``result_type`` is deliberately NOT used to decide the element grain here, unlike the sample builder
    it replaces. The relation's own schema decides it (`relation.element_count_expr`), because the
    relation is the thing being counted; a `List<…>` return whose relation carries no usable ordinal has
    no element count to give, and the result type cannot know that.
    """
    # RUN-EVAL-ID-INPUTS: the evaluation set, named by the route (`evaluation_key.EvaluationKeys`).
    suffix = eval_id
    libraries = closure.closure(data_source_id, suffix, root=library, draft=draft)
    if not libraries:
        raise MetricsUnavailable(
            f"no compiled libraries for data source {data_source_id!r} at evaluation {suffix} — "
            f"the measure has not been run against this data source and measurement period")

    # (library, define) -> the relation that holds it, from the compiler's own record.
    located: dict[tuple[str, str], RelationRef] = {}
    for define_library, name, _ in defines:
        ref = locate(libraries, define_library, name, results_catalog)
        if ref is not None:
            located[(define_library, name)] = ref

    columns = relation_columns(results_catalog, located.values(), execute=execute)
    counts = count_relations(execute, columns, eval_id=suffix)

    out: list[DefineMetric] = []
    for define_library, name, _ in defines:
        ref = located.get((define_library, name))
        if ref is None or ref not in counts:
            continue  # never materialized here — absent, not zero
        subjects, elements = counts[ref]
        out.append(DefineMetric(define=name, library=define_library,
                                subjects=subjects, elements=elements))

    return MeasureMetrics(
        library=library, version=version, provenance=PROVENANCE_RUN,
        generated=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        subjects=_evaluated_subjects(patient_function, execute=execute),
        caveat=_caveat(defines, out), defines=out,
    )


def _evaluated_subjects(patient_function: str | None, *, execute: Execute) -> int:
    """The EVALUATED total — every subject the measure ran against, before any criterion (`SPEC-METRIC`).

    **Read from the bound Patient retrieve, and only from there** (D3, `BIND-RETRIEVE-UDF`). This is the
    decision the metric set waited on, and the reason it is the binding rather than a result table: a
    result relation's subject set is whatever its joins LEFT — the numerator relation mentions 2,713
    subjects, but that excludes every patient with no reading at all, so it is a lower
    bound on the cohort and not the cohort. The binding is also already the authority for "what a patient
    is" when the strata read demographics, so the cohort and its breakdowns come from one definition
    rather than two that can disagree.

    **THE DATA SOURCE IS THE POPULATION.** The call passes no argument: a retrieve's rows depend only on its
    arguments and its data source (`EMIT-ROOT-INDEPENDENT`), and a measure's population is chosen by
    choosing its data source (`DATA-CONFORMANCE-MEASURE-SELECT`, one conformance source per measure).

    A Data Source with no Patient binding has no cohort to state, and 0 is not the answer — it reports
    absence upward by raising, since `SPEC-METRIC` requires a share's base be STATED. A binding that
    resolves but yields no subjects is the same absence for the same reason: a document cannot take
    shares against an empty cohort, and rendering "0 → 67" is a base that is not a base.
    """
    if not patient_function:
        raise MetricsUnavailable(
            "the Data Source has no Patient data binding, so the evaluated-subject total the shares "
            "are taken against cannot be stated (SPEC-METRIC requires the base be stated)")

    sql = (f"SELECT COUNT(DISTINCT {SUBJECT_ID}) AS subjects "
           f"FROM {patient_function}()")
    rows = execute(sql)
    if not rows or not rows[0]:
        raise MetricsUnavailable(
            f"the Patient binding {patient_function} returned no count for the evaluated-subject total")
    subjects = int(rows[0][0] or 0)
    if subjects == 0:
        raise MetricsUnavailable(
            f"the Patient binding {patient_function} holds no subjects, so the "
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
