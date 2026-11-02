"""Unit tests for providers + credentials + resolution (stdlib unittest; requests is mocked, so no
network and no pyspark needed). BUILD-CLIENT."""
import unittest
from unittest import mock

from cql_databricks.compiler import config, providers
from cql_databricks.compiler.providers import (
    CompileError, CompileRequest, NoAuth, OAuth2TokenExchange, RemoteProvider, StaticBearer,
    resolve_credentials, resolve_provider,
)


def _resp(status=200, json_body=None, text=""):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    r.json.return_value = json_body if json_body is not None else {}
    r.raise_for_status = mock.Mock()
    return r


class CredentialTests(unittest.TestCase):
    def test_no_auth_and_static_bearer(self):
        self.assertEqual(NoAuth().auth_headers(), {})
        self.assertEqual(StaticBearer("tok").auth_headers(), {"Authorization": "Bearer tok"})
        with self.assertRaises(ValueError):
            StaticBearer("")

    def test_oauth2_token_exchange_is_rfc8693_and_cached(self):
        cred = OAuth2TokenExchange(token_url="https://host/oidc/v1/token",
                                   subject_token="subj", audience="app-client-id")
        with mock.patch("requests.post", return_value=_resp(json_body={"access_token": "AT"})) as post:
            self.assertEqual(cred.auth_headers(), {"Authorization": "Bearer AT"})
            self.assertEqual(cred.auth_headers(), {"Authorization": "Bearer AT"})  # cached
        post.assert_called_once()
        _, kwargs = post.call_args
        data = kwargs["data"]
        self.assertEqual(data["grant_type"], "urn:ietf:params:oauth:grant-type:token-exchange")
        self.assertEqual(data["subject_token"], "subj")
        self.assertEqual(data["audience"], "app-client-id")
        self.assertEqual(data["requested_token_type"], "urn:ietf:params:oauth:token-type:access_token")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 30)

    def test_oauth_failure_reports_only_safe_error_code(self):
        """SVC-DISCOVERY-PLUGIN — token errors must be diagnosable without exposing response secrets."""
        cred = OAuth2TokenExchange("http://localhost/token", "synthetic-secret", "synthetic-app")
        for error_code, expected in [("invalid_scope", "invalid_scope"), ("synthetic-secret", "unrecognized OAuth error")]:
            response = _resp(status=400, json_body={"error": error_code, "error_description": "synthetic-secret"})
            with mock.patch("requests.post", return_value=response):
                with self.assertRaisesRegex(CompileError, expected) as error:
                    cred.auth_headers()
            self.assertNotIn("synthetic-secret", str(error.exception))
            self.assertIsNone(cred._cached)

    def _clock(self):
        """A fake ``time`` clock: ``sleep`` advances ``monotonic``, and records each wait."""
        now, waits = [0.0], []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        return mock.patch("time.monotonic", lambda: now[0]), mock.patch("time.sleep", sleep), waits

    def test_retryable_error_is_retried_until_the_exchange_succeeds(self):
        """SVC-EXCHANGE-RETRY — an opted-in error is retried with backoff, logged by code only, and the
        caller gets the token."""
        cred = OAuth2TokenExchange("http://localhost/token", "synthetic-secret", "synthetic-app",
                                   retry_errors=("invalid_grant",), retry_seconds=180)
        failed = _resp(status=400, json_body={"error": "invalid_grant", "error_description": "synthetic-secret"})
        monotonic, sleep, waits = self._clock()
        with monotonic, sleep, mock.patch("requests.post",
                                          side_effect=[failed, failed, _resp(json_body={"access_token": "AT"})]) as post:
            with self.assertLogs("cql_databricks.compiler.providers", "WARNING") as logs:
                self.assertEqual(cred.auth_headers(), {"Authorization": "Bearer AT"})
        self.assertEqual(post.call_count, 3)
        self.assertEqual(waits, [5.0, 10.0])
        self.assertEqual(len(logs.output), 2)
        self.assertIn("invalid_grant", logs.output[0])
        self.assertNotIn("synthetic-secret", "".join(logs.output))

    def test_retryable_error_fails_loud_once_the_window_is_spent(self):
        """SVC-EXCHANGE-RETRY — no wait starts past the window; the last failure raises the usual error."""
        cred = OAuth2TokenExchange("http://localhost/token", "s", "a",
                                   retry_errors=("invalid_grant",), retry_seconds=60)
        monotonic, sleep, waits = self._clock()
        with monotonic, sleep, mock.patch("requests.post",
                                          return_value=_resp(status=400, json_body={"error": "invalid_grant"})):
            with self.assertLogs("cql_databricks.compiler.providers", "WARNING"):
                with self.assertRaisesRegex(CompileError, r"OAuth token exchange failed \(400, invalid_grant\)"):
                    cred.auth_headers()
        self.assertEqual(waits, [5.0, 10.0, 20.0])  # the next wait (30 s) would end past 60 s
        self.assertIsNone(cred._cached)

    def test_errors_are_not_retried_unless_opted_in(self):
        """SVC-EXCHANGE-RETRY — off by default, and an opted-in exchange still fails at once on other errors."""
        for cred, code in [(OAuth2TokenExchange("http://localhost/token", "s", "a"), "invalid_grant"),
                           (OAuth2TokenExchange("http://localhost/token", "s", "a", retry_errors=("invalid_grant",),
                                                retry_seconds=180), "invalid_scope")]:
            with mock.patch("time.sleep") as sleep, mock.patch(
                    "requests.post", return_value=_resp(status=400, json_body={"error": code})) as post:
                with self.assertRaisesRegex(CompileError, code):
                    cred.auth_headers()
            post.assert_called_once()
            sleep.assert_not_called()

    def test_oauth2_requires_all_inputs(self):
        with self.assertRaises(ValueError):
            OAuth2TokenExchange(token_url="", subject_token="s", audience="a")


