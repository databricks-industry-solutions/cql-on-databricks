"""CLIENT-KNOWLEDGE-INCLUDES — tagged knowledge resolves library references, after cells and the folder, earliest
scope first. Synthetic libraries only."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cql_databricks.compiler.knowledge_libraries import (FolderThenKnowledge, KnowledgeLibraryProvider,
                                                         from_knowledge)
from cql_databricks.compiler.libraries import FileLibraryProvider, LibraryRef, LibraryResolutionError, LibrarySource
from cql_databricks.compiler.session_libraries import SessionLibraryProvider
from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema

DEV = TaggedSchema(KNOWLEDGE_TAG, "dev", "acme_dev", "cql", 0)
SHARED = TaggedSchema(KNOWLEDGE_TAG, "quality_measures", "acme_shared", "quality_measures", 1)


def _cql(name: str, version: str, *includes: tuple[str, str]) -> str:
    lines = [f"library {name} version '{version}'"]
    lines += [f"include {n} version '{v}'" for n, v in includes]
    return "\n".join(lines) + "\n"


def _library(knowledge: TaggedSchema, name: str, version: str, *includes: tuple[str, str]) -> Library:
    return Library(name=name, version=version, url=f"http://example.org/Library/{name}", fhir_version=version,
                   source=_cql(name, version, *includes), description="", title="", kind="library", model="FHIR",
                   data_requirements=[], knowledge=knowledge)


#: A published measure in the shared scope, its helpers, and a dev scope that overrides one helper.
LIBRARIES = [
    _library(DEV, "Common", "2.0.0"),
    _library(DEV, "Helpers", "1.0.0"),
    _library(SHARED, "Measure", "1.0.0", ("Common", "1.0.0"), ("FHIRHelpers", "4.0.1")),
    _library(SHARED, "Common", "1.0.0", ("Helpers", "1.0.0")),
    _library(SHARED, "Helpers", "1.0.0"),
    _library(SHARED, "Helpers", "3.0.0"),
    _library(SHARED, "FHIRHelpers", "4.0.1"),
]


class _Content:
    """The resolver as the provider uses it: a loaded corpus and the environment's catalog."""

    catalog = "acme_shared"

    def __init__(self, libraries=LIBRARIES):
        self._corpus = Corpus(lambda *a: [], [DEV, SHARED], libraries)

    def corpus(self):
        return self._corpus


def _where(sources):
    return [(s.name, s.version, s.origin.split("/")[2]) for s in sources]


class KnowledgeLibraryProviderTest(unittest.TestCase):

    def setUp(self):
        self.built = []
        self.knowledge = KnowledgeLibraryProvider(lambda: self.built.append(1) or _Content())

    def test_nothing_is_read_until_a_library_is_asked_for(self):
        self.assertEqual(self.built, [])
        self.knowledge.resolve(LibraryRef("Measure", "1.0.0"))
        self.knowledge.resolve(LibraryRef("Common"))
        self.assertEqual(self.built, [1])

    def test_an_exact_pin_wins_over_an_earlier_scopes_other_version(self):
        source = self.knowledge.resolve(LibraryRef("Common", "1.0.0"))
        self.assertEqual((source.version, source.origin), ("1.0.0", "uc://acme_shared.quality_measures/Common|1.0.0"))
        self.assertTrue(from_knowledge(source))

    def test_by_name_the_earliest_scope_wins_over_a_newer_version_later(self):
        self.assertEqual(self.knowledge.resolve(LibraryRef("Helpers")).origin, "uc://acme_dev.cql/Helpers|1.0.0")

    def test_a_pin_no_scope_holds_fails_rather_than_taking_another_version(self):
        """KNOWLEDGE-RESOLVE-API — a pinned version matches exactly."""
        with self.assertRaisesRegex(LibraryResolutionError, "Helpers"):
            self.knowledge.resolve(LibraryRef("Helpers", "9.9.9"))

    def test_the_closure_is_one_walk_of_the_corpus_fhirhelpers_included(self):
        """LANG-FHIRHELPERS-FROM-CONTENT: FHIRHelpers comes from tagged knowledge like any included library."""
        root = self.knowledge.resolve(LibraryRef("Measure", "1.0.0"))
        self.assertEqual(_where(self.knowledge.closure(root)), [
            ("Helpers", "1.0.0", "acme_dev.cql"), ("Common", "1.0.0", "acme_shared.quality_measures"),
            ("FHIRHelpers", "4.0.1", "acme_shared.quality_measures")])

    def test_an_include_the_knowledge_lacks_fails_naming_the_environments_knowledge(self):
        with self.assertRaisesRegex(LibraryResolutionError, "acme_shared.knowledge"):
            self.knowledge.resolve(LibraryRef("Missing"))
        draft = LibrarySource("Draft", "1", cql=_cql("Draft", "1", ("Missing", "1.0.0")))
        with self.assertRaisesRegex(LibraryResolutionError, "needs Missing 1.0.0.*acme_shared.knowledge"):
            self.knowledge.closure(draft)

    def test_search_lists_each_library_once(self):
        self.assertEqual([str(r) for r in self.knowledge.search("Help*")],
                         ["Helpers|1.0.0", "Helpers|3.0.0"])


class PrecedenceTest(unittest.TestCase):
    """Cells, then the folder, then tagged knowledge: one lookup order."""

    def setUp(self):
        self.folder_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder_dir)
        (self.folder_dir / "Common.cql").write_text(_cql("Common", "1.0.0"))
        (self.folder_dir / "Local.cql").write_text(_cql("Local", "1.0.0", ("Measure", "1.0.0")))
        self.published = FolderThenKnowledge(FileLibraryProvider([str(self.folder_dir)]),
                                             KnowledgeLibraryProvider(_Content))
        self.session = SessionLibraryProvider(self.published)

    def test_a_cell_shadows_the_folder_which_shadows_tagged_knowledge(self):
        self.assertTrue(from_knowledge(self.session.resolve(LibraryRef("Measure", "1.0.0"))))
        self.assertEqual(Path(self.session.resolve(LibraryRef("Common", "1.0.0")).origin).resolve().parent,
                         self.folder_dir.resolve())
        self.session.register(_cql("Common", "1.0.0"))
        self.assertIsNone(self.session.resolve(LibraryRef("Common", "1.0.0")).origin)

    def test_inline_cql_includes_a_published_measure_and_gets_its_published_closure(self):
        """The roadmap scenario: include a measure from tagged knowledge and add a define. The folder's copy of
        Common does not reach into the published measure's closure."""
        draft = LibrarySource("Draft", "1", cql=_cql("Draft", "1", ("Measure", "1.0.0")))
        self.assertEqual(_where(self.session.closure(draft)), [
            ("Measure", "1.0.0", "acme_shared.quality_measures"), ("Helpers", "1.0.0", "acme_dev.cql"),
            ("Common", "1.0.0", "acme_shared.quality_measures"),
            ("FHIRHelpers", "4.0.1", "acme_shared.quality_measures")])

    def test_a_folder_library_includes_what_only_tagged_knowledge_holds(self):
        local = self.session.resolve(LibraryRef("Local"))
        self.assertEqual([s.name for s in self.session.closure(local)], ["Measure", "Helpers", "Common", "FHIRHelpers"])
        self.assertTrue(all(from_knowledge(s) for s in self.session.closure(local)))

    def test_without_a_folder_tagged_knowledge_answers_alone(self):
        published = FolderThenKnowledge(None, KnowledgeLibraryProvider(_Content))
        self.assertTrue(from_knowledge(published.resolve(LibraryRef("Common", "1.0.0"))))


if __name__ == "__main__":
    unittest.main()
