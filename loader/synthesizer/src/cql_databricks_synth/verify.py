"""Standalone re-verification of an emitted cohort (SYNTH-PDS-VERIFY).

Reads the NDJSON bundles and the manifest back off disk, re-evaluates every patient
through the ELM, and reports any define whose recomputed value disagrees with the
manifest. This is the same invariant the generator enforces at emission time, exposed
as its own entry point so a cohort can be re-audited after the fact (or after edits).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import SynthesizerError
from .evaluator import Bundle, Evaluator
from .emit import serialize_value
from .fill import PATIENT_REFERENCE
from .generate import run_parameters
from .valuesets import ValueSetIndex


@dataclass
class Mismatch:
    patient_id: str
    define: str
    expected: str
    actual: str


def load_bundles(out_dir: str | Path) -> dict[str, Bundle]:
    """Per-patient bundles from the emitted <Type>.ndjson files, keyed by patient id."""
    root = Path(out_dir)
    bundles: dict[str, Bundle] = {}
    for path in sorted(root.glob("*.ndjson")):
        if path.name == "manifest.jsonl":
            continue
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                resource = json.loads(line)
                if resource["resourceType"] == "Patient":
                    key = resource["id"]
                else:
                    element = PATIENT_REFERENCE.get(resource["resourceType"], "subject")
                    reference = resource.get(element, {}).get("reference", "")
                    if not reference.startswith("Patient/"):
                        raise SynthesizerError(
                            f"resource {resource.get('id')} has no Patient reference in {element}")
                    key = reference.removeprefix("Patient/")
                bundles.setdefault(key, Bundle()).add(resource)
    return bundles


def verify_cohort(elm: dict, valuesets: ValueSetIndex, out_dir: str | Path) -> list[Mismatch]:
    """Every manifest row re-evaluated; returns the mismatches (empty = verified)."""
    root = Path(out_dir)
    manifest_path = root / "manifest.jsonl"
    if not manifest_path.exists():
        raise SynthesizerError(f"no manifest.jsonl under {root}")
    bundles = load_bundles(root)
    mismatches: list[Mismatch] = []
    with manifest_path.open() as fh:
        for line in fh:
            row = json.loads(line)
            patient_id = row["patient_id"]
            bundle = bundles.get(patient_id)
            if bundle is None:
                mismatches.append(Mismatch(patient_id, "<bundle>", "present", "missing"))
                continue
            # LANG-PARAMETER: the period the cohort was generated for travels on every row.
            evaluator = Evaluator(elm, valuesets, bundle,
                                  run_parameters(row.get("measurement_period")))
            for define, expected in row["expected"].items():
                actual = serialize_value(evaluator.define(define))
                if actual != expected:
                    mismatches.append(Mismatch(patient_id, define, expected, actual))
    return mismatches
