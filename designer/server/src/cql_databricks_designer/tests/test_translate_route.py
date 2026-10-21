"""KNOWLEDGE-LAKE-DIRECT, BUILD-RESOLVE-COMPILE, APP-SPEC-MODEL — `/api/translate` and `/api/libraries/resolve` over the
shared resolution: the closure from the corpus, the root against every include, each include against its own."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema
from cql_databricks_common.compiler import CompilerError
from cql_databricks_designer.routes import compile as compile_routes

KNOWLEDGE = TaggedSchema(tag=KNOWLEDGE_TAG, name="ecqm", catalog="k", schema="ecqm")
ROOT = "library Root version '1.0.0'\ninclude Mid version '2.0.0'\ndefine X: 1\n"
MID = "library Mid version '2.0.0'\ninclude Base version '1.0.0'\ndefine Y: 1\n"
BASE = "library Base version '1.0.0'\ndefine Z: 1\n"


def _library(name: str, version: str, source: str) -> Library:
    return Library(name=name, version=version, url=f"http://x/Library/{name}", fhir_version=version, source=source,
                   description="", title="", kind="library", model="", data_requirements=[], knowledge=KNOWLEDGE)


class _Content:
    def __init__(self, libraries: list[Library]) -> None:
        self._corpus = Corpus(lambda *a: [], [KNOWLEDGE], libraries)

    def corpus(self) -> Corpus:
        return self._corpus


class _Compiler:
    def __init__(self, unreachable: set[str] = frozenset()) -> None:
        self.unreachable = unreachable
        self.calls: list[tuple[str, list[str]]] = []

    def translate(self, cql: str, includes: list[str] | None = None) -> dict[str, Any]:
        name = cql.split()[1]
        self.calls.append((name, [s.split()[1] for s in includes or []]))
        if name in self.unreachable:
            raise CompilerError(f"compiler unreachable translating {name}")
        return {"ok": True, "elm": "{}", "diagnostics": []}


def _client(compiler: _Compiler, libraries: list[Library]) -> TestClient:
    app = FastAPI()
    app.include_router(compile_routes.router(compiler=compiler, content=_Content(libraries)))
    return TestClient(app)


ALL = [_library("Root", "1.0.0", ROOT), _library("Mid", "2.0.0", MID), _library("Base", "1.0.0", BASE)]


def test_the_root_sees_its_closure_and_each_include_only_its_own() -> None:
    compiler = _Compiler()
    body = _client(compiler, ALL).post("/api/translate", json={"cql": ROOT, "include_elm": True}).json()
    assert body["ok"] is True and body["diagnostics"] == []
    assert compiler.calls == [("Root", ["Base", "Mid"]), ("Base", []), ("Mid", ["Base"])]
    assert [(i["name"], i["version"], i["ok"]) for i in body["includes"]] == [("Base", "1.0.0", True),
                                                                              ("Mid", "2.0.0", True)]


def test_an_include_not_found_is_a_diagnostic() -> None:
    body = _client(_Compiler(), ALL[:2]).post("/api/translate", json={"cql": ROOT}).json()
    assert body["ok"] is False
    assert body["diagnostics"] == ['Included library "Base" version 1.0.0 is not in any tagged knowledge source.']


def test_an_include_the_compiler_cannot_reach_is_that_includes_diagnostic() -> None:
    body = _client(_Compiler(unreachable={"Mid"}), ALL).post(
        "/api/translate", json={"cql": ROOT, "include_elm": True}).json()
    mid = next(i for i in body["includes"] if i["name"] == "Mid")
    assert mid["ok"] is False and "unreachable" in mid["diagnostics"][0]
    assert body["ok"] is True   # the root translated


def test_the_root_unreachable_is_a_502() -> None:
    r = _client(_Compiler(unreachable={"Root"}), ALL).post("/api/translate", json={"cql": ROOT})
    assert r.status_code == 502


def test_explicit_includes_skip_resolution() -> None:
    compiler = _Compiler()
    body = _client(compiler, []).post("/api/translate", json={"cql": ROOT, "includes": [MID, BASE]}).json()
    assert body["ok"] is True
    assert compiler.calls == [("Root", ["Mid", "Base"])]
