"""Cohort generation: enumerate -> solve -> fill -> ground truth (SYNTH-PDS-ORACLE).

Every generated patient carries the expected value of EVERY define in the measure,
computed by evaluating the completed bundle through the same ELM the engine compiles —
before any engine executes. The verification invariant (SYNTH-PDS-VERIFY) is
non-negotiable and runs on 100% of patients: a solved patient whose re-evaluated
membership disagrees with its stratum is a hard failure, never a quiet re-roll.

Unsatisfiable strata are first-class outcomes (SYNTH-PDS-STRATA): they are reported
with their conflicting constraint sites — authoring feedback in their own right — rather
than silently skipped.

Two consumption shapes over one generator core (``iter_patients``):

- ``generate()`` returns the whole cohort in memory — tests, small fixtures.
- ``generate_to_dir()`` streams patients straight to disk via ``emit.CohortWriter`` —
  the million-patient path; nothing per-patient is retained (SYNTH-PDS-SCALE).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import GENERATOR_VERSION, SynthesizerError, Unsatisfiable
from .evaluator import Bundle, CqlInterval, Evaluator
from .library import Env
from .fill import materialize
from .measure_config import MeasureConfig
from .solve import Solver
from .strata import Stratum, population_defines, proportion_strata
from .valuesets import ValueSetIndex

log = logging.getLogger("cql_databricks_synth")

_PROGRESS_EVERY = 100_000
#: Bounded deterministic restarts for cross-define route conflicts.
_SOLVE_RETRIES = 8


@dataclass
class GeneratedPatient:
    patient_id: str
    stratum: str
    boundary_tags: list[str]
    resources: list[dict]
    expected: dict[str, object]


@dataclass
class StratumOutcome:
    stratum: str
    status: str                       # "satisfied" | "unsatisfiable" | "not-targeted"
    detail: str = ""
    conflicts: list[str] = field(default_factory=list)
    patient_count: int = 0
    #: SYNTH-PDS-SKIPPED — routes the solver could not invert, on patients it solved
    #: anyway via a sibling: reason -> how many patients hit it. A dark exclusion path
    #: is invisible in the data itself (the define is simply always empty), so it has to
    #: be stated here or nobody can tell it from a define the logic keeps empty.
    skipped_routes: dict[str, int] = field(default_factory=dict)


@dataclass
class CohortMeta:
    """The per-cohort constants every manifest row carries (SYNTH-PDS-ORACLE)."""
    cohort_id: str
    measure_id: str
    measure_version: str
    seed: int
    config_hash: str
    generator_version: str
    #: LANG-PARAMETER: the run-time Measurement Period, ``START/END`` in ISO dates, or None
    #: when the measure's own default applies.
    measurement_period: str | None = None


@dataclass
class Cohort:
    """An in-memory cohort (the small-fixture shape; large cohorts stream instead)."""
    meta: CohortMeta
    patients: list[GeneratedPatient]
    outcomes: list[StratumOutcome]

    @property
    def cohort_id(self) -> str:
        return self.meta.cohort_id

    @property
    def measure_id(self) -> str:
        return self.meta.measure_id

    @property
    def measure_version(self) -> str:
        return self.meta.measure_version

    @property
    def seed(self) -> int:
        return self.meta.seed

    @property
    def config_hash(self) -> str:
        return self.meta.config_hash

    @property
    def generator_version(self) -> str:
        return self.meta.generator_version


def run_parameters(measurement_period: str | None) -> dict[str, object]:
    """LANG-PARAMETER: the run-time parameter values for a ``START/END`` Measurement Period.

    Both dates are whole days and inclusive: the interval runs from the first instant of START
    to the last millisecond of END, the shape a calendar-year period has in CQL."""
    if measurement_period is None:
        return {}
    try:
        start, end = (dt.date.fromisoformat(part) for part in measurement_period.split("/"))
    except ValueError as e:
        raise SynthesizerError(
            f"measurement period {measurement_period!r} is not START/END in ISO dates "
            f"(for example 2026-01-01/2026-12-31)") from e
    if end < start:
        raise SynthesizerError(f"measurement period {measurement_period!r} ends before it starts")
    return {"Measurement Period": CqlInterval(
        dt.datetime.combine(start, dt.time()),
        dt.datetime.combine(end, dt.time(23, 59, 59, 999000)))}


def uniform_counts(strata: list[Stratum], per_stratum: int) -> dict[str, int]:
    """The uniform/adversarial mix: equal density per stratum (conformance mode)."""
    return {stratum.label: per_stratum for stratum in strata}


def cohort_meta(elm, *, seed: int, counts: dict[str, int], priors: bool = False,
                measurement_period: str | None = None, config_sha256: str | None = None) -> CohortMeta:
    env = elm if isinstance(elm, Env) else Env([elm])
    root = env.lib(env.root)
    measure_id, measure_version = root.name, root.version
    identity = {
        "measure": [measure_id, measure_version],
        "seed": seed,
        "counts": dict(sorted(counts.items())),
        "generator": GENERATOR_VERSION,
        # SYNTH-PDS-PRIORS is part of what built the data, so it is part of the identity:
        # two cohorts at one seed that differ in shaping must not share a cohort id.
        "priors": priors,
        "measurement_period": measurement_period,
    }
    if config_sha256:
        # SYNTH-PDS-MEASURE-CONFIG: the measure's config shaped the data too. Absent, the identity is unchanged.
        identity["measure_config"] = config_sha256
    config_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return CohortMeta(
        cohort_id=f"{measure_id}-{measure_version}-s{seed}-{config_hash[:8]}",
        measure_id=measure_id, measure_version=measure_version, seed=seed,
        config_hash=config_hash, generator_version=GENERATOR_VERSION,
        measurement_period=measurement_period)


def iter_patients(elm, valuesets: ValueSetIndex, *, seed: int,
                  counts: dict[str, int],
                  outcomes: list[StratumOutcome],
                  priors: bool = False,
                  measurement_period: str | None = None,
                  config: MeasureConfig | None = None) -> Iterator[GeneratedPatient]:
    """The generator core: solve, fill, ground-truth and verify one patient at a time.

    ``outcomes`` is appended to as strata complete (in stratum order), so a streaming
    caller holds the full coverage record once the iterator is exhausted.

    ``priors`` applies the FHIR-level distributional shaping (SYNTH-PDS-PRIORS). It
    changes WHICH satisfying patient is built, never whether one exists: verification
    still runs on every patient, so a prior that pushed one out of its stratum would be
    a hard failure rather than a quiet miss.
    """
    if config is not None and not priors:
        raise SynthesizerError("a measure config shapes the realistic mix only; it needs priors (--patients)")
    env = elm if isinstance(elm, Env) else Env([elm])
    parameters = run_parameters(measurement_period)
    populations = population_defines(env)
    strata = proportion_strata(populations)
    known = {stratum.label for stratum in strata}
    unknown = set(counts) - known
    if unknown:
        raise SynthesizerError(
            f"counts name unknown strata {sorted(unknown)}; known strata: {sorted(known)}")

    ordinal = 0
    for stratum in strata:
        target = counts.get(stratum.label, 0)
        # A zero-count stratum was never attempted — "satisfied" would be a claim nobody
        # checked, so it reports as not-targeted (the coverage report stays honest).
        outcome = StratumOutcome(
            stratum=stratum.label, status="satisfied" if target else "not-targeted")
        for copy_index in range(target):
            # Route choices are greedy per decision site; a cross-define conflict
            # (two sibling falsifications pinning contradictory age bands, say) is
            # retried with a re-salted chooser before the stratum is declared
            # unsatisfiable — a deterministic, bounded restart, not a fallback path.
            state = None
            unsat: Unsatisfiable | None = None
            for retry in range(_SOLVE_RETRIES):
                patient_key = f"{stratum.label}#{copy_index}" + (f"~r{retry}" if retry else "")
                solver = Solver(env, valuesets, seed=seed, patient_key=patient_key,
                                priors=priors, parameters=parameters, config=config)
                try:
                    state = solver.solve(dict(stratum.wants))
                    break
                except Unsatisfiable as e:
                    unsat = e
            if state is None:
                outcome.status = "unsatisfiable"
                outcome.detail = str(unsat)
                outcome.conflicts = unsat.conflicts if unsat else []
                break
            for reason in set(solver.skipped):
                outcome.skipped_routes[reason] = outcome.skipped_routes.get(reason, 0) + 1
            patient_id = f"pds-{seed}-{ordinal:07d}"
            ordinal += 1
            resources = materialize(
                state, patient_id=patient_id, seed=seed, patient_key=patient_key,
                period_lo=solver.period_lo, period_hi=solver.period_hi, priors=priors,
                config=solver.config)
            evaluator, expected = _ground_truth(env, valuesets, resources, parameters)
            _verify_stratum(stratum, state.define_wants, evaluator, patient_id)
            _verify_forbidden(state.forbidden, resources, valuesets, patient_id)
            outcome.patient_count += 1
            if ordinal % _PROGRESS_EVERY == 0:
                log.info("generated %d patients (through %s)", ordinal, stratum.label)
            yield GeneratedPatient(
                patient_id=patient_id, stratum=stratum.label,
                boundary_tags=["null:initial-population"] if stratum.null_target else [],
                resources=resources, expected=expected)
        outcomes.append(outcome)


def generate(elm, valuesets: ValueSetIndex, *, seed: int,
             per_stratum: int = 1, counts: dict[str, int] | None = None,
             priors: bool = False, measurement_period: str | None = None,
             config: MeasureConfig | None = None) -> Cohort:
    """An in-memory cohort: ``counts`` per stratum, or ``per_stratum`` uniform.

    ``elm`` is a single library's ELM dict or a multi-library ``Env``."""
    if counts is None:
        counts = uniform_counts(proportion_strata(population_defines(elm)), per_stratum)
    meta = cohort_meta(elm, seed=seed, counts=counts, priors=priors,
                       measurement_period=measurement_period,
                       config_sha256=config.sha256 if config else None)
    outcomes: list[StratumOutcome] = []
    patients = list(iter_patients(elm, valuesets, seed=seed, counts=counts,
                                  outcomes=outcomes, priors=priors,
                                  measurement_period=measurement_period,
                                  config=config))
    return Cohort(meta=meta, patients=patients, outcomes=outcomes)


