package com.databricks.cql.it

import com.databricks.cql.retrieve.TerminologyProvider
import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.types.{StringType, StructField, StructType}

/** A [[TerminologyProvider]] backed by hand-authored value-set expansions (NDJSON of
  * `{system, code}`), read through Spark Connect (DATA-TERMINOLOGY-ADAPTER). The expansion file
  * is named by the value set's last URL segment (`.../vs/diabetes` -> `valueset-diabetes.ndjson`).
  * The SAME expansion feeds the Java reference, so neither side can diverge on membership. */
final class SparkTerminology(spark: SparkSession, dataDir: String) extends TerminologyProvider {

  // Explicit `{system, code}` schema (DATA-SCHEMA-CONTRACT): read LAZILY without Spark's JSON
  // inference, so value-set expansion adds no AnalyzePlan round-trip during lowering — part of the
  // offline-compile guarantee (TYPE-OFFLINE-COMPILE).
  private val ExpansionSchema =
    StructType(Seq(StructField("system", StringType), StructField("code", StringType)))

  def expand(valueSetId: String): DataFrame = {
    val seg = valueSetId.split("/").last
    spark.read.schema(ExpansionSchema).json(s"$dataDir/valueset-$seg.ndjson")
      .select(F.col("system"), F.col("code"))
  }

  /** The SQL twin of [[expand]] for the standalone mount script (EMIT-SQL-EXECUTE, BIND-TERMINOLOGY-TABLE):
    * render the SINGLE `valuesets` mount as one self-contained `(valueset_id, system, code)` TABLE holding
    * EVERY value set's expansion, keyed by the canonical id — the terminology half of the portable,
    * harness-free `mounts.sql`. One keyed table (the way a real terminology export stores expansions), so a
    * membership test selects its set with `WHERE valueset_id = '<id>'`; the flat `{system, code}` schema
    * needs no `from_json`. A test/debug artifact only. `tableName`/`keyColumn` are
    * [[com.databricks.cql.transpiler.lower.SqlEvalEnv.valueSetsMountName]] /
    * [[com.databricks.cql.transpiler.lower.SqlEvalEnv.valueSetKeyColumn]] so the table matches the
    * script the mount feeds. */
  def valueSetMountsSql(ids: Iterable[String], tableName: String, keyColumn: String): String = {
    val sb = new StringBuilder
    sb ++= s"-- valueset mount (EMIT-SQL-EXECUTE): the single external `$tableName` ($keyColumn, system, code)\n"
    sb ++= "-- expansion table this fixture's SQL references, holding every value set keyed by id, inlined as\n"
    sb ++= "-- a self-contained table (DATA-TERMINOLOGY-ADAPTER, BIND-TERMINOLOGY-TABLE).\n\n"
    sb ++= s"DROP TABLE IF EXISTS `$tableName`;\n"
    sb ++= s"CREATE TABLE `$tableName` (\n  `$keyColumn` STRING,\n  `system` STRING,\n  `code` STRING\n);\n\n"
    // One INSERT per set (ordered for a stable dump), each row tagged with the value-set id — all rows
    // land in the one shared table. A set with an empty expansion contributes no rows.
    val inserts = ids.toSeq.sorted.flatMap { id =>
      val rows = expand(id).collect()
      if (rows.isEmpty) None
      else Some {
        val values = rows.map { r =>
          def lit(i: Int) = if (r.isNullAt(i)) "NULL" else s"'${sqlStr(r.getString(i))}'"
          s"  ('${sqlStr(id)}', ${lit(0)}, ${lit(1)})"
        }
        s"-- $id\nINSERT INTO `$tableName` VALUES\n${values.mkString(",\n")};\n"
      }
    }
    if (inserts.isEmpty) sb ++= "-- (no non-empty expansions — no INSERT)\n"
    else sb ++= inserts.mkString("\n")
    sb.toString
  }

  /** The TEMP-VIEW form of [[valueSetMountsSql]] for the runtime-compile E2E ([[RuntimeCompileE2ESpec]]):
    * the single `valuesets` mount as a `CREATE OR REPLACE TEMPORARY VIEW` over an inline `(valueset_id,
    * system, code)` `VALUES` table. Session-scoped + idempotent (no `spark-warehouse` location), so a fresh
    * client session mounts it cleanly on every run (the managed-table [[valueSetMountsSql]] form collides
    * with a lingering warehouse location on re-run). */
  def valueSetMountsSqlTempView(ids: Iterable[String], viewName: String, keyColumn: String): String = {
    val rows = ids.toSeq.sorted.flatMap { id =>
      expand(id).collect().map { r =>
        def lit(i: Int) = if (r.isNullAt(i)) "NULL" else s"'${sqlStr(r.getString(i))}'"
        s"  ('${sqlStr(id)}', ${lit(0)}, ${lit(1)})"
      }
    }
    val sb = new StringBuilder
    sb ++= s"-- valueset mount (EMIT-SQL-EXECUTE, TEMP-VIEW form): the single `$viewName` ($keyColumn,\n"
    sb ++= "-- system, code) expansion view, every set keyed by id, session-scoped + idempotent.\n"
    sb ++= s"CREATE OR REPLACE TEMPORARY VIEW `$viewName` AS\n"
    sb ++= s"SELECT `c1` AS `$keyColumn`, `c2` AS `system`, `c3` AS `code`\n"
    if (rows.isEmpty)
      sb ++= "FROM VALUES (CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING)) " +
        "AS `t`(`c1`, `c2`, `c3`) WHERE 1=0;\n"
    else
      sb ++= "FROM VALUES\n" + rows.mkString(",\n") + "\nAS `t`(`c1`, `c2`, `c3`);\n"
    sb.toString
  }

  /** Escape a value for a single-quoted Spark SQL string literal (`valueSetMountsSql`). */
  private def sqlStr(s: String): String = s.replace("\\", "\\\\").replace("'", "\\'")
}
