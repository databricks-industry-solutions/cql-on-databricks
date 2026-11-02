"""DOC-OPEN-SAVE, DOC-AUTO-SAVE, DOC-EDITABLE-IN-SCOPE — saving a library back to its package folder; a tagged store is
never written, only copied from.

The folder is a dict standing in for the Workspace API; the store is a fake runner answering the row reads and MERGEs
publishing runs (`DOC-PUBLISH`, `CLIENT-PUBLISH`, tested whole in the client). Every fixture is synthetic."""
from __future__ import annotations

import base64
import json

import pytest

from cql_databricks.compiler.libraries import LibraryFiles, glob_match
from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema
from cql_databricks_designer.save import (SaveConflict, SaveRefused, SaveRequest, StaleCopies, cql_sha256,
                                          save_to_folder)

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


def test_a_folder_save_of_cql_that_does_not_compile_drops_the_elm_from_every_json_copy():
    folder = _package()
    save_to_folder(_request(delete_stale=[f"{F}/CMS1-FHIR.xml", f"{F}/CMS1-FHIR.html",
                                          f"{F}/resources/library-Measure-1.0.0.xml"]),
                   F, files=folder, writer=folder, elm=None)
    library = json.loads(folder.files[f"{F}/resources/library-Measure-1.0.0.json"])
    bundled = json.loads(folder.files[f"{F}/CMS1-FHIR.json"])["entry"][0]["resource"]
    assert folder.files[f"{F}/cql/Measure-1.0.0.cql"] == NEW
    assert library["content"] == bundled["content"] == [{"contentType": "text/cql", "data": _b64(NEW)}]


# ── a tagged store: read-only, copied from by Save As ──────────────────────────────────────────────────────────────────────

STORE = TaggedSchema(tag=KNOWLEDGE_TAG, name="measures", catalog="k", schema="measures", position=0)


def _corpus(measure=None):
    return Corpus(lambda *a: [], [STORE], [Library(
        name="Measure", version="1.0.0", url="https://example.org/Library/Measure", fhir_version="1.0.0",
        source=OLD, description="", title="", kind="measure" if measure else "library", model="",
        data_requirements=[], knowledge=STORE, measure=measure)])


# ── the route ─────────────────────────────────────────────────────────────────────────────────────

reloads: list[int] = []


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
    content = SimpleNamespace(corpus=_corpus, knowledge_sources=lambda: [STORE], reload=lambda: reloads.append(1))
    contents = SimpleNamespace(get=lambda name: content)
    app = FastAPI()
    app.include_router(save_routes.router(compiler=compiler, contents=contents, warehouse=lambda: "wh"))
    return TestClient(app)


BODY = {"location": f"folder:{F}", "name": "Measure", "version": "1.0.0", "cql": NEW, "baseSha256": cql_sha256(OLD)}


def test_the_route_writes_cql_that_does_not_compile_and_says_why(monkeypatch):
    # DOC-AUTO-SAVE — every edit is written, whether or not it compiles.
    folder = _package()
    client = _route(monkeypatch, folder, compiles=False)
    asked = client.post("/api/libraries/save", json=BODY)
    response = client.post("/api/libraries/save", json={**BODY, "deleteStale": asked.json()["paths"]})
    assert response.status_code == 200
    assert (response.json()["compiled"], response.json()["diagnostics"]) == (False, ["Syntax error at 2:9"])
    assert folder.files[f"{F}/cql/Measure-1.0.0.cql"] == NEW


def test_the_route_asks_before_deleting_and_saves_once_told(monkeypatch):
    folder = _package()
    client = _route(monkeypatch, folder)
    asked = client.post("/api/libraries/save", json=BODY)
    assert asked.status_code == 409 and asked.json()["kind"] == "stale"
    saved = client.post("/api/libraries/save", json={**BODY, "deleteStale": asked.json()["paths"]})
    assert saved.status_code == 200 and folder.files[f"{F}/cql/Measure-1.0.0.cql"] == NEW
    assert (saved.json()["compiled"], saved.json()["diagnostics"]) == (True, [])
    again = client.post("/api/libraries/save", json=BODY)   # the base is now out of date
    assert again.status_code == 409 and again.json()["kind"] == "conflict"


def test_the_route_never_writes_a_tagged_store(monkeypatch):
    # DOC-EDITABLE-IN-SCOPE — a store is published and read-only in the Designer; New and Save As go to a folder.
    client = _route(monkeypatch, _package())
    saved = client.post("/api/libraries/save", json={**BODY, "location": "store:k.measures"})
    created = client.post("/api/libraries/new", json={"location": "store:k.measures", "name": "Screening",
                                                      "version": "0.1.0", "kind": "library"})
    for response in (saved, created):
        assert response.status_code == 400 and "save a copy to a workspace folder" in response.json()["error"]


