"""DOC-AUTO-SAVE — the ``Library`` resource the Designer's saves write (``cql_databricks.compiler.library_resource``)."""
import base64
import unittest

from cql_databricks.compiler.library_resource import cql_of, library_resource, with_content

BASE = "https://measures.example.org/fhir"
ELM = '{"library": {}}'


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
