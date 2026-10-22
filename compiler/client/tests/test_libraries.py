"""The library SOURCE seam (`BUILD-LIBRARY-PROVIDER`): `FileLibraryProvider` resolution over a search
path, the published folder layouts, include-closure walking, and the external-injection API.

Stdlib unittest over real temp directories — the layouts ARE filesystem conventions, so mocking `open`
would test the mock rather than the convention.
"""
import base64
import json
import os
import pathlib
import tempfile
import unittest

from cql_databricks.compiler import config
from cql_databricks.compiler.libraries import (DEFAULT_LAYOUTS, FileLibraryProvider,
                                        LibraryProvider, LibraryRef, LibraryResolutionError,
                                        LibrarySource, identity_of, includes_of,
                                        resolve_library_provider)
from cql_databricks.compiler.library import load_library


def _cql(name, version="1.0.000", includes=(), body='define "x": 1'):
    """A minimal but REAL CQL library header — the same shape published content ships."""
    inc = "".join(f"include {n} version '{v}' called {n}\n" for n, v in includes)
    return f"library {name} version '{version}'\nusing FHIR version '4.0.1'\n{inc}{body}\n"


def _write(root, rel, text):
    path = pathlib.Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class ParsingTests(unittest.TestCase):
    def test_identity_from_cql_header_and_from_elm(self):
        self.assertEqual(identity_of(_cql("ScreeningMeasure", "0.3.000")), ("ScreeningMeasure", "0.3.000"))
        elm = json.dumps({"library": {"identifier": {"id": "ScreeningMeasure", "version": "0.3.000"}}})
        self.assertEqual(identity_of(elm, elm=True), ("ScreeningMeasure", "0.3.000"))

    def test_includes_from_cql_and_from_elm_agree(self):
        src = LibrarySource(name="Root", cql=_cql("Root", includes=[("CommonHelpers", "2.0.000")]))
        self.assertEqual(includes_of(src), [LibraryRef("CommonHelpers", "2.0.000")])
        elm_src = LibrarySource(name="Root", elm=json.dumps({"library": {"includes": {"def": [
            {"localIdentifier": "CommonHelpers", "path": "CommonHelpers", "version": "2.0.000"}]}}}))
        self.assertEqual(includes_of(elm_src), [LibraryRef("CommonHelpers", "2.0.000")])

    def test_commented_out_includes_are_not_walked(self):
        """A commented `include` names a library that need not exist; walking it would fail a closure
        that is actually complete."""
        cql = ("library Root version '1.0.0'\n"
               "// include GhostLine version '1.0.0' called G\n"
               "/* include GhostBlock version '1.0.0' called G2 */\n"
               "include Real version '1.0.0' called R\n")
        self.assertEqual(includes_of(LibrarySource(name="Root", cql=cql)), [LibraryRef("Real", "1.0.0")])

    def test_a_source_must_carry_a_body(self):
        with self.assertRaises(ValueError):
            LibrarySource(name="Empty")


