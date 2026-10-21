"""CONTENT-SAMPLE-PRELOAD — reading ``examples/`` into the rows the deploy writes. No Spark, no network."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from content_ingest.samples import CANONICAL_BASE, SampleContentError, library_resource, read_samples

EXAMPLES = Path(__file__).resolve().parents[5] / "examples"

CQL = "library Example version '1.2.3'\nusing FHIR version '4.0.1'\ncontext Patient\ndefine \"X\": true\n"


def _examples(tmp_path: Path, *, measure: dict | None = None, patients: str = '{"resourceType":"Patient","id":"p1"}\n') -> Path:
    (tmp_path / "measures").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "measures" / "Example.cql").write_text(CQL)
    if measure is not None:
        (tmp_path / "measures" / "Example.measure.json").write_text(json.dumps(measure))
    (tmp_path / "data" / "Patient.ndjson").write_text(patients)
    return tmp_path


def _measure(*library: str) -> dict:
    return {"resourceType": "Measure", "url": f"{CANONICAL_BASE}/Measure/Example", "version": "1.2.3",
            "library": list(library)}


def test_a_library_resource_carries_the_cql_and_its_header_identity():
    lib = library_resource(CQL, "measures/Example.cql")
    assert (lib["url"], lib["version"], lib["name"]) == (f"{CANONICAL_BASE}/Library/Example", "1.2.3", "Example")
    assert base64.b64decode(lib["content"][0]["data"]).decode() == CQL
    assert lib["content"][0]["contentType"] == "text/cql"


def test_a_file_named_for_another_library_fails():
    with pytest.raises(SampleContentError, match="must be named Example.cql"):
        library_resource(CQL, "measures/Other.cql")


def test_cql_with_no_header_fails():
    with pytest.raises(SampleContentError, match="no `library"):
        library_resource("define X: 1", "measures/Example.cql")


def test_the_shipped_examples_load(tmp_path):
    """The real `examples/`: every CQL file a Library, every sample measure a Measure naming its library."""
    content = read_samples(EXAMPLES)
    libraries = {json.loads(r.resource_json)["name"] for r in content.knowledge.libraries}
    assert {"BasicEncounterAndCondition", "EncounterDuringPeriod"} <= libraries
    measure_libraries = {ref for r in content.knowledge.measures for ref in json.loads(r.resource_json)["library"]}
    assert measure_libraries <= {r.url for r in content.knowledge.libraries}
    assert content.patients["Patient"]


def test_a_measure_naming_a_library_that_is_not_there_fails(tmp_path):
    with pytest.raises(SampleContentError, match="is not a CQL file"):
        read_samples(_examples(tmp_path, measure=_measure(f"{CANONICAL_BASE}/Library/Missing")))


def test_no_measure_resource_fails(tmp_path):
    """Without a Measure the catalog lists no measure at all (CONTENT-MEASURE-LIBRARY-LINK)."""
    with pytest.raises(SampleContentError, match="nothing would be a measure"):
        read_samples(_examples(tmp_path))


def test_no_patients_fails(tmp_path):
    with pytest.raises(SampleContentError, match="no patients"):
        read_samples(_examples(tmp_path, measure=_measure(f"{CANONICAL_BASE}/Library/Example"), patients=""))


def test_a_versioned_library_reference_resolves(tmp_path):
    content = read_samples(_examples(tmp_path, measure=_measure(f"{CANONICAL_BASE}/Library/Example|1.2.3")))
    assert len(content.knowledge.measures) == 1
