"""L1 unit tests for the notebook session as a library source (SVC-NOTEBOOK-CELL-LIBRARY).

Real files for the folder tier and real CQL text for the cell tier — the point of these tests is the
LOOKUP ORDER and the closure walk, so nothing about resolution is mocked. The compile provider is, since
none of this reaches a service.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from cql_databricks.compiler.libraries import (FileLibraryProvider, LibraryRef, LibraryResolutionError,
                                        LibrarySource)

import cql_databricks.compiler.zeroconfig as client_mod
import cql_databricks.compiler.session_libraries as session_mod
from cql_databricks.compiler import connect
from cql_databricks.compiler.session_libraries import SessionLibraryProvider

UDF_SCHEMA = "acme.runtime"

COMMON = ("library DiabetesCommon version '1.0.0'\n"
          "using FHIR version '4.0.1'\n"
          "context Patient\n"
          "define \"Is Adult\": AgeInYears() >= 18\n")

MEASURE = ("library DiabetesCareGap version '1.0.0'\n"
           "using FHIR version '4.0.1'\n"
           "include DiabetesCommon version '1.0.0' called Common\n"
           "context Patient\n"
           "define \"Denominator\": Common.\"Is Adult\"\n")


class _Frame:
    def __init__(self, spark, rows):
        self._spark, self._rows = spark, list(rows)

    def createOrReplaceTempView(self, name):
        self._spark.views[name] = self._rows

    def collect(self):
        return [dict(zip(("kind", "name", "payload"), r)) for r in self._rows]


class _SessionSpark:
    """Just enough of a Spark session to hold the session record (CLIENT-SESSION-STATE)."""

    def __init__(self):
        self.views = {}
        self.catalog = mock.Mock()
        self.catalog.dropTempView.side_effect = lambda name: self.views.pop(name, None) is not None

    def table(self, name):
        if name not in self.views:
            raise RuntimeError(f"[TABLE_OR_VIEW_NOT_FOUND] The table or view `{name}` cannot be found.")
        return _Frame(self, self.views[name])

    def createDataFrame(self, rows, schema):
        return _Frame(self, rows)


class _StubProvider:
    """A stand-in for the mounted folder, so a lookup-order test needs no filesystem."""

    def resolve(self, ref: LibraryRef) -> LibrarySource:
        return LibrarySource(name=ref.name, version="1.0.0", cql=COMMON, origin="/published/copy.cql")

    def closure(self, root: LibrarySource) -> list[LibrarySource]:
        return []

    def search(self, pattern=None) -> list[LibraryRef]:
        return []


class RegistrationTest(unittest.TestCase):
    """A cell's identity is what its `library` header declares."""

    def setUp(self):
        self.provider = SessionLibraryProvider()

    def test_a_cell_is_registered_under_its_declared_identity(self):
        source = self.provider.register(COMMON)

        self.assertEqual((source.name, source.version), ("DiabetesCommon", "1.0.0"))
        self.assertEqual(self.provider.resolve(LibraryRef("DiabetesCommon")).cql, COMMON)

    def test_a_headerless_snippet_declares_nothing(self):
        """`%%cql` cells are often one-liners; a snippet with no header is not a library and must not
        become one under some invented name."""
        self.assertIsNone(self.provider.register('define "Two": 2 + 2'))
        self.assertEqual(self.provider.declared(), [])

    def test_re_running_a_cell_replaces_the_definition(self):
        """The session is the source of truth here — an edited cell must be what the next compile sees."""
        self.provider.register(COMMON)
        self.provider.register(COMMON.replace("18", "21"))

        self.assertIn("21", self.provider.resolve(LibraryRef("DiabetesCommon")).cql)
        self.assertEqual([str(r) for r in self.provider.declared()], ["DiabetesCommon|1.0.0"])

    def test_a_version_pin_is_a_hint_not_a_miss(self):
        """A cell being drafted is the only definition of that name in the session; failing the compile
        over a stale pin in the including cell would be a paper cut with no upside."""
        self.provider.register(COMMON)
        self.assertEqual(self.provider.resolve(LibraryRef("DiabetesCommon", "9.9.9")).name,
                         "DiabetesCommon")

    def test_a_draft_source_is_readable_for_publishing(self):
        """Publishing a draft is writing its own text to a file — the notebook needs the text back."""
        self.provider.register(COMMON)
        self.assertEqual(self.provider.source("DiabetesCommon").cql, COMMON)
        self.assertIsNone(self.provider.source("NeverDeclared"))

    def test_clear_hands_the_names_back_to_the_folder(self):
        provider = SessionLibraryProvider(_StubProvider())
        provider.register(COMMON)
        self.assertIsNone(provider.resolve(LibraryRef("DiabetesCommon")).origin)

        provider.clear()

        self.assertEqual(provider.declared(), [])
        self.assertEqual(provider.resolve(LibraryRef("DiabetesCommon")).origin, "/published/copy.cql")

    def test_an_unresolvable_name_says_what_the_session_does_have(self):
        self.provider.register(COMMON)
        with self.assertRaises(LibraryResolutionError) as caught:
            self.provider.resolve(LibraryRef("NotDeclared"))
        self.assertIn("DiabetesCommon", str(caught.exception))


