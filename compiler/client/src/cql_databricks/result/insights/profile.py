"""APP-DATA-PROFILE / APP-PROFILE-STRATA — one define's profile, read from the evaluation.

What ``insights/sample.py`` invented, this derives. It does NOT derive all of it, and the split is the
point: three blocks are answerable from tables the app already reads, two are not, and the payload says
which is which per block (``DefineProfile.sampled``) rather than carrying one word for the whole thing.

**DERIVED HERE**

``values``   — the two grains and the null rate, over the define's own result relation. The subject and
               element counts come from the same fan-out-immune expressions the metric set uses
               (``relation``), so the pane and the document cannot print different numbers for one
               define.
``sample``   — the top-N returned values, ``GROUP BY`` the value column. That column is identified by
               ELIMINATION of the keys the emit owns, never by name (``relation.value_column``): a
               boolean or scalar define's value column is named after the DEFINE.
``strata``   — sex and age band, from the bound Patient retrieve joined to the subjects the define is
               true of. The binding is already the authority for "what a patient is" when the evaluated
               total is read (``metrics.py``), so the cohort and its breakdowns cannot disagree.

**NOT DERIVED, AND WHY** — each stays sample, marked, rather than being approximated here:

``valueSets`` — code hits need the codes the RETRIEVED records carry, and a retrieve's rows are inlined
                into the pipeline SQL rather than materialized. There is no relation to count.
``temporal``  — needs the date property the retrieve filtered on, which is per FHIR type
                (``Encounter.period.start`` vs ``Observation.effective…``). The ENGINE knows it — it is
                ``date_property`` in the retrieve contract — and this layer would be guessing
                (``APP-DECOUPLE``: do not re-derive client-side what the compiler owns).
``cardinality`` — elements per subject needs ``__ordinal``, which is NULL on every row the emit writes
                (``relation.element_grain_available``, measured). It is OMITTED rather than sampled:
                it sits inside ``values``, a block this module marks as real, and an invented histogram
                inside a real block is the one thing a per-block mark cannot express.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone

from ..relation import SUBJECT_ID, literal, met_expr, patient_rows, quote, value_column
from ..results import Execute, PinnedRelation, count_relations, relation_columns, result_relation
from .evaluation import ClosureSource, MetricsUnavailable, locate
from .model import (
    BLOCK_SAMPLE, BLOCK_TEMPORAL, BLOCK_VALUE_SETS,
    PROVENANCE_MIXED, PROVENANCE_RUN, Bin, DefineProfile, Stratum, ValueProfile, ValueSample,
)
from .sample import sample_profile
from .extractors import DEFAULT_MODEL, KIND_SCALAR, UNKNOWN_BIN, elements_for
from .terminology import TerminologyUnavailable, extract_terminology

log = logging.getLogger("cql_databricks.result.insights.profile")

#: How many returned values the pane shows. The block is a top-N, not a distribution — a list-valued
#: define returns near-unique resources and a longer tail says nothing a reader can use.
TOP_VALUES = 8

#: Age bands, as the sample generator already labelled them — one vocabulary, so turning a cut real
#: does not renumber the axis under a reader who knew it.
AGE_BANDS = ((0, 18), (18, 45), (45, 65), (65, 75), (75, 200))


def define_profile(
    *,
    library: str,
    version: str,
    define: str,
    define_library: str,
    data_source_id: str,
    period_start: str | None,
    period_end: str | None,
    eval_id: str,
    draft: str,
    closure: "ClosureSource",
    patient_relation: str | None,
    results_catalog: str,
    execute: Execute,
) -> DefineProfile:
    """One define's profile for ONE evaluation: derived where it can be, sampled where it cannot.

    ``define_library`` is the library that DECLARES the define, which is not always the measure — two
    libraries may declare one name (`SPEC-ANCHOR`), so an unqualified lookup resolves the wrong one.
    """
    # RUN-CLIENT-RESULT-SETS: the complete selection's identity, which the closure is checked against.
    suffix = eval_id
    libraries = closure.closure(data_source_id, suffix, root=library, draft=draft)
    if not libraries:
        raise MetricsUnavailable(
            f"no compiled libraries for data source {data_source_id!r} at evaluation {suffix} — "
            f"the measure has not been run against this data source and measurement period")

    ref = locate(libraries, define_library, define)
    if ref is None:
        raise MetricsUnavailable(
            f"{define_library}.{define!r} has no result relation in this evaluation, so there is "
            f"nothing to profile — it was never materialized here (APP-DATA-PROFILE)")

    columns = relation_columns(results_catalog, [ref])
    relation_cols = columns[ref]
    subjects, elements = count_relations(execute, columns, eval_id=suffix)[ref]

    values = ValueProfile(
        subjects=subjects,
        elements=elements,
        null_rate=_null_rate(ref, relation_cols, eval_id=suffix, execute=execute),
        # Deliberately empty — see the module docstring. `__ordinal` carries no values yet.
        cardinality=[],
    )
    top = _top_values(ref, relation_cols, eval_id=suffix, execute=execute)
    strata = _subject_strata(
        ref, relation_cols, patient_relation=patient_relation, period_start=period_start, eval_id=suffix,
        execute=execute)

    # The profile is WHOLLY DERIVED. `provenance` reflects that without qualification, so the pane
    # reads without checking a mark: `SPEC-TERMINOLOGY-EXTRACT` reads real codes off the define's own
    # results, not an invented list, and strata are derived only when the data structure supports them.

    # SPEC-TERMINOLOGY-EXTRACT — a failed codes read leaves the rest of the profile standing, and says so
    # in its own block: the reason rides on the payload where the codes would have been.
    try:
        terminology = extract_terminology(ref, relation_cols, eval_id=suffix, execute=execute)
        terminology_error = None
    except TerminologyUnavailable as e:
        log.warning("Coded values unreadable for %s/%s: %s", define_library, define, e)
        terminology, terminology_error = [], e.reason

    return DefineProfile(
        library=define_library, define=define,
        # `mixed` only when something on the payload actually is. A Boolean define has no code hits and
        # no temporal spread to invent, so its profile is wholly read — and saying `mixed` there would
        # put a warning on a page with nothing to warn about.
        provenance=PROVENANCE_RUN,
        generated=_generated(),
        caveat=_caveat(strata, False),
        sample=top,
        values=values,
        # SPEC-TERMINOLOGY-EXTRACT — derived, and independent of everything above: a `GROUP BY` over the
        # curated coded paths of this define's own results. Never fabricated, so it is absent rather
        # than sampled when the define returns no resources.
        terminology=terminology,
        terminology_error=terminology_error,
        strata=strata,
        sampled=[],
    )


def _sampled(top: list[ValueSample], fabricated: DefineProfile) -> list[str]:
    """Which whole BLOCKS on this payload are fabricated.

    Computed from what was actually produced rather than hard-coded, so a block that silently comes back
    empty is reported as sampled instead of passing for a derived block that found nothing. The strata
    block is absent from this list by design — it is mixed within itself, so each cut carries its own
    flag (`Stratum.sampled`) and a block-level answer could only be wrong about half of them.
    """
    blocks = []
    if not top:
        blocks.append(BLOCK_SAMPLE)
    if fabricated.value_sets:
        blocks.append(BLOCK_VALUE_SETS)
    if fabricated.temporal:
        blocks.append(BLOCK_TEMPORAL)
    return blocks


def _caveat(strata: list[Stratum], anything_sampled: bool) -> str:
    """Say what is real and what is not, in the words the pane has room for."""
    derived = "The value profile and top values are read from this evaluation"
    if strata:
        derived += "; sex and age band are read from the Patient binding"
    if not anything_sampled:
        return f"{derived}."
    return f"{derived}. Everything marked below is a SAMPLE — no table answers it yet."


def _generated() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _null_rate(ref: PinnedRelation, columns: dict[str, str], *, eval_id: str, execute: Execute) -> float:
    """The share of the relation's subjects whose value is null or empty.

    Over SUBJECTS, not rows, for the same reason every other figure here is: the relation is not one row
    per subject, and a row-level rate on a table carrying 66 million join rows for 2,713 subjects
    measures the fan-out rather than the data.
    """

    col = value_column(columns)
    if col is None:
        return 0.0  # a relation carrying only keys has no value that could be null
    sql = (f"SELECT COUNT(DISTINCT CASE WHEN {quote(col)} IS NULL THEN {SUBJECT_ID} END) AS nulls, "
           f"COUNT(DISTINCT {SUBJECT_ID}) AS subjects "
           f"FROM {result_relation(ref, eval_id)}")
    rows = execute(sql)
    if not rows or not rows[0]:
        return 0.0
    nulls, subjects = int(rows[0][0] or 0), int(rows[0][1] or 0)
    return nulls / subjects if subjects else 0.0


def _top_values(ref: PinnedRelation, columns: dict[str, str], *, eval_id: str,
                execute: Execute) -> list[ValueSample]:
    """The top-N returned values by how many SUBJECTS returned them.

    STRUCT values are rendered as JSON rather than projected to a field. A scalar define returns a CQL
    Quantity (`{"value": 139, "unit": "mm[Hg]"}`) and a list-valued one returns a resource, so there is
    no single field to pick and picking one per type would be the client-side FHIR knowledge
    `APP-DECOUPLE` keeps out of this layer. The pane truncates and carries the full value in its
    tooltip.
    """

    col = value_column(columns)
    if col is None:
        return []
    rendered = (f"to_json({quote(col)})" if columns.get(col, "").upper().startswith("STRUCT")
                else f"CAST({quote(col)} AS STRING)")
    sql = (f"SELECT {rendered} AS v, COUNT(DISTINCT {SUBJECT_ID}) AS n "
           f"FROM {result_relation(ref, eval_id)} WHERE {quote(col)} IS NOT NULL "
           f"GROUP BY 1 ORDER BY n DESC, v LIMIT {TOP_VALUES}")
    rows = execute(sql) or []
    total = sum(int(r[1] or 0) for r in rows)
    if not total:
        return []
    return [ValueSample(value=str(r[0]), count=int(r[1] or 0), share=int(r[1] or 0) / total)
            for r in rows]


def _subject_strata(ref: PinnedRelation, columns: dict[str, str], *, patient_relation: str | None,
                    period_start: str | None,
                    eval_id: str, execute: Execute) -> list[Stratum]:
    """Sex and age band over the subjects this define is TRUE of.

    Empty with no Patient binding — a Data Source that has not bound Patient has no demographics to cut
    by, and that is an absence rather than an error: the value profile above is still real, so the
    profile is worth serving without them (unlike the evaluated total, which is a base and must be
    stated or the whole set fails).

    The join is against the define's MET subjects (`relation.met_expr`), not against every subject the
    relation touches. On a measured deck 96% of a boolean relation's rows are false, so cutting the
    touched set would describe the population the pipeline scanned rather than the one the define
    selected.
    """

    if not patient_relation:
        return []
    patient_relation = patient_rows(patient_relation, execute)
    col = value_column(columns)
    met = met_expr(col, columns.get(col) if col else None)
    subjects_sql = (f"SELECT DISTINCT {met} AS {SUBJECT_ID} FROM {result_relation(ref, eval_id)}")

    # Age at the START of the measurement period, which is what the label says and what an eCQM means
    # by an age criterion. With no period stated there is no age to state either — the band cut is
    # dropped rather than silently measured from today, which would drift with the wall clock.
    age_expr = (f"floor(months_between({literal(period_start)}, "
                f"CAST(p.__resource.birthDate AS DATE)) / 12)" if period_start else None)

    # DATA-INSIGHT-EXTRACTION — the cuts come from the shared catalog, not from a list here. The same
    # curation feeds the Terminology sections; adding a demographic is an entry in `extractors.py`
    # rather than an edit to this function, which is the point of naming the mechanism.
    cuts: list[tuple[str, str, str, str, str | None]] = [
        (e.id or e.path, e.label, e.id or e.path, e.accessor.replace("{r}", "p.__resource"), e.note)
        for e in elements_for(DEFAULT_MODEL, "Patient", kind=KIND_SCALAR)
    ]
    # AGE IS COMPOSED HERE, NOT CURATED. Its bands depend on the measurement period — a property of the
    # evaluation, not of the data model — so the catalog would have to carry an evaluation's parameter
    # to hold it. With no period stated there is no age to state either, and the cut is dropped rather
    # than silently measured from the wall clock.
    if age_expr:
        band = " ".join(
            f"WHEN {age_expr} >= {lo} AND {age_expr} < {hi} THEN {literal(_band_label(lo, hi))}"
            for lo, hi in AGE_BANDS)
        cuts.insert(1, ("age", "Age band", "age", f"CASE {band} END",
                        "Age at the start of the measurement period."))

    out: list[Stratum] = []
    for sid, label, dimension, expression, note in cuts:
        sql = (f"SELECT {expression} AS bin, COUNT(DISTINCT p.{SUBJECT_ID}) AS n "
               f"FROM {patient_relation} p "
               f"JOIN ({subjects_sql}) d ON d.{SUBJECT_ID} = p.{SUBJECT_ID} "
               f"GROUP BY 1 ORDER BY n DESC")
        rows = execute(sql) or []
        total = sum(int(r[1] or 0) for r in rows)
        if not total:
            continue
        out.append(Stratum(
            id=sid, label=label, grain="subject", dimension=dimension, unit="patients",
            # UNKNOWN IS A BUCKET, NOT A DROPPED ROW. `GROUP BY` gives NULLs their own group, so a
            # cohort where 40% have no recorded sex reports that as a bar rather than as a quietly
            # smaller chart — which is a data-readiness finding, and the reason it is labelled rather
            # than filtered (`SPEC-METRIC`: absence is stated, never silently absorbed).
            bins=[Bin(label=str(r[0]) if r[0] is not None else UNKNOWN_BIN,
                      count=int(r[1] or 0), share=int(r[1] or 0) / total) for r in rows],
            note=note,
        ))
    return out


def _band_label(lo: int, hi: int) -> str:
    return f"{lo}+" if hi >= 200 else f"{lo}–{hi}"
