package com.databricks.cql.sema

import com.databricks.cql.sema.generated.FhirR4Model
import com.databricks.cql.sema.generated.FhirR4Model.{Element, FhirType}
import org.apache.spark.sql.types._

/** The RAW physical Spark schema of a FHIR resource/datatype, derived from the static model
  * (`DATA-SCHEMA-CONTRACT`, `compiler/docs/data-model.md`, `compiler/docs/data-model.md`
  * section 4). This is the JSON-native shape the `DataProvider` reads (FHIR primitives as their JSON
  * scalar types — temporal/code as strings, the engine converts at use; numbers as long/double, cast
  * to `decimal` at use) — DISTINCT from the CQL canonical `Encoding` of a converted value. It is the
  * single source for the schema facts lowering used to round-trip the live frame for, and for the
  * explicit-schema read that makes the retrieve frame lazy (`TYPE-OFFLINE-COMPILE`).
  *
  * The schema stays FINITE and small by two bounded-encoding conventions (a literal "project every
  * element" expansion explodes into thousands of leaf fields and OOM-crashes the Connect server):
  *
  *  - BOUNDED NESTING DEPTH (`compiler/docs/data-model.md`): a configurable [[maxNesting]] cap
  *    (default [[DefaultMaxNesting]], mirroring Pathling's `maxNestingLevel`) bounds recursion through
  *    self-referential types (`Reference` → `Identifier` → `Reference`, deep backbones). A complex
  *    element whose struct would sit at or beyond the cap is DROPPED (navigation past the cap → null).
  *
  *  - EXTENSION ALLOW-LIST + OPEN TYPES (`compiler/docs/data-model.md`): FHIR extensions are
  *    open (any URL, any depth, polymorphic `value[x]` with ~50 types), so the full `Extension` type
  *    is NEVER expanded. Instead `extension` is rendered at the RESOURCE level only as a bounded
  *    synthetic struct — `url` plus one typed slot per [[OpenValueTypes|open value type]] — and dropped
  *    from every nested struct. The us-core-sex extension `Patient.sex` navigates is covered by the
  *    `code` open type (`valueCode`); navigation resolves the typed slot from the profiled-extension
  *    model ([[FhirModel.profiledExtensionValueType]]), not a coalesced field. `contained`/
  *    `modifierExtension` (recursive abstract bulk) are dropped entirely.
  *
  * Profiles/extensions beyond base R4 are not in the model (out of XSD scope) — absent fields simply
  * read as null (navigation is total by construction). */
object FhirSchema {

  /** The default maximum nesting depth. `Config.fhirMaxNestingLevel` mirrors this; both the
    * explicit-schema read (`DataProvider`) and lowering must use the SAME cap so the read frame and the
    * schema lowering projects against agree. Bumped 3→4 (was Pathling's default of 3) so a CodeableConcept
    * at FHIR depth 4 — e.g. `Encounter.hospitalization.dischargeDisposition.coding` (a
    * dischargeDisposition `~ concept`) — keeps its `.coding` rather than truncating to `{text}`. Bumped
    * 4→5 so a complex value[x] slot at FHIR depth 5 — `dosageInstruction.timing.repeat.boundsPeriod`
    * (read by a cumulative medication duration helper) — keeps its `{start,end}` rather than
    * being dropped (navigation past the cap → null). */
  val DefaultMaxNesting: Int = 5

  /** The closed OPEN-TYPE set for a bounded synthetic `Extension` value slot (`DATA-MODEL-SCHEMA`
    * section 4.4; mirrors Pathling `openTypes`). A small, finite set of the FHIR types an allow-listed
    * extension's `value[x]` may take — NOT the full ~50-slot `Extension.value[x]` surface. Each becomes
    * a `value<Type>` slot (`code` → `valueCode`, `CodeableConcept` → `valueCodeableConcept`). */
  val OpenValueTypes: Seq[String] = Seq(
    "boolean", "integer", "decimal", "string", "code", "uri", "url", "canonical",
    "dateTime", "date", "time", "instant",
    "Coding", "CodeableConcept", "Quantity", "Period", "Reference", "Identifier")

  private def types: Map[String, FhirType] = FhirR4Model.types

  /** The FHIR JSON-native Spark type of a primitive base. */
  private def primitiveSpark(base: String): DataType = base match {
    case "boolean"                                  => BooleanType
    case "integer" | "positiveInt" | "unsignedInt" => LongType
    case "decimal"                                  => DoubleType
    case _                                          => StringType // date/dateTime/instant/time/code/string/uri/id/…
  }

  // Recursive abstract bulk dropped entirely: `contained` is `Resource` (recursive, abstract, not
  // navigated); `modifierExtension` is the open Extension surface on every backbone. `extension` is
  // NOT dropped here — it is handled specially (a bounded synthetic struct at the RESOURCE level,
  // dropped when nested), so the full `Extension` type is never expanded (the schema-explosion source).
  private val Dropped = Set("contained", "modifierExtension")

  // Inheritance-resolved elements (shared with FhirModel's notion; recomputed here to stay Spark-local).
  private def elementsOf(typeName: String): Vector[Element] = computeElements(typeName, Set.empty)
  private def computeElements(typeName: String, seen: Set[String]): Vector[Element] =
    types.get(typeName) match {
      case Some(t) if !seen(typeName) =>
        t.base.toVector.flatMap(b => computeElements(b, seen + typeName)) ++ t.elements
      case _ => Vector.empty
    }

