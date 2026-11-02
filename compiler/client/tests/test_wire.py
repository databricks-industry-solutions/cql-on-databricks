"""The client -> service WIRE contract (`BUILD-CLIENT-COMPAT`, `BUILD-COMPILE-LINK`): what
`CompileRequest.to_json` emits, what it deliberately OMITS, and the append-only rules that keep an old
host and a new client (and vice versa) working.

These are the assertions that make the compatibility policy enforceable rather than aspirational.
"""
import unittest

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import (RESOURCE, SCALAR, VALUE_COLUMN, CompileRequest, DefineShape,
                                        LibraryGraph, SqlCompileResult, SqlStatement)


class RequestShapeTests(unittest.TestCase):
    def test_request_is_keyword_only_so_field_order_is_never_a_contract(self):
        """BUILD-CLIENT-COMPAT. A third-party provider plugin constructs this type; if positional
        construction were allowed, inserting a field would silently re-bind someone else's arguments."""
        with self.assertRaises(TypeError):
            CompileRequest("some elm")                       # positional is refused outright
        self.assertEqual(CompileRequest(elm="some elm").elm, "some elm")

    def test_a_default_request_omits_every_optional_key(self):
        """The body a caller sends with no new features must stay byte-identical to the pre-existing
        one, so an older host never meets a key it has to ignore."""
        body = CompileRequest(elm="{}").to_json()
        self.assertEqual(set(body), {"elm", "cql", "target", "boundaries", "context", "overrides",
                                     "grainFirst", "parameters"})

    def test_unset_context_serializes_as_wire_null_not_a_forced_patient(self):
        self.assertIsNone(CompileRequest(elm="{}").to_json()["context"])

    def test_parameters_and_boundaries_are_sorted_for_a_deterministic_body(self):
        body = CompileRequest(elm="{}", parameters={"b": "2", "a": "1"},
                              boundaries={"z", "a"}).to_json()
        self.assertEqual(list(body["parameters"]), ["a", "b"])
        self.assertEqual(body["boundaries"], ["a", "z"])


class ClosureWireTests(unittest.TestCase):
    def test_an_elm_dependency_ships_as_a_bare_string(self):
        """The long-standing `deps` form — an array of ELM JSON strings — is unchanged, so a closure
        resolved to ELM is wire-identical to a hand-built request."""
        dep = LibrarySource(name="CommonHelpers", version="2.0.000", elm='{"library":{}}')
        self.assertEqual(CompileRequest(elm="{}", deps=(dep,)).to_json()["knowledge"], ['{"library":{}}'])

    def test_a_cql_only_dependency_ships_as_an_object_carrying_its_identity(self):
        """A pure-Python caller cannot translate, so it ships CQL and the service translates. The name
        and version ride along so a service-side diagnostic can say WHICH library failed."""
        dep = LibrarySource(name="Exclusions", version="6.18.000", cql="library Exclusions version '6.18.000'")
        entry = CompileRequest(elm="{}", deps=(dep,)).to_json()["knowledge"][0]
        self.assertEqual(entry, {"cql": "library Exclusions version '6.18.000'",
                                 "name": "Exclusions", "version": "6.18.000"})

    def test_a_mixed_closure_ships_both_forms_in_one_array(self):
        deps = (LibrarySource(name="A", elm='{"a":1}'), LibrarySource(name="B", cql="library B"))
        entries = CompileRequest(elm="{}", deps=deps).to_json()["knowledge"]
        self.assertEqual(entries[0], '{"a":1}')
        self.assertEqual(entries[1]["cql"], "library B")

    def test_elm_is_preferred_when_a_dependency_carries_both_and_the_root_is_elm(self):
        """A FHIR `Library` resource often carries both; ELM is the interchange form, so it wins and the
        service does no redundant translation."""
        dep = LibrarySource(name="Both", elm='{"library":{}}', cql="library Both")
        self.assertEqual(CompileRequest(elm="{}", deps=(dep,)).to_json()["knowledge"], ['{"library":{}}'])

    def test_a_CQL_root_takes_each_dependencys_CQL_even_when_it_also_has_ELM(self):
        """The form depends on the ROOT, not only on what the dependency carries. A CQL root must be
        TRANSLATED, and translation needs the closure as CQL SOURCES — shipping an ELM-only dependency
        leaves the root's `include` unresolvable ("Could not load source for library …") even though the
        ELM was right there."""
        dep = LibrarySource(name="Both", version="1.0.0", elm='{"library":{}}', cql="library Both")
        entry = CompileRequest(cql="library Root", deps=(dep,)).to_json()["knowledge"][0]
        self.assertEqual(entry["cql"], "library Both")

    def test_a_CQL_root_still_ships_an_ELM_ONLY_dependency_as_ELM(self):
        """Nothing else can be done for it — and it is still linkable, just not a translation source."""
        dep = LibrarySource(name="ElmOnly", elm='{"library":{}}')
        self.assertEqual(CompileRequest(cql="library Root", deps=(dep,)).to_json()["knowledge"],
                         ['{"library":{}}'])


