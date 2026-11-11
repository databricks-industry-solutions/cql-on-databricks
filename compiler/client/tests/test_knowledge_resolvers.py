"""CLIENT-KNOWLEDGE-RESOLVER, CLIENT-RESOLVER-OVERLAY, SVC-NOTEBOOK-LIBRARY-FOLDER: the shipped knowledge resolvers
and the one closure walk over a chain of them.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from cql_databricks.compiler.knowledge_libraries import FolderThenKnowledge
from cql_databricks.resolvers.knowledge import (CellsKnowledgeResolver, FolderKnowledgeResolver,
                                                         TableKnowledgeResolver, library_provider)
from cql_databricks.compiler.libraries import LibraryRef, LibraryResolutionError, LibrarySource
from cql_databricks.resolvers.interfaces import KnowledgeResolver, Overlay
from cql_databricks.compiler.session_libraries import SessionLibraryProvider


class _Mem(KnowledgeResolver):
    def __init__(self, name, libs):
        self.name, self.libs = name, libs

    def identity(self):
        return self.name

    def library(self, name, version, site=None):
        cql = self.libs.get(name)
        return LibrarySource(name, None, cql=cql, origin=self.name) if cql else None

    def measure(self, url, version):
        return None

    def libraries(self):
        return [LibraryRef(n) for n in self.libs]


def _write(folder, name, body):
    with open(os.path.join(folder, f"{name}.cql"), "w") as f:
        f.write(body)


class KnowledgeResolversTest(unittest.TestCase):
    def test_cells_answer_by_name_and_exact_version(self):
        cells = CellsKnowledgeResolver(cells={})
        cells.provider.register("library Draft version '0.1'\ndefine X: 1")
        self.assertEqual(cells.library("Draft", None).version, "0.1")
        self.assertIsNone(cells.library("Draft", "0.2"))
        self.assertEqual(list(cells.libraries()), [LibraryRef("Draft", "0.1")])

    def test_a_folder_answers_from_its_files(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Helpers", "library Helpers version '1.0.0'\ndefine Y: 2")
            folder = FolderKnowledgeResolver(d)
            self.assertEqual(folder.library("Helpers", "1.0.0").name, "Helpers")
            self.assertIsNone(folder.library("Missing", None))
            self.assertEqual(folder.identity(), f"folder:{d}")

    def test_no_knowledge_resolver_writes(self):
        """DEC-NO-CONTENT-PUBLISHING, CLIENT-RESOLVER-READ-WRITE: cells, folders and an environment's knowledge are
        read, never written."""
        with tempfile.TemporaryDirectory() as d:
            for resolver in (TableKnowledgeResolver(lambda: None), CellsKnowledgeResolver(cells={}),
                             FolderKnowledgeResolver(d)):
                self.assertFalse(hasattr(resolver, "publish"), type(resolver).__name__)


class ClosureTest(unittest.TestCase):
    def test_the_shipped_order_keeps_its_rules(self):
        """SVC-NOTEBOOK-LIBRARY-FOLDER: cells, then the folder, then the environment's knowledge, as one chain."""
        with tempfile.TemporaryDirectory() as d:
            chain = Overlay(CellsKnowledgeResolver(cells={}), Overlay(FolderKnowledgeResolver(d), TableKnowledgeResolver(lambda: None)))
            provider = library_provider(chain)
            self.assertIsInstance(provider, SessionLibraryProvider)
            self.assertIsInstance(provider.fallthrough, FolderThenKnowledge)
            self.assertIsInstance(library_provider(Overlay(FolderKnowledgeResolver(d), TableKnowledgeResolver(lambda: None))),
                                  FolderThenKnowledge)

    def test_any_other_chain_resolves_includes_from_the_serving_layer_on(self):
        """CLIENT-RESOLVER-OVERLAY: a library from a later layer never resolves an include from an earlier one."""
        dev = _Mem("dev", {"Root": "library Root\ninclude Helpers\ndefine X: 1",
                           "Helpers": "library Helpers\ndefine Dev: 1"})
        base = _Mem("base", {"Helpers": "library Helpers\ninclude Common\ndefine Base: 1",
                             "Common": "library Common\ndefine C: 1", "Solo": "library Solo\ninclude Root"})
        provider = library_provider(Overlay(dev, base))
        closure = provider.closure(provider.resolve(LibraryRef("Root")))
        self.assertEqual([(s.name, s.origin) for s in closure], [("Helpers", "dev")])
        with self.assertRaises(LibraryResolutionError):
            provider.closure(provider.resolve(LibraryRef("Solo")))  # base's Solo cannot include dev's Root
        self.assertEqual([r.name for r in provider.search()], ["Root", "Helpers", "Common", "Solo"])


if __name__ == "__main__":
    unittest.main()
