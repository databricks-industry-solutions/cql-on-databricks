"""The Specification's prose: quoted from the measure's FHIR resource, or parsed from a narration instance.

`DEC-SPEC-SPLIT` split the Specification's prose from the FHIR `Measure` it quotes, and the overlay
model went to `cql_databricks_designer.specification.model`. The quoting (`measure_metadata.py`) came back here
at CARVE-CONTENT: content holds Library and Measure but not the Specification
(`DEC-SPECIFICATION-SCOPE`), so turning a Measure into the document's authored prose is the Designer's.
Vocabulary and tag alignment: `designer/docs/narrative-tags.md`.
"""
from __future__ import annotations

from .parser import parse_specification

__all__ = ["parse_specification"]