class RemoteProviderTests(unittest.TestCase):
    def test_compile_sql_posts_and_parses(self):
        body = {
            "statements": [{"name": "Numerator", "kind": "view", "sql": "CREATE OR REPLACE TEMP VIEW Numerator AS SELECT 1"}],
            "target": "Numerator",
            "defines": ["Numerator", "Denominator"],
        }
        provider = RemoteProvider("http://127.0.0.1:9999", StaticBearer("tok"))
        with mock.patch("requests.post", return_value=_resp(json_body=body)) as post:
            res = provider.compile_sql(CompileRequest(elm="{...}", target="Numerator",
                                                      boundaries={"rel_B", "ctx_Patient"}, context="Patient"))
        self.assertEqual(res.target, "Numerator")
        self.assertEqual([s.name for s in res.statements], ["Numerator"])
        self.assertEqual(res.defines, ["Numerator", "Denominator"])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://127.0.0.1:9999/compile-sql")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        sent = kwargs["json"]
        self.assertEqual(sent["target"], "Numerator")
        self.assertEqual(sent["context"], "Patient")
        self.assertEqual(sent["boundaries"], ["ctx_Patient", "rel_B"])  # sorted, deterministic

    def test_describe_posts_and_parses(self):
        body = {
            "defines": ["A", "B"],
            "dependencies": {"B": ["A"], "A": []},
            "boundaries": ["def_A", "ctx_Patient"],
            "materializedDefines": ["A"],
            "compilerVersion": "1.2.3",
            "terminologyVersion": None,
        }
        provider = RemoteProvider("http://127.0.0.1:9999")
        with mock.patch("requests.post", return_value=_resp(json_body=body)) as post:
            g = provider.describe(CompileRequest(elm="{...}", context="Patient", grain_first=True))
        self.assertEqual(g.defines, ["A", "B"])
        self.assertEqual(g.dependencies, {"B": ["A"], "A": []})
        self.assertEqual(g.boundaries, ["def_A", "ctx_Patient"])
        self.assertEqual(g.materialized_defines, ["A"])
        self.assertEqual(g.compiler_version, "1.2.3")
        self.assertIsNone(g.terminology_version)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://127.0.0.1:9999/describe")
        self.assertTrue(kwargs["json"]["grainFirst"])

    def test_compile_pipeline_posts_and_parses(self):
        # PIPE-SQL-DAG-REUSE: one dataset recipe per materialized define; a dependent recipe DECLARES a read
        # of a published sibling dataset and references it by name in its statements.
        body = {"datasets": [
            {"name": "test_1_0_0__diabeteslist", "target": "DiabetesList", "reads": [],
             "query": "WITH DiabetesList AS (SELECT * FROM retrieve_Condition) SELECT * FROM DiabetesList",
             "sqlFile": "-- Library: Test v1.0.0 | Define: DiabetesList\nCREATE OR REFRESH MATERIALIZED VIEW test_1_0_0__diabeteslist\nAS\nWITH DiabetesList AS (SELECT * FROM retrieve_Condition) SELECT * FROM DiabetesList",
             "statements": [{"name": "DiabetesList", "kind": "cache_table",
                             "sql": "CACHE TABLE DiabetesList AS SELECT * FROM retrieve_Condition"}]},
            {"name": "test_1_0_0__denominator", "target": "Denominator", "reads": ["test_1_0_0__diabeteslist"],
             "query": "WITH Denominator AS (SELECT * FROM test_1_0_0__diabeteslist) SELECT * FROM Denominator",
             "sqlFile": "-- Library: Test v1.0.0 | Define: Denominator\nCREATE OR REFRESH MATERIALIZED VIEW test_1_0_0__denominator\nAS\nWITH Denominator AS (SELECT * FROM test_1_0_0__diabeteslist) SELECT * FROM Denominator",
             "statements": [{"name": "Denominator", "kind": "view",
                             "sql": "CREATE OR REPLACE TEMP VIEW Denominator AS SELECT * FROM test_1_0_0__diabeteslist"}]},
        ]}
        provider = RemoteProvider("http://127.0.0.1:9999", StaticBearer("tok"))
        with mock.patch("requests.post", return_value=_resp(json_body=body)) as post:
            plan = provider.compile_pipeline(CompileRequest(elm="{...}", context="Patient"))
        self.assertEqual([d.name for d in plan.datasets], ["test_1_0_0__diabeteslist", "test_1_0_0__denominator"])
        den = plan.datasets[1]
        self.assertEqual(den.target, "Denominator")
        self.assertEqual(den.reads, ["test_1_0_0__diabeteslist"])
        self.assertEqual([s.name for s in den.statements], ["Denominator"])
        self.assertIn("test_1_0_0__diabeteslist", den.statements[0].sql)
        self.assertIn("test_1_0_0__diabeteslist", den.query)  # the declarative WITH query reads the sibling dataset
        self.assertIn("CREATE OR REFRESH MATERIALIZED VIEW", den.sql_file)  # PIPE-EMIT-SQL
        self.assertIn(den.query, den.sql_file)  # sql_file embeds the query
        self.assertEqual(post.call_args[0][0], "http://127.0.0.1:9999/compile-pipeline")

    def test_sql_file_deploy_flow_writes_one_file_per_recipe(self):
        """PIPE-EMIT-SQL: a client writes each recipe's sql_file to transformations/{name}.sql —
        the complete SDP deploy artifact, no runtime compilation or client wheel needed."""
        import os, tempfile
        body = {"datasets": [
            {"name": "test_1_0_0__diabeteslist", "target": "DiabetesList", "reads": [],
             "query": "WITH DiabetesList AS (SELECT 1) SELECT * FROM DiabetesList",
             "sqlFile": "-- Library: Test v1.0.0 | Define: DiabetesList\n"
                        "CREATE OR REFRESH MATERIALIZED VIEW test_1_0_0__diabeteslist\nAS\n"
                        "WITH DiabetesList AS (SELECT 1) SELECT * FROM DiabetesList",
             "statements": []},
            {"name": "test_1_0_0__denominator", "target": "Denominator", "reads": ["test_1_0_0__diabeteslist"],
             "query": "WITH Denominator AS (SELECT * FROM test_1_0_0__diabeteslist) SELECT * FROM Denominator",
             "sqlFile": "-- Library: Test v1.0.0 | Define: Denominator\n"
                        "CREATE OR REFRESH MATERIALIZED VIEW test_1_0_0__denominator\nAS\n"
                        "WITH Denominator AS (SELECT * FROM test_1_0_0__diabeteslist) SELECT * FROM Denominator",
             "statements": []},
        ]}
        provider = RemoteProvider("http://127.0.0.1:9999", StaticBearer("tok"))
        with mock.patch("requests.post", return_value=_resp(json_body=body)):
            plan = provider.compile_pipeline(CompileRequest(elm="{...}", context="Patient"))

        with tempfile.TemporaryDirectory() as tmpdir:
            txn_dir = os.path.join(tmpdir, "transformations")
            os.makedirs(txn_dir)
            for recipe in plan.datasets:
                path = os.path.join(txn_dir, f"{recipe.name}.sql")
                with open(path, "w") as f:
                    f.write(recipe.sql_file)

            # One .sql file per recipe, named by the published dataset.
            files = sorted(os.listdir(txn_dir))
            self.assertEqual(files, ["test_1_0_0__denominator.sql", "test_1_0_0__diabeteslist.sql"])

            # Each file is self-contained: starts with the ELM origin comment + DDL.
            for recipe in plan.datasets:
                path = os.path.join(txn_dir, f"{recipe.name}.sql")
                with open(path) as f:
                    content = f.read()
                self.assertTrue(content.startswith("-- Library:"),
                                f"{recipe.name}.sql must start with ELM origin comment")
                self.assertIn("CREATE OR REFRESH MATERIALIZED VIEW", content)
                self.assertIn(recipe.query, content)

    def test_sql_file_defaults_empty_for_older_services(self):
        """Backward compatibility: an older compile service that omits sqlFile → empty string."""
        body = {"datasets": [
            {"name": "test_1_0_0__foo", "target": "Foo", "reads": [],
             "query": "SELECT 1", "statements": []},
        ]}
        provider = RemoteProvider("http://127.0.0.1:9999", StaticBearer("tok"))
        with mock.patch("requests.post", return_value=_resp(json_body=body)):
            plan = provider.compile_pipeline(CompileRequest(elm="{...}", context="Patient"))
        self.assertEqual(plan.datasets[0].sql_file, "")

    def test_the_plan_reports_the_compiler(self):
        # BUILD-VERSION-PIN: the plan carries the compiler identity.
        request = CompileRequest(elm="x")
        provider = RemoteProvider("http://h")
        body = {"datasets": [], "compilerVersion": "engine-abc+svc-abc"}
        with mock.patch("requests.post", return_value=_resp(json_body=body)):
            self.assertEqual(provider.compile_pipeline(request).compiler_version, "engine-abc+svc-abc")

    def test_translate_returns_elm_text_and_fails_loudly(self):
        provider = RemoteProvider("http://h")
        with mock.patch("requests.post", return_value=_resp(json_body={"ok": True, "elm": {"library": {}}})) as post:
            self.assertEqual(provider.translate("library A", ["library B"]), '{"library": {}}')
        self.assertEqual(post.call_args[1]["json"], {"cql": "library A", "includes": ["library B"]})
        with mock.patch("requests.post", return_value=_resp(json_body={"ok": False, "diagnostics": ["boom"]})):
            with self.assertRaisesRegex(CompileError, "boom"):
                provider.translate("library A")

    def test_resource_types_are_the_modeled_set(self):
        provider = RemoteProvider("http://h")
        with mock.patch("requests.post", return_value=_resp(json_body={"resourceTypes": ["Patient", "Encounter"]})):
            self.assertEqual(provider.resource_types(), ["Encounter", "Patient"])
        with mock.patch("requests.post", return_value=_resp(json_body={})):
            with self.assertRaises(CompileError):
                provider.resource_types()

    def test_resource_schemas_decode_the_models_struct(self):
        """CLIENT-RESOURCE-SCHEMA: one call for the asked types, each with a session-free StructType."""
        schema = {"type": "struct", "fields": [
            {"name": "id", "type": "string", "nullable": True, "metadata": {}},
            {"name": "__resource_type", "type": "string", "nullable": True, "metadata": {}}]}
        body = {"schemas": {"Patient": {"modeled": True, "patientContext": True,
                                        "ddl": "id STRING, __resource_type STRING", "schema": schema}}}
        provider = RemoteProvider("http://h")
        with mock.patch("requests.post", return_value=_resp(json_body=body)) as post:
            out = provider.resource_schemas(["Patient", "Patient"])
        self.assertEqual(post.call_args[1]["json"], {"types": ["Patient"]})
        patient = out["Patient"]
        self.assertTrue(patient.patient_context)
        self.assertEqual(patient.ddl, "id STRING, __resource_type STRING")
        self.assertEqual([f.name for f in patient.struct.fields], ["id", "__resource_type"])

    def test_resource_schemas_refuse_an_unmodeled_type_and_an_old_service(self):
        provider = RemoteProvider("http://h")
        with mock.patch("requests.post", return_value=_resp(json_body={"schemas": {"Widget": {"modeled": False}}})):
            with self.assertRaisesRegex(CompileError, "does not model Widget"):
                provider.resource_schemas(["Widget"])
        old = {"schemas": {"Patient": {"modeled": True, "patientContext": True, "ddl": "id STRING"}}}
        with mock.patch("requests.post", return_value=_resp(json_body=old)):
            with self.assertRaisesRegex(CompileError, "no schema for Patient"):
                provider.resource_schemas(["Patient"])

    def test_non_200_fails_loudly(self):
        provider = RemoteProvider("http://h")
        with mock.patch("requests.post", return_value=_resp(status=400, text="bad elm")):
            with self.assertRaises(CompileError):
                provider.compile_sql(CompileRequest(elm="x"))

    def test_app_login_redirect_is_not_followed(self):
        """SVC-DISCOVERY-PLUGIN — an opaque runtime token must not turn into a sign-in HTML response."""
        response = _resp(status=302, text="sensitive login body")
        with mock.patch("requests.post", return_value=response) as post:
            with self.assertRaisesRegex(CompileError, "authentication/endpoint redirect") as error:
                RemoteProvider("http://localhost:9999").translate_result("synthetic")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        response.json.assert_not_called()
        self.assertNotIn("sensitive login body", str(error.exception))

    def test_non_json_success_has_actionable_transport_error(self):
        """SVC-DISCOVERY-PLUGIN — preserve the decode cause without exposing a login page or token."""
        response = _resp(text="sensitive login body")
        response.json.side_effect = ValueError("not JSON")
        with mock.patch("requests.post", return_value=response):
            with self.assertRaisesRegex(CompileError, "non-JSON response") as error:
                RemoteProvider("http://localhost:9999").translate_result("synthetic")
        self.assertIsInstance(error.exception.__cause__, ValueError)
        self.assertNotIn("sensitive login body", str(error.exception))

    def test_endpoint_required(self):
        with self.assertRaises(ValueError):
            RemoteProvider("")


