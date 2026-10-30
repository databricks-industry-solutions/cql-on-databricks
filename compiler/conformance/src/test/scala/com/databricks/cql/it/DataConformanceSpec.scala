package com.databricks.cql.it

import com.databricks.cql.sema.FhirSchema
import org.apache.spark.sql.{functions => F}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** Data-conformance gate (`DATA-SCHEMA-CONTRACT`, `compiler/docs/data-model.md`): the
  * committed FHIR NDJSON sample data conforms, per resource type, to the model-derived raw schema
  * ([[FhirSchema.rawResourceSchema]]). Reading with the EXPLICIT declared schema in FAILFAST mode
  * fails loud on any type mismatch — so the schema the engine lowers against (and the lazy
  * explicit-schema read that enables the offline-compile gate) is proven to match the real bytes.
  *
  * Skips cleanly when no Spark Connect server is reachable. */
@RunWith(classOf[JUnitRunner])
final class DataConformanceSpec extends AnyFunSuite {

  /** The data dirs the suite reads: the default FHIR fixtures, the QICore fixtures and every measure's data. */
  private def dataDirs: Seq[File] =
    (Seq(Paths.dataDir, Corpus.qicoreDataDir) ++ Corpus.measureDefs.map(_.dataDir)).filter(_.isDirectory)

  private def resourceFiles(dir: File): Seq[File] =
    Option(dir.listFiles).getOrElse(Array.empty[File]).toSeq
      .filter(f => f.getName.endsWith(".ndjson") && !f.getName.startsWith("valueset-"))
      .sortBy(_.getName)

  for {
    dir <- dataDirs
    file <- resourceFiles(dir)
  } {
    val resourceType = file.getName.stripSuffix(".ndjson")
    test(s"data conforms to model schema — ${dir.getName}/$resourceType") {
      val spark = SparkConnect.session()
      assert(FhirSchema.isModeled(resourceType),
        s"resource '$resourceType' is not in the generated model — widen the allow-list in FhirModelGenerator")
      val schema = FhirSchema.rawResourceSchema(resourceType)
      // FAILFAST: a JSON value that cannot coerce into the declared field type throws here.
      val df = spark.read.schema(schema).option("mode", "FAILFAST").json(file.getAbsolutePath)
      val n = df.count() // force the parse
      assert(n > 0, s"$resourceType data is empty")
      // Sanity: the resource id resolves (every FHIR resource has one) — proves navigation lands.
      assert(df.where(F.col("id").isNotNull).count() == n, s"$resourceType: every row must have an id")
    }
  }
}
