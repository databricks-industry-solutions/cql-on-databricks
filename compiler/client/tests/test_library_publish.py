"""CLIENT-PUBLISH, BIND-PUBLISHED-VIEW — publishing a folder library into an environment's knowledge store
(``cql_databricks.compiler.library_publish``).

Stdlib unittest. The folder is a dict standing in for workspace files, the store a fake runner answering the row
read and the MERGE publishing runs, and the compiler a fake that fails CQL marked ``BROKEN``. Every fixture is
synthetic.
"""
import base64
import json
import unittest
from types import SimpleNamespace

from cql_databricks.compiler.libraries import LibraryFiles, glob_match
from cql_databricks.compiler.library_publish import (NOT_WRITTEN, PUBLISHED, REFUSED, UNCHANGED, PublishFailed,
                                                     PublishRefused, publish_folder_library)
from cql_databricks.compiler.library_resource import cql_of, library_resource, with_content
from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema

BASE = "https://measures.example.org/fhir"
F = "/Workspace/Users/a@example.org/project"
STORE = TaggedSchema(tag=KNOWLEDGE_TAG, name="measures", catalog="k", schema="measures", position=0)
LIBRARY_TABLE, MEASURE_TABLE = STORE.table("library"), STORE.table("measure")

SHARED = "library Shared version '1.0.0'\ndefine S: 1\n"
OTHER = "library Other version '1.0.0'\ndefine O: 2\n"
ROOT = ("library Screening version '0.1.0'\ninclude Shared version '1.0.0'\ninclude Other version '1.0.0'\n"
        "include Base version '1.0.0'\ndefine X: Shared.S\n")
MEASURE = {"resourceType": "Measure", "url": f"{BASE}/Measure/Screening", "version": "0.1.0", "name": "Screening",
           "library": [f"{BASE}/Library/Screening|0.1.0"]}
ELM = '{"library": {}}'


class _Folder(LibraryFiles):
    def __init__(self, files):
        self.files = dict(files)

    def is_dir(self, path):
        return any(p.startswith(path.rstrip("/") + "/") for p in self.files)

    def glob(self, root, pattern):
        root = root.rstrip("/")
        return sorted(p for p in self.files if p.startswith(root + "/") and glob_match(p[len(root) + 1:], pattern))

    def read_text(self, path):
        return self.files.get(path)


def _project(root=ROOT, shared=SHARED):
    return _Folder({f"{F}/cql/Screening-0.1.0.cql": root, f"{F}/cql/Shared-1.0.0.cql": shared,
                    f"{F}/cql/Other-1.0.0.cql": OTHER,
                    f"{F}/resources/measure-Screening-0.1.0.json": json.dumps(MEASURE)})


def _content():
    """Tagged knowledge: the store, holding Base, which the project includes but does not hold."""
    corpus = Corpus(lambda *a: [], [STORE], [Library(
        name="Base", version="1.0.0", url=f"{BASE}/Library/Base", fhir_version="1.0.0",
        source="library Base version '1.0.0'\n", description="", title="", kind="library", model="",
        data_requirements=[], knowledge=STORE)])
    return SimpleNamespace(corpus=lambda: corpus, knowledge_sources=lambda: corpus.sources)


class _Store:
    """The store's rows by (table, url, version), read and MERGEd as publishing does. ``fail_on`` refuses the
    MERGE of that resource name."""

    def __init__(self, rows=None, fail_on=None, view_selects_store=True):
        self.rows, self.fail_on, self.merged = dict(rows or {}), fail_on, []
        self.view_selects_store = view_selects_store

    def __call__(self, sql, params=None):
        if sql.startswith("SELECT url, version FROM `k`.`knowledge`.`library`"):
            # BIND-PUBLISHED-VIEW: the published view over the store's library table.
            if not self.view_selects_store:
                return []
            return [{"url": url, "version": version} for (table, url, version) in self.rows
                    if table.endswith("`library`")]
        table = sql.split(" FROM ")[1].split(" WHERE")[0] if sql.startswith("SELECT") else \
            sql.split("MERGE INTO ")[1].split(" AS target")[0]
        if sql.startswith("SELECT"):
            held = self.rows.get((table, params["url"], params["version"]))
            return [{"resource": json.dumps(held)}] if held is not None else []
        resource = json.loads(params["resource_json"])
        if resource.get("name") == self.fail_on:
            raise RuntimeError("PERMISSION_DENIED: MODIFY on the table")
        self.rows[(table, resource["url"], resource["version"])] = resource
        self.merged.append((table, resource["name"], params["source_lineage"]))
        return [{"num_affected_rows": 1}]


