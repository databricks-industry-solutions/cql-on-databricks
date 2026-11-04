package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import com.databricks.cql.sema.ResourceShape
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** DATA-PATH-IN-SCHEMA, DATA-MODEL-SUBSET: a FHIR element the bound data's shape leaves out fails the compile,
  * naming the path; with no data bound, paths are checked against FHIR R4 alone. Uses an injected `typeOf`, as
  * `RetrieveDemandSpec` does, so the check runs without full inference. */
@RunWith(classOf[JUnitRunner])
class SchemaPathsSpec extends AnyFunSuite {

  private def retrieve(t: String): Retrieve = Retrieve(t, None, None, None, None, None)
  private val typeOf: Expr => CqlType = {
    case Retrieve(t, _, _, _, _, _, _, _) => CqlType.ListType(CqlType.NamedType(t))
    case _                                => CqlType.AnyType
  }

  /** `[rt] A return A.<path>` */
  private def nav(rt: String, path: String): Library =
    lib(edef("D", Query(Seq(QuerySource("A", retrieve(rt))), None, Some(Property(None, Some("A"), path)))))

  private def shapes(byType: (String, String)*): Option[Map[String, ResourceShape.Checked]] =
    Some(byType.map { case (t, ddl) => t -> ResourceShape.parse(t, s"struct<resource:$ddl>") }.toMap)

  private val Condition = "struct<id:string,code:struct<coding:array<struct<system:string,code:string>>>," +
    "onsetDateTime:string,subject:struct<reference:string>>"
  private val Patient = "struct<id:string,birthDate:string,extension:array<struct<url:string," +
    "extension:array<struct<url:string,extension:array<struct<url:string,valueCoding:struct<code:string>>>>>>>>"

  private def check(l: Library, s: Option[Map[String, ResourceShape.Checked]] = None): Unit =
    SchemaPaths.check(l, typeOf, s)

  private def rejects(l: Library, s: Option[Map[String, ResourceShape.Checked]], mention: String*): Unit = {
    val e = intercept[UnsupportedNode](check(l, s))
    (mention :+ "DATA-MODEL-SUBSET").foreach(m => assert(e.getMessage.contains(m), e.getMessage))
  }

  test("DATA-MODEL-SUBSET: with no data bound, every FHIR R4 path passes at any depth, with no cuts") {
    check(nav("Observation", "subject.identifier.type.coding.code"))
    check(nav("MedicationRequest", "dosageInstruction.timing.repeat.boundsPeriod.start"))
    check(nav("Patient", "identifier.assigner.identifier.assigner.display"))
    check(nav("Observation", "code.coding.extension.valueString"))
    check(nav("Patient", "extension.extension.extension.url"))
    check(nav("Observation", "component.modifierExtension.valueBoolean"))
  }

  test("DATA-MODEL-SUBSET: a path the shape holds passes, at any depth") {
    val s = shapes("Condition" -> Condition, "Patient" -> Patient)
    check(nav("Condition", "code.coding.code"), s)
    check(nav("Condition", "subject.reference"), s)
    check(nav("Patient", "extension.extension.extension.valueCoding.code"), s)
  }

  test("DATA-MODEL-SUBSET: a FHIR path the shape leaves out fails, naming the path") {
    val s = shapes("Condition" -> Condition)
    rejects(nav("Condition", "clinicalStatus"), s, "`Condition.clinicalStatus` is not in this environment")
    rejects(nav("Condition", "code.text"), s, "`Condition.code.text` is not in this environment")
    rejects(nav("Condition", "code.coding.display"), s, "`Condition.code.coding.display`")
  }

  test("DATA-MODEL-SUBSET: a value[x] base passes when the shape holds one typed slot, and fails with none") {
    val s = shapes("Condition" -> Condition)
    check(nav("Condition", "onset"), s)
    rejects(nav("Condition", "abatement"), s, "`Condition.abatement` is not in this environment",
      "abatementDateTime")
  }

  test("an element FHIR does not define is left to lowering") {
    check(nav("Patient", "race"))            // a profile element
    check(nav("Patient", "birthDate.value")) // a primitive's value
    check(nav("Patient", "race"), shapes("Patient" -> Patient))
  }

  test("DATA-MODEL-SUBSET: the shape is followed through a query alias bound to a nested extension") {
    // [Patient] P return (P.extension E return (E.extension X return X.<leaf>))
    def nested(leaf: String) = {
      val inner = Query(Seq(QuerySource("X", Property(None, Some("E"), "extension"))), None,
        Some(Property(None, Some("X"), leaf)))
      val outer = Query(Seq(QuerySource("E", Property(None, Some("P"), "extension"))), None, Some(inner))
      lib(edef("D", Query(Seq(QuerySource("P", retrieve("Patient"))), None, Some(outer))))
    }
    val s = shapes("Patient" -> Patient)
    check(nested("extension"), s)
    rejects(nested("value"), s, "`Patient.extension.extension.value` is not in this environment")
    rejects(nested("valueString"), s, "`Patient.extension.extension.valueString` is not in this environment")
  }

  test("DATA-MODEL-SUBSET: contained and extensions on primitives are not supported by the compiler") {
    rejects(nav("MedicationRequest", "contained"), None, "contained resources are not supported by the compiler")
    rejects(nav("Patient", "birthDate.extension"), None, "extensions on primitive values are not supported")
    rejects(nav("Patient", "birthDate.extension"), shapes("Patient" -> Patient), "`Patient.birthDate.extension`")
  }
}
