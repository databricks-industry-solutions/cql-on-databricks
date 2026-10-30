"""DOC-OPEN-SAVE — saving a library where it was opened: a package folder, or a tagged store in place.

The folder is a dict standing in for the Workspace API; the store is a fake runner answering the two statements a
store save runs. Every fixture is synthetic."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from cql_databricks.compiler.libraries import LibraryFiles, glob_match
from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema
from cql_databricks_designer.save import (SaveConflict, SaveRefused, SaveRequest, StaleCopies, cql_sha256,
                                          save_to_folder, save_to_store, with_content)

OLD = "library Measure version '1.0.0'\ndefine X: 1\n"
NEW = "library Measure version '1.0.0'\ndefine X: 2\n"
ELM = '{"library": {"identifier": {"id": "Measure"}}}'
F = "/Workspace/Users/a@example.org/CMS1"


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _library(cql):
    return {"resourceType": "Library", "name": "Measure", "version": "1.0.0", "url": "https://example.org/Library/Measure",
            "content": [{"contentType": "text/cql", "data": _b64(cql)},
                        {"contentType": "application/elm+json", "data": _b64("{}")}]}


MEASURE = {"resourceType": "Measure", "version": "1.0.0", "title": "M", "library": ["https://example.org/Library/Measure"]}


class _Folder(LibraryFiles):
    def __init__(self, files):
        self.files = dict(files)
        self.mkdirs_calls, self.deleted = [], []

    # LibraryFiles
    def is_dir(self, path):
        return any(p.startswith(path.rstrip("/") + "/") for p in self.files)

    def glob(self, root, pattern):
        root = root.rstrip("/")
        return sorted(p for p in self.files if p.startswith(root + "/") and glob_match(p[len(root) + 1:], pattern))

    def read_text(self, path):
        return self.files.get(path)

    # FolderWriter
    def write_text(self, path, text):
        self.files[path] = text

    def delete(self, path):
        self.deleted.append(path)
        del self.files[path]

    def mkdirs(self, path):
        self.mkdirs_calls.append(path)


def _package():
    return _Folder({
        f"{F}/cql/Measure-1.0.0.cql": OLD,
        f"{F}/resources/library-Measure-1.0.0.json": json.dumps(_library(OLD)),
        f"{F}/resources/library-Measure-1.0.0.xml": "<Library/>",
        f"{F}/resources/measure-Measure-1.0.0.json": json.dumps(MEASURE),
        f"{F}/resources/measure-Measure-1.0.0.xml": "<Measure/>",
        f"{F}/CMS1-FHIR.json": json.dumps({"resourceType": "Bundle", "entry": [
            {"resource": _library(OLD)}, {"resource": MEASURE}]}),
        f"{F}/CMS1-FHIR.xml": "<Bundle/>",
        f"{F}/CMS1-FHIR.html": "<html/>",
    })


def _request(**changes):
    return SaveRequest(**({"name": "Measure", "version": "1.0.0", "cql": NEW, "base_sha256": cql_sha256(OLD)}
                          | changes))


def test_the_first_save_in_a_package_names_the_copies_it_cannot_regenerate():
    folder = _package()
    with pytest.raises(StaleCopies) as refused:
        save_to_folder(_request(), F, files=folder, writer=folder, elm=ELM)
    assert refused.value.paths == [f"{F}/CMS1-FHIR.html", f"{F}/CMS1-FHIR.xml", f"{F}/resources/library-Measure-1.0.0.xml"]
    assert folder.files[f"{F}/cql/Measure-1.0.0.cql"] == OLD   # nothing written


def test_a_confirmed_save_rewrites_every_json_copy_and_deletes_only_what_was_agreed():
    folder = _package()
    stale = [f"{F}/CMS1-FHIR.html", f"{F}/CMS1-FHIR.xml", f"{F}/resources/library-Measure-1.0.0.xml"]
    out = save_to_folder(_request(delete_stale=stale), F, files=folder, writer=folder, elm=ELM)
    assert folder.files[f"{F}/cql/Measure-1.0.0.cql"] == NEW
    library = json.loads(folder.files[f"{F}/resources/library-Measure-1.0.0.json"])
    assert base64.b64decode(library["content"][0]["data"]).decode() == NEW
    assert base64.b64decode(library["content"][1]["data"]).decode() == ELM
    bundle = json.loads(folder.files[f"{F}/CMS1-FHIR.json"])
    assert base64.b64decode(bundle["entry"][0]["resource"]["content"][0]["data"]).decode() == NEW
    assert out["deleted"] == stale and folder.deleted == stale
    # The Measure was not edited, so neither it nor its XML copy is touched.
    assert f"{F}/resources/measure-Measure-1.0.0.xml" in folder.files
    assert out["sha256"] == cql_sha256(NEW)


def test_an_edited_measure_is_written_to_its_resource_and_the_bundle():
    folder = _package()
    edited = {**MEASURE, "description": "Adults with a reading."}
    stale = [f"{F}/CMS1-FHIR.html", f"{F}/CMS1-FHIR.xml", f"{F}/resources/library-Measure-1.0.0.xml",
             f"{F}/resources/measure-Measure-1.0.0.xml"]
    save_to_folder(_request(measure=edited, delete_stale=stale), F, files=folder, writer=folder, elm=ELM)
    assert json.loads(folder.files[f"{F}/resources/measure-Measure-1.0.0.json"])["description"] == "Adults with a reading."
    assert json.loads(folder.files[f"{F}/CMS1-FHIR.json"])["entry"][1]["resource"]["description"] == "Adults with a reading."


def test_a_library_changed_since_it_was_opened_is_not_saved():
    folder = _package()
    with pytest.raises(SaveConflict):
        save_to_folder(_request(base_sha256=cql_sha256("something else")), F, files=folder, writer=folder, elm=ELM)
    with pytest.raises(SaveConflict):   # opened as a new library, but one is already there
        save_to_folder(_request(base_sha256=None), F, files=folder, writer=folder, elm=ELM)


def test_a_new_library_goes_where_a_package_keeps_its_cql():
    folder = _Folder({})
    out = save_to_folder(_request(base_sha256=None), F, files=folder, writer=folder, elm=ELM)
    assert out["saved"] == [f"{F}/cql/Measure-1.0.0.cql"]
    assert folder.mkdirs_calls == [f"{F}/cql"] and folder.files[f"{F}/cql/Measure-1.0.0.cql"] == NEW


def test_with_content_changes_only_the_cql_and_elm():
    resource = {**_library(OLD), "description": "kept"}
    updated = with_content(resource, NEW, ELM)
    assert updated["description"] == "kept" and resource["content"][0]["data"] == _b64(OLD)
    assert with_content({"resourceType": "Library"}, NEW, ELM)["content"] == [
        {"contentType": "text/cql", "data": _b64(NEW)}]


# ── a tagged store, in place ──────────────────────────────────────────────────────────────────────

STORE = TaggedSchema(tag=KNOWLEDGE_TAG, name="measures", catalog="k", schema="measures", position=0)


def _corpus(measure=None):
    return Corpus(lambda *a: [], [STORE], [Library(
        name="Measure", version="1.0.0", url="https://example.org/Library/Measure", fhir_version="1.0.0",
        source=OLD, description="", title="", kind="measure" if measure else "library", model="",
        data_requirements=[], knowledge=STORE, measure=measure)])


class _Store:
    """Answers the read and the guarded MERGE a store save runs."""

    def __init__(self, stored, updated=1):
        self.stored, self.updated, self.statements = stored, updated, []

    def __call__(self, sql, params=None):
        self.statements.append((sql, params or {}))
        if sql.startswith("SELECT to_json(resource)"):
            return [{"resource": self.stored, "source_package": "pkg", "source_year": 2026}]
        return [{"num_affected_rows": self.updated, "num_updated_rows": self.updated, "num_inserted_rows": 0}]


def test_a_store_save_replaces_the_row_only_while_it_is_unchanged():
    stored = json.dumps(_library(OLD))
    run = _Store(stored)
    out = save_to_store(_request(), ("k", "measures"), corpus=_corpus(), run=run, elm=ELM)
    merge, params = run.statements[1]
    assert "MERGE INTO `k`.`measures`.`library`" in merge and ":resource_sha256" in merge
    assert params["resource_sha256"] == hashlib.sha256(stored.encode()).hexdigest()
    written = json.loads(params["resource_json"])
    assert base64.b64decode(written["content"][0]["data"]).decode() == NEW
    assert (params["source_package"], params["source_lineage"]) == ("pkg", "designer")
    assert out["location"] == "store:k.measures"


def test_a_store_row_changed_by_someone_else_is_not_overwritten():
    with pytest.raises(SaveConflict):   # the CQL in the row is not what was opened
        save_to_store(_request(), ("k", "measures"), corpus=_corpus(), run=_Store(json.dumps(_library(NEW))), elm=ELM)
    with pytest.raises(SaveConflict):   # changed between the read and the MERGE
        save_to_store(_request(), ("k", "measures"), corpus=_corpus(), run=_Store(json.dumps(_library(OLD)), 0), elm=ELM)


def test_a_store_save_never_creates_a_library():
    with pytest.raises(SaveRefused, match="save a new library to a folder"):
        save_to_store(_request(name="Other"), ("k", "measures"), corpus=_corpus(), run=_Store("{}"), elm=ELM)


# ── the route ─────────────────────────────────────────────────────────────────────────────────────

def _route(monkeypatch, folder, *, compiles=True):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cql_databricks_designer.routes import save as save_routes
    monkeypatch.setattr(save_routes, "files_for", lambda location: folder)
    monkeypatch.setattr(save_routes, "user_client", lambda: None)
    monkeypatch.setattr(save_routes, "WorkspaceFolderWriter", lambda client: folder)
    compiler = SimpleNamespace(translate=lambda cql, includes: {
        "ok": compiles, "elm": ELM if compiles else None, "diagnostics": [] if compiles else ["Syntax error at 2:9"]})
    content = SimpleNamespace(corpus=_corpus)
    app = FastAPI()
    app.include_router(save_routes.router(compiler=compiler, content=content, warehouse=lambda: "wh"))
    return TestClient(app)


BODY = {"location": f"folder:{F}", "name": "Measure", "version": "1.0.0", "cql": NEW, "baseSha256": cql_sha256(OLD)}


def test_the_route_refuses_cql_that_does_not_compile(monkeypatch):
    folder = _package()
    response = _route(monkeypatch, folder, compiles=False).post("/api/libraries/save", json=BODY)
    assert response.status_code == 422 and response.json()["diagnostics"] == ["Syntax error at 2:9"]
    assert folder.files[f"{F}/cql/Measure-1.0.0.cql"] == OLD


def test_the_route_asks_before_deleting_and_saves_once_told(monkeypatch):
    folder = _package()
    client = _route(monkeypatch, folder)
    asked = client.post("/api/libraries/save", json=BODY)
    assert asked.status_code == 409 and asked.json()["kind"] == "stale"
    saved = client.post("/api/libraries/save", json={**BODY, "deleteStale": asked.json()["paths"]})
    assert saved.status_code == 200 and folder.files[f"{F}/cql/Measure-1.0.0.cql"] == NEW
    again = client.post("/api/libraries/save", json=BODY)   # the base is now out of date
    assert again.status_code == 409 and again.json()["kind"] == "conflict"


def test_the_route_refuses_cql_that_names_another_library(monkeypatch):
    response = _route(monkeypatch, _package()).post("/api/libraries/save", json={**BODY, "name": "Other"})
    assert response.status_code == 400 and "save it as a new library" in response.json()["error"]


# ── New and Save As ───────────────────────────────────────────────────────────────────────────────

BASE = "https://measures.example.org/fhir"
P = "/Workspace/Users/a@example.org/Screening"


def _new(**changes):
    from cql_databricks_designer.save import NewLibrary, starting_cql, starting_measure
    fields = {"name": "Screening", "version": "0.1.0",
              "cql": starting_cql("Screening", "0.1.0", measure=True),
              "measure": starting_measure("Screening", "0.1.0", base=BASE)}
    return NewLibrary(**(fields | changes))


def test_a_new_measure_starts_with_three_populations_its_measure_names():
    from cql_databricks_designer.save import starting_cql, starting_measure
    cql = starting_cql("Screening", "0.1.0", measure=True)
    assert cql.startswith("library Screening version '0.1.0'\n")
    assert "using QICore version '6.0.0'" in cql and "include FHIRHelpers version '4.0.1'" in cql
    measure = starting_measure("Screening", "0.1.0", base=BASE)
    named = [p["criteria"]["expression"] for p in measure["group"][0]["population"]]
    assert named == ["Initial Population", "Denominator", "Numerator"]
    assert all(f'define "{n}":\n  true' in cql for n in named)
    assert measure["url"] == f"{BASE}/Measure/Screening" and measure["status"] == "draft"
    assert measure["library"] == [f"{BASE}/Library/Screening|0.1.0"]
    assert "define" not in starting_cql("Helpers", "0.1.0", measure=False)


def test_a_new_measure_in_a_folder_is_laid_out_as_a_package():
    from cql_databricks_designer.save import create_in_folder
    folder = _Folder({})
    out = create_in_folder(_new(), P, files=folder, writer=folder, elm=ELM, base=BASE)
    assert out["saved"] == [f"{P}/cql/Screening-0.1.0.cql", f"{P}/resources/library-Screening-0.1.0.json",
                            f"{P}/resources/measure-Screening-0.1.0.json"]
    library = json.loads(folder.files[f"{P}/resources/library-Screening-0.1.0.json"])
    assert library["url"] == f"{BASE}/Library/Screening" and library["version"] == "0.1.0"
    assert base64.b64decode(library["content"][1]["data"]).decode() == ELM
    assert folder.mkdirs_calls == [f"{P}/cql", f"{P}/resources"]


def test_a_new_library_is_never_written_over_one_that_is_there():
    from cql_databricks_designer.save import create_in_folder
    folder = _Folder({f"{P}/resources/measure-Screening-0.1.0.json": "{}"})
    with pytest.raises(SaveConflict, match="already exists"):
        create_in_folder(_new(), P, files=folder, writer=folder, elm=ELM, base=BASE)
    assert folder.files == {f"{P}/resources/measure-Screening-0.1.0.json": "{}"}
    package = _package()   # Measure 1.0.0, flat under cql/
    with pytest.raises(SaveConflict, match="already in"):
        create_in_folder(_new(name="Measure", version="1.0.0", cql=OLD, measure=None), F, files=package,
                         writer=package, elm=ELM, base=BASE)


def test_a_copy_is_a_draft_under_the_deployment_base_linked_to_its_original():
    from cql_databricks_designer.save import measure_copy
    original = {**MEASURE, "url": "https://publisher.example.org/Measure/CMS1", "name": "CMS1",
                "identifier": [{"value": "CMS1"}], "text": {"div": "<div/>"}, "status": "active"}
    copied = measure_copy(original, "MyScreening", "0.1.0", base=BASE)
    assert (copied["url"], copied["name"], copied["version"], copied["status"]) == (
        f"{BASE}/Measure/MyScreening", "MyScreening", "0.1.0", "draft")
    assert copied["library"] == [f"{BASE}/Library/MyScreening|0.1.0"] and copied["title"] == "M"
    assert copied["relatedArtifact"] == [{"type": "derived-from",
                                          "resource": "https://publisher.example.org/Measure/CMS1|1.0.0"}]
    assert "identifier" not in copied and "text" not in copied and original["status"] == "active"


def test_a_copy_renames_only_the_library_header():
    from cql_databricks.compiler.libraries import with_identity
    cql = "/* library Not This version '9' */\nlibrary \"Measure\" version '1.0.0'\n\ndefine X: 1 // library Y\n"
    assert with_identity(cql, "Copy", "0.1.0") == (
        "/* library Not This version '9' */\nlibrary Copy version '0.1.0'\n\ndefine X: 1 // library Y\n")
    with pytest.raises(ValueError):
        with_identity("define X: 1\n", "Copy", "0.1.0")


def test_a_name_that_cannot_be_a_file_or_a_url_is_refused():
    from cql_databricks_designer.save import check_identity
    check_identity("My_Measure2", "0.1.0-draft")
    for name, version in (("2Measure", "1"), ("My Measure", "1"), ("../x", "1"), ("M", "1/2"), ("M", "")):
        with pytest.raises(SaveRefused):
            check_identity(name, version)


class _NewStore:
    """Answers the Measure key check and the insert-only MERGEs a store create runs."""

    def __init__(self, *, measure_held=False, inserted=1):
        self.measure_held, self.inserted, self.statements = measure_held, inserted, []

    def __call__(self, sql, params=None):
        self.statements.append((sql, params or {}))
        if sql.startswith("SELECT 1"):
            return [{"held": 1}] if self.measure_held else []
        return [{"num_affected_rows": self.inserted, "num_updated_rows": 0, "num_inserted_rows": self.inserted}]


def test_a_new_measure_in_a_store_inserts_its_library_and_measure_rows():
    from cql_databricks_designer.save import create_in_store
    run = _NewStore()
    out = create_in_store(_new(), ("k", "measures"), corpus=_corpus(), run=run, elm=ELM, base=BASE)
    merges = [(sql, params) for sql, params in run.statements if "MERGE" in sql]
    assert [sql.split("AS target")[0].split("INTO")[1].strip() for sql, _ in merges] == [
        "`k`.`measures`.`library`", "`k`.`measures`.`measure`"]
    assert all("WHEN MATCHED" not in sql for sql, _ in merges)
    assert json.loads(merges[0][1]["resource_json"])["url"] == f"{BASE}/Library/Screening"
    assert merges[0][1]["source_lineage"] == "designer"
    assert out["location"] == "store:k.measures" and len(out["saved"]) == 2


def test_a_store_create_writes_nothing_over_what_is_there():
    from cql_databricks_designer.save import create_in_store
    with pytest.raises(SaveConflict, match="already in"):   # the store holds the library by name and version
        create_in_store(_new(name="Measure", version="1.0.0", cql=OLD), ("k", "measures"), corpus=_corpus(),
                        run=_NewStore(), elm=ELM, base=BASE)
    run = _NewStore(measure_held=True)
    with pytest.raises(SaveConflict):   # the Measure key is taken: nothing is written
        create_in_store(_new(), ("k", "measures"), corpus=_corpus(), run=run, elm=ELM, base=BASE)
    assert not any("MERGE" in sql for sql, _ in run.statements)
    with pytest.raises(SaveConflict):   # the Library (url, version) is taken: the insert-only MERGE wrote nothing
        create_in_store(_new(), ("k", "measures"), corpus=_corpus(), run=_NewStore(inserted=0), elm=ELM, base=BASE)
    with pytest.raises(SaveRefused, match="not a tagged knowledge store"):
        create_in_store(_new(), ("k", "other"), corpus=_corpus(), run=_NewStore(), elm=ELM, base=BASE)


NEW_BODY = {"location": f"folder:{P}", "name": "Screening", "version": "0.1.0"}


def test_the_route_creates_a_new_measure_under_the_configured_base(monkeypatch):
    monkeypatch.setenv("CQL_CANONICAL_BASE", BASE)
    # The starting CQL includes FHIRHelpers, which resolves from the folder first.
    folder = _Folder({f"{P}/cql/FHIRHelpers-4.0.1.cql": "library FHIRHelpers version '4.0.1'\n"})
    client = _route(monkeypatch, folder)
    response = client.post("/api/libraries/new", json=NEW_BODY)
    assert response.status_code == 200, response.json()
    measure = json.loads(folder.files[f"{P}/resources/measure-Screening-0.1.0.json"])
    assert measure["url"] == f"{BASE}/Measure/Screening"
    again = client.post("/api/libraries/new", json=NEW_BODY)
    assert again.status_code == 409 and again.json()["kind"] == "conflict"


def test_the_route_saves_a_copy_with_the_original_measure(monkeypatch):
    monkeypatch.setenv("CQL_CANONICAL_BASE", BASE)
    folder = _package()
    client = _route(monkeypatch, folder)
    response = client.post("/api/libraries/new", json={
        "location": f"folder:{P}", "name": "MyCopy", "version": "0.1.0", "cql": NEW,
        "copyOf": {"location": f"folder:{F}", "name": "Measure", "version": "1.0.0"}})
    assert response.status_code == 200, response.json()
    assert folder.files[f"{P}/cql/MyCopy-0.1.0.cql"] == "library MyCopy version '0.1.0'\ndefine X: 2\n"
    copied = json.loads(folder.files[f"{P}/resources/measure-MyCopy-0.1.0.json"])
    assert copied["title"] == "M" and copied["name"] == "MyCopy"
    library = json.loads(folder.files[f"{P}/resources/library-MyCopy-0.1.0.json"])
    assert library["relatedArtifact"] == [{"type": "derived-from",
                                           "resource": "https://example.org/Library/Measure|1.0.0"}]
    assert folder.files[f"{F}/cql/Measure-1.0.0.cql"] == OLD   # the original is untouched


def test_the_route_refuses_a_new_library_with_no_base(monkeypatch):
    monkeypatch.delenv("CQL_CANONICAL_BASE", raising=False)
    with pytest.raises(RuntimeError, match="canonical_base"):
        _route(monkeypatch, _Folder({})).post("/api/libraries/new", json=NEW_BODY)
