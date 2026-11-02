package com.databricks.cql.sema

import com.databricks.cql.ir.CqlType
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.sema.generated.FhirR4Model
import com.databricks.cql.sema.generated.FhirR4Model.{Element, FhirType}

/** The FHIR model-typing facade (`TYPE-FHIR-MODEL`, `LANG-CHOICE-MODEL`, `DATA-RETRIEVER`).
  *
  * The single engine-facing interpretation of the build-time-generated FHIR R4 element model
  * ([[FhirR4Model]], `BUILD-FHIR-CODEGEN`, `compiler/docs/data-model.md`). It supersedes the old
  * hand-maintained partial model (the `ChoiceElements`/`DateElements`/`choiceOfSlot` lists) — every
  * fact here is now DERIVED from the generated model, so there is one source of truth. Profile
  * extensions (e.g. the us-core-sex extension for `Patient.sex`) are NOT in the base FHIR XSD and
  * stay hand-modeled where the engine needs them; the engine resolves profiles → base at retrieve,
  * so the base model is sufficient for navigation / value[x] / schema completeness.
  *
  * This facade is the CQL-TYPE-level view (Spark-free): it maps FHIR types/elements onto the
  * [[CqlType]] lattice for `typeOf` precision and answers the `value[x]` choice questions lowering
  * asks. The RAW physical Spark schema of a resource (for `resourceStruct` / explicit-schema reads /
  * schema canonicalization) is the separate [[FhirSchema]] projection. */
object FhirModel {

  private def types: Map[String, FhirType] = FhirR4Model.types

  /** Localize a CQL/ELM type name to a model key: strip the ELM namespace brace
    * (`{http://hl7.org/fhir}Encounter` → `Encounter`). A bare/backbone name (`Encounter`,
    * `Patient.Contact`, `CodeableConcept`) is returned unchanged — backbone keys carry dots, so we
    * do NOT split on `.` here. */
  def localName(name: String): String = name.substring(name.lastIndexOf('}') + 1)

  /** True iff `name` (localized) is a modeled FHIR type. */
  def isModeled(name: String): Boolean = types.contains(localName(name))

  /** The modeled FHIR RESOURCE type names — the entities a retrieve can bind against (`Patient`,
    * `Encounter`, `Observation`, …), EXCLUDING datatypes / backbones / primitives pulled into the model
    * by the closure (`CodeableConcept`, `Quantity`, `code`, …). Derived from the generated allow-list
    * closure (`BUILD-FHIR-CODEGEN`, `compiler/docs/data-model.md`) filtered by the model's `isResource`
    * flag (the XSD `base` chain reaching `Resource`). The single ENUMERATION over the modeled set — the
    * per-type companion of [[isModeled]]/[[isPatientContext]] — served over HTTP so Designer's Data Rooms
    * editor offers only bindable entities (`SVC-RESOURCE-SCHEMA`). Deterministic (a `Set`); callers sort
    * at the wire (`TECH-DETERMINISM`). FHIR R4 only — OMOP/QDM live inside a binding's UDTF, never here. */
  def resourceTypes: Set[String] =
    FhirR4Model.types.collect { case (name, t) if t.isResource => name }.toSet

  // -- Inheritance-resolved element lookup -----------------------------------

  /** A type's elements with inherited base elements resolved (own elements last so a derived type
    * cannot be shadowed). Walks the XSD `base` chain (DomainResource → Resource, BackboneElement →
    * Element, …); cycle-safe. */
  private val resolvedElements: scala.collection.mutable.Map[String, Vector[Element]] =
    scala.collection.mutable.Map.empty
  private def elementsOf(typeName: String): Vector[Element] =
    resolvedElements.getOrElseUpdate(typeName, computeElements(typeName, Set.empty))
  private def computeElements(typeName: String, seen: Set[String]): Vector[Element] =
    types.get(typeName) match {
      case Some(t) if !seen(typeName) =>
        val inherited = t.base.toVector.flatMap(b => computeElements(b, seen + typeName))
        inherited ++ t.elements
      case _ => Vector.empty
    }

  /** The element named `path` on a (localized) FHIR type, if modeled and monomorphic (not a
    * `value[x]` choice base). */
  private def element(typeName: String, path: String): Option[Element] =
    elementsOf(localName(typeName)).find(e => e.name == path && e.choiceBase.isEmpty)

  // -- CqlType navigation (TYPE-FHIR-MODEL) ----------------------------------

  /** The CQL type of a modeled FHIR primitive's JSON-native value. Temporal/numeric primitives map
    * to their precision-/unit-aware [[CqlType]]; everything string-backed (string, code, uri, id,
    * markdown, base64Binary, …) is [[StringType]]. */
  private def primitiveCqlType(base: String): CqlType = base match {
    case "boolean"                                  => BooleanType
    case "integer" | "positiveInt" | "unsignedInt" => IntegerType
    case "decimal"                                  => DecimalType
    case "date"                                     => DateType(Some(DateTimePrecision.Day))
    case "dateTime" | "instant"                     => DateTimeType()
    case "time"                                     => TimeType()
    case _                                          => StringType
  }

