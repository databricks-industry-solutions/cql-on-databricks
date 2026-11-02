package com.databricks.cql.it

import com.databricks.cql.retrieve.{DataProvider, ResourceStruct, RetrieveHints}
import com.databricks.cql.sema.{FhirModel, FhirSchema}
import com.databricks.cql.spark.SystemColumns
import org.apache.spark.sql.types.StructType
import org.apache.spark.sql.{Column, DataFrame, SparkSession, functions => F}

/** A [[DataProvider]] backed by the hand-authored FHIR NDJSON sample data, read through Spark
  * Connect (CONF-TESTDATA, DATA-RETRIEVER). It attaches the `__subject_id` context key the
  * engine's Patient-context lowering groups on: the resource id for `Patient`, the `subject`
  * reference id for other resources. The SAME files feed the Java ground-truth reference
  * (`GroundTruthRunner`), so neither side of the differential can diverge through the data.
  *
  * The subject-reference id (DATA-SUBJECT-KEY) is promoted with `substring_index` — the segment
  * after the last `/` of `"Patient/<id>"` — a cheap top-level projection rather than a per-row
  * `regexp_extract` in the hot path. (The production ingest-time/partitioned key is deferred.) */
final class SparkFhirData(spark: SparkSession, dataDir: String,
                          maxNesting: Int = FhirSchema.DefaultMaxNesting) extends DataProvider {

  // DATA-RETRIEVE-VIEW: mount the resource frame as the single `(__subject_id, __resource)` schema
  // contract — the subject key plus the resource packed into one struct ([[ResourceStruct.of]]). The
  // sample-data adapter IGNORES the advisory pushdown hints (value-set / date predicates — the engine
  // re-applies every predicate at its natural position, CONF-GATE-SELFDIFF), but it DOES honor the
  // ARCH-RETRIEVE-PROJECT `project` demand: narrow the read schema + the struct to the navigated fields.
  // Honoring is a pure read/proto optimization — the engine re-projects the mount to the same set as a
  // correctness guard, so a narrowed vs full read is identical (as pushdown-on vs -off is).
  override def retrieve(fhirType: String, hints: RetrieveHints = RetrieveHints.none): DataFrame =
    rawRetrieve(fhirType, hints.project).select(
      subjectKeyExpr(fhirType).as(SystemColumns.SubjectId),
      ResourceStruct.of(fhirType, maxNesting, hints.project).as(SystemColumns.Resource))

  /** DATA-SCHEMA-CONTRACT: read with the EXPLICIT model-derived schema (FhirSchema) — LAZY (no inference
    * action, the enabler for the offline-compile gate, TYPE-OFFLINE-COMPILE) and schema-complete (every
    * model element present, absent → null) so lowering reads schema from the model, never the frame.
    * `maxNesting` MUST match the lowering side (`Config.fhirMaxNestingLevel`). An unmodeled type has no
    * static schema → fall back to inference (never hit by the corpus). The flat read `retrieve` packs
    * into the `__resource` struct via [[ResourceStruct.of]].
    *
    * ARCH-RETRIEVE-PROJECT: when `keep = Some(fields)`, read ONLY those top-level fields PLUS the ones
    * this adapter itself needs — `id` (the `__resource_id` lineage / Patient subject key) and, for a
    * non-Patient type, its patient-reference field (the subject key). So the pruned scan still computes the
    * subject key and identity. */
  private def rawRetrieve(fhirType: String, keep: Option[Set[String]] = None): DataFrame =
    if (FhirSchema.isModeled(fhirType)) {
      val full = FhirSchema.rawResourceSchema(fhirType, maxNesting)
      val schema = keep match {
        case Some(fields) =>
          // A context-less type (LANG-REFERENCE-FOLLOW, e.g. Medication) has no subject reference — don't
          // request one.
          val subj = if (FhirModel.isPatientContext(fhirType)) subjectRefFields(fhirType).toSet else Set.empty[String]
          val required = fields ++ Set("id") ++ subj
          StructType(full.fields.filter(f => required.contains(f.name)))
        case None => full
      }
      // A modeled type with NO NDJSON file has ZERO instances in this corpus (e.g. `Medication` — reached
      // only by a reference follow, never authored as a patient-scoped resource). Synthesize an EMPTY frame
      // with the model schema rather than fail the read (PATH_NOT_FOUND): the retrieve is genuinely empty,
      // and the schema-complete model type keeps lowering offline (DATA-SCHEMA-CONTRACT, TYPE-OFFLINE-COMPILE).
      val file = new java.io.File(s"$dataDir/$fhirType.ndjson")
      if (file.exists) spark.read.schema(schema).json(file.getPath)
      else spark.createDataFrame(new java.util.ArrayList[org.apache.spark.sql.Row](), schema)
    } else spark.read.json(s"$dataDir/$fhirType.ndjson")

  /** The fields that can hold a resource's patient reference (DATA-SUBJECT-KEY), in the order the client's
    * reshape reads them: `subject`, else `patient`, else `beneficiary` (`Coverage`). Only the ones `fhirType`
    * declares. */
  private def subjectRefFields(fhirType: String): Seq[String] = {
    val declared = FhirSchema.rawResourceSchema(fhirType, maxNesting).fieldNames.toSet
    FhirModel.SubjectReferenceFields.filter(declared)
  }

  /** The Patient-context key (DATA-SUBJECT-KEY): the resource id for `Patient`, else the trailing id of the
    * first patient reference the type declares (`"Patient/<id>"` → `<id>` via `substring_index` — a cheap
    * top-level projection, no per-row regex). Always defined (the corpus is Patient-context). */
  private def subjectKeyExpr(fhirType: String): Column =
    if (fhirType == "Patient") F.col("id")
    // A context-less resource (LANG-REFERENCE-FOLLOW, e.g. Medication) has NO patient subject — it is
    // reached by a reference follow (`M.id = <ref>`), not grouped by subject. Its `__subject_id` is NULL,
    // and the reference-follow relationship join correlates on the `such that` alone, not this key.
    else if (!FhirModel.isPatientContext(fhirType)) F.lit(null).cast("string")
    else F.substring_index(F.coalesce(subjectRefFields(fhirType).map(f => F.col(s"$f.reference")): _*), "/", -1)

}