def _translate(cql, includes):
    broken = "BROKEN" in cql
    return {"ok": not broken, "elm": None if broken else ELM, "diagnostics": ["Syntax error at 2:9"] if broken else []}


def _publish(folder, run, **kw):
    return publish_folder_library(_content(), folder, F, "Screening", "0.1.0", store=("k", "measures"),
                                  view=("k", "knowledge"), run=run, translate=_translate, base=BASE, **kw)


def _outcomes(summary):
    return [(lib.name, lib.outcome) for lib in summary.libraries]


class PublishLibraryTest(unittest.TestCase):
    def test_publishes_the_library_and_its_folder_includes_dependencies_first(self):
        run = _Store()
        summary = _publish(_project(), run)
        self.assertEqual(_outcomes(summary), [("Shared", PUBLISHED), ("Other", PUBLISHED), ("Screening", PUBLISHED)])
        self.assertEqual(run.merged, [(LIBRARY_TABLE, "Shared", "publish"), (LIBRARY_TABLE, "Other", "publish"),
                                      (LIBRARY_TABLE, "Screening", "publish"), (MEASURE_TABLE, "Screening", "publish")])
        # Base resolved from tagged knowledge: it is already published and is left alone.
        self.assertNotIn("Base", [name for _, name, _ in run.merged])
        written = run.rows[(LIBRARY_TABLE, f"{BASE}/Library/Screening", "0.1.0")]
        self.assertEqual(cql_of(written), ROOT)
        self.assertIn({"contentType": "application/elm+json", "data": base64.b64encode(ELM.encode()).decode()},
                      written["content"])
        self.assertEqual(summary.store, "k.measures")

    def test_writes_only_what_changed(self):
        run = _Store()
        _publish(_project(), run)
        run.merged.clear()
        edited = ROOT.replace("Shared.S", "Shared.S + 1")
        summary = _publish(_project(root=edited), run)
        self.assertEqual(_outcomes(summary), [("Shared", UNCHANGED), ("Other", UNCHANGED), ("Screening", PUBLISHED)])
        self.assertEqual([name for _, name, _ in run.merged], ["Screening", "Screening"])
        # A shared library edited in the folder is published again, and replaces its version.
        run.merged.clear()
        summary = _publish(_project(root=edited, shared=SHARED.replace("1\n", "10\n")), run)
        self.assertEqual(_outcomes(summary), [("Shared", PUBLISHED), ("Other", UNCHANGED), ("Screening", UNCHANGED)])

    def test_line_endings_alone_are_no_change(self):
        run = _Store()
        _publish(_project(), run)
        run.merged.clear()
        summary = _publish(_project(root=ROOT.replace("\n", "\r\n")), run)
        self.assertEqual([lib.outcome for lib in summary.libraries], [UNCHANGED] * 3)
        self.assertEqual(run.merged, [])

    def test_an_edited_measure_alone_is_published(self):
        run = _Store()
        _publish(_project(), run)
        run.merged.clear()
        folder = _project()
        folder.files[f"{F}/resources/measure-Screening-0.1.0.json"] = json.dumps({**MEASURE, "title": "Screening"})
        self.assertEqual(_outcomes(_publish(folder, run))[-1], ("Screening", PUBLISHED))

    def test_nothing_is_written_when_a_library_to_write_does_not_compile(self):
        run = _Store()
        with self.assertRaises(PublishRefused) as refused:
            _publish(_project(shared=SHARED + "BROKEN\n"), run)
        self.assertEqual(run.merged, [])
        self.assertEqual(_outcomes(refused.exception.summary),
                         [("Shared", REFUSED), ("Other", NOT_WRITTEN), ("Screening", NOT_WRITTEN)])
        self.assertEqual(refused.exception.summary.libraries[0].detail, ("Syntax error at 2:9",))

    def test_a_failed_write_says_what_was_written_before_it(self):
        run = _Store(fail_on="Other")
        with self.assertRaises(PublishFailed) as failed:
            _publish(_project(), run)
        self.assertEqual(_outcomes(failed.exception.summary),
                         [("Shared", PUBLISHED), ("Other", REFUSED), ("Screening", NOT_WRITTEN)])
        self.assertIn("PERMISSION_DENIED", failed.exception.summary.libraries[1].detail[0])

    def test_a_view_that_does_not_select_the_store_fails_the_publish(self):
        """BIND-PUBLISHED-VIEW: one read of the published view after the writes; what it does not show fails."""
        with self.assertRaises(PublishFailed) as failed:
            _publish(_project(), _Store(view_selects_store=False))
        self.assertIn("does not select the store", str(failed.exception))
        self.assertEqual({lib.outcome for lib in failed.exception.summary.libraries}, {PUBLISHED})

    def test_a_folder_library_keeps_its_own_resource_and_url(self):
        folder = _project()
        own = {"resourceType": "Library", "name": "Shared", "version": "1.0.0", "url": "https://other.example.org/Library/Shared",
               "title": "Shared logic", "content": [{"contentType": "text/cql", "data": ""}]}
        folder.files[f"{F}/resources/library-Shared-1.0.0.json"] = json.dumps(own)
        run = _Store()
        summary = _publish(folder, run)
        self.assertEqual(summary.libraries[0].url, "https://other.example.org/Library/Shared")
        written = run.rows[(LIBRARY_TABLE, "https://other.example.org/Library/Shared", "1.0.0")]
        self.assertEqual((written["title"], cql_of(written)), ("Shared logic", SHARED))