  /** The CQL type a FHIR type name denotes when navigated to as a value: a primitive's scalar
    * [[CqlType]], otherwise a [[NamedType]] carrying the raw model type name (so further navigation
    * resolves it directly). */
  private def cqlTypeOf(fhirType: String): CqlType =
    types.get(fhirType) match {
      case Some(t) if t.isPrimitive => primitiveCqlType(t.primitiveBase.getOrElse("string"))
      case _                        => NamedType(fhirType)
    }

  /** The precise CQL type of navigating element `path` on (localized) FHIR type `typeName`, if the
    * element is modeled and monomorphic. `None` for an unmodeled type/element OR a `value[x]` choice
    * base — the caller keeps its conservative `AnyType`/`As`/`Is` handling (don't guess,
    * `TYPE-FHIR-MODEL`). A `0..*` element is a `List` of its element type. */
  def navType(typeName: String, path: String): Option[CqlType] =
    element(typeName, path).map { e =>
      val base = cqlTypeOf(e.fhirType)
      if (e.isList) ListType(base) else base
    }

  /** The FHIR type name of element or typed slot `path` on (localized) FHIR type `typeName` (`Observation`,
    * `code` → `CodeableConcept`; `valueQuantity` → `Quantity`; `birthDate` → `date`), if modeled. */
  def elementType(typeName: String, path: String): Option[String] =
    elementsOf(localName(typeName)).find(_.name == path).map(_.fhirType)

  /** The CqlType of a value[x] typed SLOT (`Dosage.DoseAndRate` `doseQuantity` → `Quantity`,
    * `Observation` `valueQuantity` → `Quantity`). [[navType]] deliberately does NOT resolve a choice
    * base OR its slots (so a bare `.value` routes through the `As`/`Is` path); this resolves a
    * navigation that has ALREADY been dispatched to a concrete slot, so nested value[x] typing can
    * continue down the chain (`LANG-CHOICE-MODEL`). `None` for a non-slot element. */
  def slotType(typeName: String, slot: String): Option[CqlType] =
    elementsOf(localName(typeName)).find(e => e.name == slot && e.choiceBase.isDefined).map { e =>
      val base = cqlTypeOf(e.fhirType)
      if (e.isList) ListType(base) else base
    }

  /** True iff element `path` on (localized) FHIR type `typeName` is `0..*` (a list) — e.g.
    * `Encounter.type` (a list of `CodeableConcept`) vs `Observation.code` (single). Drives the
    * value-set membership coding-cardinality decision without a runtime schema read
    * (`DATA-SCHEMA-CONTRACT`). An unmodeled element defaults to single (conservative). */
  def isListElement(typeName: String, path: String): Boolean =
    element(typeName, path).exists(_.isList)

  /** True iff `typeName` is a PATIENT-CONTEXT resource — one the engine keys by the patient subject
    * (DATA-SUBJECT-KEY): `Patient` itself, or a resource carrying a `subject`, `patient` or `beneficiary`
    * (`Coverage`) reference, the fields the client's reshape reads. A resource with NONE (e.g. `Medication`, a definitional
    * resource) is CONTEXT-LESS: it is not patient-scoped and is reached only by a reference FOLLOW
    * (`with [Medication] M such that M.id = <ref>`), joined by reference id rather than patient subject
    * (LANG-REFERENCE-FOLLOW). Model-derived, so it stays offline (TYPE-OFFLINE-COMPILE). */
  def isPatientContext(typeName: String): Boolean =
    localName(typeName) == "Patient" || SubjectReferenceFields.exists(element(typeName, _).isDefined)

  /** The elements that can hold a resource's patient reference (DATA-SUBJECT-KEY), in the order the client's
    * reshape reads them. */
  val SubjectReferenceFields: Seq[String] = Seq("subject", "patient", "beneficiary")

  /** A model-derived temporal-leaf fallback for navigation whose SOURCE type is unmodeled
    * (`AnyType`, e.g. a context `ExpressionRef` with no resolvable body): the CQL temporal type if
    * EVERY modeled element named `path` agrees on a temporal primitive (so it is unambiguous), else
    * `None`. Supersedes the old hand-maintained `DateElements`/`DateTimeElements` lists with a
    * model-derived, conflict-free fact. */
  val temporalLeaf: Map[String, CqlType] = {
    val byName = types.values.flatMap(_.elements)
      .filter(_.choiceBase.isEmpty)
      .groupBy(_.name)
    byName.flatMap { case (name, es) =>
      val fhirTypes = es.map(_.fhirType).toVector.distinct
      val temporalBases = fhirTypes.flatMap { ft =>
        types.get(ft).filter(_.isPrimitive).flatMap(_.primitiveBase)
      }.collect { case b @ ("date" | "dateTime" | "instant" | "time") => b }.distinct
      // Unambiguous only if every occurrence is the SAME temporal primitive and none is non-temporal.
      if (temporalBases.size == 1 && temporalBases.size == fhirTypes.size)
        Some(name -> primitiveCqlType(temporalBases.head))
      else None
    }.toMap
  }

