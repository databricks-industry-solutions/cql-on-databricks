"""RUN-ADDRESS-LOCATION, AGENT-DEFINITION-DISCOVERY — the agent reads a definition from a store or a folder."""
from __future__ import annotations

import json

from cql_databricks.agent.service import definitions
from cql_databricks.compiler.libraries import LibraryFiles, glob_match
from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema

MEASURE = "library Measure version '1.0.0'\ndefine \"Initial Population\": true\n"
SCORED = {"resourceType": "Measure", "title": "Folder measure", "version": "1.0.0",
          "library": ["https://example.org/Library/Measure"],
          "scoring": {"coding": [{"code": "proportion"}]}}


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


def _corpus():
    first = TaggedSchema(tag=KNOWLEDGE_TAG, name="first", catalog="k", schema="first", position=0)
    second = TaggedSchema(tag=KNOWLEDGE_TAG, name="second", catalog="k", schema="second", position=1)
    return Corpus(lambda *a: [], [first, second], [
        Library(name="Measure", version="1.0.0", url="http://x/Library/Measure", fhir_version="1.0.0",
                source=MEASURE + "// first\n", description="", title="", kind="library", model="",
                data_requirements=[], knowledge=first),
        Library(name="Measure", version="1.0.0", url="http://y/Library/Measure", fhir_version="1.0.0",
                source=MEASURE + "// second\n", description="", title="", kind="library", model="",
                data_requirements=[], knowledge=second),
    ])


def test_every_listed_definition_says_which_store_it_is_in():
    entries = definitions.list_definitions(corpus=_corpus)
    assert {e["location"] for e in entries} == {"store:k.first", "store:k.second"}


def test_a_store_location_reads_that_stores_copy():
    out = definitions.get_definition("Measure", "1.0.0", corpus=_corpus, location="store:k.second")
    assert out["cql"].endswith("// second\n") and out["location"] == "store:k.second"
    assert definitions.get_definition("Measure", "1.0.0", corpus=_corpus)["cql"].endswith("// first\n")


def test_a_folder_location_reads_the_cql_and_the_packages_measure():
    files = _Files({"/W/m/cql/Measure-1.0.0.cql": MEASURE,
                    "/W/m/resources/measure-Measure-1.0.0.json": json.dumps(SCORED)})
    out = definitions.get_definition("Measure", "1.0.0", corpus=_corpus, location="folder:/W/m", files=files)
    assert (out["kind"], out["title"], out["scoring"], out["cql"]) == ("measure", "Folder measure", "proportion",
                                                                       MEASURE)
    assert definitions.folder_scoring(files, "/W/m", "Measure", "1.0.0") == "proportion"
    assert definitions.get_definition("Nope", "1", corpus=_corpus, location="folder:/W/m", files=files) is None