def generate_to_dir(elm, valuesets: ValueSetIndex, out_dir: str | Path, *,
                    seed: int, counts: dict[str, int],
                    priors: bool = False,
                    measurement_period: str | None = None,
                    config: MeasureConfig | None = None) -> tuple[CohortMeta, list[StratumOutcome]]:
    """Stream a cohort to ``out_dir`` without holding it in memory (SYNTH-PDS-SCALE).

    Byte-identical to ``generate()`` + ``emit.write_cohort()`` for the same inputs
    (SYNTH-PDS-DETERMINISM); the only difference is when patients hit disk.
    """
    from .emit import CohortWriter

    meta = cohort_meta(elm, seed=seed, counts=counts, priors=priors,
                       measurement_period=measurement_period,
                       config_sha256=config.sha256 if config else None)
    outcomes: list[StratumOutcome] = []
    writer = CohortWriter(out_dir, meta)
    try:
        for patient in iter_patients(elm, valuesets, seed=seed, counts=counts,
                                     outcomes=outcomes, priors=priors,
                                     measurement_period=measurement_period,
                                     config=config):
            writer.add(patient)
    finally:
        writer.close(outcomes)
    return meta, outcomes


def _ground_truth(env, valuesets: ValueSetIndex, resources: list[dict],
                  parameters: dict[str, object]) -> tuple[Evaluator, dict[str, object]]:
    bundle = Bundle()
    for resource in resources:
        bundle.add(resource)
    evaluator = Evaluator(env, valuesets, bundle, parameters)
    return evaluator, {name: evaluator.define(name) for name in evaluator.define_names()}


