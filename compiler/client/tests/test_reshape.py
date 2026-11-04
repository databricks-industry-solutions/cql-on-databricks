"""BIND-RESHAPE — the one reshape to the mount contract, and the findings it makes on the way
(`BIND-SCHEMA-CHECK`). Pure: PySpark's type classes need no session, and the SQL is checked as text here.
The SQL runs against Spark in the loader's equivalence test.
"""
import unittest

from pyspark.sql.types import (ArrayType, DateType, IntegerType, LongType, StringType, StructField, StructType,
                               TimestampType)

from cql_databricks.compiler.providers import ResourceSchema
from cql_databricks.compiler.reshape import (ERROR, INFO, WARN, FhirJson, json_sql, require_ok, structured_sql,
                                             table_name, type_predicate, SchemaCheckError)


def _s(*fields):
    return StructType([StructField(n, t) for n, t in fields])


CODING = _s(("system", StringType()), ("code", StringType()))
MODEL_OBS = _s(("id", StringType()), ("status", StringType()),
               ("code", _s(("coding", ArrayType(CODING)), ("text", StringType()))),
               ("subject", _s(("reference", StringType()))),
               ("valueQuantity", _s(("value", LongType()))), ("valueString", StringType()))
OBS = ResourceSchema(fhir_type="Observation", struct=MODEL_OBS, ddl="id STRING", patient_context=True)
PATIENT = ResourceSchema(fhir_type="Patient", struct=_s(("id", StringType()), ("birthDate", StringType())),
                         ddl="id STRING, birthDate STRING", patient_context=True)


def _issues(source, schema=OBS):
    return structured_sql("`c`.`s`.`t`", source, schema)


def _by(issues, severity):
    return {i.path for i in issues if i.severity == severity}


