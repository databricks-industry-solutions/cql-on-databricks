package com.databricks.cql.sema

import com.databricks.cql.sema.FhirShape.{Field, Leaf, Shape, Struct}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The data model's shape (`DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`): FHIR R4 with fields left out, a loop
  * cut instead of a depth cap, and bounded extensions. Pure (Spark-free). */
@RunWith(classOf[JUnitRunner])
class FhirShapeSpec extends AnyFunSuite {

  private val resources = FhirModel.resourceTypes.toSeq.sorted

  private def has(rt: String, path: String): Boolean =
    path.split('.').foldLeft(Option[Shape](FhirShape.resource(rt))) {
      case (Some(s: Struct), seg) => s.field(seg).map(_.shape)
      case _                      => None
    }.isDefined

  test("every modeled resource builds: each loop in its type graph is in the cut list") {
    resources.foreach(rt => assert(FhirShape.resource(rt).fields.nonEmpty, rt))
  }

  test("deep paths are kept at any depth") {
    Seq(
      "MedicationRequest" -> "dosageInstruction.timing.repeat.boundsPeriod.start",
      "Encounter"         -> "hospitalization.dischargeDisposition.coding.code",
      "Observation"       -> "subject.identifier.type.coding.code",
      "Observation"       -> "component.valueQuantity.value",
      "Patient"           -> "identifier.assigner.reference",
      "Patient"           -> "extension.extension.valueCoding.code",
      "Observation"       -> "modifierExtension.valueBoolean",
      "Observation"       -> "component.modifierExtension.url"
    ).foreach { case (rt, p) => assert(has(rt, p), s"$rt.$p should be kept") }
  }

  test("the cuts and the extension rules leave out exactly what they name") {
    Seq(
      "Patient"     -> "identifier.assigner.identifier",   // the Reference → Identifier loop cut
      "Patient"     -> "extension.extension.extension",    // extensions nest one level
      "Observation" -> "modifierExtension.extension",      // a modifier extension does not nest
      "Observation" -> "code.coding.extension",            // extensions are kept on resources only
      "Observation" -> "component.modifierExtension.valueBoolean", // a backbone keeps the url only
      "Observation" -> "contained"
    ).foreach { case (rt, p) => assert(!has(rt, p), s"$rt.$p should be left out") }
  }

  test("an extension keeps one value slot per open type, after its url") {
    val ext = FhirShape.ofType("Extension").get.asInstanceOf[Struct]
    assert(ext.fields.map(_.name) ==
      ("url" +: FhirShape.OpenValueTypes.map(t => "value" + t.capitalize)) :+ "extension")
  }

  test("every datatype has one shape, except where a cut lands") {
    val shapes = scala.collection.mutable.Map.empty[String, Set[Shape]]
    def walk(typeName: String, s: Shape): Unit = s match {
      case st: Struct =>
        shapes(typeName) = shapes.getOrElse(typeName, Set.empty) + st
        st.fields.foreach { case Field(n, sub, _) =>
          FhirModel.elementType(typeName, n).foreach(t => walk(FhirModel.localName(t), sub))
        }
      case _: Leaf => ()
    }
    resources.foreach(rt => walk(rt, FhirShape.resource(rt)))
    val many = shapes.collect { case (t, ss) if ss.size > 1 => t -> ss.size }.toMap
    assert(many == Map(
      "Reference" -> 2,  // full, and inside Identifier.assigner without `identifier`
      "Extension" -> 3), // on a resource; nested (and a resource's modifier extension); a backbone's url only
      s"types with more than one shape: $many")
  }
}