def test_save_as_copies_a_library_from_a_store_into_a_folder(monkeypatch):
    monkeypatch.setenv("CQL_CANONICAL_BASE", BASE)
    folder = _package()
    response = _route(monkeypatch, folder).post("/api/libraries/new", json={
        "location": f"folder:{P}", "name": "MyCopy", "version": "0.1.0", "cql": OLD,
        "copyOf": {"location": "store:k.measures", "name": "Measure", "version": "1.0.0"}})
    assert response.status_code == 200, response.json()
    library = json.loads(folder.files[f"{P}/resources/library-MyCopy-0.1.0.json"])
    assert library["relatedArtifact"] == [{"type": "derived-from",
                                           "resource": "https://example.org/Library/Measure|1.0.0"}]


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


# ── Publish ──────────────────────────────────────────────────────────────────────────────────────

#: CLIENT-ENVIRONMENT — the environment the caller can see, in catalog ``k``.
ENVIRONMENT_TAGS = [{"catalog": "k", "tag": "cql_databricks_environment", "value": "k_env", "comment": None},
                    {"catalog": "k", "tag": "cql_databricks_environment_version", "value": "1", "comment": None},
                    {"catalog": "k", "tag": "cql_databricks_environment_default", "value": "true", "comment": None}]


class _PublishStore:
    """The store's rows by (url, version), read and MERGEd as the client's publish does, and the published view over
    them: ``hidden`` is a view that does not select the store."""

    def __init__(self, fail=False, hidden=False):
        self.rows, self.fail, self.hidden, self.merged, self.written = {}, fail, hidden, [], []

    def __call__(self, sql, params=None):
        if "system.information_schema.catalog_tags" in sql:
            return ENVIRONMENT_TAGS
        if sql.startswith("SELECT url, version FROM"):
            # BIND-PUBLISHED-VIEW: the read-back through the environment's knowledge view.
            assert "`k`.`knowledge`.`library`" in sql
            return [] if self.hidden else [{"url": u, "version": v} for u, v in self.rows]
        if sql.startswith("SELECT"):
            held = self.rows.get((params["url"], params["version"]))
            return [{"resource": json.dumps(held)}] if held else []
        if self.fail:
            raise RuntimeError("PERMISSION_DENIED")
        self.written.append(sql)
        resource = json.loads(params["resource_json"])
        self.rows[(resource["url"], resource["version"])] = resource
        self.merged.append(resource["resourceType"])
        return [{"num_affected_rows": 1}]


def _publishing(monkeypatch, store):
    from cql_databricks_designer.routes import save as save_routes
    monkeypatch.setenv("CQL_CANONICAL_BASE", BASE)
    monkeypatch.setattr(save_routes, "user_session", lambda warehouse: store)
    folder = _package()
    folder.files[f"{F}/resources/measure-Measure-1.0.0.json"] = json.dumps({**MEASURE, "url": f"{BASE}/Measure/M"})
    reloads.clear()
    return _route(monkeypatch, folder)


PUBLISH = {"location": f"folder:{F}", "name": "Measure", "version": "1.0.0", "environment": "k_env"}


def test_publish_writes_the_folder_library_into_the_store_as_the_user(monkeypatch):
    # DOC-PUBLISH — through the client's publish (CLIENT-PUBLISH), then the corpus is read again.
    store = _PublishStore()
    response = _publishing(monkeypatch, store).post("/api/libraries/publish", json=PUBLISH)
    assert response.status_code == 200, response.json()
    assert [(lib["name"], lib["outcome"]) for lib in response.json()["libraries"]] == [("Measure", "published")]
    assert store.merged == ["Library", "Measure"] and reloads == [1]
    # DOC-PUBLISH — into the environment's knowledge store, not its published views.
    assert all("`k`.`knowledge_store`." in sql for sql in store.written)
    again = _publishing(monkeypatch, store).post("/api/libraries/publish", json=PUBLISH)
    assert [lib["outcome"] for lib in again.json()["libraries"]] == ["unchanged"]


def test_publish_says_what_was_refused_and_what_failed(monkeypatch):
    client = _publishing(monkeypatch, _PublishStore())
    refused = client.post("/api/libraries/publish", json={**PUBLISH, "environment": "missing"})
    assert refused.status_code == 400 and "no environment named 'missing'" in refused.json()["error"]
    from_store = client.post("/api/libraries/publish", json={**PUBLISH, "location": "store:k.measures"})
    assert from_store.status_code == 400
    failed = _publishing(monkeypatch, _PublishStore(fail=True)).post("/api/libraries/publish", json=PUBLISH)
    assert failed.status_code == 502 and failed.json()["summary"]["libraries"][0]["outcome"] == "refused"
    assert reloads == [1]
    hidden = _publishing(monkeypatch, _PublishStore(hidden=True)).post("/api/libraries/publish", json=PUBLISH)
    assert hidden.status_code == 502 and "does not select the store" in hidden.json()["error"]