class RuntimeWireTests(unittest.TestCase):
    def test_udf_schema_rides_in_runtime(self):
        """EMIT-UDF-QUALIFY: the schema of the `cql_*` functions is `runtime.schema` on the wire."""
        body = CompileRequest(elm="{}", udf_schema="acme.runtime").to_json()
        self.assertEqual(body["runtime"], {"schema": "acme.runtime"})


class ObjectPrefixWireTests(unittest.TestCase):
    def test_prefix_is_omitted_by_default(self):
        self.assertNotIn("objectPrefix", CompileRequest(elm="{}").to_json())

    def test_prefix_is_sent_when_set(self):
        self.assertEqual(CompileRequest(elm="{}", object_prefix="screeningmeasure_0_3").to_json()["objectPrefix"],
                         "screeningmeasure_0_3")


class EvalIdWireTests(unittest.TestCase):
    """PIPE-EVAL-SCOPED-READ — the evaluation set a pipeline plan is compiled for."""

    def test_eval_id_is_omitted_by_default(self):
        self.assertNotIn("evalId", CompileRequest(elm="{}").to_json())

    def test_eval_id_is_sent_when_set(self):
        self.assertEqual(CompileRequest(elm="{}", eval_id="abcd1234").to_json()["evalId"], "abcd1234")


class ResponseToleranceTests(unittest.TestCase):
    """BUILD-CLIENT-COMPAT, the other direction: an OLDER service omits fields a NEWER client reads."""

    def test_a_result_without_shapes_falls_back_to_the_documented_default(self):
        res = SqlCompileResult(statements=[], target="t", defines=["Numerator"])
        self.assertIsNone(res.shape("Numerator"))

    def test_durable_recipe_claim_is_explicit_and_older_plans_remain_readable(self):
        """PIPE-DURABLE-IDENTITY — optional claims are parsed, never fabricated for an old service."""
        from cql_databricks.compiler.providers import RemoteProvider

        body = {"compilerVersion": "synthetic", "datasets": [
            {"name": "result", "target": "Result", "query": "SELECT 1"}]}

        class Provider(RemoteProvider):
            def _post(self, path, request):
                return body

        provider = Provider("http://localhost", None)
        self.assertIsNone(provider.compile_pipeline(CompileRequest()).datasets[0].durable)
        body["datasets"][0]["durable"] = {
            "format": "cql-result-identity-v1", "fingerprint": "a" * 64, "reusable": False}
        claim = provider.compile_pipeline(CompileRequest(durable=True)).datasets[0].durable
        self.assertEqual(claim.fingerprint, "a" * 64)
        self.assertFalse(claim.reusable)

    def test_durable_request_is_opt_in(self):
        """PIPE-DURABLE-IDENTITY — legacy request bytes remain unchanged when unused."""
        self.assertNotIn("durable", CompileRequest().to_json())
        self.assertTrue(CompileRequest(durable=True).to_json()["durable"])

    def test_a_graph_without_define_kinds_defaults_to_scalar(self):
        g = LibraryGraph(defines=["N"], dependencies={}, boundaries=[], materialized_defines=[],
                         compiler_version="x")
        self.assertEqual(g.kind_of("N"), SCALAR)
        self.assertEqual(g.value_column, VALUE_COLUMN)

    def test_object_resolution_prefers_the_published_shape_over_re_deriving_the_name(self):
        """`SqlNames.sanitize` is one-way and NOT injective, and it is now prefix-aware — so a consumer
        that re-derives it in Python gets the wrong object the moment a prefix is in play. The compiler
        publishes `DefineShape.obj` precisely so nobody re-derives it."""
        from cql_databricks.compiler.cql import _object_for
        res = SqlCompileResult(
            statements=[SqlStatement("m__Denominator_Exclusions", "view", "…")],
            target="t", defines=["Denominator Exclusions"],
            shapes=[DefineShape(define="Denominator Exclusions", obj="m__Denominator_Exclusions",
                                kind=SCALAR)])
        self.assertEqual(_object_for("Denominator Exclusions", res), "m__Denominator_Exclusions")

    def test_object_resolution_falls_back_to_sanitize_for_a_pre_shapes_service(self):
        res = SqlCompileResult(
            statements=[SqlStatement("Denominator_Exclusions", "view", "…")],
            target="t", defines=["Denominator Exclusions"])
        from cql_databricks.compiler.cql import _object_for
        self.assertEqual(_object_for("Denominator Exclusions", res), "Denominator_Exclusions")


if __name__ == "__main__":
    unittest.main()
