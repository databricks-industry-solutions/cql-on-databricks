"""Setting resolution (`BUILD-RESOLVE`) + library resolution (stdlib unittest; no pyspark — conf and elm
passed explicitly)."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from cql_databricks.compiler import config
from cql_databricks.compiler.config import ConfigResolutionError, resolve_setting
from cql_databricks.compiler.library import load_library
from cql_databricks.compiler.providers import RemoteProvider


class ResolveSettingTests(unittest.TestCase):
    def test_full_precedence_explicit_conf_env_default(self):
        conf = {"k": "fromconf"}
        # explicit wins over everything
        self.assertEqual(resolve_setting("x", explicit="E", conf=conf, conf_key="k", env="ENVV",
                                         default="def"), "E")
        # conf next
        self.assertEqual(resolve_setting("x", conf=conf, conf_key="k", env="ENVV", default="def"), "fromconf")
        # env next
        with mock.patch.dict(os.environ, {"ENVV": "fromenv"}):
            self.assertEqual(resolve_setting("x", conf={}, conf_key="k", env="ENVV", default="def"), "fromenv")
        # compiled-in default value last
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_setting("x", conf={}, conf_key="k", env="ENVV", default="def"), "def")

    def test_empty_strings_are_unset(self):
        self.assertEqual(resolve_setting("x", explicit="", conf={"k": ""}, conf_key="k", default="def"), "def")

    def test_required_fails_loudly_naming_every_way(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigResolutionError) as cm:
                resolve_setting("CQL context", conf={}, conf_key=config.CONF_CONTEXT,
                                env=config.ENV_CONTEXT, required=True)
        msg = str(cm.exception)
        self.assertIn("CQL context", msg)
        self.assertIn(config.CONF_CONTEXT, msg)
        self.assertIn(config.ENV_CONTEXT, msg)

    def test_not_required_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(resolve_setting("x", conf={}, conf_key="k"))

    def test_resolve_shim_conf_over_env_over_default(self):
        self.assertEqual(config.resolve({"k": "fromconf"}, "k", "ENVV", "def"), "fromconf")
        with mock.patch.dict(os.environ, {"ENVV": "fromenv"}):
            self.assertEqual(config.resolve({}, "k", "ENVV", "def"), "fromenv")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.resolve({}, "k", "ENVV", "def"), "def")
            self.assertIsNone(config.resolve({}, "k"))


class LoadLibraryTests(unittest.TestCase):
    def test_resolves_elm_provider_and_context_from_conf(self):
        conf = {config.CONF_PROVIDER_ENDPOINT: "http://127.0.0.1:8080", config.CONF_CONTEXT: "Unfiltered"}
        lib = load_library("cql/Sample.cql", elm='{"library":{}}', conf=conf)
        self.assertEqual(lib.elm, '{"library":{}}')
        self.assertEqual(lib.context, "Unfiltered")
        self.assertIsInstance(lib.provider, RemoteProvider)
        self.assertEqual(lib.resource, "cql/Sample.cql")

    def test_explicit_provider_injection_and_unset_context(self):
        # LANG-CONTEXT-DEFAULT: NO compiled-in context default — an unset context stays None (sent UNSET),
        # so the compiler applies the spec resolution (declared context → Unfiltered), never a forced Patient.
        marker = object()
        with mock.patch.dict(os.environ, {}, clear=True):
            lib = load_library("cql/Sample.cql", elm="{}", conf={}, provider=marker)
        self.assertIs(lib.provider, marker)
        self.assertIsNone(lib.context)

    def test_context_arg_overrides_conf(self):
        lib = load_library("x", elm="{}", conf={config.CONF_CONTEXT: "Unfiltered"},
                           provider=object(), context="Population")
        self.assertEqual(lib.context, "Population")

    def test_context_from_conf_and_env(self):
        lib = load_library("x", elm="{}", conf={config.CONF_CONTEXT: "Practitioner"}, provider=object())
        self.assertEqual(lib.context, "Practitioner")
        with mock.patch.dict(os.environ, {config.ENV_CONTEXT: "Population"}, clear=True):
            lib = load_library("x", elm="{}", conf={}, provider=object())
        self.assertEqual(lib.context, "Population")

    def test_data_model_defaults_to_fhir_and_is_overridable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            lib = load_library("x", elm="{}", provider=object(), conf={})
            self.assertEqual(lib.overrides[config.OVERRIDE_DATA_MODEL], "FHIR")  # compiled-in default
            lib = load_library("x", elm="{}", provider=object(), conf={}, data_model="OMOP")
            self.assertEqual(lib.overrides[config.OVERRIDE_DATA_MODEL], "OMOP")  # explicit wins
            lib = load_library("x", elm="{}", provider=object(),
                               conf={config.CONF_DATA_MODEL: "QDM"})
            self.assertEqual(lib.overrides[config.OVERRIDE_DATA_MODEL], "QDM")   # conf next

    def test_endpoint_kwarg_is_top_precedence(self):
        lib = load_library("x", elm="{}", conf={config.CONF_PROVIDER_ENDPOINT: "http://conf:1"},
                           endpoint="http://explicit:2")
        self.assertIsInstance(lib.provider, RemoteProvider)
        self.assertEqual(lib.provider._base, "http://explicit:2")

    def test_versions_carried_as_overrides(self):
        lib = load_library("x", elm="{}", provider=object(), conf={}, compiler_version="1.2.3",
                           terminology_version="2024-09")
        self.assertEqual(lib.overrides, {
            config.OVERRIDE_DATA_MODEL: "FHIR",
            config.OVERRIDE_COMPILER_VERSION: "1.2.3",
            config.OVERRIDE_TERMINOLOGY_VERSION: "2024-09",
        })

    def test_data_free_library_carries_only_the_data_model(self):
        # No explicit/conf/env, no spark -> no pin carried. The data model is always carried with its
        # compiled-in FHIR default (an explicit knob, not a discovered one).
        with mock.patch.dict(os.environ, {}, clear=True):
            lib = load_library("x", elm="{}", provider=object(), conf={})
        self.assertEqual(lib.overrides, {config.OVERRIDE_DATA_MODEL: "FHIR"})

    def test_library_source_roots_a_relative_resource(self):
        """`library_source` roots a RELATIVE resource; the sibling `.elm.json` is still preferred."""
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "cql").mkdir()
            (pathlib.Path(d) / "cql" / "Sample.elm.json").write_text('{"library": {}}')
            lib = load_library("cql/Sample.cql", provider=object(), conf={}, library_source=d)
        self.assertEqual(lib.elm, '{"library": {}}')
        self.assertEqual(lib.resource, "cql/Sample.cql")

    def test_missing_resource_names_both_paths_it_looked_for(self):
        """A resolution miss is LOUD and diagnosable (`CORE-NO-SILENT-MISLOWER`): the error names the CQL
        path AND the ELM path, so "which file did it actually want" never needs guessing."""
        with self.assertRaises(FileNotFoundError) as ctx:
            load_library("cql/Nope.cql", provider=object(), conf={}, library_source="/pkg/libs")
        self.assertIn("/pkg/libs/cql/Nope.cql", str(ctx.exception))
        self.assertIn("/pkg/libs/cql/Nope.elm.json", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
