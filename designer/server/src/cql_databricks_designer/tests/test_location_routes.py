"""RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — the Designer reads a library from a tagged store or a workspace folder.

The folder is held in memory, standing in for the Workspace API the App reads it through as the signed-in user."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks.compiler.libraries import LibraryFiles, glob_match
from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema
from cql_databricks_designer import assistant_tools
from cql_databricks_designer.routes import designer, knowledge

MEASURE = "library Measure version '1.0.0'\ndefine X: 1\n"
FOLDER = "/Workspace/Users/a@example.org/CMS1"


class _Files(LibraryFiles):
    def __init__(self, files):
        self.files = files

    def is_dir(self, path):
        return any(p.startswith(path.rstrip("/") + "/") for p in self.files)

    def glob(self, root, pattern):
        root = root.rstrip("/")
        return sorted(p for p in self.files if p.startswith(root + "/") and glob_match(p[len(root) + 1:], pattern))

    def read_text(self, path):
        return self.files.get(path)


FILES = _Files({
    f"{FOLDER}/cql/Measure-1.0.0.cql": MEASURE,
    f"{FOLDER}/resources/measure-Measure-1.0.0.json": json.dumps({
        "resourceType": "Measure", "title": "Folder measure", "version": "1.0.0",
        "description": "Adults with a reading.", "publisher": "Example Steward",
        "url": "https://example.org/Measure/Measure", "library": ["https://example.org/Library/Measure"]}),
    f"{FOLDER}/resources/library-Measure-1.0.0.json": json.dumps({
        "resourceType": "Library", "name": "Measure", "version": "1.0.0",
        "dataRequirement": [{"type": "Observation"}]}),
})


def _store(schema, position):
    return TaggedSchema(tag=KNOWLEDGE_TAG, name=schema, catalog="k", schema=schema, position=position)


class _Content:
    def __init__(self):
        self.stores = [_store("first", 0), _store("second", 1)]
        self._corpus = Corpus(lambda *a: [], self.stores, [
            Library(name="Measure", version="1.0.0", url="http://x/Library/Measure", fhir_version="1.0.0",
                    source=MEASURE + "// first\n", description="", title="", kind="library", model="",
                    data_requirements=[], knowledge=self.stores[0]),
            Library(name="Other", version="2.0.0", url="http://x/Library/Other", fhir_version="2.0.0",
                    source="library Other version '2.0.0'\n", description="", title="", kind="library", model="",
                    data_requirements=[{"type": "Patient"}], knowledge=self.stores[1]),
        ])

    def corpus(self):
        return self._corpus

    def knowledge_sources(self):
        return self.stores


def _client(monkeypatch):
    monkeypatch.setattr(knowledge, "user_workspace_files", lambda: FILES)
    monkeypatch.setattr(knowledge, "files_for", lambda location: FILES)
    monkeypatch.setattr(assistant_tools, "files_for", lambda location: FILES)
    monkeypatch.setattr(knowledge, "forwarded_identity", lambda: ("token", "a@example.org"))
    app = FastAPI()
    content = _Content()
    app.include_router(knowledge.router(content=content, warehouse=lambda: "wh"))
    app.include_router(designer.router(content=content))
    return TestClient(app)


def test_locations_list_every_store_and_the_users_folders(monkeypatch):
    body = _client(monkeypatch).get("/api/locations").json()
    assert [s["location"] for s in body["stores"]] == ["store:k.first", "store:k.second"]
    assert body["folders"][0] == {"location": "folder:/Workspace/Users/a@example.org", "label": "My folder"}


def test_discover_at_a_folder_lists_its_libraries_with_their_kind(monkeypatch):
    (record,) = _client(monkeypatch).get("/api/libraries/discover", params={"location": f"folder:{FOLDER}"}).json()
    assert record["id"] == {"name": "Measure", "version": "1.0.0"}
    assert (record["kind"], record["title"], record["location"]) == ("measure", "Folder measure", f"folder:{FOLDER}")


def test_discover_at_a_store_lists_only_that_store(monkeypatch):
    client = _client(monkeypatch)
    every = client.get("/api/libraries/discover").json()
    assert {r["location"] for r in every} == {"store:k.first", "store:k.second"}
    only = client.get("/api/libraries/discover", params={"location": "store:k.second"}).json()
    assert [r["id"]["name"] for r in only] == ["Other"]


def test_source_is_read_from_the_location(monkeypatch):
    client = _client(monkeypatch)
    folder = client.get("/api/libraries/Measure/1.0.0/source", params={"location": f"folder:{FOLDER}"}).json()
    assert folder == {"source": MEASURE, "path": f"{FOLDER}/cql/Measure-1.0.0.cql"}
    assert client.get("/api/libraries/Measure/1.0.0/source").json()["source"].endswith("// first\n")
    missing = client.get("/api/libraries/Measure/1.0.0/source", params={"location": "store:k.second"})
    assert missing.status_code == 404


def test_a_folder_measures_specification_and_reads_come_from_its_package_files(monkeypatch):
    client = _client(monkeypatch)
    at = {"location": f"folder:{FOLDER}"}
    spec = client.get("/api/libraries/Measure/1.0.0/specification", params=at).json()
    assert spec["overview"]["description"]["text"] == "Adults with a reading."
    assert spec["overview"]["description"]["provenance"] == "authored"
    reads = client.get("/api/libraries/Measure/1.0.0/data-requirements", params=at).json()
    assert reads == {"dataRequirements": [{"type": "Observation"}]}


def test_folder_browses_one_level_and_lists_libraries_only_where_there_is_cql(monkeypatch):
    from databricks.sdk.service.workspace import ObjectType
    listing = {
        "/Workspace/Users/a@example.org": [SimpleNamespace(path=FOLDER, object_type=ObjectType.DIRECTORY)],
        FOLDER: [SimpleNamespace(path=f"{FOLDER}/cql", object_type=ObjectType.DIRECTORY),
                 SimpleNamespace(path=f"{FOLDER}/resources", object_type=ObjectType.DIRECTORY)],
    }
    workspace = SimpleNamespace(list=lambda path: listing[path])
    monkeypatch.setattr(knowledge, "user_client", lambda: SimpleNamespace(workspace=workspace))
    client = _client(monkeypatch)
    home = client.get("/api/folders", params={"path": "/Workspace/Users/a@example.org"}).json()
    assert home["folders"] == [{"name": "CMS1", "path": FOLDER}] and home["libraries"] == []
    package = client.get("/api/folders", params={"path": FOLDER}).json()
    assert [lib["id"]["name"] for lib in package["libraries"]] == ["Measure"]


def test_an_include_the_folder_lacks_reads_its_data_requirements_from_knowledge(monkeypatch):
    """Data requirements follow the include order: the folder first, then tagged knowledge."""
    client = _client(monkeypatch)
    at = {"location": f"folder:{FOLDER}"}
    assert client.get("/api/libraries/Other/2.0.0/data-requirements", params=at).json() == {
        "dataRequirements": [{"type": "Patient"}]}
