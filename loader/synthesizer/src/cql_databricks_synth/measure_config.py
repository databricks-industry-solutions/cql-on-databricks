"""SYNTH-PDS-MEASURE-CONFIG — a measure's own realism, stated beside it instead of coded here.

``distributions.py`` holds what is true of FHIR data in general and may never key on a measure. What a single
measure needs to look like a population — how its funnel fills, which exclusion route a patient takes, the
plausible range of a reading it compares, which members of its value sets are common — is stated per measure,
in ``<Measure>.synth.json`` beside its CQL, and pinned with it (``pins.json``, SYNTH-PDS-FIXTURE-PINS)::

    {
      "measure": "HypertensionBPControl",
      "version": "1.0.0",
      "populations": {"Initial Population": 0.70},
      "routes": {"Denominator Exclusions": {"Common.In Hospice Care": 1, "Has Kidney Failure": 3}},
      "quantities": {"Example Reading A": {"range": [85, 185]}},
      "members": {"Example Condition Diagnoses": {"example-hypertension": 3, "example-asthma": 1}}
    }

Every key resolves against the measure's ELM — a root population or define, a declared code or value set, a
member of that value set's expansion — and an unknown key is an error, never ignored. A route branch that is a
define of an included library is named by the alias the measure includes it as (``"Common.In Hospice Care"``).
Every section is optional. The file applies in the realistic mix mode only (``--patients``), like the FHIR-level priors: uniform
and explicit modes exist to cover every route and member evenly, and shaping them would defeat that.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SynthesizerError
from .library import Env
from .strata import population_defines
from .valuesets import ValueSetIndex

#: The population roles a rate can be set for, and the realistic-mix flag each one sets (SYNTH-PDS-SCALE).
#: The denominator has no rate of its own: the generator fills it from the initial population.
_RATE_ROLES = {
    "initial-population": "in_ipp",
    "denominator-exclusion": "exclusion_rate",
    "numerator": "numerator_rate",
    "denominator-exception": "exception_rate",
}

_SECTIONS = {"measure", "version", "populations", "routes", "quantities", "members"}


@dataclass(frozen=True)
class MeasureConfig:
    """A measure's realism, resolved against its ELM. Keys are what the solver and fill already see."""

    #: sha256 of the file, so a cohort's identity names the config that shaped it.
    sha256: str = ""
    #: Realistic-mix rates by flag name (``in_ipp``, ``exclusion_rate``, ``numerator_rate``, ``exception_rate``).
    rates: dict[str, float] = field(default_factory=dict)
    #: ``(library, Or locator)`` -> one weight per flattened disjunct, in operand order.
    routes: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    #: Measured code -> plausible ``(lo, hi)``.
    quantities: dict[str, tuple[float, float]] = field(default_factory=dict)
    #: Value-set URL -> ``(system, code)`` -> weight multiplier.
    members: dict[str, dict[tuple[str, str], float]] = field(default_factory=dict)

    def route_weights(self, library: str, locator: str | None) -> list[float] | None:
        return self.routes.get((library, locator or ""))

    def quantity_range(self, code: str | None) -> tuple[float, float] | None:
        return self.quantities.get(code or "")

    def member_multipliers(self, url: str) -> dict[tuple[str, str], float] | None:
        return self.members.get(url)


