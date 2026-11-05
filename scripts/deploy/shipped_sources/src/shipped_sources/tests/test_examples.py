"""CONTENT-EXAMPLE-PRELOAD — reading ``examples/`` into the rows the deploy writes. No Spark, no network."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from shipped_sources.examples import CANONICAL_BASE, ExampleContentError, library_resource, read_examples

EXAMPLES = Path(__file__).resolve().parents[6] / "examples"

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
    with pytest.raises(ExampleContentError, match="must be named Example.cql"):
        library_resource(CQL, "measures/Other.cql")


def test_cql_with_no_header_fails():
    with pytest.raises(ExampleContentError, match="no `library"):
        library_resource("define X: 1", "measures/Example.cql")


def test_the_shipped_examples_load(tmp_path):
    """The real `examples/`: every CQL file a Library, every example measure a Measure naming its library."""
    content = read_examples(EXAMPLES)
    libraries = [r for r in content.resources if r["resourceType"] == "Library"]
    assert {"HypertensionBPControl", "MeasureCommon", "FHIRHelpers"} <= {r["name"] for r in libraries}
    measure_libraries = {ref for r in content.resources if r["resourceType"] == "Measure" for ref in r["library"]}
    assert measure_libraries <= {r["url"] for r in libraries}
    assert content.codes
    assert content.patients["Patient"]


def test_a_measure_naming_a_library_that_is_not_there_fails(tmp_path):
    with pytest.raises(ExampleContentError, match="is not a CQL file"):
        read_examples(_examples(tmp_path, measure=_measure(f"{CANONICAL_BASE}/Library/Missing")))


def test_no_measure_resource_fails(tmp_path):
    """Without a Measure the catalog lists no measure at all (CONTENT-MEASURE-LIBRARY-LINK)."""
    with pytest.raises(ExampleContentError, match="nothing would be a measure"):
        read_examples(_examples(tmp_path))


def test_no_patients_fails(tmp_path):
    with pytest.raises(ExampleContentError, match="no patients"):
        read_examples(_examples(tmp_path, measure=_measure(f"{CANONICAL_BASE}/Library/Example"), patients=""))


def test_a_versioned_library_reference_resolves(tmp_path):
    content = read_examples(_examples(tmp_path, measure=_measure(f"{CANONICAL_BASE}/Library/Example|1.2.3")))
    assert sum(r["resourceType"] == "Measure" for r in content.resources) == 1


# ── The environment's content as files (DATA-ENVIRONMENT-LAYOUT, DEPLOY-DEFAULT-ENVIRONMENT) ─────────────────────────

def test_the_volumes_hold_the_same_examples_as_the_tables():
    """DEPLOY-DEFAULT-ENVIRONMENT: the patients, every library and every value set, from the same examples/."""
    from shipped_sources.examples import volume_files

    files = volume_files(EXAMPLES)
    assert sorted(files) == ["data", "knowledge"]
    assert sorted(files["data"]) == sorted(f"fhir/{p.name}" for p in (EXAMPLES / "data").glob("*.ndjson"))
    assert files["data"]["fhir/Patient.ndjson"] == (EXAMPLES / "data" / "Patient.ndjson").read_bytes()
    libraries = sorted(p for p in files["knowledge"] if p.startswith("libraries/"))
    assert libraries == sorted(f"libraries/{p.name}" for p in (EXAMPLES / "measures").glob("*.cql"))
    value_sets = sorted(p for p in files["knowledge"] if p.startswith("valuesets/"))
    assert value_sets == sorted(f"valuesets/{p.stem}.json" for p in (EXAMPLES / "valuesets").glob("*.ndjson"))


def test_a_value_set_file_is_a_fhir_value_set_with_its_expansion():
    """CLIENT-TERMINOLOGY-RESOLVER: the engine expands nothing, so the codes are the expansion; a folder resolver
    reads it back to the same codes as the table rows."""
    from cql_databricks.resolvers.terminology import parse_value_set
    from shipped_sources.examples import volume_files

    source = EXAMPLES / "valuesets" / "colonoscopy.ndjson"
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    resource = json.loads(volume_files(EXAMPLES)["knowledge"]["valuesets/colonoscopy.json"])
    assert resource["resourceType"] == "ValueSet" and resource["url"] == rows[0]["valueset_id"]
    parsed = parse_value_set(resource, "colonoscopy.json")
    assert sorted((c.system, c.code) for c in parsed.codes) == sorted((r["system"], r["code"]) for r in rows)


def test_a_value_set_file_holding_two_value_sets_fails():
    from shipped_sources.examples import value_set_resource

    rows = [{"valueset_id": "urn:a", "system": "s", "code": "1"}, {"valueset_id": "urn:b", "system": "s", "code": "2"}]
    with pytest.raises(ExampleContentError, match="2 value sets"):
        value_set_resource("mixed", rows, "mixed.ndjson")


class _Files:
    """The SDK's Files API over a dict of paths."""

    def __init__(self, held=()):
        self.held = {path: b"old" for path in held}

    def upload(self, path, body, overwrite):
        assert overwrite
        self.held[path] = body.read()

    def delete(self, path):
        del self.held[path]

    def list_directory_contents(self, folder):
        from types import SimpleNamespace
        children = {p[len(folder) + 1:].split("/", 1)[0] for p in self.held if p.startswith(folder + "/")}
        return [SimpleNamespace(path=f"{folder}/{c}", is_directory=f"{folder}/{c}" not in self.held)
                for c in sorted(children)]


def test_a_volume_folder_ends_holding_exactly_the_files_given():
    """DEPLOY-DEFAULT-ENVIRONMENT: every file written over what was there, and any other file removed, at any depth."""
    from shipped_sources.examples import sync_volume

    root = "/Volumes/c/knowledge/files"
    files = _Files([f"{root}/valuesets/gone.json", f"{root}/valuesets/kept.json", f"{root}/old/deep/x.cql"])
    done = sync_volume(files, root, {"valuesets/kept.json": b"new", "libraries/A.cql": b"library A"})
    assert files.held == {f"{root}/valuesets/kept.json": b"new", f"{root}/libraries/A.cql": b"library A"}
    assert done == {"written": 2, "deleted": 2}


def test_the_deploy_writes_both_volumes_of_the_catalog():
    from shipped_sources.examples import publish_example_files

    files = _Files()
    counts = publish_example_files(files, catalog="c", examples=EXAMPLES)
    assert {p.split("/")[3] for p in files.held} == {"data", "knowledge"}
    assert all(p.startswith(("/Volumes/c/data/files/fhir/", "/Volumes/c/knowledge/files/valuesets/",
                             "/Volumes/c/knowledge/files/libraries/")) for p in files.held)
    assert counts == {"data_files": 6, "knowledge_files": len(files.held) - 6}
