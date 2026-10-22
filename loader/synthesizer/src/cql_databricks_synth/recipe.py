"""DATA-SYNTH-SMALL — a population built from a recipe: one realistic cohort per pinned measure, merged.

A recipe names each cohort by its ``pins.json`` key with a seed of its own, and gives one size and one measurement
period for all of them::

    {"patients": 1000, "measurementPeriod": "2026-01-01/2026-12-31",
     "cohorts": [{"measure": "HypertensionBPControl", "seed": 201}, ...]}

Each cohort is generated in the realistic mix from its pinned fixture, shaped by its pinned config
(SYNTH-PDS-MEASURE-CONFIG), and every patient is re-verified as it is generated (SYNTH-PDS-VERIFY). The population
is the cohorts' FHIR resources only, one ``<Type>.ndjson`` per type: no manifest and no expected results ship with
it. Generation is deterministic (SYNTH-PDS-DETERMINISM), so a recipe and its pins give the same bytes every time.
Patient ids are ``pds-<seed>-<n>``, so the seeds must differ.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import SynthesizerError
from .fixtures import CONFIG_NAME, fixture_dir
from .generate import generate_to_dir
from .library import Env
from .measure_config import load as load_config
from .strata import population_defines, proportion_strata, realistic_counts, realistic_rates
from .valuesets import ValueSetIndex


@dataclass(frozen=True)
class Cohort:
    measure: str
    seed: int


@dataclass(frozen=True)
class Recipe:
    patients: int
    measurement_period: str
    cohorts: list[Cohort]

    @classmethod
    def from_file(cls, path: str | Path) -> Recipe:
        try:
            raw = json.loads(Path(path).read_text())
            recipe = cls(patients=int(raw["patients"]), measurement_period=raw["measurementPeriod"],
                         cohorts=[Cohort(measure=c["measure"], seed=int(c["seed"])) for c in raw["cohorts"]])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise SynthesizerError(f"recipe {path}: {e}") from e
        seeds = [cohort.seed for cohort in recipe.cohorts]
        if len(set(seeds)) != len(seeds):
            raise SynthesizerError(f"recipe {path}: seeds {seeds} repeat, so patient ids would collide")
        return recipe


def build_population(recipe: Recipe, out_dir: str | Path) -> dict[str, int]:
    """Generate every cohort of ``recipe`` and write the merged population to ``out_dir``. Every ``*.ndjson``
    already there is replaced, so a type no cohort emits does not linger. Returns resource counts per type."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        cohort_dirs = []
        for cohort in recipe.cohorts:
            fixtures = fixture_dir(cohort.measure)
            if fixtures is None:
                raise SynthesizerError(
                    f"{cohort.measure} fixtures not built (or stale) — run: python -m cql_databricks_synth fixtures")
            env = Env.from_paths([fixtures], root=cohort.measure)
            valuesets = ValueSetIndex.from_ndjson_dir(fixtures)
            config = load_config(fixtures / CONFIG_NAME, env, valuesets) if (fixtures / CONFIG_NAME).exists() \
                else None
            populations = population_defines(env)
            counts = realistic_counts(proportion_strata(populations), populations, recipe.patients,
                                      **realistic_rates(config))
            cohort_dir = Path(scratch) / cohort.measure
            generate_to_dir(env, valuesets, cohort_dir, seed=cohort.seed, counts=counts, priors=True,
                            measurement_period=recipe.measurement_period, config=config)
            cohort_dirs.append(cohort_dir)
        for stale in out.glob("*.ndjson"):
            stale.unlink()
        types = sorted({path.stem for d in cohort_dirs for path in d.glob("*.ndjson")})
        written: dict[str, int] = {}
        for fhir_type in types:
            lines = [line for d in cohort_dirs if (d / f"{fhir_type}.ndjson").exists()
                     for line in (d / f"{fhir_type}.ndjson").read_text().splitlines() if line]
            (out / f"{fhir_type}.ndjson").write_text("".join(f"{line}\n" for line in lines))
            written[fhir_type] = len(lines)
    return written
