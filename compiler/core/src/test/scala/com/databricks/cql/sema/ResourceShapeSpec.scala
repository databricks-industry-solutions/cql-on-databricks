package com.databricks.cql.sema

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** DATA-MODEL-SUBSET, BIND-CONTRACT: a bound relation's row is checked against the contract, its `resource` struct
  * as a subset of FHIR R4 for the modeled resources, naming every problem at once. Spark-free. */
@RunWith(classOf[JUnitRunner])
class ResourceShapeSpec extends AnyFunSuite {

  private def refuses(rt: String, ddl: String, mentions: String*): ResourceShape.ShapeError = {
    val e = intercept[ResourceShape.ShapeError](ResourceShape.parse(rt, ddl))
    (mentions :+ "DATA-MODEL-SUBSET").foreach(m => assert(e.getMessage.contains(m), e.getMessage))
    e
  }

  /** A view's row with `resource` of type `res`, and any root columns beside it. */
  private def rowOf(res: String, roots: String = ""): String =
    s"struct<resource:$res${if (roots.isEmpty) "" else "," + roots}>"
  private def parseR(rt: String, res: String): ResourceShape.Checked = ResourceShape.parse(rt, rowOf(res))
  private def refusesR(rt: String, res: String, mentions: String*): ResourceShape.ShapeError =
    refuses(rt, rowOf(res), mentions: _*)

  test("DATA-MODEL-SUBSET: the stock shape the reshape writes is a subset, for every modeled resource") {
    FhirModel.resourceTypes.toSeq.sorted.foreach { rt =>
      val checked = ResourceShape.parse(rt, com.databricks.cql.TestFixtures.stockShapeText(rt))
      assert(checked.struct.field("id").isDefined, rt)
    }
  }

  test("DATA-MODEL-SUBSET: a few fields, in any order, at any depth, are a subset") {
    val c = parseR("Condition",
      "struct<subject:struct<reference:string>,id:string," +
        "code:struct<coding:array<struct<code:string,system:string>>>,onsetDateTime:string>")
    assert(c.struct.fields.map(_.name) == Vector("subject", "id", "code", "onsetDateTime"))
    assert(c.fieldsByType("Condition") == Set("subject", "id", "code", "onsetDateTime"))
    assert(c.fieldsByType("Coding") == Set("code", "system"))
  }

  test("DATA-MODEL-SUBSET: loops FHIR's type graph closes are followed as deep as the shape goes") {
    parseR("Patient",
      "struct<id:string,identifier:array<struct<assigner:struct<identifier:struct<assigner:struct<display:string>>>>>," +
        "extension:array<struct<url:string,extension:array<struct<url:string,extension:array<struct<url:string," +
        "valueCodeableConcept:struct<coding:array<struct<code:string,extension:array<struct<url:string," +
        "valueString:string>>>>>>>>>>>>")
  }

  test("DATA-MODEL-SUBSET: an extension takes any of FHIR's value types, beyond the stock shape's") {
    val p = parseR("Patient",
      "struct<id:string,extension:array<struct<url:string,valueAddress:struct<city:string>,valueMarkdown:string>>>")
    assert(p.fieldsByType("Extension") == Set("url", "valueAddress", "valueMarkdown"))
  }

  test("DATA-MODEL-SUBSET: a field FHIR does not define there is refused, naming its path") {
    refusesR("Condition", "struct<id:string,legacy_flag:boolean>",
      "`Condition.legacy_flag` is not a FHIR R4 element of Condition")
    refusesR("Condition", "struct<id:string,code:struct<coding:array<struct<cd:string>>>>",
      "`Condition.code.coding.cd` is not a FHIR R4 element of Coding")
  }

  test("DATA-MODEL-SUBSET: lists are lists and single values are single") {
    refusesR("Condition", "struct<id:string,category:struct<text:string>>",
      "`Condition.category` is single; FHIR's Condition.category is a list")
    refusesR("Condition", "struct<id:string,code:array<struct<text:string>>>",
      "`Condition.code` is a list; FHIR's Condition.code is single")
  }

  test("DATA-MODEL-SUBSET: a primitive has FHIR JSON's type") {
    refusesR("Patient", "struct<id:string,birthDate:date>", "`Patient.birthDate` is date; FHIR date is string")
    refusesR("Patient", "struct<id:string,active:string>", "`Patient.active` is string; FHIR boolean is boolean")
    refusesR("Condition", "struct<id:string,code:string>", "`Condition.code` is string; FHIR CodeableConcept is a struct")
  }