class FileProviderTests(unittest.TestCase):
    def test_flat_layout_resolves_plain_and_versioned_filenames(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "CommonHelpers.cql", _cql("CommonHelpers", "2.0.000"))
            _write(d, "Exclusions-6.18.000.cql", _cql("Exclusions", "6.18.000"))
            p = FileLibraryProvider([d])
            self.assertEqual(p.resolve(LibraryRef("CommonHelpers")).version, "2.0.000")
            self.assertEqual(p.resolve(LibraryRef("Exclusions")).version, "6.18.000")

    def test_ig_layout_scans_input_cql(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "input/cql/ScreeningMeasure.cql", _cql("ScreeningMeasure", "0.3.000"))
            self.assertEqual(FileLibraryProvider([d]).resolve(LibraryRef("ScreeningMeasure")).version, "0.3.000")

    def test_fhir_library_resource_carries_base64_cql_and_elm(self):
        """The `Library` RESOURCE form IG output and measure bundles ship."""
        with tempfile.TemporaryDirectory() as d:
            cql_text = _cql("Bundled", "1.2.3")
            elm_text = json.dumps({"library": {"identifier": {"id": "Bundled", "version": "1.2.3"}}})
            _write(d, "input/resources/library/Library-Bundled.json", json.dumps({
                "resourceType": "Library", "name": "Bundled", "version": "1.2.3",
                "content": [
                    {"contentType": "text/cql",
                     "data": base64.b64encode(cql_text.encode()).decode()},
                    {"contentType": "application/elm+json",
                     "data": base64.b64encode(elm_text.encode()).decode()},
                ]}))
            src = FileLibraryProvider([d]).resolve(LibraryRef("Bundled"))
            self.assertEqual(src.version, "1.2.3")
            self.assertIn('library Bundled', src.cql)
            self.assertIn('"identifier"', src.elm)

    def test_a_non_library_json_in_a_scanned_dir_is_ignored_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Library-Junk.json", json.dumps({"resourceType": "Measure", "name": "Junk"}))
            _write(d, "Real.cql", _cql("Real"))
            self.assertEqual([r.name for r in FileLibraryProvider([d]).search()], ["Real"])

    def test_search_path_order_decides_which_copy_wins(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write(a, "Shared.cql", _cql("Shared", "1.0.0"))
            _write(b, "Shared.cql", _cql("Shared", "2.0.0"))
            self.assertEqual(FileLibraryProvider([a, b]).resolve(LibraryRef("Shared")).version, "1.0.0")
            self.assertEqual(FileLibraryProvider([b, a]).resolve(LibraryRef("Shared")).version, "2.0.0")

    def test_a_pin_matches_exactly_and_a_pin_nothing_declares_fails_loud(self):
        """The exact-version rule: another version of the same name never stands in for a pin."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Shared-1.0.0.cql", _cql("Shared", "1.0.0"))
            _write(d, "Shared-2.0.0.cql", _cql("Shared", "2.0.0"))
            p = FileLibraryProvider([d])
            self.assertEqual(p.resolve(LibraryRef("Shared", "2.0.0")).version, "2.0.0")
            with self.assertRaises(LibraryResolutionError) as ctx:
                p.resolve(LibraryRef("Shared", "3.0.0"))
            self.assertIn("1.0.0, 2.0.0, not 3.0.0", str(ctx.exception))

    def test_missing_library_fails_loudly_naming_the_search_path(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LibraryResolutionError) as ctx:
                FileLibraryProvider([d]).resolve(LibraryRef("Nope"))
            self.assertIn("Nope", str(ctx.exception))
            self.assertIn(d, str(ctx.exception))

    def test_one_empty_file_does_not_break_the_whole_search_path(self):
        """`_scan` promises never to raise on malformed content. An empty `.cql` carries no body, so
        constructing a `LibrarySource` for it raises — and an escaping exception would take out every
        resolve/search/closure on the path, not just its own entry."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Empty.cql", "")
            _write(d, "Whitespace.cql", "   \n\n  ")
            _write(d, "Real.cql", _cql("Real"))
            p = FileLibraryProvider([d])
            self.assertEqual([r.name for r in p.search()], ["Real"])
            self.assertEqual(p.resolve(LibraryRef("Real")).name, "Real")

    def test_provider_requires_at_least_one_root_and_a_known_layout(self):
        with self.assertRaises(ValueError):
            FileLibraryProvider([])
        with self.assertRaises(ValueError):
            FileLibraryProvider(["/tmp"], layouts=("nonsense",))