class ResolveTests(unittest.TestCase):
    def test_resolve_credentials_builtins(self):
        self.assertIsInstance(resolve_credentials({}), NoAuth)
        self.assertIsInstance(
            resolve_credentials({config.CONF_AUTH_KIND: "bearer", config.CONF_AUTH_TOKEN: "t"}), StaticBearer)
        cred = resolve_credentials({
            config.CONF_AUTH_KIND: "oauth2-token-exchange",
            config.CONF_AUTH_TOKEN_URL: "https://h/token",
            config.CONF_AUTH_SUBJECT_TOKEN: "s", config.CONF_AUTH_AUDIENCE: "a"})
        self.assertIsInstance(cred, OAuth2TokenExchange)
        with self.assertRaises(ValueError):
            resolve_credentials({config.CONF_AUTH_KIND: "magic"})

    def test_resolve_provider_remote(self):
        p = resolve_provider({config.CONF_PROVIDER_ENDPOINT: "http://127.0.0.1:8080"})  # kind defaults remote
        self.assertIsInstance(p, RemoteProvider)

    def test_resolve_provider_explicit_endpoint_beats_conf(self):
        p = resolve_provider({config.CONF_PROVIDER_ENDPOINT: "http://conf:1"}, endpoint="http://arg:2")
        self.assertIsInstance(p, RemoteProvider)
        self.assertEqual(p._base, "http://arg:2")

    def test_resolve_provider_remote_needs_endpoint(self):
        # loud FAIL — ConfigResolutionError IS a ValueError, and names every way to set the endpoint.
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(config.ConfigResolutionError) as cm:
                resolve_provider({})
        self.assertIn(config.CONF_PROVIDER_ENDPOINT, str(cm.exception))

    def test_resolve_provider_unknown_kind_without_plugin(self):
        with mock.patch.object(providers, "_load_plugin", return_value=None):
            with self.assertRaises(ValueError):
                resolve_provider({config.CONF_PROVIDER_KIND: "databricks-app"})

    def test_resolve_provider_uses_registered_plugin(self):
        sentinel = mock.Mock(name="plugin-provider")
        factory = mock.Mock(return_value=sentinel)
        with mock.patch.object(providers, "_load_plugin", return_value=factory) as load:
            got = resolve_provider({config.CONF_PROVIDER_KIND: "fabric"})
        load.assert_called_once_with("fabric")
        self.assertIs(got, sentinel)

    def test_provider_kind_resolves_from_the_in_package_registry(self):
        """Kinds beyond `remote` come from `_PROVIDER_KINDS` in this package, not an entry-point group
        (decision 10). `databricks-app` is the one shipped kind."""
        self.assertIn("databricks-app", providers._PROVIDER_KINDS)
        self.assertIsNotNone(providers._load_plugin("databricks-app"))
        self.assertIsNone(providers._load_plugin("nope"))

    def test_unknown_provider_kind_names_the_valid_kinds(self):
        with self.assertRaises(ValueError) as caught:
            resolve_provider({config.CONF_PROVIDER_KIND: "fabric"})
        msg = str(caught.exception)
        self.assertIn("fabric", msg)
        self.assertIn("databricks-app", msg)   # tell the caller what IS available, not just what is not

    def test_remote_kind_never_imports_the_platform_layer(self):
        """The neutral `remote` path must not drag in the Databricks SDK. The platform factories import
        lazily precisely so that an explicitly wired client stays platform-free — the property the OSS
        end-to-end conformance lane depends on."""
        import sys
        for mod in [m for m in list(sys.modules) if m.startswith("cql_databricks.compiler.provider")]:
            del sys.modules[mod]
        resolve_provider({config.CONF_PROVIDER_ENDPOINT: "https://compile.example"})
        self.assertNotIn("cql_databricks.compiler.provider", sys.modules)


if __name__ == "__main__":
    unittest.main()
