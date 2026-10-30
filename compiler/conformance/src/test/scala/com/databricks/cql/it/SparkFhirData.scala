package com.databricks.cql.it

import com.databricks.cql.retrieve.{DataProvider, ResourceStruct, RetrieveHints}
import com.databricks.cql.sema.{FhirModel, FhirSchema}
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.lower.SqlEvalEnv
import org.apache.spark.sql.types.{ArrayType, DataType, MapType, StructType}
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

  /** Escape a value for a single-quoted Spark SQL string literal (`mountsSql`): backslash and single
    * quote, matching Spark's default literal escaping. `to_json` output is single-line, so no newline
    * handling is needed. */
  private def sqlStr(s: String): String =
    s.replace("\\", "\\\\").replace("'", "\\'")

  /** The SQL twin of [[retrieve]] (EMIT-SQL-EXECUTE): render the statements that stand up each
    * `retrieve_<Type>` mount as a self-contained TABLE with its data inlined — a PORTABLE, COMPACT
    * script that runs standalone (a notebook / bare Connect session) with no dependency on this
    * harness OR the local NDJSON files. Each type emits a `CREATE TABLE retrieve_<Type>` on the
    * `(__subject_id, __resource)` mount contract (DATA-RETRIEVE-VIEW) — the model-derived `__resource`
    * struct (DATA-SCHEMA-CONTRACT) — followed by an `INSERT INTO … VALUES …` whose rows carry the
    * subject key plus each resource as a ONE-LINE JSON string, rebuilt into the struct by a single
    * `from_json(…, '<ddl>')`. That keeps the script compact (one schema DDL string per type instead of
    * the multi-KB raw column schema, plus null-omitting JSON) and lets Spark handle all the
    * nesting/nulls. The DATA is read through the SAME [[retrieve]] reshape the real DataFrame mount
    * uses (so the two mount forms cannot drift) — hence this needs a live session. A test/debug
    * artifact only; the transpiler never emits mount bodies (ARCH-PURITY, the mount is an EXTERNAL
    * runtime contract). The concrete BIND-CONTRACT example: keep the table + `from_json` reshape and
    * swap the inline `VALUES` for a real table/UNION source. `mountName` is
    * [[com.databricks.cql.transpiler.lower.SqlEvalEnv.retrieveMountName]] so the table names match
    * the script the mounts feed. */
  def mountsSql(types: Iterable[String], mountName: String => String): String = {
    val sb = new StringBuilder
    sb ++= "-- retrieve mounts (EMIT-SQL-EXECUTE): the external `retrieve_<Type>` contract this fixture's\n"
    sb ++= "-- SQL references. Each type is a self-contained TABLE on the (__subject_id, __resource)\n"
    sb ++= "-- contract (DATA-RETRIEVE-VIEW), created up front and populated by an INSERT whose VALUES carry\n"
    sb ++= "-- each resource as a one-line JSON string rebuilt with from_json(…, '<ddl>') into the\n"
    sb ++= "-- model-derived struct (DATA-SCHEMA-CONTRACT). Portable (no NDJSON paths) and compact (one DDL\n"
    sb ++= "-- string per type, null-omitting JSON). The concrete BIND-CONTRACT example: keep the table +\n"
    sb ++= "-- from_json reshape and swap the inline VALUES for a real table/UNION. The single `valuesets`\n"
    sb ++= "-- expansion table is appended below by the terminology adapter (SparkTerminology.valueSetMountsSql).\n\n"
    types.toSeq.sorted.foreach { rt =>
      val fn  = mountName(rt)
      val src = fn + "__src"           // BIND-RETRIEVE-UDF: the backing table; the mount NAME is the UDTF.
      sb ++= s"-- $rt\n"
      if (!FhirSchema.isModeled(rt)) {
        sb ++= "-- (unmodeled type: no static schema — a real binding must supply this mount)\n\n"
      } else {
        // The model-derived `__resource` struct. No empty-struct (STRUCT<>) leaves — the schema codegen
        // omits truncated-to-nothing complex types (DATA-NO-EMPTY-STRUCT), so this is writable as a real
        // table (a STRUCT<> column is rejected, e.g. DELTA_CANNOT_WRITE_EMPTY_SCHEMA).
        val resStruct = ResourceStruct.structType(rt, maxNesting)
        // The backing table: the (__subject_id, __resource) contract, model-derived `__resource` struct.
        // Dropped first so a re-run is idempotent (a standalone dump / memoized session).
        sb ++= s"DROP TABLE IF EXISTS `$src`;\n"
        sb ++= s"CREATE TABLE `$src` (\n"
        sb ++= s"  `${SystemColumns.SubjectId}` STRING,\n"
        sb ++= s"  `${SystemColumns.Resource}` ${resStruct.sql}\n"
        sb ++= ");\n\n"
        // Read the reshaped data through the SAME `retrieve` the real mount uses, then render each row
        // as `(<subject>, '<resource-json>')` — the resource serialized to a compact one-line JSON string
        // (`to_json` omits nulls). from_json rebuilds it against the model DDL on INSERT.
        val rows = retrieve(rt).select(
          F.col(SystemColumns.SubjectId),
          F.to_json(F.col(SystemColumns.Resource)).as("json")).collect()
        if (rows.isEmpty) {
          // No instances in the corpus (empty file, or a reference-follow-only type with none): the mount
          // is genuinely empty (mirroring `rawRetrieve`'s empty-frame synthesis) — leave the table unpopulated.
          sb ++= "-- (no instances in corpus: an empty typed mount — no INSERT)\n\n"
        } else {
          val ddl = resStruct.toDDL
          val values = rows.map { r =>
            val subj = if (r.isNullAt(0)) "NULL" else s"'${sqlStr(r.getString(0))}'"
            s"  ($subj, from_json('${sqlStr(r.getString(1))}', '$ddl'))"
          }
          sb ++= s"INSERT INTO `$src` VALUES\n"
          sb ++= values.mkString(",\n")
          sb ++= ";\n\n"
        }
        // BIND-RETRIEVE-UDF: the mount NAME the compiled SQL calls is a SQL UDTF over the backing table.
        // The parameter list comes from the SINGLE source of truth (SqlEvalEnv.retrieveParamsDdl,
        // BIND-EXPLICIT-PARAMS) so the fixture mount can never drift from what the emitter CALLS. It
        // ignores all eight arguments and returns the whole mount — the correct un-pruned superset the
        // plan re-filters above the call (BIND-CAPABILITY).
        sb ++= s"CREATE OR REPLACE TEMPORARY FUNCTION `$fn`(${SqlEvalEnv.retrieveParamsDdl})\n"
        sb ++= s"RETURNS TABLE(`${SystemColumns.SubjectId}` STRING, `${SystemColumns.Resource}` ${resStruct.sql})\n"
        sb ++= s"RETURN SELECT * FROM `$src`;\n\n"
      }
    }
    sb.toString
  }

  /** The TEMP-VIEW form of [[mountsSql]] for the runtime-compile E2E ([[RuntimeCompileE2ESpec]]): each
    * `retrieve_<Type>` mount is a `CREATE OR REPLACE TEMPORARY VIEW` over an inline `VALUES` table, one
    * resource per row as a one-line JSON string rebuilt into the model-derived struct by `from_json`.
    * Temp views are SESSION-SCOPED and idempotent (no `spark-warehouse` location), so a fresh client
    * session mounts them cleanly on every run — the managed-table [[mountsSql]] form collides with a
    * lingering warehouse location (`LOCATION_ALREADY_EXISTS`) when executed repeatedly. Same reshape
    * (`retrieve`) as the real mount, so the two cannot drift. */
  def mountsSqlTempViews(types: Iterable[String], mountName: String => String): String = {
    val sb = new StringBuilder
    sb ++= "-- retrieve mounts (EMIT-SQL-EXECUTE, TEMP-VIEW form for the runtime E2E): session-scoped,\n"
    sb ++= "-- idempotent (CREATE OR REPLACE), no warehouse location — the (__subject_id, __resource)\n"
    sb ++= "-- contract from inline one-line JSON rebuilt by from_json(…, '<ddl>') into the model struct.\n\n"
    types.toSeq.sorted.foreach { rt =>
      val fn = mountName(rt)
      sb ++= s"-- $rt\n"
      if (!FhirSchema.isModeled(rt)) {
        sb ++= "-- (unmodeled type: no static schema — a real binding must supply this mount)\n\n"
      } else {
        val resStruct = ResourceStruct.structType(rt, maxNesting)
        val ddl = resStruct.toDDL
        val rows = retrieve(rt).select(
          F.col(SystemColumns.SubjectId),
          F.to_json(F.col(SystemColumns.Resource)).as("json")).collect()
        // BIND-RETRIEVE-UDF: the mount is a SQL UDTF `retrieve_<Type>(<8 explicit args>)` (the sole mount
        // contract — the bare-view mount was retired), its parameter list taken from the single source of
        // truth (SqlEvalEnv.retrieveParamsDdl, BIND-EXPLICIT-PARAMS). This fixture UDTF IGNORES all eight
        // arguments and returns the whole mount — the correct un-pruned superset the plan re-filters
        // above the call (BIND-CAPABILITY).
        sb ++= s"CREATE OR REPLACE TEMPORARY FUNCTION `$fn`(${SqlEvalEnv.retrieveParamsDdl})\n"
        sb ++= s"RETURNS TABLE(`${SystemColumns.SubjectId}` STRING, `${SystemColumns.Resource}` ${resStruct.sql})\n"
        sb ++= s"RETURN SELECT `c1` AS `${SystemColumns.SubjectId}`, from_json(`c2`, '$ddl') AS `${SystemColumns.Resource}`\n"
        if (rows.isEmpty) {
          // A typed EMPTY mount: one dummy row filtered out, so the function carries the schema with no data.
          sb ++= "FROM VALUES (CAST(NULL AS STRING), CAST(NULL AS STRING)) AS `t`(`c1`, `c2`) WHERE 1=0;\n\n"
        } else {
          val values = rows.map { r =>
            val subj = if (r.isNullAt(0)) "NULL" else s"'${sqlStr(r.getString(0))}'"
            s"  ($subj, '${sqlStr(r.getString(1))}')"
          }
          sb ++= "FROM VALUES\n" + values.mkString(",\n") + s"\nAS `t`(`c1`, `c2`);\n\n"
        }
      }
    }
    sb.toString
  }
}

