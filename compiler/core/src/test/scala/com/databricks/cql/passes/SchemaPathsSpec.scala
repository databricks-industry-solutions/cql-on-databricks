package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** DATA-PATH-IN-SCHEMA: a FHIR element the data model leaves out fails the compile and names the rule. Uses
  * an injected `typeOf`, as `RetrieveDemandSpec` does, so the check runs without full inference. */
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

  private def check(l: Library): Unit = SchemaPaths.check(l, typeOf)

  private def rejects(l: Library, mention: String): Unit = {
    val e = intercept[UnsupportedNode](check(l))
    assert(e.getMessage.contains(mention), e.getMessage)
    assert(e.getMessage.contains("DATA-PATH-IN-SCHEMA"), e.getMessage)
  }

  test("kept paths pass at any depth") {
    check(nav("Observation", "subject.identifier.type.coding.code"))
    check(nav("MedicationRequest", "dosageInstruction.timing.repeat.boundsPeriod.start"))
    check(nav("Patient", "identifier.assigner.reference"))
  }

  test("a value[x] base passes when the shape keeps a typed slot") {
    check(nav("Observation", "value"))
    check(nav("Observation", "component.value"))
  }

  test("an element FHIR does not define is left to lowering") {
    check(nav("Patient", "race"))         // a profile element
    check(nav("Patient", "birthDate.value")) // a primitive's value
  }

  test("the Reference → Identifier loop cut fails where it lands") {
    rejects(nav("Patient", "identifier.assigner.identifier"), "Identifier.assigner")
  }

  test("an extension on an element, a primitive or a nested extension fails") {
    rejects(nav("Observation", "code.coding.extension"), "extensions are kept on resources only")
    rejects(nav("Patient", "birthDate.extension"), "a primitive value keeps no extensions")
    rejects(nav("Patient", "extension.extension.extension"), "an extension nests one level")
  }

  test("the cut is seen through a query alias bound to a nested extension") {
    // [Patient] P return (P.extension E return (E.extension X return X.extension))
    val inner = Query(Seq(QuerySource("X", Property(None, Some("E"), "extension"))), None,
      Some(Property(None, Some("X"), "extension")))
    val outer = Query(Seq(QuerySource("E", Property(None, Some("P"), "extension"))), None, Some(inner))
    rejects(lib(edef("D", Query(Seq(QuerySource("P", retrieve("Patient"))), None, Some(outer)))),
      "an extension nests one level")
  }

  test("a backbone modifier extension keeps only its url") {
    check(nav("Observation", "component.modifierExtension.url"))
    rejects(nav("Observation", "component.modifierExtension.value"), "none of its typed slots are kept here")
  }

  test("contained resources fail") {
    rejects(nav("MedicationRequest", "contained"), "contained resources are not kept")
  }
}
