"""CLIENT-RESULT-RESOLVER, RUN-CLIENT-RESULT-OWNERSHIP, RUN-CLIENT-TABLE-NAMES: kept results found by fingerprint and
written, in Delta, by the identity that owns them."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from cql_databricks.result._catalog_completion import result_table_name, writer_digest
from cql_databricks.result.store import DeltaResultResolver

FP = "ab" * 32
OWNER = "someone@example.com"
W = writer_digest(OWNER)


class _Runner:
    def __init__(self, rows=()):
        self.rows, self.statements = list(rows), []

    def __call__(self, statement, params=None):
        self.statements.append((statement, dict(params or {})))
        return self.rows if "table_tags" in statement else []


def _tags(catalog, schema, table, fingerprint=FP):
    values = {"cql_result_format": "cql-catalog-result-v3", "cql_result_state": "complete",
              "cql_computed_at": "2026-10-31T00:00:00.000000+00:00", "cql_fingerprint": fingerprint}
    return [{"catalog_name": catalog, "schema_name": schema, "table_name": table, "tag_name": k, "tag_value": v}
            for k, v in values.items()]


class NameTest(unittest.TestCase):
    def test_a_name_ends_with_the_fingerprint_and_the_writers_digest(self):
        """RUN-CLIENT-TABLE-NAMES: two identities computing one calculation write two tables."""
        self.assertEqual(len(W), 8)
        self.assertNotEqual(W, writer_digest("other@example.com"))
        self.assertEqual(result_table_name("Measure", "Numerator", FP, W), f"measure_numerator_{FP[:16]}_{W}")
        with self.assertRaises(ValueError):
            result_table_name("Measure", "Numerator", FP, "not-a-digest")


class DeltaResultResolverTest(unittest.TestCase):
    def test_find_reads_only_the_owners_tables_destination_first(self):
        """RUN-CLIENT-RESULT-OWNERSHIP: the lookup matches the owner's digest in the name and the owner of the table."""
        name = f"measure_numerator_{FP[:16]}_{W}"
        run = _Runner(_tags("env", "results", name) + _tags("env", "draft_results", name))
        found = DeltaResultResolver(("env", "draft_results"), run, [("env", "results")], owner=OWNER).find(FP)
        self.assertEqual(str(found), f"env.draft_results.{name}")
        statement, params = run.statements[0]
        self.assertEqual(params["suffix"], f"%\\_{FP[:16]}\\_{W}")
        self.assertEqual(params["owner"], OWNER)
        self.assertIn("t.table_owner = :owner", statement)

    def test_find_ignores_another_fingerprint_with_the_same_digits(self):
        name = f"measure_numerator_{FP[:16]}_{W}"
        run = _Runner(_tags("env", "draft_results", name, fingerprint=FP[:16] + "cd" * 24))
        self.assertIsNone(DeltaResultResolver(("env", "draft_results"), run, owner=OWNER).find(FP))

    def test_write_creates_the_owners_table_and_marks_it_complete(self):
        run = _Runner()
        recipe = SimpleNamespace(name="r", library="Measure", define="Numerator",
                                 durable=SimpleNamespace(fingerprint=FP, reusable=True))
        table = DeltaResultResolver(("env", "draft_results"), run, owner=OWNER).write(recipe, "SELECT 1 AS __val")
        self.assertEqual(table.table, f"measure_numerator_{FP[:16]}_{W}")
        self.assertTrue(run.statements[0][0].startswith("CREATE OR REPLACE TABLE `env`.`draft_results`."))
        self.assertTrue(any("cql_fingerprint" in s for s, _ in run.statements[1:]))

    def test_a_result_that_is_not_reusable_is_never_kept(self):
        """CLIENT-RESOLVER-IDENTITY."""
        recipe = SimpleNamespace(name="r", library="M", define="D", durable=SimpleNamespace(fingerprint=FP,
                                                                                            reusable=False))
        with self.assertRaises(ValueError):
            DeltaResultResolver(("env", "draft_results"), _Runner(), owner=OWNER).write(recipe, "SELECT 1")


if __name__ == "__main__":
    unittest.main()
