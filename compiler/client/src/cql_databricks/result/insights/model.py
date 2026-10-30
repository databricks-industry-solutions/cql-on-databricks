"""APP-DATA-PROFILE / APP-PROFILE-STRATA — the wire contract for one define's data profile.

The Designer's right pane asks three things about a define: what it returns, what it produced, and how
what it produced is distributed. `Returns` is answered CLIENT-SIDE from the ELM (`elmDefines.ts`) and
does not appear here — the translator already emits result types, so a second server-side copy could
disagree with the first (`APP-SPEC-MODEL`). What this module carries is everything that requires a
RUN: the sample of returned values, the value profile, code-hit frequency, temporal spread, and the
stratifications.

**PROVENANCE IS ON THE WIRE.** Every payload states whether it came from a run or from the sample
generator (`provenance`), because a distribution is indistinguishable from a plausible invention once
it is drawn as a bar. `APP-NO-UI-MOCK` forbids fabricated figures in the product; this field is what
keeps that rule enforceable while the derivation is built — the client renders sample payloads behind a
visible mark and can never mistake one for a result. The shape does NOT change when the source does,
which is the whole point of settling it now: wiring the real derivation replaces `sample.py` and
touches nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A profile's two possible origins. `sample` is scaffolding; `run` is a real evaluation.
PROVENANCE_SAMPLE = "sample"
PROVENANCE_RUN = "run"
#: A profile whose blocks do not share one origin — see `DefineProfile.sampled`.
PROVENANCE_MIXED = "mixed"

#: The profile's block names, as the wire spells them. `DefineProfile.sampled` names the ones on a
#: given payload that are FABRICATED, so the client can mark exactly those.
BLOCK_SAMPLE = "sample"
BLOCK_VALUES = "values"
BLOCK_VALUE_SETS = "valueSets"
BLOCK_TEMPORAL = "temporal"
BLOCK_STRATA = "strata"


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """Drop `None` values — the wire omits absent fields rather than carrying nulls."""
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class Bin:
    """One bucket of a distribution. `share` is of the stratum's own total, never of the measure."""

    label: str
    count: int
    share: float

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "count": self.count, "share": round(self.share, 4)}


@dataclass
class Stratum:
    """One stratification of a define's output.

    `grain` is load-bearing and is the reason this is not a flat list of charts. A stratification is
    either over the SUBJECTS the define is true of (age band, sex, payer) or over the ELEMENTS it
    returned (encounters by month, by class). "1,204" means different things in each, and a chart that
    does not say which is unreadable — an episode-of-care measure counting patients is the same defect
    `SPEC-METRIC` names for the metric pill.
    """

    id: str
    label: str
    grain: str  # "subject" | "element"
    dimension: str
    #: What one count IS — "patients", "encounters". Rendered, never inferred by the client.
    unit: str
    bins: list[Bin] = field(default_factory=list)
    #: Stated where the cut needs a caveat the bars cannot carry (e.g. an unknown/absent bucket).
    note: str | None = None
    #: APP-NO-UI-MOCK — this CUT is fabricated, whatever the rest of the payload is.
    #:
    #: Per stratum rather than per block because the strata block is the one that is internally mixed:
    #: sex and age band are read from the Patient binding while the element-grain cuts have no relation
    #: to read at all. A block-level flag would either mark the real cuts or unmark the invented ones,
    #: and both are the failure the mark exists to prevent.
    sampled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "id": self.id,
            "label": self.label,
            "grain": self.grain,
            "dimension": self.dimension,
            "unit": self.unit,
            "bins": [b.to_dict() for b in self.bins],
            "note": self.note,
            "sampled": self.sampled or None,
        })


@dataclass
class ValueSample:
    """One of the top-N most frequent returned values — the sample beside the result type."""

    value: str
    count: int
    share: float

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "count": self.count, "share": round(self.share, 4)}


@dataclass
class CodeHit:
    """A code that actually fired, and how often."""

    code: str
    display: str
    system: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "display": self.display, "system": self.system, "count": self.count}


@dataclass
class ValueSetHits:
    """Code-hit frequency for one value set the define reads (`APP-LIVING-CHARTS`).

    `declared` (how many codes the value set contains) comes from the terminology tables and is
    `None` until that read path exists — absent, never zero, because "0 codes declared" and "we have
    not looked" are opposite claims.
    """

    name: str
    oid: str | None
    declared: int | None
    fired: int
    top: list[CodeHit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "name": self.name,
            "oid": self.oid,
            "declared": self.declared,
            "fired": self.fired,
            "top": [c.to_dict() for c in self.top],
        })


@dataclass
class TemporalBin:
    """One bucket of the temporal spread. `inPeriod` separates the measurement period from its
    look-back, which is what makes an anchoring error visible instead of merely plotted."""

    label: str
    count: int
    in_period: bool

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "count": self.count, "inPeriod": self.in_period}


@dataclass
class ValueProfile:
    """What came back, at both grains (`SPEC-METRIC`).

    `elements` is `None` for a define with no element grain — a Boolean says something about the
    subject and nothing else, so an element count would be an invented dimension.
    """

    subjects: int
    elements: int | None
    null_rate: float
    #: Per-subject list length, for a list-valued define. Empty for scalar returns.
    cardinality: list[Bin] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "subjects": self.subjects,
            "elements": self.elements,
            "nullRate": round(self.null_rate, 4),
            "cardinality": [b.to_dict() for b in self.cardinality] or None,
        })


