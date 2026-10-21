"""BIND-PUBLISH — write a tagged data source from tables (``cql_databricks.compiler.publish``).

Stdlib unittest. PySpark's type classes need no session; the tables' schemas come from a fake session, the
model from a fake client, and the run goes through a recording SQL runner. One live publish runs in the
integration suite (``examples/tests/test_publish_live.py``).
"""
import unittest

from pyspark.sql.types import IntegerType, LongType, StringType, StructField, StructType, TimestampType

from cql_databricks.compiler.data_source import DataSourceLayoutError
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

PARAMS = ("value_set STRING DEFAULT NULL, "
          "code_property STRING DEFAULT NULL, codes ARRAY<STRUCT<system STRING, code STRING>> DEFAULT array(), "
          "date_property STRING DEFAULT NULL, date_low STRING DEFAULT NULL, date_high STRING DEFAULT NULL, "
          "parameters MAP<STRING, VARIANT> DEFAULT map()")

GOLDEN = f"""\
CREATE SCHEMA IF NOT EXISTS `acme`.`cql_fhir`;

CREATE OR REPLACE FUNCTION `acme`.`cql_fhir`.`retrieve_Observation`({PARAMS})
RETURNS TABLE (`__subject_id` STRING, `__resource` STRUCT<`id`: string, `status`: string, `subject`: STRUCT<`reference`: string>, `valueQuantity`: STRUCT<`value`: bigint>, `__resource_type`: string, `__resource_id`: string>)
LANGUAGE SQL
COMMENT 'BIND-PUBLISH: retrieve_Observation over acme.silver.observation; ignores the pushdown arguments (BIND-CAPABILITY)'
RETURN SELECT nullif(regexp_extract(`__r`.`subject`.`reference`, '([^/:]+)$', 1), '') AS `__subject_id`, named_struct('id', `__r`.`id`, 'status', `__r`.`status`, 'subject', `__r`.`subject`, 'valueQuantity', CASE WHEN `__r`.`valueQuantity` IS NULL THEN NULL ELSE named_struct('value', CAST(`__r`.`valueQuantity`.`value` AS bigint)) END, '__resource_type', CAST(CASE WHEN true THEN 'Observation' END AS STRING), '__resource_id', CAST(`__r`.`id` AS STRING)) AS `__resource` FROM (SELECT struct(*) AS `__r` FROM `acme`.`silver`.`observation`);

CREATE OR REPLACE FUNCTION `acme`.`cql_fhir`.`retrieve_Patient`({PARAMS})
RETURNS TABLE (`__subject_id` STRING, `__resource` STRUCT<`id`: string, `birthDate`: string, `__resource_type`: string, `__resource_id`: string>)
LANGUAGE SQL
COMMENT 'BIND-PUBLISH: retrieve_Patient over acme.silver.patient; ignores the pushdown arguments (BIND-CAPABILITY)'
RETURN SELECT CAST(`__r`.`id` AS STRING) AS `__subject_id`, named_struct('id', `__r`.`id`, 'birthDate', `__r`.`birthDate`, '__resource_type', CAST(CASE WHEN true THEN 'Patient' END AS STRING), '__resource_id', CAST(`__r`.`id` AS STRING)) AS `__resource` FROM (SELECT struct(*) AS `__r` FROM `acme`.`silver`.`patient`);

ALTER SCHEMA `acme`.`cql_fhir` SET TAGS ('cql_databricks_data_source' = 'acme_fhir');

COMMENT ON SCHEMA `acme`.`cql_fhir` IS 'Acme FHIR R4, nightly';
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
    """Records every statement; answers the layout check's routine read with ``routines``."""

    def __init__(self, routines):
        self.routines = routines
        self.statements = []

    def __call__(self, statement, params=None):
        if "information_schema.routines" in statement:
            return [{"routine_name": r} for r in self.routines]
        if "information_schema.columns" in statement:
            return []
        self.statements.append(statement)
        return []


def _plan(fhir=None, **kwargs):
    kwargs.setdefault("name", "acme_fhir")
    fhir = fhir or {"Patient": "acme.silver.patient", "Observation": "acme.silver.observation"}
    return publish_data_source(_Spark(), "acme.cql_fhir", fhir=fhir, client=_Model(), **kwargs)


class PlanTests(unittest.TestCase):
    def test_the_script_is_the_schema_one_function_per_type_then_the_declaration(self):
        plan = _plan(description="Acme FHIR R4, nightly")
        self.assertEqual(plan.sql, GOLDEN)
        self.assertEqual(dict(plan.functions), {"Observation": "acme.cql_fhir.retrieve_Observation",
                                                "Patient": "acme.cql_fhir.retrieve_Patient"})

    def test_every_finding_is_carried_whatever_its_severity(self):
        plan = _plan()
        self.assertEqual([(i.severity, i.fhir_type, i.path) for i in plan.issues],
                         [("INFO", "Observation", "valueQuantity.value")])

    def test_with_no_description_only_the_tag_is_set(self):
        plan = _plan()
        self.assertEqual(plan.statements[-1],
                         "ALTER SCHEMA `acme`.`cql_fhir` SET TAGS ('cql_databricks_data_source' = 'acme_fhir')")
        self.assertNotIn("COMMENT ON SCHEMA", plan.sql)

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
            publish_data_source(_Spark(), "acme.cql_fhir", name="acme_fhir", fhir=raw, client=_Model())

    def test_an_empty_map_is_refused(self):
        with self.assertRaisesRegex(ValueError, "nothing to publish"):
            publish_data_source(_Spark(), "acme.cql_fhir", name="acme_fhir", fhir={}, client=_Model())

    def test_strings_are_never_guessed_at(self):
        with self.assertRaisesRegex(ValueError, "write it as catalog.schema.table"):
            _plan({"Patient": "silver.patient"})
        for target in ("cql_fhir", "acme.cql_fhir.extra", "/Volumes/acme/cql_fhir"):
            with self.assertRaisesRegex(ValueError, "write the target as catalog.schema"):
                publish_data_source(_Spark(), target, name="acme_fhir",
                                    fhir={"Patient": "acme.silver.patient"}, client=_Model())


class RunTests(unittest.TestCase):
    def test_run_executes_the_script_in_order_then_checks_the_layout(self):
        plan = _plan(description="Acme FHIR R4, nightly")
        run = _Runner(["retrieve_patient", "retrieve_observation"])
        layout = plan.run(run)
        self.assertEqual(run.statements, plan.statements)
        self.assertEqual(dict(layout.bindings), {"Observation": "acme.cql_fhir.retrieve_Observation",
                                                 "Patient": "acme.cql_fhir.retrieve_Patient"})

    def test_an_error_refuses_the_run_before_any_statement(self):
        plan = _plan({"Observation": "acme.silver.bad_observation"})
        self.assertEqual([i.path for i in plan.issues if i.severity == ERROR], ["status"])
        run = _Runner(["retrieve_observation"])
        with self.assertRaisesRegex(SchemaCheckError, "Observation.status"):
            plan.run(run)
        self.assertEqual(run.statements, [])

    def test_a_type_missing_after_the_run_fails_loudly(self):
        run = _Runner(["retrieve_patient"])
        with self.assertRaisesRegex(DataSourceLayoutError, "no retrieve function for Observation"):
            _plan().run(run)


if __name__ == "__main__":
    unittest.main()