  // -- value[x] choices (LANG-CHOICE-MODEL) ----------------------------------

  /** All `value[x]` choice base names across the model (`value`, `effective`, `onset`, …). */
  val ChoiceElements: Set[String] =
    types.values.flatMap(_.elements).flatMap(_.choiceBase).toSet

  /** True iff a navigated element leaf `path` is a genuine FHIR `value[x]` choice element. */
  def isChoiceElement(path: String): Boolean = ChoiceElements.contains(path)

  /** The choice element a physical typed slot belongs to, e.g. `valueQuantity` → `value`,
    * `onsetDateTime` → `onset` (`LANG-CHOICE-MODEL`). `None` for an ordinary field. Derived from the
    * model's slot↔choice mapping. */
  def choiceOfSlot(field: String): Option[String] = slotToBase.get(field)
  private val slotToBase: Map[String, String] =
    types.values.flatMap(_.elements).flatMap(e => e.choiceBase.map(e.name -> _)).toMap

  /** The typed slot names of choice element `base` on (localized) FHIR type `typeName`
    * (`Observation`, `value` → `valueQuantity`, `valueCodeableConcept`, …). Model-driven, so the
    * value[x] rewrite resolves slots without a runtime schema read. */
  def choiceSlots(typeName: String, base: String): Seq[String] =
    elementsOf(localName(typeName)).filter(_.choiceBase.contains(base)).map(_.name)

  /** The single typed slot of choice element `base` on (localized) FHIR type `typeName` whose FHIR
    * datatype carries a (monomorphic) element named `subfield` (`LANG-CHOICE-MODEL`). Dispatches a
    * `value[x]` choice navigated to a SUB-FIELD to the slot that actually has that sub-field
    * (`MedicationRequest.medication.reference` → `medicationReference`, whose `Reference` type has a
    * `reference` element — the `medicationCodeableConcept` slot's `CodeableConcept` type has no
    * `reference`, so it is excluded). Model-driven (no runtime schema read). `None` unless EXACTLY one
    * slot's type carries the sub-field, so an ambiguous/absent navigation is never narrowed. */
  def choiceSlotForSubfield(typeName: String, base: String, subfield: String): Option[String] =
    elementsOf(localName(typeName))
      .filter(e => e.choiceBase.contains(base) && element(e.fhirType, subfield).isDefined)
      .map(_.name) match {
        case Seq(one) => Some(one)
        case _        => None
      }

  // -- Profiled extensions (DATA-MODEL-SCHEMA) -------------------------------

  /** Hand-modeled PROFILED extensions: a CLOSED allow-list of extension URLs → the declared FHIR type
    * of their `value[x]`. Base FHIR XSD does not carry profile extension definitions (out of XSD scope,
    * `compiler/docs/data-model.md`), so the engine hand-models the URLs the measures navigate.
    * This drives TYPED-SLOT DISPATCH for a bare `extension(url).value` (`Patient.sex` → the us-core-sex
    * extension → `code` → the `valueCode` slot): with a complete/heterogeneous schema the declared type
    * picks the one slot, instead of coalescing the populated slot (`compiler/docs/data-model.md`).
    * Widening to another profiled extension is a one-line allow-list change here. */
  val profiledExtensionValueType: Map[String, String] = Map(
    "http://hl7.org/fhir/us/core/StructureDefinition/us-core-sex"      -> "code",
    "http://hl7.org/fhir/us/core/StructureDefinition/us-core-birthsex" -> "code",
    // QI-Core `recorded` (the instant a resource was recorded): a `dateTime`-valued extension the
    // QI-Core model surfaces as a first-class element (e.g. `Procedure.recorded`, read by a
    // denominator exception). Navigated as `extension(url).value` → the `valueDateTime` slot.
    "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-recorded" -> "dateTime",
    // QI-Core negation-rationale extensions, both `CodeableConcept`-valued: the reason a
    // request was NOT made (`ServiceNotRequested.reasonRefused` → `doNotPerformReason`) or an
    // observation was cancelled (`ObservationCancelled.notDoneReason` → `notDoneReason`). Navigated as
    // `extension(url).value` → the `valueCodeableConcept` slot (then value-set membership reads `.coding`).
    "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-doNotPerformReason" -> "CodeableConcept",
    "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-notDoneReason"      -> "CodeableConcept")

  /** The physical typed value slot a profiled extension's `value[x]` resolves to (`code` → `valueCode`),
    * if the URL is allow-listed (`profiledExtensionValueType`). */
  def profiledExtensionSlot(url: String): Option[String] =
    profiledExtensionValueType.get(url).map(t => "value" + localName(t).capitalize)
}
