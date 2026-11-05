"""CONTENT-EXAMPLE-PRELOAD — load the example measures, their value sets and their patients from ``examples/``.

``examples/`` is the one place that pre-loads a workspace with example measures. The deploy runs this
(``scripts/deploy/provision-examples.py``). Nothing here reads the compiler's conformance corpus, which is a
separate, independent copy.

What it reads, and where each part lands in the environment in ``<catalog>``
(``DATA-ENVIRONMENT-LAYOUT``, ``DEPLOY-DEFAULT-ENVIRONMENT``):

- ``examples/measures/<Library>.cql`` — every CQL library, as a FHIR ``Library`` resource (the CQL as a
  ``text/cql`` attachment) in ``knowledge.library``.
- ``examples/measures/<Library>.measure.json`` — the authored FHIR ``Measure`` resource of each example measure,
  in ``knowledge.measure``. A library is a measure in the catalog only when a ``Measure`` references it
  (``CONTENT-MEASURE-LIBRARY-LINK``), so each one names its library's canonical URL.
- ``examples/valuesets/*.ndjson`` — value-set expansion rows ``{valueset_id, system, code, display}``, in
  ``knowledge.valueset``. Every value set a library declares must have codes here.
- ``examples/data/<Type>.ndjson`` — the synthetic population small (``DATA-SYNTH-SMALL``), in ``data``
  (``population.land``).

The Designer catalog and ``cql(library=...)`` find the measures there by name (``BIND-PUBLISHED-VIEW``).
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from cql_databricks.data_bindings.measure_import.transform import _CQL_HEADER_RE

log = logging.getLogger("shipped_sources.examples")

#: The canonical base of every sample resource.
CANONICAL_BASE = "http://example.org/fhir"

#: Provenance stamped on every row.
SOURCE_PACKAGE = "examples"
SOURCE_LINEAGE = "example"


class ExampleContentError(ValueError):
    """``examples/`` does not hold a loadable set of example measures."""


@dataclass(frozen=True)
class ExampleContent:
    """Everything ``examples/`` holds, read and checked, before anything is written."""
    resources: list[dict]           # the Library and Measure resources
    codes: list[dict]               # value-set code rows {valueset_id, system, code, display}
    patients: dict[str, list[str]]  # FHIR type -> NDJSON lines


def library_resource(cql: str, source: str) -> dict:
    """The FHIR ``Library`` resource for one CQL file: identity from its header, the CQL as an attachment."""
    header = _CQL_HEADER_RE.search(cql)
    if header is None:
        raise ExampleContentError(f"{source}: no `library <name> version '<version>'` header")
    name, version = header.group(1).strip('"'), header.group(2)
    if Path(source).stem != name:
        raise ExampleContentError(f"{source} declares library {name}; the file must be named {name}.cql")
    return {
        "resourceType": "Library",
        "id": name,
        "url": f"{CANONICAL_BASE}/Library/{name}",
        "version": version,
        "name": name,
        "status": "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/library-type",
                             "code": "logic-library"}]},
        "content": [{"contentType": "text/cql", "data": base64.b64encode(cql.encode()).decode()}],
    }


def read_examples(examples: Path) -> ExampleContent:
    """Read and check ``examples/``. Fails on anything that would load a measure the catalog cannot run."""
    measures_dir = examples / "measures"
    cql_files = sorted(measures_dir.glob("*.cql"))
    if not cql_files:
        raise ExampleContentError(f"no CQL under {measures_dir}")
    libraries = [library_resource(f.read_text(), str(f)) for f in cql_files]
    by_url = {lib["url"]: lib for lib in libraries}

    measures = []
    for path in sorted(measures_dir.glob("*.measure.json")):
        measure = json.loads(path.read_text())
        if measure.get("resourceType") != "Measure":
            raise ExampleContentError(f"{path} is not a FHIR Measure resource")
        refs = measure.get("library") or []
        missing = [ref for ref in refs if ref.split("|", 1)[0] not in by_url]
        if not refs or missing:
            raise ExampleContentError(f"{path}: library {missing or refs} is not a CQL file in {measures_dir}")
        measures.append(measure)
    if not measures:
        raise ExampleContentError(f"no <Library>.measure.json under {measures_dir}: nothing would be a measure")

    codes = [json.loads(line) for path in sorted((examples / "valuesets").glob("*.ndjson"))
             for line in path.read_text().splitlines() if line.strip()]

    patients = {path.stem: [line for line in path.read_text().splitlines() if line.strip()]
                for path in sorted((examples / "data").glob("*.ndjson"))}
    if not patients.get("Patient"):
        raise ExampleContentError(f"no patients: {examples / 'data' / 'Patient.ndjson'} is missing or empty")

    return ExampleContent(resources=libraries + measures, codes=codes, patients=patients)


def import_example_measures(spark, *, catalog: str, examples: Path) -> dict[str, int]:
    """CONTENT-IMPORT-MEASURES — the example measures and their value sets into the knowledge of the environment in
    ``catalog``, through the one import path. Returns row counts."""
    from cql_databricks.data_bindings.measure_import import import_measures

    content = read_examples(examples)
    return dict(import_measures(spark, content.resources, codes=content.codes, catalog=catalog,
                                source=SOURCE_PACKAGE, lineage=SOURCE_LINEAGE).rows)


def load_examples(spark, *, catalog: str, examples: Path, valid_from: str, valid_to: str) -> dict[str, int]:
    """Write ``examples/`` into the environment in ``catalog`` (see the module docstring): its knowledge and its
    patients' data tables. Returns row counts."""
    from .population import land

    content = read_examples(examples)
    counts = import_example_measures(spark, catalog=catalog, examples=examples)
    raw = {fhir_type: spark.createDataFrame([(line,) for line in lines], "value STRING")
           for fhir_type, lines in content.patients.items()}
    counts.update(land(spark, catalog=catalog, raw_by_type=raw, valid_from=valid_from, valid_to=valid_to))
    return counts
