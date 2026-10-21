"""SYNTH-PDS-PRIORS — distributional priors over the FHIR data model.

Uniform selection gives perfect coverage and implausible data: every member of a value
set lands on ~1/N of patients, every arm of a CQL disjunction on ~1/N, so a terminology
histogram over a generated cohort is flat where real data clusters hard. These priors
restore the clustering without touching what the solver PROVED — a prior only reorders
choices the measure logic already declared equally valid, and every patient still
re-evaluates through the full ELM before emission (SYNTH-PDS-VERIFY), so a prior can
never produce a patient in the wrong stratum.

**Scope rule — the only rule that keeps this file honest.** Every weight here is a
property of the FHIR data model or of terminology in general: an element's real-world
value mix, a coding system's share of coded entries. Nothing here may key on a measure,
a value set, a define or a population. The test: *adding a second measure to the corpus
must never require editing this file.* Measure-shaped ratios — how often an exclusion is
one exclusion route rather than another, how numerator patients spread across value ranges —
are the caller's to state through the mix-mode rate flags (SYNTH-PDS-SCALE), never this
module's to invent. Those route choices stay uniform.

Two mechanisms, because coded values arrive by two different paths:

1. **Element priors** (``ELEMENT_PRIORS``) — when the logic enumerates the permitted
   values of a named element (``C.verificationStatus ~ "confirmed" or ~ "unconfirmed"
   or ...``), weight the arms by what that element actually looks like in practice.
2. **Source-system clustering** (``member_weights``) — when the logic filters on a value
   set, the member is otherwise arbitrary. Real cohorts cluster because a site codes in
   one primary system; so each patient draws a home coding system, members from it take
   the bulk of the weight, and a mild Zipf inside each system supplies the head-and-tail
   shape. The within-system ranking is a stable hash of the code: cohort-wide consistent
   (so clusters are visible) but arbitrary — it is NOT a prevalence claim, and nothing
   here should be read as one.

Priors apply in realistic mix mode only. Uniform/adversarial mode keeps flat selection so
conformance fixtures still exercise every member of every domain, which is the whole
point of that mode.
"""

from __future__ import annotations

import hashlib

from .valuesets import Coding

#: Key standing for "element not populated" inside an ``ELEMENT_PRIORS`` row. A real
#: element mix includes its own absence rate, and CQL's ``X is not null implies ...``
#: idiom makes absence a first-class arm of the choice.
ABSENT_VALUE = "<absent>"

#: Per-element value mixes, keyed ``(resourceType, element)``. Weights are relative and
#: need not sum to 1. Deliberately EXCLUDED, and not by oversight:
#: ``Condition.clinicalStatus`` (fill pins ``active`` so ``onsetSpan()`` stays
#: ``[onset, +inf)`` — a solver invariant, see fill.py) and ``Encounter.class`` (fill's
#: unconstrained default is load-bearing for encounter-shaped criteria). An element whose
#: value the solver reasons about structurally is not a free choice to reweight.
ELEMENT_PRIORS: dict[tuple[str, str], dict[str, float]] = {
    ("Condition", "verificationStatus"): {
        "confirmed": 0.68,
        "unconfirmed": 0.08,
        "provisional": 0.04,
        "differential": 0.01,
        ABSENT_VALUE: 0.19,
    },
    ("Encounter", "status"): {
        "finished": 0.94, "in-progress": 0.03, "planned": 0.02, "cancelled": 0.01,
    },
    ("Observation", "status"): {
        "final": 0.93, "amended": 0.04, "preliminary": 0.03,
    },
    ("Procedure", "status"): {
        "completed": 0.93, "in-progress": 0.04, "not-done": 0.03,
    },
    ("Patient", "gender"): {"female": 0.51, "male": 0.49},
    ("Patient", "maritalStatus"): {"M": 0.48, "S": 0.30, "D": 0.11, "W": 0.08, "UNK": 0.03},
}

#: Marital-status codes carry a code system and display; the element prior above names
#: only the codes (v3-MaritalStatus), so fill needs the rest to build the CodeableConcept.
MARITAL_STATUS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-MaritalStatus"
MARITAL_STATUS_DISPLAY = {
    "M": "Married", "S": "Never Married", "D": "Divorced", "W": "Widowed", "UNK": "unknown",
}

#: Share of a domain's coded entries a source system writes in its PRIMARY coding system.
#: The remainder spreads over whatever other systems the value set offers.
HOME_SYSTEM_SHARE = 0.82