class ClosureTests(unittest.TestCase):
    def test_transitive_closure_is_walked_breadth_first(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Root.cql", _cql("Root", includes=[("Mid", "1.0.000")]))
            _write(d, "Mid.cql", _cql("Mid", includes=[("Leaf", "1.0.000")]))
            _write(d, "Leaf.cql", _cql("Leaf"))
            p = FileLibraryProvider([d])
            names = [s.name for s in p.closure(p.resolve(LibraryRef("Root")))]
            self.assertEqual(names, ["Mid", "Leaf"])

    def test_closure_is_cycle_safe(self):
        """Published content does contain mutual includes; a naive walk never terminates."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "A.cql", _cql("A", includes=[("B", "1.0.000")]))
            _write(d, "B.cql", _cql("B", includes=[("A", "1.0.000")]))
            p = FileLibraryProvider([d])
            self.assertEqual([s.name for s in p.closure(p.resolve(LibraryRef("A")))], ["B"])

    def test_fhirhelpers_resolves_from_the_folder_like_any_library(self):
        """LANG-FHIRHELPERS-FROM-CONTENT: the compiler bundles no FHIRHelpers, so the closure carries the
        folder's copy."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Root.cql", _cql("Root", includes=[("FHIRHelpers", "4.0.1")]))
            _write(d, "FHIRHelpers.cql", _cql("FHIRHelpers", "4.0.1"))
            p = FileLibraryProvider([d])
            closure = p.closure(p.resolve(LibraryRef("Root")))
            self.assertEqual([(s.name, s.version) for s in closure], [("FHIRHelpers", "4.0.1")])

    def test_fhirhelpers_version_no_source_provides_fails(self):
        """LANG-FHIRHELPERS-FROM-CONTENT: an include of a FHIRHelpers version the folder does not hold fails,
        naming the version asked for and the ones held, rather than leaving it to a bundled copy."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Root.cql", _cql("Root", includes=[("FHIRHelpers", "4.0.2")]))
            _write(d, "FHIRHelpers.cql", _cql("FHIRHelpers", "4.0.1"))
            p = FileLibraryProvider([d])
            with self.assertRaises(LibraryResolutionError) as ctx:
                p.closure(p.resolve(LibraryRef("Root")))
            self.assertIn("FHIRHelpers|4.0.2", str(ctx.exception))
            self.assertIn("4.0.1", str(ctx.exception))
            self.assertIn("Root", str(ctx.exception))

    def test_sibling_first_scoping_picks_the_measures_own_copy(self):
        """THE scoping rule. Published content ships a copy of each shared library PER MEASURE; a flat
        scan sees several different `CommonHelpers`. Scoping to the root's own directory is what keeps
        each measure's closure unambiguous (the Python peer of `CqlToElmCli`'s per-directory grouping)."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "screeningmeasure/ScreeningMeasure.cql", _cql("ScreeningMeasure", includes=[("CommonHelpers", "2.0.000")]))
            _write(d, "screeningmeasure/CommonHelpers.cql", _cql("CommonHelpers", "2.0.000"))
            _write(d, "clinicalmeasure/ClinicalControlMeasure.cql", _cql("ClinicalControlMeasure", includes=[("CommonHelpers", "3.0.000")]))
            _write(d, "clinicalmeasure/CommonHelpers.cql", _cql("CommonHelpers", "3.0.000"))
            p = FileLibraryProvider([d])
            for measure, want in (("ScreeningMeasure", "2.0.000"), ("ClinicalControlMeasure", "3.0.000")):
                closure = p.closure(p.resolve(LibraryRef(measure)))
                self.assertEqual([s.version for s in closure], [want],
                                 f"{measure} must get its OWN sibling copy of CommonHelpers")

    def test_an_unresolvable_dependency_names_the_root_that_required_it(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Root.cql", _cql("Root", includes=[("Ghost", "1.0.0")]))
            p = FileLibraryProvider([d])
            with self.assertRaises(LibraryResolutionError) as ctx:
                p.closure(p.resolve(LibraryRef("Root")))
            self.assertIn("Ghost", str(ctx.exception))
            self.assertIn("Root", str(ctx.exception))


class RealCorpusTests(unittest.TestCase):
    """Against the conformance corpus the Scala harness runs (`compiler/conformance`), the repository's own
    measures: the check that the layouts and the scoping rules match a real multi-library closure, rather
    than fixtures written to match the implementation (BUILD-LIBRARY-PROVIDER)."""

    CORPUS = (pathlib.Path(__file__).resolve().parents[2]
              / "conformance/src/test/resources/cql/measures")

    def setUp(self):
        self.assertTrue(self.CORPUS.is_dir(), f"conformance corpus not present at {self.CORPUS}")
        self.provider = FileLibraryProvider([str(self.CORPUS)])

    def test_hypertension_measure_includes_its_shared_libraries(self):
        """HypertensionBPControl includes SampleMeasureCommon and FHIRHelpers, both resolved from the corpus
        (LANG-FHIRHELPERS-FROM-CONTENT)."""
        root = self.provider.resolve(LibraryRef("HypertensionBPControl"))
        got = {s.name for s in self.provider.closure(root)}
        self.assertEqual(got, {"SampleMeasureCommon", "FHIRHelpers"},
                         "HypertensionBPControl closure mismatch")

    def test_every_pinned_include_in_the_corpus_resolves(self):
        """Every `include` version pin across the whole corpus resolves to a source declaring exactly that
        name and version."""
        for ref in self.provider.search():
            src = self.provider.resolve(ref)
            for inc in includes_of(src):
                prefer = pathlib.Path(src.origin).parent
                resolved = self.provider.resolve(inc, prefer_dir=prefer)
                self.assertEqual((resolved.name, resolved.version), (inc.name, inc.version),
                                 f"{src.name} -> {inc} resolved to the wrong library")


class CustomProviderTests(unittest.TestCase):
    """BUILD-LIBRARY-PROVIDER: the seam is implementable from OUTSIDE the package."""

    class _InMemoryProvider(LibraryProvider):
        """What a Databricks-side provider looks like: implement `resolve`, inherit the closure walk."""

        def __init__(self, sources):
            self._sources = {s.name: s for s in sources}
            self.resolved = []

        def resolve(self, ref):
            self.resolved.append(ref.name)
            if ref.name not in self._sources:
                raise LibraryResolutionError(f"no library '{ref}' in memory")
            return self._sources[ref.name]

    def test_a_custom_provider_only_has_to_implement_resolve(self):
        p = self._InMemoryProvider([
            LibrarySource(name="Root", cql=_cql("Root", includes=[("Dep", "1.0.0")])),
            LibrarySource(name="Dep", cql=_cql("Dep")),
        ])
        self.assertEqual([s.name for s in p.closure(p.resolve(LibraryRef("Root")))], ["Dep"])
        self.assertEqual(p.resolved, ["Root", "Dep"])

    def test_search_is_optional_and_says_so(self):
        p = self._InMemoryProvider([LibrarySource(name="X", cql=_cql("X"))])
        with self.assertRaises(NotImplementedError):
            p.search()


class InlineClosureTests(unittest.TestCase):
    """BUILD-COMPILE-LINK: an INLINE body (`cql=`, a measure typed into a cell) resolves its `include`
    closure through the mounted provider exactly as `library=` does. Before this it shipped ZERO deps and
    failed at translate with "Could not load source for library <X>"."""

    def test_an_inline_body_ships_its_transitive_closure(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Dep.cql", _cql("Dep", includes=[("Deeper", "1.0.000")]))
            _write(d, "Deeper.cql", _cql("Deeper"))
            lib = load_library(cql=_cql("Root", includes=[("Dep", "1.0.000")]),
                               provider=object(), conf={config.CONF_LIBRARY_PATH: d})
        self.assertEqual([s.name for s in lib.deps], ["Dep", "Deeper"])
        # The inline path stays CQL-only: the root is CQL text and every dep ships as CQL source.
        self.assertTrue(all(s.cql for s in lib.deps))
        self.assertEqual(lib.elm, "")

    def test_a_headerless_snippet_still_binds_with_no_deps(self):
        """No `library` header means no identity and no includes — the scratch-snippet path is unchanged,
        and must not need a library source at all."""
        with tempfile.TemporaryDirectory() as d:
            lib = load_library(cql='define "Two": 2 + 2', provider=object(),
                               conf={config.CONF_LIBRARY_PATH: d})
        self.assertEqual(lib.deps, ())
        self.assertEqual(lib.cql, 'define "Two": 2 + 2')

    def test_an_unresolvable_inline_include_fails_loudly(self):
        """`CORE-NO-SILENT-MISLOWER`: a missing dependency raises rather than compiling short — a plan
        silently missing whole populations is the worst outcome."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LibraryResolutionError) as ctx:
                load_library(cql=_cql("Root", includes=[("Ghost", "1.0.000")]),
                             provider=object(), conf={config.CONF_LIBRARY_PATH: d})
        self.assertIn("Ghost", str(ctx.exception))

    def test_resolve_closure_off_sends_the_inline_root_alone(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Dep.cql", _cql("Dep"))
            lib = load_library(cql=_cql("Root", includes=[("Dep", "1.0.000")]), resolve_closure=False,
                               provider=object(), conf={config.CONF_LIBRARY_PATH: d})
        self.assertEqual(lib.deps, ())


class ResolutionTests(unittest.TestCase):
    def test_library_path_conf_builds_a_file_provider(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write(a, "A.cql", _cql("A"))
            _write(b, "B.cql", _cql("B"))
            p = resolve_library_provider({config.CONF_LIBRARY_PATH: os.pathsep.join([a, b])})
            self.assertEqual(sorted(r.name for r in p.search()), ["A", "B"])

    def test_the_legacy_single_library_source_still_works_as_a_one_entry_path(self):
        """`.library_source(path)` predates the search path; existing config must keep working."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "Solo.cql", _cql("Solo"))
            p = resolve_library_provider({config.CONF_LIBRARY_SOURCE: d})
            self.assertEqual([r.name for r in p.search()], ["Solo"])

    def test_no_source_configured_is_none_not_an_error(self):
        """A session that only binds inline CQL needs no library source at all."""
        self.assertIsNone(resolve_library_provider({}))

    def test_unknown_provider_kind_fails_loudly(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_library_provider({config.CONF_LIBRARY_PROVIDER_KIND: "nonesuch"})
        self.assertIn("cql_databricks.libraries", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
