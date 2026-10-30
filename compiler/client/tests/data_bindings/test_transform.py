"""Unit tests for transform (CONTENT-NO-DERIVED-COLUMNS) — no Spark, no network."""
from __future__ import annotations

import base64
import json

from cql_databricks.data_bindings.measure_import.transform import (
    _decode_content,
    _has_cql_content,
    transform_resources,
)


def _make_measure(url="http://test/Measure/Example", version="1.0"):
    return {
        "resourceType": "Measure",
        "id": "Example",
        "url": url,
        "version": version,
        "name": "Example",
        "title": "Test Measure",
        "status": "active",
        "library": ["http://test/Library/ExampleLogic"],
    }


def _make_library(url="http://test/Library/ExampleLogic", version="1.0",
                  cql="library ExampleLogic version '1.0'"):
    return {
        "resourceType": "Library",
        "id": "ExampleLogic",
        "url": url,
        "version": version,
        "name": "ExampleLogic",
        "status": "active",
        "type": {"coding": [{"code": "logic-library"}]},
        "content": [{"contentType": "text/cql",
                     "data": base64.b64encode(cql.encode()).decode()}],
    }


def test_has_cql_content_true():
    assert _has_cql_content({"content": [{"contentType": "text/cql", "data": "bGlicmFyeQ=="}]})


def test_has_cql_content_false():
    assert not _has_cql_content({"content": [{"contentType": "application/xml", "data": "abc"}]})


def test_has_cql_content_empty():
    assert not _has_cql_content({})


def test_decode_content_roundtrips_base64():
    lib = _make_library(cql="library X version '2.0'")
    assert _decode_content(lib, "text/cql") == "library X version '2.0'"


def test_decode_content_missing_type():
    assert _decode_content(_make_library(), "application/elm+json") is None


# ── The published Lakehouse shape ────────────────────────────────────────────

def test_persists_only_resource_identity_and_provenance():
    """CONTENT-NO-DERIVED-COLUMNS — the row is the resource, its identity, its provenance."""
    result = transform_resources([_make_measure(), _make_library()],
                                 source_package="test-pkg", source_year=2024,
                                 source_lineage="authored")
    assert len(result.measures) == 1
    assert len(result.libraries) == 1

    measure = result.measures[0]
    assert measure.url == "http://test/Measure/Example"
    assert measure.version == "1.0"
    assert measure.source_package == "test-pkg"
    assert measure.source_year == 2024
    assert measure.source_lineage == "authored"
    # No derived columns on the row itself.
    assert not hasattr(measure, "title")
    assert not hasattr(measure, "library_refs")
    assert not hasattr(result.libraries[0], "content_cql")
    assert not hasattr(result.libraries[0], "valueset_refs")


def test_resource_json_is_verbatim():
    """CONTENT-SHARE-FAITHFUL — the payload is the resource as published, base64 and all."""
    measure = _make_measure()
    result = transform_resources([measure, _make_library()], source_package="p",
                                 source_year=2024, source_lineage="authored")
    assert json.loads(result.measures[0].resource_json) == measure

    library = _make_library()
    lib_result = transform_resources([library], source_package="p", source_year=2024,
                                     source_lineage="authored")
    stored = json.loads(lib_result.libraries[0].resource_json)
    assert stored == library
    # The CQL stays base64 inside content[] — not decoded into a column.
    assert stored["content"][0]["data"] == library["content"][0]["data"]


def test_library_version_is_fhir_canonical_not_cql_header():
    """The MERGE key is FHIR canonical identity; the CQL header identity is derived downstream."""
    cql = "library SharedEncounters version '5.0.000'\nusing FHIR version '4.0.1'"
    lib = _make_library(url="http://test/Library/AOE", version="2026.0.0", cql=cql)
    lib["name"] = "SharedEncountersFHIR"
    result = transform_resources([lib], source_package="t", source_year=2026,
                                 source_lineage="dqm")
    assert result.libraries[0].version == "2026.0.0"   # FHIR, not "5.0.000"
    assert result.libraries[0].url == "http://test/Library/AOE"


def test_skips_non_cql_library():
    lib = {"resourceType": "Library", "id": "X", "url": "http://x", "version": "1",
           "name": "X", "status": "active", "type": {"coding": [{"code": "model-definition"}]},
           "content": [{"contentType": "application/xml", "data": "abc"}]}
    result = transform_resources([lib], source_package="t", source_year=2024,
                                 source_lineage="authored")
    assert result.libraries == []


def test_skips_measure_without_library_ref():
    measure = {"resourceType": "Measure", "id": "M", "url": "http://m", "version": "1",
               "name": "M", "title": "M", "status": "active"}
    result = transform_resources([measure], source_package="t", source_year=2024,
                                 source_lineage="authored")
    assert result.measures == []