#: Relative likelihood that a source system's primary system is a given one, used both to
#: draw a patient's home system and to spread the non-home remainder. Systems absent from
#: a value set drop out and the rest renormalize, so this is a preference order over
#: whatever terminology the measure happens to bind.
SYSTEM_MIX: dict[str, float] = {
    "http://snomed.info/sct": 0.58,
    "http://hl7.org/fhir/sid/icd-10-cm": 0.30,
    "http://hl7.org/fhir/sid/icd-9-cm": 0.04,
    "http://loinc.org": 0.50,
    "http://www.nlm.nih.gov/research/umls/rxnorm": 0.50,
    "http://www.ama-assn.org/go/cpt": 0.34,
    "http://www.icd10data.com/icd10pcs": 0.08,
}

#: Weight of any system not named above — present so an unfamiliar terminology still
#: participates rather than vanishing from the cohort.
SYSTEM_MIX_DEFAULT = 0.10

#: Zipf exponent for member selection within one coding system. 0 is flat; ~1 is a
#: classic long tail. Kept mild so the tail still populates at cohort scale.
MEMBER_ZIPF = 0.9

def plausible_domain(lo: float, hi: float, bounds: tuple[float, float] | None) -> tuple[float, float]:
    """``(lo, hi)`` narrowed to a plausible ``bounds`` where the two overlap.

    Which quantities a measure compares, and what range is plausible for them, are measure-shaped facts, so
    ``bounds`` comes from the measure's own config (SYNTH-PDS-MEASURE-CONFIG), never from this module. With
    none, the solved domain is sampled whole. Where the two do not overlap, the SOLVED domain wins untouched:
    the measure's constraint is truth and plausibility is decoration. That case is real — a measure asking for
    an extreme reading is not wrong, it is just outside the everyday band — so it must not be treated as an
    error.
    """
    if bounds is None:
        return lo, hi
    low, high = max(lo, bounds[0]), min(hi, bounds[1])
    return (low, high) if low <= high else (lo, hi)


def element_weights(resource_type: str, prop: str, values: list[str]) -> list[float] | None:
    """Relative weights for ``values`` of one element, or None if no prior covers it.

    None means "no opinion" and leaves the caller's selection uniform — an element this
    module has never heard of is not one it should be shaping.
    """
    prior = ELEMENT_PRIORS.get((resource_type, prop))
    if prior is None:
        return None
    weights = [prior.get(value, 0.0) for value in values]
    # An element whose prior names none of the offered values tells us nothing useful;
    # an all-zero weight vector would also make the pick undefined.
    return weights if any(weights) else None


def _rank_key(coding: Coding) -> bytes:
    """Stable cohort-wide ordering key for a code — arbitrary, but the SAME arbitrary
    everywhere, which is what makes a cluster visible instead of averaging flat."""
    return hashlib.blake2b(f"{coding.system}|{coding.code}".encode(), digest_size=8).digest()


def home_system(present: list[str], draw: float) -> str:
    """The primary coding system for one patient's source system.

    ``present`` is the systems the value set actually offers and ``draw`` a uniform
    [0, 1) from the patient's chooser, so the home system is deterministic per patient
    (SYNTH-PDS-DETERMINISM) and varies across the cohort.
    """
    weights = [SYSTEM_MIX.get(system, SYSTEM_MIX_DEFAULT) for system in present]
    total = sum(weights)
    threshold = draw * total
    running = 0.0
    for system, weight in zip(present, weights):
        running += weight
        if threshold < running:
            return system
    return present[-1]


def member_weights(members: list[Coding], draw: float) -> list[float]:
    """Relative weights over a value set's members: home-system bulk, Zipf tail.

    Group weight goes ``HOME_SYSTEM_SHARE`` to the patient's home system and the rest to
    the other systems in ``SYSTEM_MIX`` proportion; inside a group, member ``i`` of the
    stable ranking gets ``1 / (i + 1) ** MEMBER_ZIPF``.
    """
    systems: dict[str, list[int]] = {}
    for index, coding in enumerate(members):
        systems.setdefault(coding.system, []).append(index)
    present = sorted(systems)
    home = home_system(present, draw)

    others = [system for system in present if system != home]
    other_total = sum(SYSTEM_MIX.get(system, SYSTEM_MIX_DEFAULT) for system in others)
    group_weight = {home: HOME_SYSTEM_SHARE if others else 1.0}
    for system in others:
        share = SYSTEM_MIX.get(system, SYSTEM_MIX_DEFAULT) / other_total
        group_weight[system] = (1.0 - HOME_SYSTEM_SHARE) * share

    weights = [0.0] * len(members)
    for system, indices in systems.items():
        ranked = sorted(indices, key=lambda i: _rank_key(members[i]))
        zipf = [1.0 / (rank + 1) ** MEMBER_ZIPF for rank in range(len(ranked))]
        scale = group_weight[system] / sum(zipf)
        for rank, index in enumerate(ranked):
            weights[index] = zipf[rank] * scale
    return weights
