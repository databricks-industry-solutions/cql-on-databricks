"""L1 unit tests for the zero-config convenience surface (connect / cql, SVC-ZERO-CONFIG-NOTEBOOK). The
`_workspace` seams are monkeypatched, so this drives the wiring — a discovering CqlClient built directly
from make_provider, and the lazy default client behind module-level cql — with no Databricks SDK or
workspace. Needs cql-databricks on the path.

`connect()` also resolves the `cql_*` UDF schema from UC (SVC-NOTEBOOK-RUNTIME-BIND), which needs a Spark
session; these tests supply the `udf_schema` override instead, so they stay Spark-free.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.compiler import config, connect, cql, resolve_libraries

UDF_SCHEMA = "acme.runtime"


def _ws(**over):
    """A fake _workspace with every seam resolving (override per test)."""
    m = mock.Mock()
    m.app_url.return_value = "https://cql-databricks-compiler-123.aws.databricksapps.com"
    m.workspace_host.return_value = "https://myworkspace.cloud.databricks.com"
    m.app_oauth_client_id.return_value = "client-abc"
    m.ambient_subject_token.return_value = "subj-token-xyz"
    for k, v in over.items():
        getattr(m, k).return_value = v
    return m


class ConnectTest(unittest.TestCase):
    def setUp(self):
        client_mod._default_client = None  # isolate the lazy default across tests

    tearDown = setUp

    def test_connect_builds_a_discovering_client(self):
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()):
            c = connect(udf_schema=UDF_SCHEMA)
        from cql_databricks.compiler import CqlClient, RemoteProvider
        self.assertIsInstance(c, CqlClient)
        # endpoint discovered from the App registry; OAuth token-exchange wired (no manual provider.kind)
        self.assertIsInstance(c._provider, RemoteProvider)
        self.assertEqual(c._provider._base, "https://cql-databricks-compiler-123.aws.databricksapps.com")

    def test_connect_wraps_the_provider_in_the_notebook_compile_shim(self):
        """SVC-NOTEBOOK-RUNTIME-BIND — every notebook run goes through the shim, so barriers are
        serverless-safe without the notebook asking."""
        from cql_databricks.compiler.compile_shim import CqlDatabricksCompileProvider
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()):
            c = connect(udf_schema=UDF_SCHEMA)
        self.assertIsInstance(c._provider, CqlDatabricksCompileProvider)

    def test_udf_schema_is_carried_as_client_config_not_a_body_patch(self):
        """EMIT-UDF-QUALIFY — the OSS client owns this knob now (`.udf_schema()`), so the qualification
        must reach the compiler as ordinary session config rather than a post-serialization body edit."""
        from cql_databricks.compiler import config
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()):
            c = connect(udf_schema=UDF_SCHEMA)
        self.assertEqual(c._conf[config.CONF_UDF_SCHEMA], UDF_SCHEMA)

    def test_connect_discovers_the_udf_schema_when_not_given(self):
        from cql_databricks.compiler import config
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
                mock.patch("cql_databricks.compiler.zeroconfig.discover_udf_schema",
                           return_value="acme.rt") as disc:
            c = connect(spark="a-session")
        disc.assert_called_once_with("a-session")
        self.assertEqual(c._conf[config.CONF_UDF_SCHEMA], "acme.rt")

    def test_app_name_override_flows_to_discovery(self):
        ws = _ws()
        with mock.patch("cql_databricks.compiler.provider._workspace", ws):
            connect(app_name="custom-compiler", udf_schema=UDF_SCHEMA)
        ws.app_url.assert_called_once_with("custom-compiler")

    def test_cql_binds_via_a_lazy_default_client(self):
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
                mock.patch("cql_databricks.compiler.zeroconfig.discover_udf_schema", return_value=UDF_SCHEMA):
            q = cql("library T version '1'\ndefine \"Two\": 2 + 2")
            q2 = cql("library T version '1'\ndefine \"Three\": 3")
        from cql_databricks.compiler import CqlQuery
        self.assertIsInstance(q, CqlQuery)
        # the default client is built ONCE and reused
        self.assertIs(q._lib.provider, q2._lib.provider)


class LibraryFolderTest(unittest.TestCase):
    """SVC-NOTEBOOK-LIBRARY-FOLDER — a mounted directory of CQL (a Databricks Git Folder, a UC Volume) is
    the session's library source, so a published measure runs BY NAME and its `include` closure ships with
    the compile (`BUILD-LIBRARY-PROVIDER`, `BUILD-COMPILE-LINK`). Real files, no mocked provider."""

    def setUp(self):
        client_mod._default_client = None
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, name: str, text: str) -> None:
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def _connect(self, **kw):
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()):
            return connect(udf_schema=UDF_SCHEMA, **kw)

    def test_a_measure_in_the_folder_resolves_by_name_with_its_closure(self):
        self._write("Helpers-1.0.0.cql", "library Helpers version '1.0.0'\ndefine \"H\": 1\n")
        self._write("Root-1.0.0.cql",
                    "library Root version '1.0.0'\n"
                    "include Helpers version '1.0.0' called H\n"
                    "define \"R\": 2\n")

        q = self._connect(library_path=self.dir).cql(library="Root")

        self.assertIn("library Root", q._lib.cql)
        self.assertEqual([d.name for d in q._lib.deps], ["Helpers"])

    def test_a_bare_string_path_is_one_root_not_a_character_sequence(self):
        from cql_databricks.compiler import config
        c = self._connect(library_path=self.dir)
        self.assertEqual(c._conf[config.CONF_LIBRARY_PATH], self.dir)

    def test_several_roots_keep_their_order(self):
        from cql_databricks.compiler import config
        c = self._connect(library_path=[self.dir, "/opt/vendored-content"])
        self.assertEqual(c._conf[config.CONF_LIBRARY_PATH],
                         os.pathsep.join([self.dir, "/opt/vendored-content"]))

    def test_the_env_tier_mounts_the_folder_without_a_client_being_handed_one(self):
        """The `%%cql` magic and the module-level `cql()` build their own client, so the folder has to be
        reachable through config — `BUILD-RESOLVE`'s env tier — not only through a connect() keyword."""
        self._write("Solo-1.0.0.cql", "library Solo version '1.0.0'\ndefine \"S\": 1\n")
        with mock.patch.dict(os.environ, {"CQL_DATABRICKS_LIBRARY_PATH": self.dir}):
            q = self._connect().cql(library="Solo")
        self.assertIn("library Solo", q._lib.cql)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return [mock.Mock(asDict=mock.Mock(return_value=r)) for r in self._rows]