object SparkFhirData {

  /** The `RETURNS TABLE (…)` column DDL for a retrieve-mount UDTF, with NULLABILITY STRIPPED
    * (`BIND-RETRIEVE-UDF`).
    *
    * `StructType.toDDL` renders `NOT NULL` for every non-nullable field, and a mount DataFrame picks up
    * non-nullability incidentally — `ResourceStruct` projects `__resource_type` as `F.lit(<type>)`, and a
    * literal column is non-nullable. Spark rejects `NOT NULL` in a UDTF signature
    * (`USER_DEFINED_FUNCTIONS.NOT_NULL_ON_FUNCTION_PARAMETERS`), so passing `df.schema.toDDL` straight
    * through fails to register the mount at all.
    *
    * Stripping it is also the HONEST schema: the mount contract makes no non-null promise — a FHIR
    * element is absent far more often than not — so declaring `NOT NULL` would claim something the data
    * does not support. This mirrors what the Python client already does (`mount.py`, CLIENT-SESSION-MOUNT,
    * renders its `RETURNS TABLE` type with `reshape.sql_type`, which never carries nullability); the two mount
    * registrars are peers and must not drift.
    *
    * Built via `toDDL` over a fully-nullable copy rather than `catalogString`/`simpleString`, both of
    * which truncate a wide struct at `spark.sql.debug.maxToStringFields` — silent truncation of a
    * ~40-field FHIR resource would produce a valid-looking but WRONG signature. */
  def mountReturnsDDL(schema: StructType): String = allNullable(schema).toDDL

  private def allNullable(s: StructType): StructType =
    StructType(s.fields.map(f => f.copy(dataType = allNullable(f.dataType), nullable = true)))

  private def allNullable(t: DataType): DataType = t match {
    case s: StructType => allNullable(s)
    case a: ArrayType  => a.copy(elementType = allNullable(a.elementType), containsNull = true)
    case m: MapType    => m.copy(keyType = allNullable(m.keyType), valueType = allNullable(m.valueType),
                                 valueContainsNull = true)
    case other         => other
  }
}