def load(path: str | Path, env: Env, valuesets: ValueSetIndex) -> MeasureConfig:
    """Read and resolve ``path`` against the measure ``env`` and its expansions. Fails on the first file it
    cannot read, and lists every unresolvable key at once."""
    try:
        data = Path(path).read_bytes()
        raw = json.loads(data)
    except (OSError, json.JSONDecodeError) as e:
        raise SynthesizerError(f"measure config {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SynthesizerError(f"measure config {path}: expected a JSON object")
    root = env.lib(env.root)
    problems: list[str] = []
    for key in sorted(set(raw) - _SECTIONS):
        problems.append(f"unknown section {key!r}; sections are {sorted(_SECTIONS)}")
    if (raw.get("measure"), raw.get("version")) != (root.name, root.version):
        problems.append(f"is for {raw.get('measure')!r} {raw.get('version')!r}, "
                        f"but the measure is {root.name!r} {root.version!r}")
    config = MeasureConfig(
        sha256=hashlib.sha256(data).hexdigest(),
        rates=_rates(_section(raw, "populations", problems), env, problems),
        routes=_routes(_section(raw, "routes", problems), root, problems),
        quantities=_quantities(_section(raw, "quantities", problems), root, valuesets, problems),
        members=_members(_section(raw, "members", problems), root, valuesets, problems),
    )
    if problems:
        raise SynthesizerError(f"measure config {path}:\n  " + "\n  ".join(problems))
    return config


def _section(raw: dict, name: str, problems: list[str]) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        problems.append(f"{name!r} must be an object")
        return {}
    return value


def _rate(value: Any, where: str, problems: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        problems.append(f"{where}: rate must be a number from 0 to 1, got {value!r}")
        return None
    return float(value)


def _weight(value: Any, where: str, problems: list[str]) -> float | None:
    # A zero weight would make a route or member unreachable; SYNTH-PDS-PRIORS keeps every one reachable.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        problems.append(f"{where}: weight must be a positive number, got {value!r}")
        return None
    return float(value)


def _rates(section: dict, env: Env, problems: list[str]) -> dict[str, float]:
    by_define = {define: role for role, define in population_defines(env).items()}
    rates: dict[str, float] = {}
    for define, value in section.items():
        role = by_define.get(define)
        if role is None:
            problems.append(f"populations: {define!r} is not a population of the measure "
                            f"({sorted(by_define)})")
        elif role not in _RATE_ROLES:
            problems.append(f"populations: {define!r} has no rate of its own; the generator fills it from "
                            "the population above")
        elif (rate := _rate(value, f"populations.{define}", problems)) is not None:
            rates[_RATE_ROLES[role]] = rate
    return rates


def _disjuncts(node: dict) -> list[dict]:
    # Flattened exactly as the solver flattens an `or` chain (Solver._conjuncts), so the weights line up
    # with the routes it tries.
    parts: list[dict] = []
    for operand in node["operand"]:
        parts.extend(_disjuncts(operand) if operand.get("type") == "Or" else [operand])
    return parts


def _branch_define(part: dict) -> str | None:
    """The define an `or` branch names: ``"X"`` or ``exists "X"``, and ``"Common.X"`` for a define of an included
    library, by the alias the measure includes it as."""
    if part.get("type") == "Exists":
        part = part.get("operand") or {}
    if part.get("type") != "ExpressionRef":
        return None
    return f"{part['libraryName']}.{part['name']}" if part.get("libraryName") else part.get("name")


def _routes(section: dict, root, problems: list[str]) -> dict[tuple[str, str], list[float]]:
    routes: dict[tuple[str, str], list[float]] = {}
    for define, weights in section.items():
        statement = root.defines.get(define)
        expression = statement.get("expression") if statement else None
        if expression is None:
            problems.append(f"routes: {define!r} is not a define of {root.name}")
            continue
        if expression.get("type") != "Or":
            problems.append(f"routes: {define!r} is not an `or` of defines")
            continue
        if not isinstance(weights, dict):
            problems.append(f"routes.{define} must be an object of define -> weight")
            continue
        names = [_branch_define(part) for part in _disjuncts(expression)]
        if None in names:
            problems.append(f"routes: every branch of {define!r} must be a define, or `exists` of one")
            continue
        if set(weights) != set(names):
            problems.append(f"routes.{define}: weights name {sorted(weights)}, the branches are {sorted(names)}")
            continue
        resolved = [_weight(weights[name], f"routes.{define}.{name}", problems) for name in names]
        if None not in resolved:
            routes[(root.name, expression.get("locator") or "")] = resolved
    return routes


def _codes_of(name: str, root, valuesets: ValueSetIndex) -> list[str] | None:
    if name in root.codes and name in root.valuesets:
        return None
    if name in root.codes:
        return [root.codes[name].code]
    if name in root.valuesets and root.valuesets[name] in valuesets.urls():
        return [member.code for member in valuesets.members(root.valuesets[name])]
    return None


def _quantities(section: dict, root, valuesets: ValueSetIndex,
                problems: list[str]) -> dict[str, tuple[float, float]]:
    quantities: dict[str, tuple[float, float]] = {}
    for name, spec in section.items():
        codes = _codes_of(name, root, valuesets)
        if not codes:
            problems.append(f"quantities: {name!r} is not one declared code or value set of {root.name}")
            continue
        bounds = spec.get("range") if isinstance(spec, dict) else None
        if set(spec if isinstance(spec, dict) else {}) != {"range"} or not isinstance(bounds, list) \
                or len(bounds) != 2 or not all(isinstance(b, (int, float)) and not isinstance(b, bool)
                                               for b in bounds) or bounds[0] > bounds[1]:
            problems.append(f"quantities.{name} must be {{\"range\": [low, high]}} with low <= high")
            continue
        for code in codes:
            quantities[code] = (float(bounds[0]), float(bounds[1]))
    return quantities


def _members(section: dict, root, valuesets: ValueSetIndex,
             problems: list[str]) -> dict[str, dict[tuple[str, str], float]]:
    members: dict[str, dict[tuple[str, str], float]] = {}
    for name, weights in section.items():
        url = root.valuesets.get(name)
        if url is None:
            problems.append(f"members: {name!r} is not a value set of {root.name}")
            continue
        if not isinstance(weights, dict):
            problems.append(f"members.{name} must be an object of code -> weight")
            continue
        if url not in valuesets.urls():
            problems.append(f"members: {name!r} ({url}) has no loaded expansion")
            continue
        by_code: dict[str, list[tuple[str, str]]] = {}
        for member in valuesets.members(url):
            by_code.setdefault(member.code, []).append((member.system, member.code))
        resolved: dict[tuple[str, str], float] = {}
        for code, value in weights.items():
            keys = by_code.get(code)
            if keys is None:
                problems.append(f"members.{name}: {code!r} is not a member of {url}")
            elif (weight := _weight(value, f"members.{name}.{code}", problems)) is not None:
                for key in keys:
                    resolved[key] = weight
        members[url] = resolved
    return members
