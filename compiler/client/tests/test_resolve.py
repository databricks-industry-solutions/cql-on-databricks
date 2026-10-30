"""BUILD-RESOLVE-COMPILE — the one resolution every compiler caller uses: the closure, the data source, the ELM.

Needs `cql_databricks.data_bindings` (shipped by this distribution since BINDINGS-INTO-CLIENT) and its SDK
dependency; skipped where only the stdlib and this package's source are on the path."""
import unittest

try:
    from cql_databricks.data_bindings import DATA_SOURCE_TAG, KNOWLEDGE_TAG, Corpus, Library, TaggedSchema
    from cql_databricks.compiler.resolve import (ResolutionError, is_published, require_translated, resolve,
                                                 resolve_data_source, translate_includes, translate_root)
except ImportError:  # pragma: no cover - the stdlib-only fallback of scripts/test.sh
    Corpus = None

KNOWLEDGE = None if Corpus is None else TaggedSchema(tag=KNOWLEDGE_TAG, name="quality_measures", catalog="k",
                                                     schema="quality_measures")

ROOT = "library Root version '1.0.0'\ninclude Mid version '2.0.0'\ninclude Base version '1.0.0'\ndefine X: 1\n"
MID = "library Mid version '2.0.0'\ninclude Base version '1.0.0'\ndefine Y: 1\n"
BASE = "library Base version '1.0.0'\ndefine Z: 1\n"


def _library(name, version, source):
    return Library(name=name, version=version, url=f"http://x/Library/{name}", fhir_version=version, source=source,
                   description="", title="", kind="library", model="", data_requirements=[], knowledge=KNOWLEDGE)


class _Content:
    scopes = ("cat",)

    def __init__(self, libraries, data_sources=()):
        self._corpus = Corpus(lambda *a: [], [KNOWLEDGE], libraries)
        self._data_sources = list(data_sources)

    def corpus(self):
        return self._corpus

    def data_source_schemas(self):
        return self._data_sources


def _content(*extra):
    return _Content([_library("Root", "1.0.0", ROOT), _library("Mid", "2.0.0", MID), _library("Base", "1.0.0", BASE),
                     *extra])


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class ResolveTests(unittest.TestCase):

    def test_a_tagged_library_and_its_closure_in_compile_order(self):
        r = resolve(_content(), library="Root", version="1.0.0")
        self.assertEqual((r.name, r.version, r.source), ("Root", "1.0.0", ROOT))
        self.assertEqual([i.id.name for i in r.includes], ["Base", "Mid"])   # dependencies first
        self.assertEqual(r.sources, [ROOT, BASE, MID])
        self.assertIs(r.require_complete(), r)

    def test_cql_text_resolves_its_includes_and_may_have_no_header(self):
        r = resolve(_content(), cql=MID)
        self.assertEqual((r.name, r.version), ("Mid", "2.0.0"))
        self.assertEqual(r.include_sources, [BASE])
        self.assertEqual(resolve(_content(), cql="define X: 1").name, "")

    def test_gaps_are_reported_or_refused_by_the_caller(self):
        r = resolve(_Content([_library("Root", "1.0.0", ROOT), _library("Base", "1.0.0", BASE)]), library="Root")
        self.assertEqual(r.unresolved_messages(),
                         ['Included library "Mid" version 2.0.0 is not in any tagged knowledge source.'])
        with self.assertRaisesRegex(ResolutionError, "Root: includes not in any tagged knowledge source: Mid 2.0.0"):
            r.require_complete()

    def test_a_pinned_version_the_corpus_lacks_is_unresolved(self):
        """KNOWLEDGE-RESOLVE-API — a pin matches exactly; another version of the same name does not stand in."""
        content = _Content([_library("Root", "1.0.0", ROOT), _library("Mid", "2.0.0", MID),
                            _library("Base", "1.1.0", BASE)])
        r = resolve(content, library="Root")
        self.assertEqual([(u.name, u.version) for u in r.unresolved], [("Base", "1.0.0")])

    def test_published_is_the_corpus_source_exactly_line_endings_aside(self):
        """RUN-ADDRESS-BY-LIBRARY, `DEC-PUBLISHED-TEST` — the one test of whether CQL may read as the published version."""
        content = _content()
        self.assertTrue(is_published(content, ROOT, "Root", "1.0.0"))
        self.assertTrue(is_published(content, ROOT.replace("\n", "\r\n"), "Root", "1.0.0"))
        self.assertFalse(is_published(content, ROOT + "define Y: 2\n", "Root", "1.0.0"))
        self.assertFalse(is_published(content, ROOT, "Root", "9.9.9"))
        self.assertFalse(is_published(content, ROOT, "", ""))

    def test_refusals(self):
        with self.assertRaisesRegex(ResolutionError, "exactly one"):
            resolve(_content(), library="Root", cql=ROOT)
        with self.assertRaisesRegex(ResolutionError, "exactly one"):
            resolve(_content())
        with self.assertRaisesRegex(ResolutionError, r"Nope \(newest\) is not in any tagged knowledge source"):
            resolve(_content(), library="Nope")

    def test_the_data_source_is_its_tag_value(self):
        source = TaggedSchema(tag=DATA_SOURCE_TAG, name="synthetic 1M", catalog="cat", schema="pop")
        content = _Content([], data_sources=[source])

        def run(statement, params=None):
            if "information_schema.routines" in statement:
                self.assertEqual(params, {"catalog": "cat", "schema": "pop"})
                return [{"routine_name": "retrieve_patient"}]
            return []
        layout = resolve_data_source(content, run, "synthetic 1M", ["Patient"])
        self.assertEqual(dict(layout.bindings), {"Patient": "cat.pop.retrieve_Patient"})
        with self.assertRaisesRegex(ResolutionError, "no data source is tagged 'production' in catalog scopes cat; known: synthetic 1M"):
            resolve_data_source(content, run, "production", ["Patient"])


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class TranslateTests(unittest.TestCase):

    def _translate(self, calls, failing=()):
        def translate(cql, includes):
            name = cql.split()[1]
            calls.append((name, [s.split()[1] for s in includes]))
            if name in failing:
                return {"ok": False, "elm": None, "diagnostics": [f"{name} is broken"]}
            return {"ok": True, "elm": {"library": name}, "diagnostics": []}
        return translate

    def test_the_root_sees_every_include_and_each_include_only_its_own(self):
        calls = []
        r = resolve(_content(), library="Root")
        root = translate_root(self._translate(calls), r)
        includes = translate_includes(self._translate(calls), r)
        self.assertEqual(calls, [("Root", ["Base", "Mid"]), ("Base", []), ("Mid", ["Base"])])
        self.assertTrue(root.ok)
        self.assertEqual(root.elm, '{"library": "Root"}')   # an ELM object arrives as JSON text
        self.assertEqual([(i.name, i.version, i.ok) for i in includes], [("Base", "1.0.0", True), ("Mid", "2.0.0", True)])
        self.assertEqual(includes[1].to_dict()["source"], MID)

    def test_a_library_that_does_not_translate_is_kept_and_can_be_refused(self):
        r = resolve(_content(), library="Root")
        includes = translate_includes(self._translate([], failing={"Mid"}), r)
        self.assertEqual([i.ok for i in includes], [True, False])     # never skipped
        self.assertEqual(includes[1].diagnostics, ("Mid is broken",))
        with self.assertRaisesRegex(ResolutionError, "did not translate: Mid 2.0.0: Mid is broken"):
            require_translated(includes)
        self.assertEqual(len(require_translated(includes[:1])), 1)


if __name__ == "__main__":
    unittest.main()