class _KnowledgeSpark:
    """A Spark session in one environment, catalog ``k``, answering its discovery and the corpus load
    (`CLIENT-ENVIRONMENT`, `KNOWLEDGE-LAKE-DIRECT`)."""

    def __init__(self, libraries):
        self.libraries = libraries
        self.statements = []
        self.conf = mock.Mock(get=lambda key, default=None: default)

    def sql(self, statement, args=None):
        self.statements.append(statement)
        if "information_schema.catalog_tags" in statement:
            return _Rows([{"catalog": "k", "tag": tag, "value": value, "comment": None}
                          for tag, value in (("cql_databricks_environment", "env"),
                                             ("cql_databricks_environment_version", "3"),
                                             ("cql_databricks_environment_default", "true"))])
        if "measure_url" in statement:
            assert "`k`.`knowledge`.`measure`" in statement
            return _Rows([])
        if "content_cql" in statement:
            assert "`k`.`knowledge`.`library`" in statement
            return _Rows([{"url": f"http://x/Library/{n}", "version": v, "title": None, "description": None,
                           "data_requirement": None, "content_cql": cql} for n, v, cql in self.libraries])
        raise AssertionError(f"unexpected statement: {statement}")

    def table(self, name):
        # CLIENT-SESSION-STATE: this session has declared no cells and mounted nothing.
        raise RuntimeError(f"[TABLE_OR_VIEW_NOT_FOUND] The table or view `{name}` cannot be found.")


class ResolveLibrariesTest(unittest.TestCase):
    """KNOWLEDGE-RESOLVE-API, BUILD-RESOLVE-COMPILE — the notebook resolves its include closure in its own session from
    tagged knowledge, as its user, and returns the closure's sources for `deps`."""

    EXCLUSIONS = "library Exclusions version '6.18'\ninclude StatusHelpers version '1.0'\n"
    STATUS = "library StatusHelpers version '1.0'\n"

    def test_resolves_closure_sources_from_the_environments_knowledge_in_this_session(self):
        """CLIENT-ENVIRONMENT, BIND-PUBLISHED-VIEW: the default environment, found by its catalog tags; its knowledge
        views read with no layout check."""
        spark = _KnowledgeSpark([("Exclusions", "6.18", self.EXCLUSIONS), ("StatusHelpers", "1.0", self.STATUS)])
        sources = resolve_libraries('include "Exclusions" version \'6.18\'', spark=spark)
        self.assertEqual(sources, [self.STATUS, self.EXCLUSIONS])   # dependencies first
        self.assertTrue(any("catalog_tags" in s for s in spark.statements))
        self.assertFalse(any("schema_tags" in s or "information_schema.columns" in s for s in spark.statements))

    def test_unresolved_include_is_loud(self):
        spark = _KnowledgeSpark([("StatusHelpers", "1.0", self.STATUS)])
        with self.assertRaises(RuntimeError) as ctx:
            resolve_libraries('include "Missing" version \'1.0.0\'', spark=spark)
        self.assertIn("Missing", str(ctx.exception))

    def test_connect_resolves_an_include_from_the_environments_knowledge(self):
        """CLIENT-KNOWLEDGE-INCLUDES — with no cell and no folder, an inline include resolves from the environment's
        knowledge, read once through this session."""
        from cql_databricks.compiler.libraries import LibrarySource
        spark = _KnowledgeSpark([("Exclusions", "6.18", self.EXCLUSIONS), ("StatusHelpers", "1.0", self.STATUS)])
        provider = client_mod._library_provider([], spark, client_mod._environment(spark, None))
        draft = LibrarySource("Draft", "1", cql="library Draft version '1'\ninclude Exclusions version '6.18'\n")
        self.assertEqual([s.name for s in provider.closure(draft)], ["Exclusions", "StatusHelpers"])
        self.assertEqual(sum("catalog_tags" in s for s in spark.statements), 1)

    def test_the_designer_app_is_no_longer_asked(self):
        with self.assertRaisesRegex(TypeError, "no longer calls the Designer"):
            resolve_libraries("library X version '1'", spark=_KnowledgeSpark([]), bff_app_name="cql-databricks-designer")


if __name__ == "__main__":
    unittest.main()