class ClosureTest(unittest.TestCase):
    """The include walk across the two roots."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, name: str, text: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_a_cell_includes_another_cell(self):
        provider = SessionLibraryProvider()
        provider.register(COMMON)
        root = provider.register(MEASURE)

        self.assertEqual([d.name for d in provider.closure(root)], ["DiabetesCommon"])

    def test_a_cell_includes_a_library_from_the_mounted_folder(self):
        self._write("DiabetesCommon-1.0.0.cql", COMMON)
        provider = SessionLibraryProvider(FileLibraryProvider([self.dir]))
        root = provider.register(MEASURE)

        deps = provider.closure(root)
        self.assertEqual([d.name for d in deps], ["DiabetesCommon"])
        self.assertTrue(deps[0].origin.endswith("DiabetesCommon-1.0.0.cql"))

    def test_a_cell_shadows_a_published_library_of_the_same_name(self):
        """Cells are the first root: drafting an override against a vendored content repo is the point."""
        self._write("DiabetesCommon-1.0.0.cql", COMMON.replace("18", "65"))
        provider = SessionLibraryProvider(FileLibraryProvider([self.dir]))
        provider.register(COMMON)
        root = provider.register(MEASURE)

        deps = provider.closure(root)
        self.assertIn("18", deps[0].cql)
        self.assertIsNone(deps[0].origin)

    def test_a_transitive_folder_dependency_keeps_its_sibling_first_scoping(self):
        """A file-backed dependency's own closure is the fallthrough's walk, so a content repo's
        per-measure copy of a shared library wins over another measure's copy."""
        os.makedirs(os.path.join(self.dir, "measure"))
        os.makedirs(os.path.join(self.dir, "other"))
        shared = "library Shared version '1.0.0'\ndefine \"S\": {}\n"
        with open(os.path.join(self.dir, "other", "Shared-1.0.0.cql"), "w") as f:
            f.write(shared.format("'other copy'"))
        with open(os.path.join(self.dir, "measure", "Shared-1.0.0.cql"), "w") as f:
            f.write(shared.format("'the measure own copy'"))
        with open(os.path.join(self.dir, "measure", "Published-1.0.0.cql"), "w") as f:
            f.write("library Published version '1.0.0'\n"
                    "include Shared version '1.0.0' called S\n"
                    "define \"P\": 1\n")

        provider = SessionLibraryProvider(FileLibraryProvider([os.path.join(self.dir, "other"),
                                                               os.path.join(self.dir, "measure")]))
        root = provider.register("library Cell version '1.0.0'\n"
                                 "include Published version '1.0.0' called P\n"
                                 "define \"C\": 1\n")

        deps = {d.name: d for d in provider.closure(root)}
        self.assertEqual(sorted(deps), ["Published", "Shared"])
        self.assertIn("the measure own copy", deps["Shared"].cql)

    def test_a_folder_root_is_walked_entirely_by_the_folder_provider(self):
        """A root carrying an origin came from a directory; its closure is the fallthrough's, unchanged."""
        self._write("DiabetesCommon-1.0.0.cql", COMMON)
        path = self._write("DiabetesCareGap-1.0.0.cql", MEASURE)
        provider = SessionLibraryProvider(FileLibraryProvider([self.dir]))

        root = LibrarySource(name="DiabetesCareGap", version="1.0.0", cql=MEASURE, origin=path)
        self.assertEqual([d.name for d in provider.closure(root)], ["DiabetesCommon"])

    def test_fhirhelpers_resolves_from_a_cell_like_any_library(self):
        """LANG-FHIRHELPERS-FROM-CONTENT: the compiler bundles no FHIRHelpers; the session supplies it."""
        provider = SessionLibraryProvider()
        provider.register("library FHIRHelpers version '4.0.1'\ndefine \"H\": 1\n")
        root = provider.register("library Cell version '1.0.0'\n"
                                 "include FHIRHelpers version '4.0.1' called FHIRHelpers\n"
                                 "define \"C\": 1\n")
        self.assertEqual([d.name for d in provider.closure(root)], ["FHIRHelpers"])

    def test_fhirhelpers_no_cell_or_folder_provides_fails(self):
        """LANG-FHIRHELPERS-FROM-CONTENT: an include of FHIRHelpers nothing in the session provides fails."""
        provider = SessionLibraryProvider()
        root = provider.register("library Cell version '1.0.0'\n"
                                 "include FHIRHelpers version '4.0.1' called FHIRHelpers\n"
                                 "define \"C\": 1\n")
        with self.assertRaises(LibraryResolutionError) as ctx:
            provider.closure(root)
        self.assertIn("FHIRHelpers|4.0.1", str(ctx.exception))

    def test_mutual_includes_terminate(self):
        provider = SessionLibraryProvider()
        provider.register("library A version '1.0.0'\ninclude B version '1.0.0' called B\n"
                          "define \"A\": 1\n")
        root = provider.register("library B version '1.0.0'\ninclude A version '1.0.0' called A\n"
                                 "define \"B\": 1\n")
        self.assertEqual([d.name for d in provider.closure(root)], ["A"])

    def test_search_lists_cells_ahead_of_the_folder(self):
        self._write("Published-1.0.0.cql", "library Published version '1.0.0'\ndefine \"P\": 1\n")
        provider = SessionLibraryProvider(FileLibraryProvider([self.dir]))
        provider.register(COMMON)

        self.assertEqual([r.name for r in provider.search()], ["DiabetesCommon", "Published"])
        self.assertEqual([r.name for r in provider.search("Diab*")], ["DiabetesCommon"])


