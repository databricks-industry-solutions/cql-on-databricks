"""RUN-ADDRESS-LOCATION — a library's address carries where it is read from: a tagged store or a folder.

Needs `cql_databricks.data_bindings` (shipped by this distribution) and its SDK dependency; skipped where only the
stdlib and this package's source are on the path."""
import unittest
from fnmatch import fnmatch

try:
    from cql_databricks.data_bindings import KNOWLEDGE_TAG, Corpus, Library, TaggedSchema
    from cql_databricks.compiler.libraries import FileLibraryProvider, LibraryFiles, LibraryRef, glob_match
    from cql_databricks.compiler.location import Location
    from cql_databricks.compiler.resolve import ResolutionError, resolve
    from cql_databricks.compiler.folder_content import folder_libraries, folder_library_resource, folder_measure
except ImportError:  # pragma: no cover - the stdlib-only fallback of scripts/test.sh
    Corpus = None

MEASURE = "library Measure version '1.0.0'\ninclude Common version '2.0.0'\ninclude Helpers version '1.0.0'\ndefine X: 1\n"
COMMON = "library Common version '2.0.0'\ninclude Helpers version '1.0.0'\ndefine Y: 1\n"
HELPERS = "library Helpers version '1.0.0'\ndefine Z: 1\n"
OTHER_COMMON = "library Common version '2.0.0'\ninclude Helpers version '1.0.0'\ndefine Y: 2\n"


class _MemoryFiles(LibraryFiles if Corpus is not None else object):
    """A folder tree held in a dict: what a Workspace listing returns, without a workspace."""

    def __init__(self, files):
        self.files = dict(files)

    def is_dir(self, path):
        return any(p.startswith(path.rstrip("/") + "/") for p in self.files)

    def glob(self, root, pattern):
        root = root.rstrip("/")
        return sorted(p for p in self.files if p.startswith(root + "/") and glob_match(p[len(root) + 1:], pattern))

    def read_text(self, path):
        return self.files.get(path)


def _store(schema, position):
    return TaggedSchema(tag=KNOWLEDGE_TAG, name=schema, catalog="k", schema=schema, position=position)


def _library(name, version, source, store):
    return Library(name=name, version=version, url=f"http://x/Library/{name}", fhir_version=version, source=source,
                   description="", title="", kind="library", model="", data_requirements=[], knowledge=store)


class _Content:
    catalog = "k"

    def __init__(self, libraries, stores):
        self._corpus = Corpus(lambda *a: [], list(stores), libraries)

    def corpus(self):
        return self._corpus


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class LocationTests(unittest.TestCase):

    def test_round_trips_its_wire_form(self):
        for text in ("store:k.measures", "folder:/Workspace/Users/a@example.org/CMS165"):
            self.assertEqual(str(Location.parse(text)), text)
        self.assertEqual(str(Location.parse("folder:/Workspace/x/")), "folder:/Workspace/x")

    def test_refuses_what_is_not_a_location(self):
        for text in ("", "k.measures", "store:measures", "folder:relative/path", "table:k.m"):
            with self.assertRaises(ValueError, msg=text):
                Location.parse(text)


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class GlobMatchTests(unittest.TestCase):

    def test_agrees_with_the_layouts_a_local_glob_scans(self):
        cases = [("cql/A-1.0.cql", "*/*.cql", True), ("A.cql", "*.cql", True), ("cql/A.cql", "*.cql", False),
                 ("input/cql/a/b/A.cql", "input/cql/**/*.cql", True), ("input/cql/A.cql", "input/cql/**/*.cql", True),
                 ("resources/library-A.json", "Library-*.json", False), ("Library-A.json", "Library-*.json", True)]
        for path, pattern, want in cases:
            self.assertEqual(glob_match(path, pattern), want, f"{path} vs {pattern}")
        # `*` never crosses a segment, where fnmatch's does.
        self.assertTrue(fnmatch("cql/A.cql", "*.cql"))


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class FileSeamTests(unittest.TestCase):
    """The provider's rules hold over any LibraryFiles, not only the local filesystem."""

    def test_reads_a_package_folder_through_its_file_source(self):
        files = _MemoryFiles({"/W/CMS1/cql/Measure-1.0.0.cql": MEASURE, "/W/CMS1/cql/Helpers-1.0.0.cql": HELPERS,
                              "/W/CMS1/resources/measure-Measure-1.0.0.json": "{}"})
        provider = FileLibraryProvider(["/W/CMS1"], files=files)
        root = provider.resolve(LibraryRef("Measure", "1.0.0"))
        self.assertEqual(root.origin, "/W/CMS1/cql/Measure-1.0.0.cql")
        self.assertEqual(provider.home_of(root), "/W/CMS1/cql")

    def test_resolves_sibling_first_over_another_folders_copy(self):
        files = _MemoryFiles({"/W/a/Common-2.0.0.cql": OTHER_COMMON, "/W/b/Common-2.0.0.cql": COMMON})
        provider = FileLibraryProvider(["/W"], layouts=("nested",), files=files)
        self.assertEqual(provider.resolve(LibraryRef("Common"), prefer_dir="/W/b").cql, COMMON)
        self.assertEqual(provider.resolve(LibraryRef("Common"), prefer_dir="/W/a").cql, OTHER_COMMON)


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class ResolveAtLocationTests(unittest.TestCase):

    def setUp(self):
        self.first, self.second = _store("first", 0), _store("second", 1)
        self.content = _Content([
            _library("Common", "2.0.0", OTHER_COMMON, self.first),
            _library("Helpers", "1.0.0", HELPERS, self.first),
            _library("Measure", "1.0.0", MEASURE, self.second),
            _library("Common", "2.0.0", COMMON, self.second),
        ], [self.first, self.second])

    def test_no_location_is_all_of_tagged_knowledge_in_scope_order(self):
        r = resolve(self.content, library="Measure", version="1.0.0")
        self.assertIsNone(r.location)
        self.assertIn(OTHER_COMMON, r.include_sources)   # the earlier scope's copy

    def test_a_store_location_prefers_its_own_copy_of_an_include(self):
        r = resolve(self.content, library="Measure", version="1.0.0", location="store:k.second")
        self.assertEqual(str(r.location), "store:k.second")
        self.assertEqual(r.include_sources, [HELPERS, COMMON])   # its own Common; Helpers from the other store

    def test_a_store_location_reads_the_root_only_from_that_store(self):
        with self.assertRaisesRegex(ResolutionError, "Measure 1.0.0 is not in the store k.first"):
            resolve(self.content, library="Measure", version="1.0.0", location=Location("store", "k.first"))

    def test_a_folder_location_resolves_the_folder_first_then_knowledge(self):
        files = _MemoryFiles({"/W/CMS1/cql/Measure-1.0.0.cql": MEASURE, "/W/CMS1/cql/Common-2.0.0.cql": COMMON})
        r = resolve(self.content, library="Measure", version="1.0.0", location="folder:/W/CMS1", files=files)
        self.assertEqual((r.name, r.version, r.source), ("Measure", "1.0.0", MEASURE))
        self.assertEqual(r.include_sources, [HELPERS, COMMON])   # Helpers from knowledge, Common from the folder
        self.assertEqual([i.id.name for i in r.includes], ["Helpers", "Common"])   # dependencies first

    def test_cql_text_at_a_folder_resolves_its_includes_there(self):
        files = _MemoryFiles({"/W/CMS1/cql/Common-2.0.0.cql": COMMON, "/W/CMS1/cql/Helpers-1.0.0.cql": HELPERS})
        r = resolve(self.content, cql=MEASURE, location="folder:/W/CMS1", files=files)
        self.assertEqual(r.include_sources, [HELPERS, COMMON])

    def test_a_folder_reports_every_missing_include_naming_the_folder(self):
        lonely = _Content([], [self.first])
        files = _MemoryFiles({"/W/CMS1/cql/Measure-1.0.0.cql": MEASURE})
        r = resolve(lonely, library="Measure", location="folder:/W/CMS1", files=files)
        self.assertEqual(sorted(u.name for u in r.unresolved), ["Common", "Helpers"])
        self.assertIn("is not in the folder /W/CMS1 or the environment's knowledge", r.unresolved_messages()[0])
        with self.assertRaisesRegex(ResolutionError, "includes not in the folder /W/CMS1"):
            r.require_complete()

    def test_a_folder_location_needs_a_file_source(self):
        with self.assertRaisesRegex(ResolutionError, "needs a file source"):
            resolve(self.content, library="Measure", location="folder:/W/CMS1")


