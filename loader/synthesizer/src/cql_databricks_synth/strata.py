"""Population strata enumeration over a measure's defines (SYNTH-PDS-STRATA).

A stratum is a truth assignment over the measure's population criteria. Enumeration is
scoring-type aware: for a proportion measure, numerator membership is only meaningful
inside the un-excluded denominator, so structurally redundant combinations are never
targeted — but combinations that LOOK expressible and turn out contradictory (e.g.
denominator false while the initial population it aliases is true) are still emitted as
targets so the solver can mark them unsatisfiable rather than silently skipping them.

Population kinds are detected from define names, normalized to FHIR's measure-population
codes (``initial-population``, ``denominator``, ...). The normalization is a small, fixed rule,
not evolving logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import SynthesizerError


def normalize_population(name: str) -> str:
    """Normalize a CQL define name to the measure-population code space."""
    import re
    s = name.strip().lower()
    s = re.sub(r"_\d+$", "", s)                 # drop a trailing group index (Numerator_1)
    s = re.sub(r"[\s_]+", "-", s)               # spaces/underscores -> hyphen
    s = re.sub(r"-+", "-", s).strip("-")
    s = re.sub(r"(exclusion|exception)s$", r"\1", s)  # plural define name -> FHIR singular code
    return s

#: Population criteria of a proportion measure, in funnel order.
_PROPORTION_KINDS = (
    "initial-population",
    "denominator",
    "denominator-exclusion",
    "denominator-exception",
    "numerator",
    "numerator-exclusion",
)

_KIND_ABBREV = {
    "initial-population": "IPP",
    "denominator": "DEN",
    "denominator-exclusion": "DENEX",
    "denominator-exception": "DENEXCEP",
    "numerator": "NUM",
    "numerator-exclusion": "NUMEX",
}


@dataclass(frozen=True)
class Stratum:
    """A target truth assignment over population defines. ``None`` in ``wants`` is a CQL
    null target (the three-valued 'missing data' membership path)."""
    label: str
    wants: dict[str, bool | None] = field(hash=False)
    null_target: bool = False

    def describe(self) -> str:
        return self.label


def population_defines(elm) -> dict[str, str]:
    """Map population kind -> define name for every ROOT-library define whose name
    normalizes to a population code. Duplicate kinds (group suffixes) are rejected — the
    synthesizer v1 targets single-group measures."""
    from .library import Env

    env = elm if isinstance(elm, Env) else Env([elm])
    kinds: dict[str, str] = {}
    for name in env.lib(env.root).defines:
        kind = normalize_population(name)
        if kind in _KIND_ABBREV:
            if kind in kinds:
                raise SynthesizerError(
                    f"two defines normalize to population {kind!r}: "
                    f"{kinds[kind]!r} and {name!r} — multi-group measures are not "
                    f"supported in the synthesizer v1")
            kinds[kind] = name
    if "initial-population" not in kinds:
        raise SynthesizerError("no define normalizes to 'initial-population' — not a measure library?")
    return kinds


def proportion_strata(populations: dict[str, str]) -> list[Stratum]:
    """The stratum targets for a proportion measure over the populations present.

    Funnel-ordered: out of IPP; in IPP but out of denominator; excluded; numerator hit;
    numerator miss; plus one null-membership target on the initial population (the
    missing-data path three-valued logic makes reachable). A measure with no numerator (an
    initial population alone, as a cohort measure has) targets its innermost population instead
    of a numerator hit and miss.
    """
    unknown = set(populations) - set(_PROPORTION_KINDS)
    if unknown:
        raise SynthesizerError(f"population kinds {sorted(unknown)} not part of proportion scoring")

    ipp = populations["initial-population"]
    den = populations.get("denominator")
    exclusion = populations.get("denominator-exclusion")
    numerator = populations.get("numerator")

    def label(parts: dict[str, bool | None]) -> str:
        bits = []
        for kind in _PROPORTION_KINDS:
            name = populations.get(kind)
            if name is None or name not in parts:
                continue
            want = parts[name]
            prefix = "" if want else ("null:" if want is None else "!")
            bits.append(prefix + _KIND_ABBREV[kind])
        return "+".join(bits)

    exception = populations.get("denominator-exception")
    strata: list[Stratum] = []

    def add(parts: dict[str, bool | None], *, null_target: bool = False) -> None:
        strata.append(Stratum(label=label(parts), wants=parts, null_target=null_target))

    add({ipp: False})
    if den:
        add({ipp: True, den: False})
        in_denominator: dict[str, bool | None] = {ipp: True, den: True}
    else:
        in_denominator = {ipp: True}
    if exclusion:
        add({**in_denominator, exclusion: True})
        in_denominator = {**in_denominator, exclusion: False}
    if numerator:
        add({**in_denominator, numerator: True})
        add({**in_denominator, numerator: False})
        if exception:
            # A denominator exception only changes scoring for a numerator miss, so
            # that is the combination worth a stratum. The exception is demanded BEFORE
            # the numerator falsification (dict order is solve order): its resources
            # exist first, and the numerator's escape routes must solve around them
            # rather than forbid them away.
            add({**in_denominator, exception: True, numerator: False})
    else:
        add(dict(in_denominator))
    add({ipp: None}, null_target=True)
    return strata


def realistic_counts(strata: list[Stratum], populations: dict[str, str], total: int, *,
                     out_of_ipp: float = 0.30, exclusion_rate: float = 0.05,
                     numerator_rate: float = 0.72, null_rate: float = 0.01,
                     exception_rate: float = 0.02) -> dict[str, int]:
    """Per-stratum counts realizing target funnel rates over ``total`` patients.

    The realistic mix mode: ``out_of_ipp`` and ``null_rate`` are fractions of the whole
    cohort; ``exclusion_rate`` is a fraction of the denominator; ``numerator_rate`` and
    ``exception_rate`` are fractions of the scored (un-excluded) denominator, exceptions
    coming out of the numerator-miss share (an exception only changes scoring for a
    miss). Structurally contradictory strata (e.g. in-IPP-but-out-of-denominator when
    the denominator aliases the IPP) get zero — a demo cohort has no use for patients
    that cannot exist.

    ``numerator_rate`` is a TARGET, not the score the cohort will report: the score divides
    by the denominator the ENGINE scores, and every exclusion the engine declines to count
    enlarges that base. At 0.70 the shipped cohort reported 69.9% for exactly that reason
    (``DATA-SYNTH-LARGE``), so the default carries margin rather than sitting on a boundary.
    """
    if total < 1:
        raise SynthesizerError(f"cohort total must be positive, got {total}")
    ipp = populations["initial-population"]
    den = populations.get("denominator")
    exclusion = populations.get("denominator-exclusion")
    numerator = populations.get("numerator")
    exception = populations.get("denominator-exception")

    n_null = round(total * null_rate)
    n_out = round(total * out_of_ipp)
    denominator = total - n_null - n_out
    if denominator < 0:
        raise ValueError(f"rates leave no denominator: total={total}, out={n_out}, null={n_null}")
    n_excluded = round(denominator * exclusion_rate) if exclusion else 0
    scored = denominator - n_excluded
    n_numerator = round(scored * numerator_rate) if numerator else 0
    n_exception = round(scored * exception_rate) if exception and numerator else 0
    n_miss = scored - n_numerator - n_exception if numerator else scored

    counts: dict[str, int] = {}
    for stratum in strata:
        if stratum.null_target:
            counts[stratum.label] = n_null
        elif stratum.wants.get(ipp) is False:
            counts[stratum.label] = n_out
        elif exclusion and stratum.wants.get(populations["denominator-exclusion"]) is True:
            counts[stratum.label] = n_excluded
        elif exception and stratum.wants.get(exception) is True:
            counts[stratum.label] = n_exception
        elif numerator and stratum.wants.get(numerator) is True:
            counts[stratum.label] = n_numerator
        elif numerator and stratum.wants.get(numerator) is False:
            counts[stratum.label] = n_miss
        elif not numerator and stratum.wants.get(ipp) is True and (den is None or stratum.wants.get(den) is True):
            counts[stratum.label] = scored  # no numerator: the whole scored population
        else:
            counts[stratum.label] = 0  # structural strata (e.g. IPP without denominator)
    return counts
