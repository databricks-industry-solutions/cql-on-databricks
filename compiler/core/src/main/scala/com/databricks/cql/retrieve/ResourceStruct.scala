package com.databricks.cql.retrieve

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.sema.FhirSchema
import com.databricks.cql.spark.SystemColumns
import org.apache.spark.sql.types.{StringType, StructField, StructType}
import org.apache.spark.sql.{Column, functions => F}

/** BIND-CONTRACT, DATA-MODEL-SCHEMA: the stock resource struct, the `resource` a stock relation returns: the FHIR
  * data fields of a type from the STATIC model ([[FhirSchema]]), never a live-frame `.columns` round-trip
  * (DATA-SCHEMA-CONTRACT, ARCH-NO-RUNTIME-SCHEMA), so the struct is schema-complete (every model element, absent →
  * null) and navigation is total. It holds FHIR's fields only: no engine lineage. Lowering reads a resource's
  * identity from its `id` and its type from the retrieve (DATA-EXPORT).
  *
  * PUBLIC and engine-core so a host's `DataProvider` builds the contract with the engine's own schema logic (the
  * conformance adapter calls this). An unmodeled type has no static schema → loud frontier
  * (CORE-NO-SILENT-MISLOWER). */
object ResourceStruct {

  /** The resource struct over a raw retrieve frame's top-level columns: every modeled data field of `rt` (the
    * reserved `__*` columns excluded), selected by name, so the column's schema is IDENTICAL to [[structType]]. */
  def of(rt: String): Column = of(rt, None)

  /** As [[of]], but ARCH-RETRIEVE-PROJECT: when `keep = Some(fields)`, build ONLY those top-level data fields (and
    * `id`), so the adapter emits a demand-pruned struct rather than the whole body. `keep = None` → the full body.
    * A `keep` field absent from the model is ignored by [[structType]]. */
  def of(rt: String, keep: Option[Set[String]]): Column =
    F.struct(structType(rt, keep).fields.toIndexedSeq.map(f => F.col(f.name)): _*)

  /** ARCH-RETRIEVE-PROJECT correctness GUARD: re-project an EXISTING resource column (`resource`, whatever the
    * adapter returned — full or already-narrowed) to the pruned schema `structType(rt, Some(keep))` by selecting each
    * kept field BY NAME, so the mount's physical struct is exactly what the engine assumes (never a runtime
    * `.schema` read — the field names come from the model, TYPE-OFFLINE-COMPILE). */
  def reproject(resource: Column, rt: String, keep: Set[String]): Column =
    F.struct(structType(rt, Some(keep)).fields.toIndexedSeq.map(f =>
      resource.getField(f.name).as(f.name)): _*)

  /** The static `DataType` of the [[of]] resource struct: the FHIR data fields (reserved `__*` excluded) of `rt`
    * from the STATIC model ([[FhirSchema.rawResourceSchema]]), in the model's order. This is the resource `__val`
    * schema a retrieve/union arm carries, derived WITHOUT a live-frame `.schema` round-trip
    * (ARCH-NO-RUNTIME-SCHEMA, TYPE-OFFLINE-COMPILE). An unmodeled type is a loud frontier
    * (CORE-NO-SILENT-MISLOWER). */
  def structType(rt: String): StructType =
    structType(rt, None)

  /** As [[structType]], but ARCH-RETRIEVE-PROJECT: when `keep = Some(fields)`, keep only those top-level data fields
    * and `id`. `keep = None` → every modeled data field. The field ORDER follows the model (`rawResourceSchema`), so
    * the pruned struct is deterministic and both arms of a same-type union / the engine guard agree. */
  def structType(rt: String, keep: Option[Set[String]]): StructType = {
    if (!FhirSchema.isModeled(rt))
      throw UnsupportedNode("Retrieve", s"resource type '$rt' is not in the static FHIR model — widen the allow-list in FhirModelGenerator")
    // `id` is ALWAYS retained under a projection: it is the resource identity lowering relies on structurally
    // (the `__ordinal`/dedup key, DATA-EXPORT) independent of any CQL navigation (ARCH-RETRIEVE-PROJECT).
    StructType(FhirSchema.rawResourceSchema(rt).fields
      .filterNot(f => SystemColumns.isReserved(f.name))
      .filter(f => keep.forall(k => k.contains(f.name) || f.name == "id"))
      .toIndexedSeq)
  }
}
