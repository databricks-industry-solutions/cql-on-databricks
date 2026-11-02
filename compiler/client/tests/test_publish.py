"""BIND-PUBLISH — publish an environment's data views over tables (``cql_databricks.compiler.publish``).

Stdlib unittest. PySpark's type classes need no session; the tables' schemas come from a fake session, the
model from a fake client, and the run goes through a recording SQL runner. One live publish runs in the
integration suite (``examples/tests/test_publish_live.py``).
"""
import unittest

from pyspark.sql.types import IntegerType, LongType, StringType, StructField, StructType, TimestampType

from cql_databricks.compiler.providers import ResourceSchema
from cql_databricks.compiler.publish import publish_data_source
from cql_databricks.compiler.reshape import ERROR, FhirJson, SchemaCheckError


def _s(*fields):
    return StructType([StructField(n, t) for n, t in fields])


LINEAGE = (("__resource_type", StringType()), ("__resource_id", StringType()))
PATIENT = ResourceSchema(fhir_type="Patient", struct=_s(("id", StringType()), ("birthDate", StringType()), *LINEAGE),
                         ddl="", patient_context=True)
OBS = ResourceSchema(fhir_type="Observation",
                     struct=_s(("id", StringType()), ("status", StringType()),
                               ("subject", _s(("reference", StringType()))),
                               ("valueQuantity", _s(("value", LongType()))), *LINEAGE),
                     ddl="", patient_context=True)

TABLES = {
    "acme.silver.patient": _s(("id", StringType()), ("birthDate", StringType())),
    "acme.silver.observation": _s(("id", StringType()), ("status", StringType()),
                                  ("subject", _s(("reference", StringType()))),
                                  ("valueQuantity", _s(("value", IntegerType())))),
    "acme.silver.bad_observation": _s(("id", StringType()), ("status", TimestampType()),
                                      ("subject", _s(("reference", StringType())))),
}

GOLDEN = f"""\
CREATE OR REPLACE VIEW `acme_env`.`data`.`observation` AS SELECT nullif(regexp_extract(`__r`.`subject`.`reference`, '([^/:]+)$', 1), '') AS `__subject_id`, named_struct('id', `__r`.`id`, 'status', `__r`.`status`, 'subject', `__r`.`subject`, 'valueQuantity', CASE WHEN `__r`.`valueQuantity` IS NULL THEN NULL ELSE named_struct('value', CAST(`__r`.`valueQuantity`.`value` AS bigint)) END, '__resource_type', CAST(CASE WHEN true THEN 'Observation' END AS STRING), '__resource_id', CAST(`__r`.`id` AS STRING)) AS `__resource` FROM (SELECT struct(*) AS `__r` FROM `acme`.`silver`.`observation`);

CREATE OR REPLACE VIEW `acme_env`.`data`.`patient` AS SELECT CAST(`__r`.`id` AS STRING) AS `__subject_id`, named_struct('id', `__r`.`id`, 'birthDate', `__r`.`birthDate`, '__resource_type', CAST(CASE WHEN true THEN 'Patient' END AS STRING), '__resource_id', CAST(`__r`.`id` AS STRING)) AS `__resource` FROM (SELECT struct(*) AS `__r` FROM `acme`.`silver`.`patient`);
"""


class _Model:
    def resource_schema(self, fhir_type=None):
        schemas = {"Patient": PATIENT, "Observation": OBS}
        return dict(schemas) if fhir_type is None else schemas[fhir_type]


class _Spark:
    class _Table:
        def __init__(self, schema):
            self.schema = schema

    def table(self, name):
        return self._Table(TABLES[name])


class _Runner:
    """Records every statement."""

    def __init__(self):
        self.statements = []

    def __call__(self, statement, params=None):
        self.statements.append(statement)
        return []


def _plan(fhir=None):
    fhir = fhir or {"Patient": "acme.silver.patient", "Observation": "acme.silver.observation"}
    return publish_data_source(_Spark(), "acme_env", fhir=fhir, client=_Model())


class PlanTests(unittest.TestCase):
    def test_the_script_is_one_published_view_per_type_in_the_environments_data(self):
        plan = _plan()
        self.assertEqual(plan.sql, GOLDEN)
        self.assertEqual(dict(plan.views), {"Observation": "acme_env.data.observation",
                                            "Patient": "acme_env.data.patient"})

    def test_every_finding_is_carried_whatever_its_severity(self):
        plan = _plan()
        self.assertEqual([(i.severity, i.fhir_type, i.path) for i in plan.issues],
                         [("INFO", "Observation", "valueQuantity.value")])

    def test_it_tags_nothing_and_creates_no_function(self):
        """BIND-PUBLISH, CLIENT-ENVIRONMENT — the environment's catalog carries the tags; a view is the mapping."""
        self.assertNotIn("TAGS", _plan().sql)
        self.assertNotIn("FUNCTION", _plan().sql)

    def test_nothing_runs_while_planning(self):
        # The fake session has no sql(): planning only reads table schemas.
        self.assertFalse(hasattr(_Spark(), "sql"))
        _plan()


class SourceTests(unittest.TestCase):
    def test_a_dataframe_is_refused_naming_the_fix(self):
        with self.assertRaisesRegex(TypeError, r"publish reads tables only.*to_df\(spark, 'Observation'\)"):
            _plan({"Observation": object()})

    def test_raw_json_is_refused(self):
        raw = FhirJson("acme.bronze.fhir_raw", column="resource")
        with self.assertRaisesRegex(TypeError, "FhirJson.*publish reads tables only"):
            _plan({"Observation": raw})
        with self.assertRaisesRegex(TypeError, "publish reads tables only"):
            publish_data_source(_Spark(), "acme_env", fhir=raw, client=_Model())

    def test_an_empty_map_is_refused(self):
        with self.assertRaisesRegex(ValueError, "nothing to publish"):
            publish_data_source(_Spark(), "acme_env", fhir={}, client=_Model())

    def test_strings_are_never_guessed_at(self):
        with self.assertRaisesRegex(ValueError, "write it as catalog.schema.table"):
            _plan({"Patient": "silver.patient"})
        for target in ("", "acme.cql_fhir", "/Volumes/acme/cql_fhir"):
            with self.assertRaisesRegex(ValueError, "an environment's catalog"):
                publish_data_source(_Spark(), target, fhir={"Patient": "acme.silver.patient"}, client=_Model())


class RunTests(unittest.TestCase):
    def test_run_executes_the_script_in_order(self):
        plan = _plan()
        run = _Runner()
        self.assertEqual(dict(plan.run(run)), dict(plan.views))
        self.assertEqual(run.statements, plan.statements)

    def test_an_error_refuses_the_run_before_any_statement(self):
        plan = _plan({"Observation": "acme.silver.bad_observation"})
        self.assertEqual([i.path for i in plan.issues if i.severity == ERROR], ["status"])
        run = _Runner()
        with self.assertRaisesRegex(SchemaCheckError, "Observation.status"):
            plan.run(run)
        self.assertEqual(run.statements, [])


if __name__ == "__main__":
    unittest.main()