@dataclass
class DefineMetric:
    """APP-SAMPLE-METRICS — the two grains for ONE define, and nothing else.

    Deliberately numbers only. `SPEC-METRIC` decides a metric's UNIT from the ELM `context`, its FORM
    from the result type and its BASE from the enclosing population — all three already derived in the
    client (`specDocument.ts`). Putting any of them here would be a second opinion able to disagree
    with the first (`APP-SPEC-MODEL`), so the split is: the client says what the figure MEANS, this
    says what the figure IS.

    `elements` is `None` wherever there is no element grain. A Boolean define says something about the
    subject and nothing else, so an element count there would be an invented dimension.

    SPEC-POPULATION-CASCADE — `subjects` is MET: the define true across every evaluated subject.
    `counted` is the met subjects inside the cohort the client named for this define, and
    `cohort_subjects` is that cohort's size. The client chose the cohort and names it; these are the
    numbers.
    """

    define: str
    library: str
    subjects: int
    counted: int
    cohort_subjects: int
    elements: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "define": self.define,
            "library": self.library,
            "subjects": self.subjects,
            "counted": self.counted,
            "cohortSubjects": self.cohort_subjects,
            "elements": self.elements,
        })


@dataclass
class MeasureMetrics:
    """APP-SAMPLE-METRICS — every figure one Specification renders, in one payload.

    ONE REQUEST, NOT ONE PER DEFINE. The document, the nav and the reported result must agree to the
    digit — a nav row saying 8,204 beside a section saying 8,207 is the two-surfaces-disagreeing
    failure `APP-SPEC-MODEL` exists to prevent — and figures fetched per element cannot be made
    coherent after the fact. Population attrition is the sharper case: `Denominator` is only meaningful
    against `Initial Population`, so the chain has to be dealt in one hand.

    NO FUNNEL AND NO RATE IN THIS PAYLOAD. The funnel is drawn from these counts (`funnelFromCounts()`,
    `model.ts`). The score is `insights/score.py`'s (`RUN-CLIENT-MEASURE-SCORE`), sent beside this payload
    by the route, so "what did this measure score" has one answer, computed in one place.

    `subjects` is the EVALUATED total — every subject the measure was run against, before any
    criterion. It is the base a `COMMON` define's share is taken against (`SPEC-METRIC`), which is why
    it is stated rather than inferred from the largest population.
    """

    library: str
    version: str
    provenance: str
    #: ISO-8601. For a run this is when it evaluated; for a sample, when it was generated.
    generated: str
    subjects: int
    caveat: str | None = None
    defines: list[DefineMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "library": self.library,
            "version": self.version,
            "provenance": self.provenance,
            "generated": self.generated,
            "subjects": self.subjects,
            "caveat": self.caveat,
            "defines": [d.to_dict() for d in self.defines] or None,
        })


@dataclass
class DefineProfile:
    """One define's data profile, library-qualified.

    Library-qualified because two libraries may declare one name (`SPEC-ANCHOR`), so an unqualified
    profile would resolve to the wrong define.
    """

    library: str
    define: str
    provenance: str
    #: ISO-8601. For a run this is when it evaluated; for a sample, when it was generated.
    generated: str
    #: Free-text reason the payload is what it is — rendered by the client next to the sample mark.
    caveat: str | None = None
    sample: list[ValueSample] = field(default_factory=list)
    values: ValueProfile | None = None
    value_sets: list[ValueSetHits] = field(default_factory=list)
    #: SPEC-TERMINOLOGY-EXTRACT — the coded values the returned records actually carry, one entry per
    #: CURATED path (`insights/terminology.py`). DERIVED, never sampled: it is a `GROUP BY` over the
    #: define's own result relation, so a define with no resource-valued output simply has none. This is
    #: not `value_sets` by another name — that block reports hits against a value set the logic BINDS,
    #: which needs a filter this layer may not reconstruct (`SPEC-CLAUSE-PROBE`, rejected); this one
    #: reports what is ON the records, which needs no filter at all.
    terminology: list[dict] = field(default_factory=list)
    temporal: list[TemporalBin] = field(default_factory=list)
    strata: list[Stratum] = field(default_factory=list)
    #: APP-NO-UI-MOCK — the blocks on THIS payload that are fabricated (`BLOCK_*`).
    #:
    #: PROVENANCE MOVED FROM THE PAYLOAD TO THE BLOCK when the derivable blocks were wired, and it had
    #: to. `provenance` is one word for the whole profile, which was true while every block came from
    #: the same place; a payload whose value profile is read from the evaluation and whose temporal
    #: spread is still invented cannot be described by one word, and the honest options were both bad —
    #: stamp it `run` and the invented bars lose their mark, stamp it `sample` and real figures carry a
    #: warning that is false. The block list is what lets the mark land on exactly the invented bars.
    #: `provenance` survives as the payload's summary (`mixed` when this list is a proper subset).
    sampled: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean({
            "library": self.library,
            "define": self.define,
            "provenance": self.provenance,
            "generated": self.generated,
            "caveat": self.caveat,
            "sampled": self.sampled or None,
            "sample": [s.to_dict() for s in self.sample] or None,
            "values": self.values.to_dict() if self.values else None,
            "valueSets": [v.to_dict() for v in self.value_sets] or None,
            "terminology": self.terminology or None,
            "temporal": [t.to_dict() for t in self.temporal] or None,
            "strata": [s.to_dict() for s in self.strata] or None,
        })
