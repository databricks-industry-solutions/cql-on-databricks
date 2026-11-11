"""Distributional fill, minimal: skeletons -> complete FHIR resources.

Determining attributes come solved from the skeleton (a value, a temporal/numeric domain
to sample inside, or a demanded absence). Everything else is constant fill: the fixed
QI-Core scaffolding a resource type needs to be a plausible, profile-shaped instance.
Sampling is jittered *within* satisfying domains from the cohort seed
(SYNTH-PDS-DETERMINISM) — never on domain edges.

Fill invariants the solver's slot algebra relies on (see solve.py):
- Conditions carry NO abatement and clinicalStatus ``active`` unless solved otherwise,
  so the onset/abatement interval helper yields ``[onset, +inf)``.
- Choice elements are point-valued (onsetDateTime / effectiveDateTime /
  performedDateTime), so temporal boundary helpers collapse to the point.
- Vital-sign-profile observations always carry BOTH component values.
- A resource names its patient in the element FHIR R4 gives it: ``subject``, or ``patient`` on
  AllergyIntolerance, Device and Immunization, and ``beneficiary`` on Coverage.

Every resource is tagged synthetic — ``meta.security`` HTEST plus a generator-version
``meta.tag`` (SYNTH-PDS-SYNTHETIC-TAG) — so generated data can never pass as real.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random

from . import GENERATOR_VERSION, SynthesizerError, distributions
from .library import Code
from .measure_config import MeasureConfig
from .solve import (ABSENT, PRESENT, DateDomain, NumericDomain, ResourceSketch,
                    SolveState, TemporalDomain)
from .valuesets import Coding

_TAG_SYSTEM = "http://cql-databricks.databricks.com/patient-data-synthesizer"
_HTEST = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
    "code": "HTEST",
    "display": "test health data",
}

_PATIENT_PROFILE = "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-patient"
_UCUM = "http://unitsofmeasure.org"

_ENCOUNTER_DURATION = dt.timedelta(hours=1)

#: The element that names the patient, where FHIR R4 does not call it ``subject``.
PATIENT_REFERENCE = {"Immunization": "patient", "Coverage": "beneficiary",
                     "AllergyIntolerance": "patient", "Device": "patient"}

#: Temporal elements that are a plain dateTime, not a ``[x]`` choice, on the generic template.
_PLAIN_TEMPORAL = frozenset({"authoredOn", "issued", "recordedDate", "dateWritten", "recorded"})

#: The unit of a vital-sign component the logic demands present but compares nowhere, so no solved domain carries
#: one: the FHIR R4 Vital Signs blood pressure profile (observation-bp) fixes both components in mm[Hg].
_COMPONENT_UNITS = {"8480-6": "mm[Hg]", "8462-4": "mm[Hg]"}

#: Elements that are LISTS of CodeableConcept in FHIR; a solved list of Codes
#: serializes element-per-code.
_CODEABLE_LIST_FIELDS = frozenset({"reasonCode", "category"})

#: A leftover PRESENT marker on Observation.value (an `is not null` demand nothing else
#: constrained): a synthetic result concept, so the element is populated without inventing a
#: measurement or naming a real code.
_PRESENT_RESULT = Code(system="http://example.org/fhir/CodeSystem/cql-test", code="synthetic-result",
                       display="Synthetic result")


def _rng(seed: int, patient_key: str, stream: str) -> random.Random:
    digest = hashlib.blake2b(f"{seed}:{patient_key}:{stream}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _meta(profile: str | None) -> dict:
    meta: dict = {}
    if profile:
        meta["profile"] = [profile]
    meta["security"] = [dict(_HTEST)]
    meta["tag"] = [{"system": _TAG_SYSTEM, "code": f"pds-{GENERATOR_VERSION}"}]
    return meta


def _codeable(coding: Coding | Code) -> dict:
    entry = {"system": coding.system, "code": coding.code}
    if coding.display:
        entry["display"] = coding.display
    return {"coding": [entry]}


def _fhir_datetime(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


def _sample_datetime(rng: random.Random, domain: TemporalDomain) -> dt.datetime:
    seconds = int((domain.hi - domain.lo).total_seconds())
    point = domain.lo + dt.timedelta(seconds=rng.randrange(seconds + 1))
    daytime = point.replace(hour=rng.randrange(8, 18), minute=rng.choice((0, 15, 30, 45)),
                            second=0, microsecond=0)
    if domain.lo <= daytime <= domain.hi:
        return daytime
    return max(domain.lo, min(domain.hi, point.replace(second=0, microsecond=0)))


def _sample_date(rng: random.Random, domain: DateDomain) -> dt.date:
    days = (domain.hi - domain.lo).days
    return domain.lo + dt.timedelta(days=rng.randrange(days + 1))


def _sample_numeric(rng: random.Random, domain: NumericDomain, priors: bool,
                    code: str | None = None, config: MeasureConfig | None = None) -> float:
    """Uniform across the satisfying domain, or (SYNTH-PDS-PRIORS) triangular inside the
    part of it the measure's config calls plausible for ``code`` (SYNTH-PDS-MEASURE-CONFIG).

    The narrowing has to come first: a solved domain is bounded only where the measure
    compared something, so centre-weighting it on its own pulls readings toward the
    middle of ``0 .. threshold`` rather than toward a plausible value.
    """
    if not priors:
        return float(int(domain.lo + rng.random() * (domain.hi - domain.lo)))
    bounds = config.quantity_range(code) if config else None
    lo, hi = distributions.plausible_domain(domain.lo, domain.hi, bounds)
    return float(int(rng.triangular(lo, hi)))


def materialize(state: SolveState, *, patient_id: str, seed: int, patient_key: str,
                period_lo: dt.datetime, period_hi: dt.datetime,
                priors: bool = False, config: MeasureConfig | None = None) -> list[dict]:
    """The completed FHIR resources for one solved patient (patient first)."""
    rng = _rng(seed, patient_key, "fill")
    resources = [_patient(state, rng, patient_id, period_hi, priors)]
    for index, sketch in enumerate(state.resources):
        resources.append(
            _resource(sketch, rng, patient_id, index, period_lo, period_hi, priors, config))
    return resources


def _weighted(rng: random.Random, prior: dict[str, float]) -> str:
    codes = sorted(prior)
    return rng.choices(codes, weights=[prior[code] for code in codes], k=1)[0]


def _patient(state: SolveState, rng: random.Random, patient_id: str,
             period_hi: dt.datetime, priors: bool) -> dict:
    patient: dict = {
        "resourceType": "Patient",
        "id": patient_id,
        "meta": _meta(_PATIENT_PROFILE),
        "identifier": [{"system": _TAG_SYSTEM, "value": patient_id}],
        "name": [{"family": "Synth", "given": [f"Case {patient_id.rsplit('-', 1)[-1]}"]}],
    }
    gender = state.patient_fields.get("gender")
    if gender is None:
        gender = (_weighted(rng, distributions.ELEMENT_PRIORS[("Patient", "gender")])
                  if priors else rng.choice(["female", "male"]))
    if gender is not ABSENT:
        patient["gender"] = gender
    if priors and "maritalStatus" not in state.patient_fields:
        # A demographic no measure criterion needs to read, carried because a Patient
        # with no marital status is not what a real extract looks like (SYNTH-PDS-PRIORS).
        code = _weighted(rng, distributions.ELEMENT_PRIORS[("Patient", "maritalStatus")])
        patient["maritalStatus"] = {"coding": [{
            "system": distributions.MARITAL_STATUS_SYSTEM, "code": code,
            "display": distributions.MARITAL_STATUS_DISPLAY[code]}]}
    _place_extensions(patient, state.patient_fields, rng)
    for prop, value in state.patient_fields.items():
        if prop == "gender" or prop.startswith("extension:") or value is ABSENT:
            continue
        if isinstance(value, (TemporalDomain, DateDomain, NumericDomain)):
            raise SynthesizerError(f"unfilled domain on patient property {prop!r}")
        patient.setdefault(prop, value)

    birth = state.birth_date
    if birth is None:
        birth = DateDomain(lo=period_hi.date().replace(year=period_hi.year - 60),
                           hi=period_hi.date().replace(year=period_hi.year - 30))
    if birth is not ABSENT:
        patient["birthDate"] = _sample_date(rng, birth).isoformat()
    return patient


def _resource(sketch: ResourceSketch, rng: random.Random, patient_id: str, index: int,
              period_lo: dt.datetime, period_hi: dt.datetime, priors: bool,
              config: MeasureConfig | None) -> dict:
    default_domain = TemporalDomain(lo=period_lo + dt.timedelta(days=30),
                                    hi=period_hi - dt.timedelta(days=30))

    def temporal(prop: str) -> dt.datetime | None:
        value = sketch.fields.get(prop)
        if value is ABSENT:
            return None
        if value is None or value is PRESENT:
            value = default_domain
        if not isinstance(value, TemporalDomain):
            raise SynthesizerError(f"{sketch.resource_type}.{prop} is not a temporal slot")
        return _sample_datetime(rng, value)

    def coded(prop: str, default: str):
        value = sketch.fields.get(prop, default)
        return None if value is ABSENT else value

    resource: dict = {
        "resourceType": sketch.resource_type,
        "id": f"{patient_id}-r{index}",
        "meta": _meta(sketch.template_id),
        PATIENT_REFERENCE.get(sketch.resource_type, "subject"): {"reference": f"Patient/{patient_id}"},
    }

    handled = {"status", "class", "clinicalStatus", "verificationStatus",
               "period", "onset", "effective", "performed"}
    _place_code(resource, sketch)

    if sketch.resource_type == "Encounter":
        if (status := coded("status", "finished")) is not None:
            resource["status"] = status
        klass = coded("class", "AMB") or "AMB"
        resource["class"] = {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                             "code": klass.code if isinstance(klass, Code) else klass}
        if (start := temporal("period")) is not None:
            resource["period"] = {"start": _fhir_datetime(start),
                                  "end": _fhir_datetime(start + _ENCOUNTER_DURATION)}
    elif sketch.resource_type == "Condition":
        # A demanded ABSENT status is omitted, never defaulted: absence IS the constraint.
        if (clinical := coded("clinicalStatus", "active")) is not None:
            resource["clinicalStatus"] = _status_concept(
                clinical, "http://terminology.hl7.org/CodeSystem/condition-clinical")
        if (verification := coded("verificationStatus", "confirmed")) is not None:
            resource["verificationStatus"] = _status_concept(
                verification, "http://terminology.hl7.org/CodeSystem/condition-ver-status")
        resource["category"] = [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-category",
            "code": "problem-list-item", "display": "Problem List Item"}]}]
        if (onset := temporal("onset")) is not None:
            resource["onsetDateTime"] = _fhir_datetime(onset)
    elif sketch.resource_type == "Observation":
        if (status := coded("status", "final")) is not None:
            resource["status"] = status
        resource["category"] = [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory"}]}]
        if (effective := temporal("effective")) is not None:
            resource["effectiveDateTime"] = _fhir_datetime(effective)
        value = sketch.fields.get("value")
        if isinstance(value, NumericDomain):
            resource["valueQuantity"] = {
                "value": _sample_numeric(rng, value, priors,
                                         sketch.code.code if sketch.code else None, config),
                "unit": value.unit, "system": _UCUM, "code": value.unit}
            handled.add("value")
        elif value is PRESENT:
            # `value is not null` was the whole demand: a synthetic result concept.
            resource["valueCodeableConcept"] = _codeable(_PRESENT_RESULT)
            handled.add("value")
        _place_components(resource, sketch, rng, priors=priors, config=config)
    elif sketch.resource_type == "Procedure":
        if (status := coded("status", "completed")) is not None:
            resource["status"] = status
        if (performed := temporal("performed")) is not None:
            resource["performedDateTime"] = _fhir_datetime(performed)
    else:
        # Generic template for request-shaped types (ServiceRequest, DeviceRequest,
        # MedicationRequest, ...): status + coded element + any solved temporal fields,
        # serialized under their FHIR choice/plain keys. Verification catches any type
        # whose real shape needs more than this (SYNTH-PDS-VERIFY).
        if (status := coded("status", "active")) is not None:
            resource["status"] = status
        for prop, value in list(sketch.fields.items()):
            if prop.startswith("extension:"):
                continue
            if isinstance(value, TemporalDomain):
                point = temporal(prop)
                if point is not None:
                    key = prop if prop in _PLAIN_TEMPORAL else f"{prop}DateTime"
                    resource[key] = _fhir_datetime(point)
                handled.add(prop)

    _place_extensions(resource, sketch.fields, rng)
    for prop, value in sketch.fields.items():
        if prop in handled or prop.startswith("component") \
                or prop.startswith("extension:") or value is ABSENT:
            continue
        if isinstance(value, (TemporalDomain, DateDomain, NumericDomain)):
            raise SynthesizerError(f"unplaced domain field {sketch.resource_type}.{prop}")
        if value is PRESENT:
            raise SynthesizerError(
                f"presence demanded on {sketch.resource_type}.{prop} with no fill shape")
        if isinstance(value, Code):
            # A coded choice element serializes under its variant key (value[x]); a backbone element's field
            # (`hospitalization.dischargeDisposition`) nests under its parent.
            key = "valueCodeableConcept" if prop == "value" else prop
            *parents, leaf = key.split(".")
            owner = resource
            for parent in parents:
                owner = owner.setdefault(parent, {})
            owner.setdefault(leaf, _codeable(value))
        elif isinstance(value, list) and value and all(isinstance(c, Code) for c in value):
            # A solved coded list (`reasonCode in "…"`), element per code.
            resource.setdefault(prop, [_codeable(c) for c in value])
        else:
            resource.setdefault(prop, value)
    return resource


def _place_extensions(resource: dict, fields: dict, rng: random.Random) -> None:
    """Serialize solved ``extension:<url>`` fields: a string is a valueCode, a Code a
    valueCodeableConcept, a temporal domain a sampled valueDateTime."""
    extensions = []
    for key in sorted(k for k in fields if k.startswith("extension:")):
        value = fields[key]
        if value is ABSENT:
            continue
        url = key.removeprefix("extension:")
        if isinstance(value, TemporalDomain):
            extensions.append({"url": url,
                               "valueDateTime": _fhir_datetime(_sample_datetime(rng, value))})
        elif isinstance(value, Code):
            extensions.append({"url": url, "valueCodeableConcept": _codeable(value)})
        elif isinstance(value, str):
            extensions.append({"url": url, "valueCode": value})
        else:
            raise SynthesizerError(f"extension {url} holds unfillable {value!r}")
    if extensions:
        resource["extension"] = extensions


def _place_code(resource: dict, sketch: ResourceSketch) -> None:
    coding = sketch.code
    if coding is None:
        return
    element = _codeable(coding)
    if sketch.code_property == "type":
        resource["type"] = [element]
    else:
        resource[sketch.code_property or "code"] = element


def _status_concept(value, system: str) -> dict:
    if isinstance(value, Code):
        return _codeable(value)
    return {"coding": [{"system": system, "code": value}]}


def _place_components(resource: dict, sketch: ResourceSketch, rng: random.Random,
                      *, priors: bool, config: MeasureConfig | None) -> None:
    domains: dict[str, NumericDomain] = {}
    systems: dict[str, str] = {}
    for key, value in sketch.fields.items():
        if key.startswith("component:") and value is PRESENT:
            code = key.removeprefix("component:")
            if code not in _COMPONENT_UNITS:
                raise SynthesizerError(f"component {code!r} demanded present with no unit to fill it with")
            value = NumericDomain(lo=0.0, hi=400.0, unit=_COMPONENT_UNITS[code])
        if key.startswith("component:") and isinstance(value, NumericDomain):
            domains[key.removeprefix("component:")] = value
        if key.startswith("component-system:"):
            systems[key.removeprefix("component-system:")] = value
    if not domains:
        return
    components = []
    for code in sorted(domains):
        domain = domains[code]
        if code not in systems:
            raise SynthesizerError(f"component {code!r} was solved with no code system")
        components.append({
            "code": {"coding": [{"system": systems[code], "code": code}]},
            "valueQuantity": {"value": _sample_numeric(rng, domain, priors, code, config),
                              "unit": domain.unit, "system": _UCUM, "code": domain.unit},
        })
    resource["component"] = components
