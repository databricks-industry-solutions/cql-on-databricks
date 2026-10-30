"""the synthesizer — coverage-directed synthetic cohort generation (SYNTH-PDS-MODULE).

Generates synthetic patients FROM measure logic: enumerate population strata over a
measure's ELM (SYNTH-PDS-STRATA), solve each stratum's constraints into skeleton
patients (SYNTH-PDS-ORACLE), and emit FHIR NDJSON plus an expected-results manifest
whose ground truth is known by construction and verified against the same ELM before
anything is written (SYNTH-PDS-VERIFY).

The ELM produced by the Compiler Service is the sole source of measure semantics
(SYNTH-PDS-IR-CONSUMER): the synthesizer walks it, never re-implements CQL, and fails loud
on any construct outside its supported subset rather than approximating.
"""

from __future__ import annotations

GENERATOR_VERSION = "0.2.0"


class SynthesizerError(Exception):
    """A the synthesizer failure with diagnostic text (CORE-NO-SILENT-MISLOWER)."""


class UnsupportedElm(SynthesizerError):
    """An ELM construct outside the synthesizer's supported subset — reported, never approximated."""


class Unsatisfiable(SynthesizerError):
    """A stratum's constraint system has no solution; carries the conflicting sites."""

    def __init__(self, message: str, conflicts: list[str] | None = None):
        super().__init__(message)
        self.conflicts = conflicts or []