def _verify_stratum(stratum: Stratum, wants: dict[tuple[str, str], bool | None],
                    evaluator: Evaluator, patient_id: str) -> None:
    """SYNTH-PDS-VERIFY: the completed bundle must land exactly where it was aimed —
    every targeted define, in every library the solve touched."""
    for (library, name), want in wants.items():
        got = evaluator.define(name, library)
        if isinstance(got, list):
            populated = any(item is not None for item in got)
            ok = populated is want
        else:
            ok = got is want
        if not ok:
            raise SynthesizerError(
                f"verification failed for {patient_id} in stratum {stratum.label!r}: "
                f"define {library}.{name!r} was solved toward {want!r} but evaluates to {got!r}")


def _verify_forbidden(forbidden: dict[tuple[str, str | None], str], resources: list[dict],
                      valuesets: ValueSetIndex, patient_id: str) -> None:
    """No emitted resource may match a retrieve the solver promised would come up empty.

    Keys are ``(type, valueset-url)`` for code-filtered retrieves, ``(type, templateId)``
    for profile-only retrieves, ``(type, None)`` for bare type retrieves."""
    known_urls = set(valuesets.urls())
    for (resource_type, ident), site in forbidden.items():
        for resource in resources:
            if resource["resourceType"] != resource_type:
                continue
            if ident is None:
                violated = True
            elif ident.startswith("code:"):
                wanted = {tuple(pair.split("|", 1)) for pair in
                          ident.removeprefix("code:").split(",")}
                violated = False
                elements = resource.get("type") or resource.get("code") or []
                concepts = elements if isinstance(elements, list) else [elements]
                for concept in concepts:
                    for coding in concept.get("coding", []):
                        if (coding.get("system", ""), coding.get("code", "")) in wanted:
                            violated = True
            elif ident in known_urls:
                violated = False
                elements = resource.get("type") or resource.get("code") or []
                concepts = elements if isinstance(elements, list) else [elements]
                for concept in concepts:
                    for coding in concept.get("coding", []):
                        if valuesets.contains(ident, coding.get("system", ""),
                                              coding.get("code", "")):
                            violated = True
            else:  # a profile templateId
                violated = ident in (resource.get("meta") or {}).get("profile", [])
            if violated:
                raise SynthesizerError(
                    f"verification failed for {patient_id}: resource "
                    f"{resource['id']} violates forbidden retrieve {site}")