  test("DATA-MODEL-SUBSET: contained and primitive extensions are not supported; id is required") {
    refusesR("MedicationRequest", "struct<id:string,contained:array<struct<id:string>>>",
      "contained resources are not supported by the compiler")
    refusesR("Patient", "struct<id:string,_birthDate:struct<extension:array<struct<url:string>>>>",
      "`Patient._birthDate`: extensions on primitive values are not supported by the compiler")
    refusesR("Condition", "struct<code:struct<text:string>>", "`Condition.id` is missing")
  }

  test("DATA-MODEL-SUBSET: every problem is named at once") {
    val e = refusesR("Condition", "struct<legacy:string,code:string>", "legacy", "code", "id")
    assert(e.problems.size == 3, e.problems)
  }

  test("DATA-MODEL-SUBSET: the type text may be written the way Scala's DDL writes it") {
    val c = parseR("Observation",
      "STRUCT<`id`: STRING NOT NULL, valueInteger: LONG COMMENT 'a count', category: ARRAY<STRUCT<text: STRING>>>")
    assert(c.struct.fields.map(_.name) == Vector("id", "valueInteger", "category"))
    refusesR("Observation", "struct<id:string,valueInteger:int>", "`Observation.valueInteger` is int; FHIR integer is bigint")
  }

  test("BIND-CONTRACT, DATA-SUBJECT-KEY, BIND-PREFILTER: a view may return patient_id, min_date and max_date beside resource") {
    val c = ResourceShape.parse("Condition", rowOf("struct<id:string>", "patient_id:bigint,min_date:date,max_date:date"))
    assert(c.access == Set("patient_id", "min_date", "max_date") && c.hasPatientId && c.hasDates)
    assert(c.struct.fields.map(_.name) == Vector("id"), "the root columns are not part of the FHIR shape")
    val one = ResourceShape.parse("Condition", rowOf("struct<id:string>", "min_date:date"))
    assert(one.access == Set("min_date") && !one.hasDates && !one.hasPatientId)
    assert(ResourceShape.parse("Patient", rowOf("struct<id:string>", "patient_id:string")).hasPatientId)
    refuses("Condition", rowOf("struct<id:string>", "patient_id:double,min_date:timestamp"),
      "`Condition.patient_id` is double; it is bigint or int or smallint or string or tinyint (DATA-SUBJECT-KEY)",
      "`Condition.min_date` is timestamp; it is date (BIND-PREFILTER)")
  }

  test("BIND-CONTRACT: a view returns resource as a struct; without it the shape is refused") {
    refuses("Condition", "struct<id:string,code:struct<text:string>>",
      "`id` is not a column of the contract", "`resource` is missing")
    refuses("Condition", "struct<resource:string>", "`resource` is string, not a struct")
  }

  test("BIND-CONTRACT: any other root column is refused, naming it") {
    refuses("Condition", rowOf("struct<id:string>", "prefilter:struct<patient_id:string>,source_system:string"),
      "`prefilter` is not a column of the contract", "`source_system` is not a column of the contract")
  }

  test("BIND-CONTRACT: the engine's own columns are refused at the root, and its lineage inside resource") {
    refuses("Condition", rowOf("struct<id:string>", "__subject_id:string"), "`__subject_id` is the engine's own column")
    refuses("Condition", rowOf("struct<id:string,__resource_type:string,__resource_id:string>"),
      "`Condition.__resource_type` is the engine's own field", "`Condition.__resource_id` is the engine's own field")
  }

  test("BIND-CONTRACT: a view in the engine's (__subject_id, __resource) form is refused, with no fallback") {
    refuses("Condition", "struct<__subject_id:string,__resource:struct<id:string,code:struct<text:string>>>",
      "`__subject_id` is the engine's own column", "`__resource` is the engine's own column", "`resource` is missing")
  }

  test("DATA-MODEL-SUBSET: a view's lower-case name names its type") {
    assert(parseR("medicationrequest", "struct<id:string>").resourceType == "MedicationRequest")
  }

  test("DATA-MODEL-SUBSET: a type outside the model, or text that is not a struct, is refused") {
    refusesR("Questionnaire", "struct<id:string>", "Questionnaire is not a modeled FHIR resource")
    refuses("Condition", "string", "not a struct")
  }
}