  private def isPrimitive(fhirType: String): Boolean = types.get(fhirType).exists(_.isPrimitive)
  private def primBase(fhirType: String): String = types.get(fhirType).flatMap(_.primitiveBase).getOrElse("string")

  /** The bounded synthetic `Extension` struct (`DATA-MODEL-SCHEMA` section 4.4): `url` plus one typed
    * value slot per [[OpenValueTypes|open type]] that still fits under the depth cap. Nested
    * sub-extensions are kept SHALLOW (dropped) — profiled compound extensions (us-core-race/ethnicity)
    * stay hand-modeled; they read as `url` + null values, which is all the base navigation needs. */
  private def extensionStruct(depth: Int, maxNesting: Int): StructType = {
    val valueFields = OpenValueTypes.flatMap { t =>
      openValueType(t, depth, maxNesting).map(dt => StructField("value" + t.capitalize, dt, nullable = true))
    }
    StructType(StructField("url", StringType, nullable = true) +: valueFields)
  }

  /** The Spark type of an extension's open value slot: a primitive scalar, or a bounded struct for a
    * complex open type that still fits under the cap (else dropped). */
  private def openValueType(fhirType: String, depth: Int, maxNesting: Int): Option[DataType] =
    if (isPrimitive(fhirType)) Some(primitiveSpark(primBase(fhirType)))
    else if (depth + 1 >= maxNesting) None
    else nestedStruct(fhirType, depth + 1, maxNesting)

  /** A nested complex type's struct, or `None` when it truncates to ZERO fields at the depth cap
    * (`DATA-MODEL-SCHEMA` section 4.5, `DATA-NO-EMPTY-STRUCT`). An all-complex type (e.g. `Range =
    * {low, high}`, both `Quantity`) admitted just under the cap has ALL its children dropped as complex
    * (past the cap), leaving a field-less shell. Such a struct is navigationally dead — nothing to read,
    * navigation into it yields null, IDENTICAL to the field being absent — so it is OMITTED rather than
    * emitted as `STRUCT<>`. This keeps the schema to the ACTUAL reachable FHIR shape and makes the
    * `__resource` struct writable as a real table (a `STRUCT<>` column is rejected, e.g.
    * `DELTA_CANNOT_WRITE_EMPTY_SCHEMA`). */
  private def nestedStruct(typeName: String, depth: Int, maxNesting: Int): Option[DataType] = {
    val s = struct(typeName, depth, maxNesting)
    if (s.isEmpty) None else Some(s)
  }

  /** A field's Spark type at the given nesting depth — `None` when a complex element sits at/beyond the
    * depth cap (dropped; navigation past the cap → null) OR truncates to a field-less struct
    * ([[nestedStruct]]). `extension` renders as the bounded synthetic struct (`DATA-MODEL-SCHEMA`
    * section 4.4), never the full `Extension` type. */
  private def fieldType(e: Element, depth: Int, maxNesting: Int): Option[DataType] = {
    val dt: Option[DataType] =
      if (e.name == "extension") {
        if (depth + 1 >= maxNesting) None else Some(extensionStruct(depth + 1, maxNesting))
      } else if (isPrimitive(e.fhirType)) Some(primitiveSpark(primBase(e.fhirType)))
      else if (depth + 1 >= maxNesting) None                      // depth cap: drop the complex element
      else if (types.contains(e.fhirType)) nestedStruct(e.fhirType, depth + 1, maxNesting)
      else Some(StringType)                                       // unmodeled complex (shouldn't occur in closure)
    dt.map(t => if (e.isList) ArrayType(t) else t)
  }

  /** The struct of a (modeled) complex type at the given depth. `extension` is dropped from NESTED
    * structs (depth > 0); it is kept only at the resource level (depth 0), where [[fieldType]] renders
    * the bounded synthetic struct. */
  private def struct(typeName: String, depth: Int, maxNesting: Int): StructType =
    StructType(elementsOf(typeName)
      .filterNot(e => Dropped.contains(e.name) || (e.name == "extension" && depth > 0))
      .flatMap(e => fieldType(e, depth, maxNesting).map(t => StructField(e.name, t, nullable = true))))

  /** The raw physical schema of a FHIR resource as it appears in NDJSON: the `resourceType`
    * discriminator plus every modeled resource element (the JSON envelope `id`/`meta` come from the
    * resolved Resource base), bounded by [[maxNesting]]. Used as the explicit read schema
    * (`SparkFhirData`) and as the source of `resourceStruct`/alias/context schemas. The cap MUST match
    * the read and the lowering side (both pass `Config.fhirMaxNestingLevel`). */
  def rawResourceSchema(resourceType: String, maxNesting: Int = DefaultMaxNesting): StructType = {
    val rt = FhirModel.localName(resourceType)
    val base = struct(rt, 0, maxNesting)
    // `resourceType` is the JSON type discriminator, not a model element — add it explicitly so the
    // read keeps it (inert for navigation; lineage uses the retrieve's known type).
    if (base.fieldNames.contains("resourceType")) base
    else StructType(StructField("resourceType", StringType, nullable = true) +: base.fields)
  }

  /** True iff `resourceType` is modeled (so an explicit-schema read is available). */
  def isModeled(resourceType: String): Boolean = types.contains(FhirModel.localName(resourceType))
}
