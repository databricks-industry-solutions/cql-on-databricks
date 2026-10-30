"""BUILD-CLIENT-NAMESPACE (`DEC-CLIENT-PER-FEATURE`) — ``cql_databricks`` stays a PEP 420 namespace.

Each feature's public package lives under it (``cql_databricks.compiler``, ``cql_databricks.data_bindings``,
``cql_databricks.agent``), each shipped by its own distribution. One stray ``cql_databricks/__init__.py``
turns the namespace into a regular package, and every other feature's package then silently stops
importing, so this fails on one anywhere in the repository.
"""
import pathlib
import unittest

import cql_databricks

REPO = pathlib.Path(__file__).resolve().parents[3]
_SKIP = {"build", "node_modules", ".venv", ".git", "__pycache__"}


class NamespaceTest(unittest.TestCase):
    def test_no_directory_named_cql_databricks_holds_an_init(self):
        found = [p.relative_to(REPO) for p in REPO.rglob("cql_databricks/__init__.py")
                 if not _SKIP.intersection(p.relative_to(REPO).parts)]
        self.assertEqual(found, [], "cql_databricks is a PEP 420 namespace; delete these")

    def test_the_installed_root_is_a_namespace(self):
        self.assertIsNone(getattr(cql_databricks, "__file__", None))


if __name__ == "__main__":
    unittest.main()
