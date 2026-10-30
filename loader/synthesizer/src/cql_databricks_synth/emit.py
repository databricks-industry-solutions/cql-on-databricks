"""Emission: bulk-data NDJSON, the expected-results manifest, the coverage report.

Outputs are byte-deterministic for identical (measure, config, seed) inputs
(SYNTH-PDS-DETERMINISM) — which is why manifest rows carry no wall-clock timestamp;
the manifest is the contract the conformance join asserts against (SYNTH-PDS-ORACLE):
one row per patient, ``expected`` mapping every define to its serialized expected value,
including expected ``null`` under three-valued logic.

``CohortWriter`` is the streaming half (SYNTH-PDS-SCALE): patients are appended as they
are produced, one open handle per resource type, so a million-patient cohort never lives
in memory. ``write_cohort`` is the same writer driven from an in-memory cohort — the two
paths produce identical bytes.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TextIO

from .evaluator import Concept, CqlInterval, Quantity
from .generate import Cohort, CohortMeta, GeneratedPatient, StratumOutcome
from .library import Code


def serialize_value(value: object) -> str:
    """One define's expected value in manifest form: true/false/null, a scalar, a
    resource reference, or the non-null element count of a list-valued define."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return f"count:{sum(1 for item in value if item is not None)}"
    if isinstance(value, dict):
        return f"{value.get('resourceType', 'Resource')}/{value.get('id', '?')}"
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, CqlInterval):
        return f"interval[{serialize_value(value.low)}, {serialize_value(value.high)}]"
    if isinstance(value, Quantity):
        return f"{value.value:g} '{value.unit}'"
    if isinstance(value, Code):
        return f"{value.system}|{value.code}"
    if isinstance(value, Concept):
        return "concept(" + ", ".join(f"{c.system}|{c.code}" for c in value.codes) + ")"
    return json.dumps(value)


class CohortWriter:
    """Streams a cohort to disk: <Type>.ndjson per resource type + manifest.jsonl,
    then coverage.md at close. Append-per-patient; nothing retained."""

    def __init__(self, out_dir: str | Path, meta: CohortMeta):
        self.root = Path(out_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta = meta
        self.patient_count = 0
        self._files: dict[str, TextIO] = {}
        self._manifest = (self.root / "manifest.jsonl").open("w")

    def _handle(self, resource_type: str) -> TextIO:
        handle = self._files.get(resource_type)
        if handle is None:
            handle = (self.root / f"{resource_type}.ndjson").open("w")
            self._files[resource_type] = handle
        return handle

    def add(self, patient: GeneratedPatient) -> None:
        for resource in patient.resources:
            self._handle(resource["resourceType"]).write(
                json.dumps(resource, separators=(",", ":")) + "\n")
        self._manifest.write(json.dumps({
            "cohort_id": self.meta.cohort_id,
            "patient_id": patient.patient_id,
            "measure_id": self.meta.measure_id,
            "measure_version": self.meta.measure_version,
            "stratum": patient.stratum,
            "boundary_tags": patient.boundary_tags,
            "expected": {name: serialize_value(value)
                         for name, value in patient.expected.items()},
            "generator_version": self.meta.generator_version,
            "seed": self.meta.seed,
            "config_hash": self.meta.config_hash,
            "measurement_period": self.meta.measurement_period,
        }, separators=(",", ":")) + "\n")
        self.patient_count += 1

    def close(self, outcomes: list[StratumOutcome]) -> list[Path]:
        for handle in self._files.values():
            handle.close()
        self._manifest.close()
        coverage = self.root / "coverage.md"
        coverage.write_text(coverage_markdown(self.meta, outcomes, self.patient_count))
        written = sorted(self.root / f"{t}.ndjson" for t in self._files)
        return [*written, self.root / "manifest.jsonl", coverage]


def write_cohort(cohort: Cohort, out_dir: str | Path) -> list[Path]:
    """Write an in-memory cohort: <Type>.ndjson, manifest.jsonl, coverage.md."""
    writer = CohortWriter(out_dir, cohort.meta)
    for patient in cohort.patients:
        writer.add(patient)
    return writer.close(cohort.outcomes)


def coverage_markdown(meta: CohortMeta, outcomes: list[StratumOutcome],
                      patient_count: int) -> str:
    lines = [
        f"# Patient Data Synthesizer coverage — {meta.measure_id} {meta.measure_version}",
        "",
        f"- cohort: `{meta.cohort_id}`",
        f"- seed: {meta.seed} · generator: {meta.generator_version} · "
        f"config: `{meta.config_hash[:12]}`",
        f"- measurement period: {meta.measurement_period or 'the measure default'}",
        f"- patients: {patient_count}",
        "",
        "| stratum | status | patients | notes |",
        "|---|---|---|---|",
    ]
    for outcome in outcomes:
        notes = outcome.detail
        if outcome.conflicts:
            notes += " — conflicts: " + "; ".join(outcome.conflicts)
        lines.append(
            f"| `{outcome.stratum}` | {outcome.status} | {outcome.patient_count} "
            f"| {notes.strip()} |")
    lines.append("")

    # SYNTH-PDS-SKIPPED — the routes the solver could not invert. These patients were
    # solved another way, so nothing here is a failure; what it IS is the difference
    # between "this define is empty because the measure says so" and "because the
    # synthesizer cannot build data for it", which is invisible in the cohort itself.
    skipped: dict[str, tuple[set[str], int]] = {}
    for outcome in outcomes:
        for reason, count in outcome.skipped_routes.items():
            strata, total = skipped.get(reason, (set(), 0))
            skipped[reason] = (strata | {outcome.stratum}, total + count)
    if skipped:
        lines += [
            "## Routes skipped as unsupported",
            "",
            "Solvable by a sibling route, so these patients generated normally — but the",
            "criteria below are never exercised, and any define reachable only through",
            "them is empty in this cohort for a reason the data cannot show.",
            "",
            "| patients | strata | route the solver could not invert |",
            "|---|---|---|",
        ]
        for reason, (strata, total) in sorted(skipped.items(), key=lambda kv: -kv[1][1]):
            listed = ", ".join(f"`{s}`" for s in sorted(strata))
            lines.append(f"| {total} | {listed} | {reason.replace('|', '\\|')} |")
        lines.append("")
    return "\n".join(lines)
