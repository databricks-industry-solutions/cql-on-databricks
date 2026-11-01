"""CLIENT-RESULT-STORE: kept results found by fingerprint and written, in Delta."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from cql_databricks.result.store import DeltaResultStore

FP = "ab" * 32


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


class DeltaResultStoreTest(unittest.TestCase):
    def test_find_reads_the_destination_before_the_other_scopes(self):
        """CLIENT-RESULT-STORE, CLIENT-DISCOVERY-SCOPE."""
        name = f"measure_numerator_{FP[:16]}"
        run = _Runner(_tags("shared", "results", name) + _tags("acme", "mine", name))
        found = DeltaResultStore(("acme", "mine"), run, [("shared", "results")]).find(FP)
        self.assertEqual(str(found), f"acme.mine.{name}")
        self.assertEqual(run.statements[0][1]["suffix"], "%\\_" + FP[:16])

    def test_find_ignores_another_fingerprint_with_the_same_digits(self):
        name = f"measure_numerator_{FP[:16]}"
        run = _Runner(_tags("acme", "mine", name, fingerprint=FP[:16] + "cd" * 24))
        self.assertIsNone(DeltaResultStore(("acme", "mine"), run).find(FP))

    def test_write_creates_the_named_table_and_marks_it_complete(self):
        run = _Runner()
        recipe = SimpleNamespace(name="r", library="Measure", define="Numerator",
                                 durable=SimpleNamespace(fingerprint=FP, reusable=True))
        table = DeltaResultStore(("acme", "mine"), run).write(recipe, "SELECT 1 AS __val")
        self.assertEqual(table.table, f"measure_numerator_{FP[:16]}")
        self.assertTrue(run.statements[0][0].startswith("CREATE OR REPLACE TABLE `acme`.`mine`."))
        self.assertTrue(any("cql_fingerprint" in s for s, _ in run.statements[1:]))

    def test_a_result_that_is_not_reusable_is_never_kept(self):
        """CLIENT-PROVIDER-IDENTITY."""
        recipe = SimpleNamespace(name="r", library="M", define="D", durable=SimpleNamespace(fingerprint=FP,
                                                                                            reusable=False))
        with self.assertRaises(ValueError):
            DeltaResultStore(("acme", "mine"), _Runner()).write(recipe, "SELECT 1")


if __name__ == "__main__":
    unittest.main()
