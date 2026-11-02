"""SPEC-EXPLAIN-INPUTS — the client's reference-set fixtures are the EXTRACTOR's output, still.

The browser stopped deriving "what is a read" and now consumes what `reference_set.py` derives
(`APP-SPEC-MODEL`: one determination, not two). Its tests therefore run against reference-set
fixtures rather than raw ELM — and a fixture is only worth anything if it is what the real extractor
produces. Hand-maintained, it would drift, and the drift would be invisible: the TypeScript tests would
keep passing against a shape the server no longer emits, which is precisely the two-implementations
failure this test tree exists to prevent.

So this regenerates each fixture from the ELM committed beside it and asserts equality. It fails the
moment the extractor's output changes, and the fix is to regenerate rather than to edit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cql_databricks_designer.explain.reference_set import build_reference_set

def _repo_root() -> Path:
    """Anchored on a marker, not a parent count. These paths moved twice in the restructure."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "settings.gradle").is_file():
            return candidate
    raise RuntimeError("repository root not found")


#: The SPA fixtures. They live with the Designer's SPA, one directory above this component.
_FIXTURES = _repo_root() / "designer" / "src" / "measures" / "__fixtures__"

#: Each ELM fixture beside the reference-set fixture derived from it, one entry per library key.
_PAIRS = [("spec-model-elm.json", "spec-model-refset.json"), ("spec-patterns-elm.json", "spec-patterns-refset.json")]


@pytest.mark.parametrize("elm_name, refset_name", _PAIRS)
def test_reference_set_fixtures_match_the_extractor(elm_name, refset_name):
    """One reference set per entry, keyed as the ELM fixture is."""
    elm_by_key = json.loads((_FIXTURES / elm_name).read_text())
    committed = json.loads((_FIXTURES / refset_name).read_text())
    assert set(elm_by_key) == set(committed), "a library gained or lost a reference set"
    for key, entry in elm_by_key.items():
        assert build_reference_set(json.loads(entry["elm"])).to_dict() == committed[key], key


@pytest.mark.parametrize("name", [refset for _, refset in _PAIRS])
def test_the_fixtures_are_actually_present(name):
    """A missing fixture would make the TypeScript suite import `undefined` and assert nothing."""
    assert (_FIXTURES / name).is_file()


# ── the wire: reference sets ride back with the ELM they were derived from ───────────────────────


def test_a_direct_reference_code_filter_is_not_reported_as_a_value_set():
    """They are not interchangeable and collapsing them was a real defect: a value set is an EXTERNAL
    list expanded from terminology, a direct reference code is ONE concept stated inline, and they live
    in different Appendix tables. Reporting a code as a value set sent a reader to look up an OID that
    does not exist (`SPEC-TERMINOLOGY-REF`)."""
    elm = {"library": {
        "identifier": {"id": "L", "version": "1"},
        "statements": {"def": [{
            "name": "Coded", "locator": "10:1-11:9",
            "expression": {"type": "Retrieve", "locator": "11:1-11:9",
                           "dataType": "{http://hl7.org/fhir}Condition",
                           "codeProperty": "code",
                           "codes": {"type": "ToList", "operand": {"type": "CodeRef", "name": "Flu"}}},
        }]}}}
    read = build_reference_set(elm).defines[0].reads[0]
    assert read.code == "Flu"
    assert read.value_set is None
    assert read.to_dict()["code"] == "Flu" and "valueSet" not in read.to_dict()


def test_terminology_is_attributed_to_the_library_that_declares_it():
    """`Helpers."Some Encounter"` carries a LOCAL ALIAS, not a library name, so it resolves through
    the ELM's own include table. Without this the generator cannot select a `library=` for
    `SPEC-TERMINOLOGY-REF`, and unqualified is dropped rather than guessed."""
    elm = {"library": {
        "identifier": {"id": "Root", "version": "1"},
        "includes": {"def": [{"localIdentifier": "HelpersAlias", "path": "http://x/Helpers"}]},
        "statements": {"def": [{
            "name": "D", "locator": "10:1-11:9",
            "expression": {"type": "And", "locator": "11:1-11:9", "operand": [
                {"type": "ValueSetRef", "name": "Own VS", "locator": "11:1-11:5"},
                {"type": "ValueSetRef", "name": "Their VS", "libraryName": "HelpersAlias",
                 "locator": "11:6-11:9"},
            ]},
        }]}}}
    got = {(v.library, v.name) for v in build_reference_set(elm).defines[0].value_sets}
    assert got == {("Root", "Own VS"), ("Helpers", "Their VS")}