class ConnectWiringTest(unittest.TestCase):
    """SVC-NOTEBOOK-CELL-LIBRARY end to end through connect(): a cell declared in one client is
    includable from another, because the notebook — not the client — owns the session."""

    def setUp(self):
        client_mod._default_client = None
        self.spark = _SessionSpark()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def tearDown(self):
        client_mod._default_client = None

    def _connect(self, **kw):
        ws = mock.Mock()
        ws.app_url.return_value = "https://cql-databricks-compiler-123.aws.databricksapps.com"
        ws.workspace_host.return_value = "https://myworkspace.cloud.databricks.com"
        ws.app_oauth_client_id.return_value = "client-abc"
        ws.ambient_subject_token.return_value = "subj-token-xyz"
        with mock.patch("cql_databricks.compiler.provider._workspace", ws):
            return connect(self.spark, udf_schema=UDF_SCHEMA, **kw)

    def test_a_measure_written_across_cells_ships_its_closure(self):
        session_mod.register_cell(COMMON, spark=self.spark)
        client = self._connect()

        query = client.cql(MEASURE)

        self.assertEqual([d.name for d in query._lib.deps], ["DiabetesCommon"])
        self.assertIn("Is Adult", query._lib.deps[0].cql)

    def test_a_cell_library_runs_by_name(self):
        session_mod.register_cell(COMMON, spark=self.spark)
        client = self._connect()

        query = client.cql(library="DiabetesCommon")

        self.assertIn("library DiabetesCommon", query._lib.cql)

    def test_cells_and_a_mounted_folder_compose(self):
        with open(os.path.join(self.dir, "DiabetesCommon-1.0.0.cql"), "w") as f:
            f.write(COMMON)
        client = self._connect(library_path=self.dir)

        # nothing declared yet -> the folder answers
        self.assertIsNotNone(client.cql(MEASURE)._lib.deps[0].origin)
        session_mod.register_cell(COMMON.replace("18", "21"), spark=self.spark)
        # declared -> the cell shadows it
        self.assertIn("21", client.cql(MEASURE)._lib.deps[0].cql)

    def test_a_second_client_does_not_unmount_the_first_ones_folder(self):
        """Cells are shared session state; the mounted folder is per-client config. The magic builds its
        own client mid-notebook, and that must not change what an already-held client resolves against."""
        with open(os.path.join(self.dir, "Published-1.0.0.cql"), "w") as f:
            f.write("library Published version '1.0.0'\ndefine \"P\": 1\n")
        mounted = self._connect(library_path=self.dir)

        self._connect()  # what `%%cql` does: a second client, no library_path

        self.assertIn("library Published", mounted.cql(library="Published")._lib.cql)

    def test_the_session_outlives_any_one_client(self):
        """The magic builds its own client; a cell declared before it existed must still be visible."""
        session_mod.register_cell(COMMON, spark=self.spark)
        first = self._connect()
        second = self._connect(library_path=self.dir)

        for client in (first, second):
            self.assertEqual([d.name for d in client.cql(MEASURE)._lib.deps], ["DiabetesCommon"])


if __name__ == "__main__":
    unittest.main()
