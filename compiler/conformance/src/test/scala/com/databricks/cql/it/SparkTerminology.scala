package com.databricks.cql.it

import com.databricks.cql.retrieve.TerminologyProvider
import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.types.{StringType, StructField, StructType}

/** A [[TerminologyProvider]] backed by hand-authored value-set expansions (NDJSON of
  * `{system, code}`, and `version` when the file holds more than one version, LANG-VALUESET-VERSION), read
  * through Spark Connect (DATA-TERMINOLOGY-ADAPTER). The expansion file is named by the value set's last URL
  * segment (`.../vs/diabetes` -> `valueset-diabetes.ndjson`). A value set with no file has no rows, so the
  * engine's refusal of a value set no relation holds can be tested. The SAME expansion feeds the Java
  * reference, so neither side can diverge on membership. */
final class SparkTerminology(spark: SparkSession, dataDir: String) extends TerminologyProvider {

  // Explicit `{system, code}` schema (DATA-SCHEMA-CONTRACT): read LAZILY without Spark's JSON
  // inference, so value-set expansion adds no AnalyzePlan round-trip during lowering — part of the
  // offline-compile guarantee (TYPE-OFFLINE-COMPILE).
  private val ExpansionSchema = StructType(Seq(StructField("system", StringType), StructField("code", StringType),
    StructField("version", StringType)))

  def expand(valueSetId: String): DataFrame = {
    val file = s"$dataDir/valueset-${valueSetId.split("/").last}.ndjson"
    val rows =
      if (new java.io.File(file).exists) spark.read.schema(ExpansionSchema).json(file)
      else spark.createDataFrame(java.util.Collections.emptyList[org.apache.spark.sql.Row](), ExpansionSchema)
    rows.select(F.col("system"), F.col("code"), F.col("version"))
  }
}
