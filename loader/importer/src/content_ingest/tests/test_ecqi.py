"""CONTENT-ECQI-TERMS-GATE, CONTENT-ECQI-USER-PACKAGE, CONTENT-ECQI-LOAD-UNCHANGED — the eCQI import's gate and package reading. No
network: a test package is a zip built here, with made-up content."""
from __future__ import annotations

import json
import zipfile

import pytest

from content_ingest import ecqi
from content_ingest.ecqi import (ACCEPTANCE, ECQI_PACKAGE_BASE, PackageError, TermsNotAccepted, acquire,
                                 copyright_notice, extract_fhir_resources, package_url, valueset_urls)

MEASURE = {"resourceType": "Measure", "url": "http://example.org/Measure/M", "version": "1.0.0",
           "title": "Example Measure", "copyright": "Example steward. Line one.\nLine two, verbatim.",
           "library": ["http://example.org/Library/M"]}
LIBRARY = {"resourceType": "Library", "url": "http://example.org/Library/M", "version": "1.0.0",
           "content": [{"contentType": "text/cql", "data": "bGlicmFyeSBNCg=="}],
           "dataRequirement": [{"type": "Encounter", "codeFilter": [
               {"path": "type", "valueSet": "http://example.org/ValueSet/visits|2"}]}],
           "relatedArtifact": [{"type": "depends-on", "resource": "http://example.org/ValueSet/other"},
                               {"type": "depends-on", "resource": "http://example.org/Library/Helpers|1"}]}


def _package(tmp_path, *resources) -> str:
    path = tmp_path / "Example-v1.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i, r in enumerate(resources):
            zf.writestr(f"resources/r{i}.json", json.dumps(r))
            zf.writestr(f"resources/r{i}.xml", "<ignored/>")
        zf.writestr("cql/M-1.0.0.cql", "library M version '1.0.0'")
    return str(path)


class _Prompt:
    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.asked = 0

    def __call__(self, _question: str) -> str:
        self.asked += 1
        return self.answers.pop(0)


def test_the_package_is_read_as_shipped(tmp_path):
    resources = extract_fhir_resources(_package(tmp_path, MEASURE, LIBRARY))
    assert resources == [MEASURE, LIBRARY]


def test_a_zip_with_no_measure_is_not_a_measure_package(tmp_path):
    with pytest.raises(PackageError, match="no Measure"):
        extract_fhir_resources(_package(tmp_path, LIBRARY))


def test_a_file_that_is_not_a_zip_fails(tmp_path):
    path = tmp_path / "x.zip"
    path.write_text("not a zip")
    with pytest.raises(PackageError, match="not a zip"):
        extract_fhir_resources(str(path))


def test_the_copyright_is_shown_verbatim():
    notice = copyright_notice([MEASURE, LIBRARY])
    assert MEASURE["copyright"] in notice and "Example Measure" in notice


def test_a_measure_with_no_copyright_says_so():
    assert "states no copyright" in copyright_notice([{k: v for k, v in MEASURE.items() if k != "copyright"}])


def test_the_value_sets_come_from_the_libraries_as_shipped():
    assert valueset_urls([MEASURE, LIBRARY]) == ["http://example.org/ValueSet/other", "http://example.org/ValueSet/visits"]


def test_a_package_name_becomes_its_ecqi_url():
    assert package_url("Example-v1") == f"{ECQI_PACKAGE_BASE}/Example-v1.zip"
    assert package_url("Example-v1.zip") == f"{ECQI_PACKAGE_BASE}/Example-v1.zip"


def test_a_path_that_does_not_exist_is_not_taken_for_a_package_name():
    with pytest.raises(PackageError, match="neither"):
        package_url("some/dir/missing.zip")


def test_two_acceptances_load_the_package(tmp_path):
    shown: list[str] = []
    prompt = _Prompt(ACCEPTANCE, f"  {ACCEPTANCE} ")
    path, resources = acquire(_package(tmp_path, MEASURE, LIBRARY), accept_terms=False, cache_dir=str(tmp_path),
                              prompt=prompt, out=shown.append)
    assert prompt.asked == 2 and resources == [MEASURE, LIBRARY]
    assert ecqi.TERMS in shown[0] and ecqi.TERMS_STATUS in shown[0]
    assert MEASURE["copyright"] in shown[1]


def test_refusing_the_terms_stops_before_any_download(tmp_path, monkeypatch):
    monkeypatch.setattr(ecqi, "download", lambda *a: pytest.fail("downloaded before the terms were accepted"))
    with pytest.raises(TermsNotAccepted):
        acquire("Example-v1", accept_terms=False, cache_dir=str(tmp_path), prompt=_Prompt("yes"), out=lambda _: None)


def test_refusing_the_copyright_stops_before_loading(tmp_path):
    with pytest.raises(TermsNotAccepted):
        acquire(_package(tmp_path, MEASURE, LIBRARY), accept_terms=False, cache_dir=str(tmp_path),
                prompt=_Prompt(ACCEPTANCE, "i agree"), out=lambda _: None)


def test_accept_terms_still_shows_both_texts(tmp_path):
    shown: list[str] = []
    acquire(_package(tmp_path, MEASURE, LIBRARY), accept_terms=True, cache_dir=str(tmp_path),
            prompt=lambda _: pytest.fail("prompted despite --accept-terms"), out=shown.append)
    assert any(ecqi.TERMS in s for s in shown) and any(MEASURE["copyright"] in s for s in shown)


def test_a_named_package_is_downloaded_from_ecqi_after_acceptance(tmp_path, monkeypatch):
    local = _package(tmp_path, MEASURE, LIBRARY)
    fetched: list[str] = []
    monkeypatch.setattr(ecqi, "download", lambda url, dest: fetched.append(url) or local)
    acquire("Example-v1", accept_terms=True, cache_dir=str(tmp_path), out=lambda _: None)
    assert fetched == [f"{ECQI_PACKAGE_BASE}/Example-v1.zip"]
