"""CLIENT-SESSION-RESOLVERS, CLIENT-SESSION-RESET, CLIENT-TERMINOLOGY-RESOLVER: a notebook sets the resolvers its
``cql()`` calls and ``%%cql`` cells run through, and a DataFrame serves value sets for development."""
import unittest
from unittest import mock

import cql_databricks.compiler.zeroconfig as client_mod
from cql_databricks.compiler import (DataFrameTerminologyResolver, FolderKnowledgeResolver, TableDataResolver,
                                     use_resolvers)
from cql_databricks.compiler.resolvers import DataFrameRelation
from cql_databricks.compiler.session_state import reset, session_resolvers

from .test_client import UDF_SCHEMA, _ws


class _Frame:
    """The few DataFrame calls the resolver makes, over plain rows."""

    def __init__(self, rows, columns):
        self.rows, self.columns = rows, list(columns)

    def select(self, *columns):
        return _Frame([{c: r.get(c) for c in columns} for r in self.rows], columns)

    def __getattr__(self, column):
        if column in self.columns:
            return _Column(column)
        raise AttributeError(column)

    def filter(self, condition):
        return _Frame([r for r in self.rows if r[condition.column] == condition.value], self.columns)

    def collect(self):
        return self.rows


class _Column:
    def __init__(self, column):
        self.column = column

    def __eq__(self, value):
        condition = _Column(self.column)
        condition.value = value
        return condition


def _value_sets(**extra):
    rows = [{"valueset_id": "urn:vs:diabetes", "version": None, "system": "http://snomed.info/sct", "code": "44054006"},
            {"valueset_id": "urn:vs:other", "version": "1", "system": "http://loinc.org", "code": "44054006"}]
    return _Frame(rows, ["valueset_id", "version", "system", "code", *extra])


class UseResolversTest(unittest.TestCase):
    def setUp(self):
        client_mod._default_client = None
        use_resolvers()

    tearDown = setUp

    def _cql(self):
        with mock.patch("cql_databricks.compiler.provider._workspace", _ws()), \
                mock.patch("cql_databricks.compiler.zeroconfig.discover_udf_schema", return_value=UDF_SCHEMA), \
                mock.patch("cql_databricks.compiler.zeroconfig.register_runtime_udfs"):
            client_mod.cql("library T version '1'\ndefine \"Two\": 2 + 2")
        return client_mod._default_client

    def test_cells_run_through_the_session_resolvers(self):
        """The default client cql() and %%cql share is built with the session's data resolver."""
        use_resolvers(data=TableDataResolver({"Patient": "acme.fhir.patient"}))
        self.assertEqual(self._cql()._binder.data.identity(), "tables:Patient=acme.fhir.patient")

    def test_setting_them_touches_no_workspace(self):
        with mock.patch("cql_databricks.compiler.zeroconfig.connect") as connect:
            use_resolvers(data=TableDataResolver({"Patient": "acme.fhir.patient"}))
        connect.assert_not_called()

    def test_the_client_is_rebuilt_when_they_change_and_reused_while_not(self):
        first = self._cql()
        self.assertIs(self._cql(), first)
        use_resolvers(data=TableDataResolver({"Patient": "acme.fhir.patient"}))
        second = self._cql()
        self.assertIsNot(second, first)
        self.assertIs(self._cql(), second)

    def test_a_call_with_none_returns_every_role_to_the_environment(self):
        use_resolvers(data=TableDataResolver({"Patient": "acme.fhir.patient"}),
                      knowledge=FolderKnowledgeResolver("/Workspace/cql"))
        use_resolvers()
        self.assertEqual(dict(session_resolvers()), {})

    def test_a_call_sets_every_role(self):
        use_resolvers(data=TableDataResolver({"Patient": "acme.fhir.patient"}))
        use_resolvers(knowledge=FolderKnowledgeResolver("/Workspace/cql"))
        self.assertEqual(sorted(session_resolvers()), ["knowledge"])

    def test_a_resolver_of_the_wrong_role_fails_naming_it(self):
        with self.assertRaisesRegex(TypeError, "terminology= takes a TerminologyResolver"):
            use_resolvers(terminology=TableDataResolver({"Patient": "acme.fhir.patient"}))
        self.assertEqual(dict(session_resolvers()), {})

    def test_reset_drops_them(self):
        """CLIENT-SESSION-RESET: the session starts clean, its resolvers the environment's."""
        use_resolvers(data=TableDataResolver({"Patient": "acme.fhir.patient"}))
        spark = mock.MagicMock()
        spark.sql.return_value.collect.return_value = []
        reset(spark)
        self.assertEqual(dict(session_resolvers()), {})


class DataFrameTerminologyResolverTest(unittest.TestCase):
    def test_it_binds_by_rule_to_the_frame(self):
        relation = DataFrameTerminologyResolver(_value_sets()).rule()
        self.assertIsInstance(relation, DataFrameRelation)
        self.assertEqual(relation.fhir_type, "ValueSet")
        self.assertEqual(relation.frame.columns, ["valueset_id", "version", "system", "code"])

    def test_a_frame_missing_a_column_fails_naming_it(self):
        with self.assertRaisesRegex(ValueError, "it has no code, system"):
            DataFrameTerminologyResolver(_Frame([], ["valueset_id"]))

    def test_its_identity_is_its_name(self):
        self.assertEqual(DataFrameTerminologyResolver(_value_sets(), name="tour").identity(),
                         "valueset-dataframe:tour")

    def test_find_code_lists_the_value_sets_holding_it(self):
        """AGENT-VALUE-SET-LOOKUP over the frame, in one system when given."""
        resolver = DataFrameTerminologyResolver(_value_sets())
        self.assertEqual([m.id for m in resolver.find_code("44054006", None)], ["urn:vs:diabetes", "urn:vs:other"])
        self.assertEqual([m.id for m in resolver.find_code("44054006", "http://loinc.org")], ["urn:vs:other"])


if __name__ == "__main__":
    unittest.main()
