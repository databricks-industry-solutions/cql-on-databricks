package com.databricks.cql.sema

import com.databricks.cql.ir.CqlType
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.sema.generated.FhirR4Model
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The FHIR model-typing facade (`TYPE-FHIR-MODEL`, `LANG-CHOICE-MODEL`, Gate 1 unit layer): the
  * `value[x]` choice gate plus the precise FHIR-leaf typing now DERIVED from the build-time
  * generated model ([[FhirR4Model]], `BUILD-FHIR-CODEGEN`). Pure (Spark-free); the lowering behavior
  * it gates is validated end-to-end by the conformance differential + self-diff (PROC-TDD). */
@RunWith(classOf[JUnitRunner])
class FhirModelSpec extends AnyFunSuite {

  test("genuine value[x] choice elements are recognized") {
    Seq("value", "onset", "abatement", "effective", "deceased", "medication", "occurrence")
      .foreach(p => assert(FhirModel.isChoiceElement(p), s"'$p' should be a choice element"))
  }

  test("monomorphic FHIR elements are NOT choice elements (the over-fire the gate prevents)") {
    // These element names have no `[x]` form anywhere in FHIR R4. (Some names ARE a choice base in R4 —
    // `subject` via `DataRequirement.subject[x]`, and `code` via `DeviceRequest.code[x]` (Reference |
    // CodeableConcept) — so the by-name leaf set rightly contains them; the value[x] rewrite still never
    // misfires on a monomorphic `Observation.subject`/`Observation.code` because it fires only on the
    // translator's `ToValue(...)`+`As` pattern (emitted only for genuine choices) and resolves the slot
    // resource-scoped (a monomorphic property has no choice slot → the rewrite is a no-op). Step 7 makes
    // the gate per-type/model-driven outright.)
    Seq("status", "recordedDate", "period", "id", "type")
      .foreach(p => assert(!FhirModel.isChoiceElement(p), s"'$p' must not be a choice element"))
  }

  // -- The generated model is wired and correct (BUILD-FHIR-CODEGEN spot-checks) --

  test("Observation.value[x] is a choice with its typed slots modeled") {
    assert(FhirModel.isChoiceElement("value"))
    val slots = FhirModel.choiceSlots("Observation", "value")
    assert(slots.contains("valueQuantity"))
    assert(slots.contains("valueCodeableConcept"))
    assert(slots.contains("valueString"))
    assert(FhirModel.choiceOfSlot("valueQuantity").contains("value"))
    assert(FhirModel.choiceOfSlot("effectiveDateTime").contains("effective"))
  }

  test("Encounter.type is 0..* (a list of CodeableConcept)") {
    val e = FhirR4Model.types("Encounter").elements.find(_.name == "type").get
    assert(e.isList, "Encounter.type must be 0..*")
    assert(e.fhirType == "CodeableConcept")
    assert(FhirModel.navType("Encounter", "type").contains(ListType(NamedType("CodeableConcept"))))
  }

  test("Patient.birthDate is a (day-precision) Date") {
    assert(FhirModel.navType("Patient", "birthDate").contains(DateType(Some(DateTimePrecision.Day))))
  }

  test("navType localizes the ELM namespace brace and resolves primitives vs complex") {
    assert(FhirModel.navType("{http://hl7.org/fhir}Observation", "code").contains(NamedType("CodeableConcept")))
    // ObservationStatus is a code-bound enum → string-backed.
    assert(FhirModel.navType("Observation", "status").contains(StringType))
    // A value[x] choice base stays unmodeled at the navType level (the As/Is path handles it).
    assert(FhirModel.navType("Observation", "value").isEmpty)
    // An unmodeled element is conservative (no guess).
    assert(FhirModel.navType("Observation", "notAnElement").isEmpty)
  }

  test("temporal-leaf fallback is model-derived and conflict-free") {
    assert(FhirModel.temporalLeaf.get("birthDate").contains(DateType(Some(DateTimePrecision.Day))))
    assert(FhirModel.temporalLeaf.get("authoredOn").exists(_.isInstanceOf[DateTimeType]))
    // A choice base is never a (monomorphic) temporal leaf.
    assert(FhirModel.temporalLeaf.get("value").isEmpty)
  }

  test("inherited base elements resolve (Resource.id on a resource)") {
    assert(FhirModel.navType("Patient", "id").contains(StringType))
  }

  test("resourceTypes enumerates modeled RESOURCES only, not datatypes/backbones/primitives (SVC-RESOURCE-SCHEMA)") {
    val rts = FhirModel.resourceTypes
    // Allow-listed resources are present.
    Seq("Patient", "Encounter", "Observation", "Condition", "MedicationRequest")
      .foreach(t => assert(rts.contains(t), s"resourceTypes must contain the resource '$t'"))
    // Closure-pulled datatypes/backbones/primitives are NOT resources.
    Seq("CodeableConcept", "Quantity", "Period", "code", "dateTime")
      .foreach(t => assert(!rts.contains(t), s"resourceTypes must NOT contain the non-resource '$t'"))
    // Every enumerated type is modeled and flagged as a resource in the generated model.
    assert(rts.forall(FhirModel.isModeled), "every resourceTypes entry must be modeled")
    assert(rts.forall(t => FhirR4Model.types(t).isResource), "every resourceTypes entry must be a resource")
  }
}