def _measure(name, version, title):
    import json
    return json.dumps({"resourceType": "Measure", "title": title, "version": version,
                       "library": [f"https://example.org/Library/{name}"]})


@unittest.skipIf(Corpus is None, "cql_databricks.data_bindings is not importable")
class FolderContentTests(unittest.TestCase):

    def test_reads_a_package_folder_as_the_package_lays_it_out(self):
        import json
        files = _MemoryFiles({
            "/W/CMS1/cql/Measure-1.0.0.cql": MEASURE, "/W/CMS1/cql/Helpers-1.0.0.cql": HELPERS,
            "/W/CMS1/resources/measure-Measure-1.0.0.json": _measure("Measure", "1.0.0", "A measure"),
            "/W/CMS1/resources/library-Measure-1.0.0.json": json.dumps(
                {"resourceType": "Library", "name": "Measure", "version": "1.0.0"}),
            "/W/CMS1/resources/measure-Measure-1.0.0.xml": "<Measure/>",
        })
        found = {f.name: f for f in folder_libraries(files, "/W/CMS1")}
        self.assertEqual(found["Measure"].kind, "measure")
        self.assertEqual(found["Measure"].title, "A measure")
        self.assertEqual(found["Measure"].measure_path, "/W/CMS1/resources/measure-Measure-1.0.0.json")
        self.assertEqual(found["Helpers"].kind, "library")
        path, measure = folder_measure(files, "/W/CMS1", "Measure", "1.0.0")
        self.assertEqual(measure["title"], "A measure")
        self.assertEqual(folder_library_resource(files, "/W/CMS1", "Measure", "1.0.0")[0],
                         "/W/CMS1/resources/library-Measure-1.0.0.json")

    def test_reads_a_flat_folder_with_the_measure_beside_its_cql(self):
        files = _MemoryFiles({"/W/ex/Measure.cql": MEASURE,
                              "/W/ex/Measure.measure.json": _measure("Measure", "1.0.0", "Flat")})
        (only,) = folder_libraries(files, "/W/ex")
        self.assertEqual((only.name, only.kind, only.title), ("Measure", "measure", "Flat"))

    def test_a_measure_at_the_libraries_version_wins_over_another_version(self):
        files = _MemoryFiles({"/W/m/cql/Measure-1.0.0.cql": MEASURE,
                              "/W/m/resources/measure-Measure-0.9.0.json": _measure("Measure", "0.9.0", "Old"),
                              "/W/m/resources/measure-Measure-1.0.0.json": _measure("Measure", "1.0.0", "Current")})
        self.assertEqual(folder_measure(files, "/W/m", "Measure", "1.0.0")[1]["title"], "Current")
        self.assertIsNone(folder_measure(files, "/W/m", "Helpers", "1.0.0"))


if __name__ == "__main__":
    unittest.main()