class StructuredTests(unittest.TestCase):
    def test_a_matching_table_is_read_as_is(self):
        """BIND-RESHAPE, BIND-CONTRACT: the contract's resource struct and patient_id, with no engine lineage."""
        source = _s(*[(f.name, f.dataType) for f in MODEL_OBS.fields])
        sql, issues = _issues(source)
        self.assertEqual(_by(issues, ERROR), set())
        self.assertIn("'code', `__r`.`code`", sql)        # identical sub-struct: no rebuild
        self.assertIn("nullif(regexp_extract(`__r`.`subject`.`reference`, '([^/:]+)$', 1), '') AS `patient_id`", sql)
        self.assertIn(") AS `resource`", sql)
        self.assertNotIn("__resource", sql)

    def test_missing_fields_read_as_typed_nulls_and_extra_fields_are_dropped(self):
        source = _s(("id", StringType()), ("code", _s(("text", StringType()), ("extra", StringType()))),
                    ("subject", _s(("reference", StringType()))), ("ingested_at", TimestampType()))
        sql, issues = _issues(source)
        self.assertEqual(_by(issues, ERROR), set())
        self.assertIn("code.coding", _by(issues, WARN))
        self.assertIn("status", _by(issues, WARN))
        self.assertEqual({"code.extra", "ingested_at"}, _by(issues, INFO))
        self.assertIn("'coding', CAST(NULL AS ARRAY<STRUCT<`system`: string, `code`: string>>)", sql)
        self.assertIn("CASE WHEN `__r`.`code` IS NULL THEN NULL ELSE named_struct(", sql)

    def test_a_safe_widening_is_cast_and_an_unsafe_type_is_an_error(self):
        source = _s(("id", StringType()), ("subject", _s(("reference", StringType()))),
                    ("valueQuantity", _s(("value", IntegerType()))), ("status", TimestampType()))
        sql, issues = _issues(source)
        self.assertIn("valueQuantity.value", _by(issues, INFO))
        self.assertIn("CAST(`__r`.`valueQuantity`.`value` AS bigint)", sql)
        self.assertEqual(_by(issues, ERROR), {"status"})

    def test_a_date_is_its_iso_string_but_a_timestamp_is_not(self):
        model = ResourceSchema(fhir_type="Patient", struct=PATIENT.struct, ddl="", patient_context=True)
        _, issues = _issues(_s(("id", StringType()), ("birthDate", DateType())), model)
        self.assertEqual(_by(issues, ERROR), set())
        _, issues = _issues(_s(("id", StringType()), ("birthDate", TimestampType())), model)
        self.assertEqual(_by(issues, ERROR), {"birthDate"})

    def test_arrays_of_structs_are_rebuilt_element_by_element(self):
        source = _s(("id", StringType()), ("subject", _s(("reference", StringType()))),
                    ("code", _s(("coding", ArrayType(_s(("code", StringType())))))))
        sql, _ = _issues(source)
        self.assertIn("transform(`__r`.`code`.`coding`, `__e0` -> CASE WHEN `__e0` IS NULL", sql)

    def test_a_merged_choice_and_json_string_references_are_errors(self):
        source = _s(("id", StringType()), ("value", StringType()), ("subject", StringType()))
        _, issues = _issues(source)
        errors = {i.path: i.message for i in issues if i.severity == ERROR}
        self.assertIn("merged choice", errors["value"])
        self.assertIn("JSON string", errors["subject"])
        self.assertIn("", errors)                          # and no subject reference to key on

    def test_no_id_is_an_error(self):
        _, issues = _issues(_s(("subject", _s(("reference", StringType())))))
        self.assertIn("id", _by(issues, ERROR))

    def test_a_relation_on_the_contract_keeps_its_subject_key(self):
        """BIND-CONTRACT, DATA-SUBJECT-KEY: a relation already returning resource and patient_id keeps its key."""
        source = _s(("resource", _s(("id", StringType()))), ("patient_id", StringType()))
        sql, issues = _issues(source)
        self.assertEqual(_by(issues, ERROR), set())
        self.assertIn("`patient_id` AS `patient_id`", sql)
        self.assertIn("FROM (SELECT `resource` AS `__r`, `patient_id` FROM `c`.`s`.`t`)", sql)

    def test_a_flat_table_with_a_patient_id_column_keeps_it_as_the_key(self):
        """DATA-SUBJECT-KEY: the publisher's own patient_id is the key, and is not a dropped extra field."""
        sql, issues = _issues(_s(("id", StringType()), ("subject", _s(("reference", StringType()))),
                                 ("patient_id", StringType())))
        self.assertIn("`patient_id` AS `patient_id`", sql)
        self.assertNotIn("patient_id", _by(issues, INFO))

    def test_patient_is_keyed_by_its_id(self):
        sql, _ = _issues(_s(("id", StringType())), PATIENT)
        self.assertIn("CAST(`__r`.`id` AS STRING) AS `patient_id`", sql)

    def test_require_ok_lists_every_error(self):
        _, issues = _issues(_s(("value", StringType())))
        with self.assertRaises(SchemaCheckError) as cm:
            require_ok(issues)
        self.assertIn("value", str(cm.exception))
        self.assertIn("id", str(cm.exception))


class JsonTests(unittest.TestCase):
    def test_json_sql_parses_with_the_model_ddl_and_keys_like_the_loader(self):
        sql = json_sql("`c`.`s`.`raw`", PATIENT, column="resource",
                       type_filter=type_predicate("Patient", json_column="resource", type_column=None))
        self.assertIn("from_json(`resource`, 'id STRING, birthDate STRING')", sql)
        self.assertIn("get_json_object(`resource`, '$.id') AS `patient_id`", sql)
        self.assertIn("get_json_object(`resource`, '$.resourceType') = 'Patient'", sql)
        self.assertNotIn("__resource_id", sql)
        self.assertIn("length(trim(`resource`)) > 0", sql)

    def test_a_type_column_filters_instead_of_the_json(self):
        self.assertEqual(type_predicate("Encounter", json_column="j", type_column="kind"), "`kind` = 'Encounter'")

    def test_the_subject_key_coalesces_the_reference_fields(self):
        sql = json_sql("t", OBS, column="v")
        self.assertIn("coalesce(get_json_object(`v`, '$.subject.reference'), get_json_object(`v`, "
                      "'$.patient.reference'), get_json_object(`v`, '$.beneficiary.reference'))", sql)


class StringTests(unittest.TestCase):
    def test_a_table_name_must_have_three_parts_and_no_slash(self):
        self.assertEqual(table_name("a.b.c"), "a.b.c")
        for bad in ("b.c", "/Volumes/a/b/c", "a.b.c.d", "./data", "a.b/c.d"):
            with self.assertRaises(ValueError):
                table_name(bad)
        with self.assertRaises(ValueError):
            FhirJson("raw_fhir", column="resource")


if __name__ == "__main__":
    unittest.main()
