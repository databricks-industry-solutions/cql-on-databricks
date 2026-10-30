package com.databricks.cql.retrieve

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.sema.FhirSchema
import com.databricks.cql.spark.SystemColumns
import org.apache.spark.sql.types.{StringType, StructField, StructType}
import org.apache.spark.sql.{Column, functions => F}

/** The single schema-complete resource struct a retrieve mount carries (DATA-RETRIEVE-VIEW): the FHIR
  * data columns plus the data-usage lineage (DATA-EXPORT) — `__resource_type` (the retrieve's type)
  * and `__resource_id` (the resource id) — packed into ONE `STRUCT` column ([[SystemColumns.Resource]]),
  * so every adapter returns the SAME `(__subject_id, __resource)` contract and the struct flows through
  * the plan unchanged (consumers navigate `__resource.<field>`, never rebuild it).
  *
  * PUBLIC and engine-core so a host's `DataProvider` builds the contract with the engine's own schema
  * logic (the conformance adapter calls this). The resource's data fields come from the STATIC model
  * ([[FhirSchema]]), never a live-frame `.columns` round-trip (DATA-SCHEMA-CONTRACT,
  * ARCH-NO-RUNTIME-SCHEMA): with the explicit-schema read the frame carries exactly these fields, so
  * the struct is schema-complete (every model element, absent → null) and navigation is total. The
  * extra null fields are inert — a raw-resource export renders by `__resource_type`/`__resource_id`
  * identity (DATA-EXPORT), never by struct shape. An unmodeled type has no static schema → loud
  * frontier (CORE-NO-SILENT-MISLOWER). */
object ResourceStruct {

  /** The resource struct over a raw retrieve frame's top-level columns: every modeled data field of
    * `rt` (the reserved `__*` columns excluded) plus the `__resource_type`/`__resource_id` lineage.
    * `maxNesting` MUST match the explicit-schema read and the lowering side
    * ([[FhirSchema.rawResourceSchema]] / `Config.fhirMaxNestingLevel`).
    *
    * Built field-for-field from [[structType]] so the produced column's schema is IDENTICAL to the
    * statically-derived `DataType` (they cannot drift): the data fields select the raw frame's columns
    * by name, the two lineage fields carry the retrieve's type / the resource id. */
  def of(rt: String, maxNesting: Int = FhirSchema.DefaultMaxNesting): Column = of(rt, maxNesting, None)

  /** As [[of]], but ARCH-RETRIEVE-PROJECT: when `keep = Some(fields)`, build ONLY those top-level data
    * fields (plus the always-present identity lineage), so the adapter emits a demand-pruned struct
    * rather than the whole body. `keep = None` → the full body (unchanged). The kept data fields select
    * the raw frame's columns by name (present with the full-schema read, or with a matching narrowed
    * read); a `keep` field absent from the model is ignored by [[structType]]. */
  def of(rt: String, maxNesting: Int, keep: Option[Set[String]]): Column =
    F.struct(structType(rt, maxNesting, keep).fields.toIndexedSeq.map { f =>
      f.name match {
        case SystemColumns.ResourceType => F.lit(rt).as(f.name)
        case SystemColumns.ResourceId   => F.col("id").cast("string").as(f.name)
        case data                       => F.col(data)
      }
    }: _*)

  /** ARCH-RETRIEVE-PROJECT correctness GUARD: re-project an EXISTING `__resource` column (`resource`,
    * whatever the adapter returned — full or already-narrowed) to the pruned schema
    * `structType(rt, maxNesting, Some(keep))` by selecting each kept data field / identity field BY NAME.
    * So the mount's physical struct is exactly what the engine assumes, regardless of whether the adapter
    * honored the projection hint (never a runtime `.schema` read — the field names come from the model,
    * TYPE-OFFLINE-COMPILE). */
  def reproject(resource: Column, rt: String, keep: Set[String],
                maxNesting: Int = FhirSchema.DefaultMaxNesting): Column =
    F.struct(structType(rt, maxNesting, Some(keep)).fields.toIndexedSeq.map(f =>
      resource.getField(f.name).as(f.name)): _*)

  /** The static `DataType` of the [[of]] resource struct — the FHIR data fields (reserved `__*` excluded)
    * of `rt` from the STATIC model ([[FhirSchema.rawResourceSchema]]) followed by the
    * `__resource_type`/`__resource_id` lineage fields, in that order. This is the resource `__val` schema
    * a retrieve/union arm carries ([[com.databricks.cql.lower.QueryLowering.retrieveRelationFrame]]),
    * derived WITHOUT a live-frame `.schema` round-trip (ARCH-NO-RUNTIME-SCHEMA) so a heterogeneous `Choice`
    * aligns its resource-body member slots server-free (TYPE-OFFLINE-COMPILE, `FrameLowering.tagChoiceArms`).
    * An unmodeled type is a loud frontier (CORE-NO-SILENT-MISLOWER). */
  def structType(rt: String, maxNesting: Int = FhirSchema.DefaultMaxNesting): StructType =
    structType(rt, maxNesting, None)

  /** As [[structType]], but ARCH-RETRIEVE-PROJECT: when `keep = Some(fields)`, keep only those top-level
    * data fields (identity lineage is always appended). `keep = None` → every modeled data field. The
    * field ORDER follows the model (`rawResourceSchema`), so the pruned struct is deterministic and both
    * arms of a same-type union / the engine guard agree. */
  def structType(rt: String, maxNesting: Int, keep: Option[Set[String]]): StructType = {
    if (!FhirSchema.isModeled(rt))
      throw UnsupportedNode("Retrieve", s"resource type '$rt' is not in the static FHIR model — widen the allow-list in FhirModelGenerator")
    // `id` is ALWAYS retained under a projection: it is the resource identity backbone lowering relies
    // on structurally (the `__ordinal`/dedup key, the `__resource_id` lineage) independent of any CQL
    // navigation, so pruning it breaks `__resource.id` resolution (ARCH-RETRIEVE-PROJECT).
    val dataFields = FhirSchema.rawResourceSchema(rt, maxNesting).fields
      .filterNot(f => SystemColumns.isReserved(f.name))
      .filter(f => keep.forall(k => k.contains(f.name) || f.name == "id"))
    // Both lineage fields are nullable in the schema: `__resource_type` is semantically never null
    // (`F.lit(rt)`), but Spark's `RETURNS TABLE` DDL forbids NOT NULL
    // (NOT_NULL_ON_FUNCTION_PARAMETERS), so a UDTF mount cannot honor a NOT NULL contract.
    // Declaring nullable here keeps the schema consistent across all mount paths.
    StructType(dataFields.toIndexedSeq ++ Seq(
      StructField(SystemColumns.ResourceType, StringType, nullable = true),
      StructField(SystemColumns.ResourceId, StringType, nullable = true)))
  }
}
