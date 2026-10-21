"""APP-DATA-PROFILE — the SAMPLE profile generator. Scaffolding, and the only file that goes away.

`insights/metrics.py` reads measure-wide figures from the pipeline's own result tables; the document,
nav and reported result are real data. What remains here is the per-define PROFILE — the distributions
the Data tab draws — which still needs derivations that do not exist yet (a temporal anchor the emit
does not project, code hits, cardinality).

This module exists so the pane can be designed and reviewed before the run pipeline can feed it. It is
the one place in the product that invents figures, which is deliberate: `APP-NO-UI-MOCK` is
enforceable only if fabricated data has exactly one address, and this is it. Wiring the real derivation
means writing a run-backed builder that returns the same `DefineProfile` and deleting this file —
no route change, no wire change, no client change.

Two properties make it safe to build on:

* **Every payload is stamped `provenance="sample"`** with a caveat string. The client renders that mark;
  a reader can never take a bar here for a result.
* **It is DETERMINISTIC.** The seed is the library-qualified define name, so one define always profiles
  the same way. A generator that reshuffled on every request would make screenshots
  irreproducible, tests impossible, and the pane look alive when nothing is running.

What it does NOT do is guess at the ELM. Result type, subject unit and element grain are the client's,
derived from the translator's own output (`elmDefines.ts`, `EnableResultTypes`) — real today. The
archetype below picks a plausible SHAPE from the define's name for demonstration only; it is not a
classification anything may depend on, and no client logic branches on it.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .model import (
    PROVENANCE_SAMPLE,
    Bin,
    CodeHit,
    DefineProfile,
    Stratum,
    TemporalBin,
    ValueProfile,
    ValueSample,
    ValueSetHits,
)

_CAVEAT = (
    "Sample figures — no run has produced these. Shape and density only; every number is invented."
)

# ── the demographic cuts, as bin labels + rough population weights ────────────────────────────────
#
# Weights are loosely US-census-shaped so a stacked bar looks like a real population rather than a
# uniform one. They are not a claim about anybody's data.
_AGE_BANDS = [("18–29", 9), ("30–39", 13), ("40–49", 17), ("50–59", 22), ("60–69", 21), ("70+", 18)]
_SEX = [("Female", 52), ("Male", 47), ("Unknown", 1)]
_RACE = [("White", 60), ("Black or African American", 13), ("Asian", 6),
         ("American Indian or Alaska Native", 1), ("Other", 8), ("Unknown", 12)]
_ETHNICITY = [("Not Hispanic or Latino", 76), ("Hispanic or Latino", 18), ("Unknown", 6)]
_PAYER = [("Medicare", 34), ("Commercial", 31), ("Medicaid", 19), ("Self-pay", 8), ("Other", 8)]

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_WEEKDAYS = [("Mon", 19), ("Tue", 20), ("Wed", 19), ("Thu", 18), ("Fri", 16), ("Sat", 6), ("Sun", 2)]
_ENC_CLASS = [("ambulatory", 71), ("outpatient", 18), ("emergency", 6),
              ("inpatient", 4), ("virtual", 1)]


def _bins(rng: random.Random, spec: list[tuple[str, int]], total: int) -> list[Bin]:
    """Distribute `total` across `spec`'s weights, jittered, and normalise the shares.

    **Every bucket sums to exactly `total`.** Truncating each bucket independently left a remainder,
    so three cuts of the same 28,172 encounters reported 28,166, 28,169 and 28,170 — which reads as a
    bug in the data, and in the real builder WOULD be one: a stratification of N that does not sum to N
    means a subject was dropped or double-counted. The remainder goes to the largest bucket, where it is
    proportionally smallest.

    Shares are computed from the DEALT counts, not from the weights, so bar widths always agree with the
    numbers printed beside them.
    """
    jittered = [(label, max(w * rng.uniform(0.82, 1.18), 0.01)) for label, w in spec]
    weight_sum = sum(w for _, w in jittered)
    counts = [max(int(total * w / weight_sum), 0) for _, w in jittered]
    remainder = total - sum(counts)
    if counts and remainder:
        counts[counts.index(max(counts))] += remainder
    dealt = sum(counts) or 1
    return [Bin(label=label, count=c, share=c / dealt)
            for (label, _), c in zip(jittered, counts)]


def _demographic_strata(rng: random.Random, subjects: int, unit: str) -> list[Stratum]:
    """The subject-grain cuts. Every define that is true of somebody can be cut these ways."""
    return [
        Stratum(id="age", label="Age band", grain="subject", dimension="age", unit=unit,
                bins=_bins(rng, _AGE_BANDS, subjects),
                note="Age at the start of the measurement period."),
        Stratum(id="sex", label="Sex", grain="subject", dimension="sex", unit=unit,
                bins=_bins(rng, _SEX, subjects)),
        Stratum(id="race", label="Race", grain="subject", dimension="race", unit=unit,
                bins=_bins(rng, _RACE, subjects),
                note="Unknown is a recorded value, not a gap in this profile."),
        Stratum(id="ethnicity", label="Ethnicity", grain="subject", dimension="ethnicity", unit=unit,
                bins=_bins(rng, _ETHNICITY, subjects)),
        Stratum(id="payer", label="Payer", grain="subject", dimension="payer", unit=unit,
                bins=_bins(rng, _PAYER, subjects)),
    ]


def _event_strata(rng: random.Random, elements: int, element_unit: str) -> list[Stratum]:
    """The ELEMENT-grain cuts — the date stratifications an event-valued define wants.

    These count events, not people, and the unit says so. One patient with nine encounters contributes
    nine here and one above, which is exactly why the two grains are separate strata rather than one
    chart with a toggle.
    """
    return [
        Stratum(id="month", label=f"{element_unit.title()} by month", grain="element",
                dimension="month", unit=element_unit,
                bins=_bins(rng, [(m, w) for m, w in zip(_MONTHS, [8, 7, 9, 9, 9, 8, 7, 8, 9, 10, 8, 8])],
                           elements),
                note="Calendar month of the event, within the measurement period."),
        Stratum(id="weekday", label=f"{element_unit.title()} by weekday", grain="element",
                dimension="weekday", unit=element_unit, bins=_bins(rng, _WEEKDAYS, elements),
                note="A weekend-heavy profile usually means an encounter class you did not expect."),
        Stratum(id="quarter", label=f"{element_unit.title()} by quarter", grain="element",
                dimension="quarter", unit=element_unit,
                bins=_bins(rng, [("Q1", 24), ("Q2", 26), ("Q3", 24), ("Q4", 26)], elements)),
    ]


def _temporal(rng: random.Random, elements: int) -> list[TemporalBin]:
    """Twelve period months preceded by three look-back months.

    The look-back is carried separately (`in_period=False`) rather than merged, because a define whose
    events all land outside the period is the anchoring bug the chart exists to expose.
    """
    out: list[TemporalBin] = []
    for label in ("Oct", "Nov", "Dec"):
        out.append(TemporalBin(label=f"{label} (prior)",
                               count=int(elements * rng.uniform(0.004, 0.012)), in_period=False))
    for label in _MONTHS:
        out.append(TemporalBin(label=label,
                               count=int(elements * rng.uniform(0.06, 0.10)), in_period=True))
    return out


# ── archetypes: a plausible SHAPE per define, for demonstration only ──────────────────────────────

_CQL_TEST = "http://example.org/fhir/CodeSystem/cql-test"
_OBSERVATION_CODES = [
    ("example-reading-a", "Example reading A", _CQL_TEST),
    ("example-reading-b", "Example reading B", _CQL_TEST),
    ("example-reading-panel", "Example reading panel", _CQL_TEST),
    ("example-reading-a-seated", "Example reading A, seated", _CQL_TEST),
    ("example-reading-b-seated", "Example reading B, seated", _CQL_TEST),
    ("example-reading-a-mean", "Example reading A, daily mean", _CQL_TEST),
]
_ENCOUNTER_CODES = [
    ("example-office-visit", "Example office visit", _CQL_TEST),
    ("example-follow-up-visit", "Example follow-up visit", _CQL_TEST),
    ("example-short-visit", "Example short visit", _CQL_TEST),
    ("example-long-visit", "Example long visit", _CQL_TEST),
    ("example-new-patient-visit", "Example new-patient visit", _CQL_TEST),
]


def _archetype(define: str) -> str:
    """Pick a sample SHAPE from the define's name. Demonstration only — nothing depends on this.

    The real builder will not need it: a run knows the result type and the returned values, so the
    shape falls out of the data instead of being guessed from a string.
    """
    # Order matters, and the RESOURCE nouns come first. "Readings During Period" and
    # "Visits During Period" are both list-valued, so a leading "qualif" test would have profiled both
    # as Booleans — which is why `qualif` is not a cohort keyword at all. The Boolean signals are the
    # population names and the `Has …` predicate form.
    lowered = define.lower()
    if any(w in lowered for w in ("encounter", "visit")):
        return "encounter"
    if any(w in lowered for w in ("reading", "observation", "result", "lab")):
        return "observation"
    if any(w in lowered for w in ("population", "denominator", "numerator", "exclusion", "exception",
                                  "has ")):
        return "cohort"
    return "cohort"


def sample_profile(library: str, define: str, *,
                   subjects: int | None = None, elements: int | None = None) -> DefineProfile:
    """Build one define's SAMPLE profile. Deterministic in `library::define`.

    ``subjects`` / ``elements`` are the REAL totals when a caller has them (`insights/profile.py`, which
    derives what it can and asks this module only for the blocks it cannot). Passing them keeps a mixed
    payload internally coherent: an invented distribution over an invented total puts bars summing to
    18,432 under a value profile stating 67, and a reader cannot tell which of the two numbers to
    believe. With the real total the SHAPE is what is invented — which is exactly what the mark claims —
    and the amount the shape distributes is not.
    """
    key = f"{library}::{define}"
    rng = random.Random(key)
    shape = _archetype(define)
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if subjects is None:
        subjects = rng.randint(4_200, 24_000)

    if shape == "cohort":
        # A Boolean define: no element grain, so no element count, no cardinality histogram and no
        # code table. Subject-grain strata only, plus when each subject first qualified.
        profile = DefineProfile(
            library=library, define=define, provenance=PROVENANCE_SAMPLE, generated=generated,
            caveat=_CAVEAT,
            sample=[
                ValueSample(value="true", count=subjects, share=1.0),
            ],
            values=ValueProfile(subjects=subjects, elements=None,
                                null_rate=rng.uniform(0.0, 0.02)),
            strata=[
                *_demographic_strata(rng, subjects, "patients"),
                Stratum(id="first-month", label="First qualified in", grain="subject",
                        dimension="month", unit="patients",
                        bins=_bins(rng, [(m, w) for m, w in
                                         zip(_MONTHS, [14, 11, 10, 9, 8, 8, 7, 7, 7, 6, 6, 7])],
                                   subjects),
                        note="Front-loaded is normal — most subjects qualify as soon as the period opens."),
            ],
        )
        return profile

    element_unit = "encounters" if shape == "encounter" else "observations"
    codes = _ENCOUNTER_CODES if shape == "encounter" else _OBSERVATION_CODES
    if elements is None:
        elements = int(subjects * rng.uniform(2.4, 4.1))

    hit_weights = [58, 46, 22, 6, 4, 1][: len(codes)]
    weight_sum = sum(hit_weights)
    top = [
        CodeHit(code=c, display=d, system=s, count=int(elements * w / weight_sum))
        for (c, d, s), w in zip(codes, hit_weights)
    ]

    value_set = ("Example Office Visit", "http://example.org/fhir/ValueSet/example-office-visit") \
        if shape == "encounter" else ("Example Reading", "http://example.org/fhir/ValueSet/example-reading")

    return DefineProfile(
        library=library, define=define, provenance=PROVENANCE_SAMPLE, generated=generated,
        caveat=_CAVEAT,
        sample=[
            ValueSample(value=f"{c.display} · {c.code}", count=c.count, share=c.count / max(elements, 1))
            for c in top[:5]
        ],
        values=ValueProfile(
            subjects=subjects, elements=elements, null_rate=rng.uniform(0.005, 0.04),
            cardinality=_bins(rng, [("1", 22), ("2", 58), ("3", 100), ("4", 74), ("5", 46),
                                    ("6", 28), ("7", 16), ("8", 9), ("9", 5), ("10+", 3)], subjects),
        ),
        value_sets=[ValueSetHits(name=value_set[0], oid=value_set[1], declared=None,
                                 fired=len(top), top=top)],
        temporal=_temporal(rng, elements),
        strata=[
            *_demographic_strata(rng, subjects, "patients"),
            *_event_strata(rng, elements, element_unit),
            *([Stratum(id="class", label="Encounters by class", grain="element", dimension="class",
                       unit=element_unit, bins=_bins(rng, _ENC_CLASS, elements),
                       note="Derived from Encounter.class, not from the value set.")]
              if shape == "encounter" else []),
        ],
    )
