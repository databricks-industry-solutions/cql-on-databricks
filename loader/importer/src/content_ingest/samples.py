"""CONTENT-SAMPLE-PRELOAD — load the sample measures, their value sets and their patients from ``examples/``.

``examples/`` is the one place that pre-loads a workspace with sample measures. The deploy runs this
(``scripts/deploy/provision-samples.py``); it never uses the eCQI download path, and nothing here reads the
compiler's conformance corpus, which is a separate, independent copy.

What it reads, and where each part lands in ``<catalog>``:

- ``examples/measures/<Library>.cql`` — every CQL library, as a FHIR ``Library`` resource (the CQL as a
  ``text/cql`` attachment) in ``sample_measures.library``.
- ``examples/measures/<Library>.measure.json`` — the authored FHIR ``Measure`` resource of each sample measure,
  in ``sample_measures.measure``. A library is a measure in the catalog only when a ``Measure`` references it
  (``CONTENT-MEASURE-LIBRARY-LINK``), so each one names its library's canonical URL.
- ``examples/valuesets/*.ndjson`` — value-set expansion rows ``{valueset_id, system, code, display}``, in
  ``sample_measures.valueset``. The folder may be empty: the table is created either way.
- ``examples/data/<Type>.ndjson`` — the synthetic population small, landed as the data source
  ``synthetic-population-small`` (``DATA-SYNTH-SMALL``, ``population.land``). The folder's recipe is not data.

``sample_measures`` is tagged as both a knowledge and a terminology source (``CONTENT-IMPORT-TARGET-TAG``), so
the Designer catalog and ``cql(library=...)`` find the measures by name.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .population import PopulationSource
from .sources import SYNTHETIC_SMALL_DESCRIPTION, SYNTHETIC_SMALL_SCHEMA, SYNTHETIC_SMALL_SOURCE_ID
from .transform import _CQL_HEADER_RE, LibraryRow, MeasureRow, TransformResult, ValueSetExpansionRow

log = logging.getLogger("content_ingest.samples")

#: The schema the sample knowledge and terminology land in, and the tag values that name them.
SAMPLE_MEASURES_SCHEMA = "sample_measures"
SAMPLE_KNOWLEDGE_NAME = "sample-measures"
SAMPLE_TERMINOLOGY_NAME = "sample-measures"

#: DATA-SYNTH-SMALL — the patients the sample measures run over (`sources`).
SYNTHETIC_SMALL = PopulationSource(source_id=SYNTHETIC_SMALL_SOURCE_ID, schema=SYNTHETIC_SMALL_SCHEMA,
                                   description=SYNTHETIC_SMALL_DESCRIPTION)

#: The canonical base of every sample resource.
CANONICAL_BASE = "http://example.org/fhir"

#: Provenance stamped on every row.
SOURCE_PACKAGE = "examples"
SOURCE_LINEAGE = "sample"


class SampleContentError(ValueError):
    """``examples/`` does not hold a loadable set of sample measures."""


@dataclass(frozen=True)
class SampleContent:
    """Everything ``examples/`` holds, read and checked, before anything is written."""
    knowledge: TransformResult
    patients: dict[str, list[str]]  # FHIR type -> NDJSON lines


def library_resource(cql: str, source: str) -> dict:
    """The FHIR ``Library`` resource for one CQL file: identity from its header, the CQL as an attachment."""
    header = _CQL_HEADER_RE.search(cql)
    if header is None:
        raise SampleContentError(f"{source}: no `library <name> version '<version>'` header")
    name, version = header.group(1).strip('"'), header.group(2)
    if Path(source).stem != name:
        raise SampleContentError(f"{source} declares library {name}; the file must be named {name}.cql")
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


def read_samples(examples: Path) -> SampleContent:
    """Read and check ``examples/``. Fails on anything that would load a measure the catalog cannot run."""
    now = datetime.now(timezone.utc).isoformat()
    measures_dir = examples / "measures"
    cql_files = sorted(measures_dir.glob("*.cql"))
    if not cql_files:
        raise SampleContentError(f"no CQL under {measures_dir}")
    libraries = [library_resource(f.read_text(), str(f)) for f in cql_files]
    by_url = {lib["url"]: lib for lib in libraries}

    measures = []
    for path in sorted(measures_dir.glob("*.measure.json")):
        measure = json.loads(path.read_text())
        if measure.get("resourceType") != "Measure":
            raise SampleContentError(f"{path} is not a FHIR Measure resource")
        refs = measure.get("library") or []
        missing = [ref for ref in refs if ref.split("|", 1)[0] not in by_url]
        if not refs or missing:
            raise SampleContentError(f"{path}: library {missing or refs} is not a CQL file in {measures_dir}")
        measures.append(measure)
    if not measures:
        raise SampleContentError(f"no <Library>.measure.json under {measures_dir}: nothing would be a measure")

    def row(cls, resource: dict):
        return cls(url=resource["url"], version=resource["version"], resource_json=json.dumps(resource),
                   source_package=SOURCE_PACKAGE, source_year=0, source_lineage=SOURCE_LINEAGE,
                   ingested_at=now)

    expansions = []
    for path in sorted((examples / "valuesets").glob("*.ndjson")):
        for line in path.read_text().splitlines():
            if line.strip():
                code = json.loads(line)
                expansions.append(ValueSetExpansionRow(
                    valueset_id=code["valueset_id"], system=code["system"], code=code["code"],
                    display=code.get("display"), version=None, source_url=code["valueset_id"],
                    source_package=SOURCE_PACKAGE, ingested_at=now))

    patients = {path.stem: [line for line in path.read_text().splitlines() if line.strip()]
                for path in sorted((examples / "data").glob("*.ndjson"))}
    if not patients.get("Patient"):
        raise SampleContentError(f"no patients: {examples / 'data' / 'Patient.ndjson'} is missing or empty")

    knowledge = TransformResult(measures=[row(MeasureRow, m) for m in measures],
                                libraries=[row(LibraryRow, lib) for lib in libraries],
                                valueset_expansions=expansions)
    return SampleContent(knowledge=knowledge, patients=patients)


def load_samples(spark, *, catalog: str, examples: Path, valid_from: str, valid_to: str) -> dict[str, int]:
    """Write ``examples/`` into ``catalog`` (see the module docstring) and tag it. Returns row counts."""
    from .load import ensure_valueset_table, load, tag_loaded
    from .population import land

    content = read_samples(examples)
    counts = load(spark, content.knowledge, catalog=catalog, authority_schema=SAMPLE_MEASURES_SCHEMA,
                  valueset_schema=SAMPLE_MEASURES_SCHEMA)
    ensure_valueset_table(spark, catalog=catalog, valueset_schema=SAMPLE_MEASURES_SCHEMA)
    tag_loaded(spark, catalog=catalog, knowledge=(SAMPLE_MEASURES_SCHEMA, SAMPLE_KNOWLEDGE_NAME),
               terminology=(SAMPLE_MEASURES_SCHEMA, SAMPLE_TERMINOLOGY_NAME))
    raw = {fhir_type: spark.createDataFrame([(line,) for line in lines], "value STRING")
           for fhir_type, lines in content.patients.items()}
    counts.update(land(spark, catalog=catalog, source=SYNTHETIC_SMALL, raw_by_type=raw,
                       valid_from=valid_from, valid_to=valid_to))
    return counts
