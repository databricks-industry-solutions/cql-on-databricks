"""RUN-CLIENT-MEASURE-SCORE — a measure's score, the one figure the population cascade applies to.

**POPULATIONS ARE COUNTED AS WRITTEN; ONLY THE SCORE IS CASCADED.** A population define returns its own
result, like any other define. HL7 CQF Measures makes each population a subset of the one before it and
leaves that to the evaluator, so a measure's CQL does not say it: a Numerator written as "HbA1c > 9% or
missing" is true for every patient without diabetes too. The score is where that rule belongs, and this
module is its one owner: the Designer, the agent and a notebook score a measure here, so their scores
agree (`SPEC-POPULATION-CASCADE`).

**THE SCORING TYPE IS DECLARED, NOT INFERRED** (`SPEC-SCORING-METHODOLOGY`). It comes from
``Measure.scoring`` (:func:`parse_scoring`) or from the caller. The population names of a proportion and
of a ratio overlap, so inferring the type from them is wrong silently.

**A DECLARED POPULATION WITH NO RESULT RAISES.** A proportion scored without the Denominator Exclusions
it declares is the wrong number, not a missing one. So the caller names the populations the measure
declares, and every one must be counted.

Continuous-variable is not scored: its terminal is a distribution of a Measure Observation function,
which no define materializes (`APP-PROBE`). It is stated as unsupported, never approximated.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..relation import SUBJECT_ID, met_expr, value_column
from ..results import Execute, PinnedRelation, relation_columns, result_relation
from .evaluation import ClosureSource, MetricsUnavailable, locate
from .metrics import cohort_count_sql
from .model import PROVENANCE_RUN

INITIAL = "Initial Population"
DENOMINATOR = "Denominator"
EXCLUSIONS = "Denominator Exclusions"
EXCEPTIONS = "Denominator Exceptions"
NUMERATOR = "Numerator"
NUM_EXCLUSIONS = "Numerator Exclusions"
MEASURE_POPULATION = "Measure Population"
MEASURE_POP_EXCLUSIONS = "Measure Population Exclusions"

#: The scoring codes FHIR defines for ``Measure.scoring``
#: (http://terminology.hl7.org/CodeSystem/measure-scoring).
SCORING_KINDS = frozenset({"proportion", "ratio", "continuous-variable", "cohort"})

#: SPEC-POPULATION-CASCADE — the populations each population is counted among, inside the score:
#: ``(all_of, none_of)``. A proportion's Denominator Exception removes only a patient outside the
#: Numerator, so the Numerator is in its ``none_of``.
CASCADES: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "proportion": {
        INITIAL: ((), ()),
        DENOMINATOR: ((INITIAL,), ()),
        EXCLUSIONS: ((INITIAL, DENOMINATOR), ()),
        NUMERATOR: ((INITIAL, DENOMINATOR), (EXCLUSIONS,)),
        EXCEPTIONS: ((INITIAL, DENOMINATOR), (EXCLUSIONS, NUMERATOR)),
    },
    "ratio": {
        INITIAL: ((), ()),
        DENOMINATOR: ((INITIAL,), ()),
        EXCLUSIONS: ((INITIAL, DENOMINATOR), ()),
        NUMERATOR: ((INITIAL,), ()),
        NUM_EXCLUSIONS: ((INITIAL, NUMERATOR), ()),
    },
    "continuous-variable": {
        INITIAL: ((), ()),
        MEASURE_POPULATION: ((INITIAL,), ()),
        MEASURE_POP_EXCLUSIONS: ((INITIAL, MEASURE_POPULATION), ()),
    },
    "cohort": {
        INITIAL: ((), ()),
    },
}

#: Every population name any scoring type uses.
POPULATIONS = frozenset(name for rules in CASCADES.values() for name in rules)

_CV_UNSUPPORTED = ("A continuous-variable measure reports a distribution of its Measure Observation. The "
                   "observation is a function rather than a materialized define, so it is not scored here.")


def parse_scoring(resource: Mapping[str, Any] | None) -> str | None:
    """SPEC-SCORING-METHODOLOGY — ``Measure.scoring`` as a plain code, or None.

    An unrecognized code is dropped rather than passed through: a typo reaching a surface as a string
    would be indistinguishable from a measure that declared nothing. ``text`` is the human label, and is
    accepted only where it happens to be a code.
    """
    scoring = (resource or {}).get("scoring")
    if not isinstance(scoring, dict):
        return None
    for coding in scoring.get("coding") or ():
        code = coding.get("code") if isinstance(coding, dict) else None
        if isinstance(code, str) and code.strip() in SCORING_KINDS:
            return code.strip()
    text = scoring.get("text")
    return text.strip() if isinstance(text, str) and text.strip() in SCORING_KINDS else None


@dataclass(frozen=True)
class MeasureScore:
    """RUN-CLIENT-MEASURE-SCORE — what a measure reports, with the counted figures it was taken from.

    ``kind`` is ``rate`` (proportion), ``ratio``, ``count`` (cohort) or ``unsupported``. ``numerator`` and
    ``base`` are the CASCADED counts the score divides, which are not the populations' own counts: a
    rate reads "3,100 of 8,150", the Numerator within the Denominator less its removals. ``value`` is
    None when the base is empty: no patient is in the denominator, so there is no rate to state.
    """

    scoring: str
    kind: str
    value: float | None = None
    numerator: int | None = None
    base: int | None = None
    population: str | None = None
    why: str | None = None
    provenance: str = PROVENANCE_RUN

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"scoring": self.scoring, "kind": self.kind, "provenance": self.provenance}
        for key, value in (("value", self.value), ("numerator", self.numerator), ("base", self.base),
                           ("population", self.population), ("why", self.why)):
            if value is not None:
                out[key] = value
        return out


def subjects_sql(relation: str, columns: Mapping[str, str]) -> str:
    """SQL selecting the distinct subjects who met one define, over its result relation and columns.

    The met predicate is `relation.met_expr`'s: a boolean contributes only ``true``, a list only when
    non-empty, anything else its presence."""
    col = value_column(columns)
    met = met_expr(col, columns.get(col) if col else None)
    return f"SELECT DISTINCT {met} AS {SUBJECT_ID} FROM {relation} WHERE {met} IS NOT NULL"


def measure_score(*, scoring: str | None, declared: Iterable[str], subjects: Mapping[str, str],
                  execute: Execute) -> MeasureScore:
    """RUN-CLIENT-MEASURE-SCORE — score a measure from each declared population's met-subject SQL.

    ``declared`` is every define the measure's root library declares; the populations among them are the
    formula's terms. ``subjects`` maps each declared population to SQL selecting its met subjects
    (:func:`subjects_sql`). One statement counts every term.
    """
    if scoring not in SCORING_KINDS:
        raise ValueError(f"a score needs a declared scoring type, one of {sorted(SCORING_KINDS)}; got {scoring!r}")
    if scoring == "continuous-variable":
        return MeasureScore(scoring=scoring, kind="unsupported", why=_CV_UNSUPPORTED)
    rules = CASCADES[scoring]
    present = [name for name in rules if name in set(declared)]
    if INITIAL not in present:
        raise MetricsUnavailable(f"a {scoring} measure must declare an {INITIAL!r} to be scored")
    unread = [name for name in present if name not in subjects]
    if unread:
        raise MetricsUnavailable(f"the declared population(s) {unread} have no result to score from "
                                 "(SPEC-POPULATION-CASCADE)")
    counted = _cascaded_counts(scoring, present, subjects, execute)

    if scoring == "cohort":
        return MeasureScore(scoring=scoring, kind="count", population=INITIAL, numerator=counted[INITIAL])
    if scoring == "proportion":
        numerator = counted.get(NUMERATOR)
        if numerator is None:
            raise MetricsUnavailable(f"a proportion measure must declare a {NUMERATOR!r} to be scored")
        denominator = counted.get(DENOMINATOR, counted[INITIAL])
        base = denominator - counted.get(EXCLUSIONS, 0) - counted.get(EXCEPTIONS, 0)
        return MeasureScore(scoring=scoring, kind="rate", numerator=numerator, base=base,
                            value=numerator / base if base > 0 else None)
    # HL7 CQF Measures ratio: (Numerator - Numerator Exclusions) / (Denominator - Denominator Exclusions).
    if NUMERATOR not in counted or DENOMINATOR not in counted:
        raise MetricsUnavailable(f"a ratio measure must declare a {NUMERATOR!r} and a {DENOMINATOR!r}")
    numerator = counted[NUMERATOR] - counted.get(NUM_EXCLUSIONS, 0)
    base = counted[DENOMINATOR] - counted.get(EXCLUSIONS, 0)
    return MeasureScore(scoring=scoring, kind="ratio", numerator=numerator, base=base,
                        value=numerator / base if base > 0 else None)


def selection_score(*, library: str, data_source_id: str, eval_id: str, draft: str, closure: ClosureSource,
                    results_catalog: str, execute: Execute, scoring: str | None,
                    declared: Iterable[str], **_: Any) -> MeasureScore:
    """RUN-CLIENT-MEASURE-SCORE — score a measure from one complete selection's saved results.

    Takes the same options the other derivations do (``**_`` absorbs the ones scoring does not read).
    The populations are the root library's."""
    declared = list(declared)
    libraries = closure.closure(data_source_id, eval_id, root=library, draft=draft)
    located: dict[str, PinnedRelation] = {}
    for name in POPULATIONS & set(declared):
        ref = locate(libraries, library, name)
        if ref is not None:
            located[name] = ref
    columns = relation_columns(results_catalog, located.values())
    subjects = {name: subjects_sql(result_relation(ref, eval_id), columns[ref]) for name, ref in located.items()}
    return measure_score(scoring=scoring, declared=declared, subjects=subjects, execute=execute)


def _cascaded_counts(scoring: str, present: list[str], subjects: Mapping[str, str],
                     execute: Execute) -> dict[str, int]:
    """SPEC-POPULATION-CASCADE — each present population counted within the populations before it.

    Population ``p`` counted is the cohort "in every ``all_of``, in ``p``, in no ``none_of``", over the
    populations the measure declares. ``cohort_count_sql`` reports each cohort's size."""
    index = {name: i for i, name in enumerate(present)}
    cohorts = []
    for name in present:
        all_of, none_of = CASCADES[scoring][name]
        cohorts.append(([index[p] for p in (*all_of, name) if p in index],
                        [index[p] for p in none_of if p in index]))
    rows = execute(cohort_count_sql([subjects[name] for name in present], cohorts, [])) or []
    sizes = {int(row[0]): int(row[2] or 0) for row in rows if int(row[1]) == -1}
    return {name: sizes.get(i, 0) for i, name in enumerate(present)}