class NotebookPublishTest(unittest.TestCase):
    def test_a_notebook_publishes_into_its_environment_never_a_named_store(self):
        """CLIENT-PUBLISH, CLIENT-ENVIRONMENT: the store is the environment's knowledge_store."""
        from cql_databricks.compiler import publish_library
        with self.assertRaises(TypeError):
            publish_library("Screening", "0.1.0", folder=F, store="k.m", base=BASE, spark=object())


class LibraryResourceTest(unittest.TestCase):
    """DOC-AUTO-SAVE — no ``Library`` holds ELM that the CQL beside it does not compile to."""

    def _library(self, cql):
        return {"resourceType": "Library", "name": "M", "description": "kept",
                "content": [{"contentType": "text/cql", "data": base64.b64encode(cql.encode()).decode()},
                            {"contentType": "application/elm+json", "data": base64.b64encode(b"{}").decode()}]}

    def test_with_content_changes_only_the_cql_and_elm(self):
        updated = with_content(self._library("old"), "new", ELM)
        self.assertEqual((updated["description"], cql_of(updated)), ("kept", "new"))
        self.assertEqual([a["contentType"] for a in updated["content"]], ["text/cql", "application/elm+json"])

    def test_cql_that_does_not_compile_carries_no_elm_and_compiling_cql_gets_it_back(self):
        without = with_content(self._library("old"), "new", None)
        self.assertEqual([a["contentType"] for a in without["content"]], ["text/cql"])
        self.assertEqual([a["contentType"] for a in with_content(without, "new", ELM)["content"]],
                         ["text/cql", "application/elm+json"])
        xml = self._library("old")
        xml["content"].append({"contentType": "application/elm+xml", "data": ""})
        self.assertEqual([a["contentType"] for a in with_content(xml, "new", ELM)["content"]],
                         ["text/cql", "application/elm+json"])

    def test_a_built_library_is_a_draft_under_the_base(self):
        built = library_resource("Screening", "0.1.0", "cql", None, base=BASE, derived_from="x|1")
        self.assertEqual((built["url"], built["status"], built["relatedArtifact"][0]["resource"]),
                         (f"{BASE}/Library/Screening", "draft", "x|1"))
        self.assertEqual([a["contentType"] for a in built["content"]], ["text/cql"])


if __name__ == "__main__":
    unittest.main()
