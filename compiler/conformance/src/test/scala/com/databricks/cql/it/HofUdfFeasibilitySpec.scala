package com.databricks.cql.it

import org.apache.spark.sql.{functions => F, Column, Row, SparkSession}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.{BufferedOutputStream, File, FileOutputStream}
import java.util.zip.{ZipEntry, ZipOutputStream}

/** PHASE-0 gate for EVAL-HOF-CONTROL (compiler/docs/execution-model.md): the two-layer model lowers
  * calc-forcing control flow (conditionals, folds, per-element iteration) to Spark higher-order-function
  * expressions (`transform`/`filter`/`aggregate`) whose LEAVES are fixed-output catalog UDFs. Scalar and
  * struct-input catalog UDFs over Connect are already proven (`UdfFeasibilitySpec`); the ONE untested
  * corner the whole model rides on is a catalog-UDF CALL INSIDE an HOF lambda over Spark Connect. This
  * spec closes it across the four shapes the compiler emits:
  *   - a UDF in a DataFrame `transform` lambda,
  *   - a NAMED UDF in a SQL `transform` lambda,
  *   - a NAMED UDF as a SQL `filter` predicate (the `where`/`exists` comprehension shape),
  *   - a NAMED UDF in a SQL `aggregate` MERGE over a `named_struct` accumulator (the tuple-fold linchpin).
  * Green here == the two-layer cutover is feasible; a failure is the go/no-go signal to stop.
  *
  * Throwaway probe — fold the proven shapes into the real catalog wiring once Phase 1 lands. */
object HofSpikeFns {
  // Object-held `val`s so each lambda's synthetic class lives HERE (no spec capture drags scalatest
  // onto the server) — the recipe UdfFeasibilitySpec established.
  val lenFn: String => Int = s => if (s == null) 0 else s.length
  val nonEmptyFn: String => java.lang.Boolean =
    s => if (s == null) null else java.lang.Boolean.valueOf(s.nonEmpty)
  // DataFrame-path UserDefinedFunction reused inside `F.transform`; reuses UdfSpikeFns.toCanon.
  val toCanonUdf = F.udf(UdfSpikeFns.toCanon) // String => CanonTemporal(instant, precision)
}

@RunWith(classOf[JUnitRunner])
final class HofUdfFeasibilitySpec extends AnyFunSuite {

  /** Zip this module's compiled test classes into a temp jar and upload it to the Connect server. */
  private def shipTestClasses(spark: SparkSession): Unit = {
    val classesDir = new File("build/classes/scala/test")
    assert(classesDir.isDirectory, s"classes dir missing: ${classesDir.getAbsolutePath}")
    val jar = File.createTempFile("hofspike", ".jar")
    jar.deleteOnExit()
    val zos = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(jar)))
    try {
      def add(f: File, prefix: String): Unit =
        if (f.isDirectory) f.listFiles().sorted.foreach(c => add(c, s"$prefix${f.getName}/"))
        else {
          zos.putNextEntry(new ZipEntry(prefix + f.getName))
          zos.write(java.nio.file.Files.readAllBytes(f.toPath)); zos.closeEntry()
        }
      classesDir.listFiles().sorted.foreach(c => add(c, ""))
    } finally zos.close()
    spark.addArtifact(jar.getAbsolutePath)
  }

  test("catalog UDF inside a DataFrame transform lambda executes over Connect (EVAL-HOF-CONTROL)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    import spark.implicits._
    val got = Seq(Seq("2019", "2020")).toDF("years")
      .select(F.transform(F.col("years"), (x: Column) => HofSpikeFns.toCanonUdf(x)).as("cs"))
      .collect().head.getSeq[Row](0).map(r => (r.getLong(0), r.getInt(1))).toSet
    assert(got == Set((2019L, 4), (2020L, 4)), s"got=$got")
  }

  test("named catalog UDF inside a SQL transform lambda executes over Connect (EVAL-HOF-CONTROL)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_canon", UdfSpikeFns.toCanon)
    val got = spark.sql("SELECT transform(array('2019','2020'), y -> cql_canon(y)) AS cs")
      .collect().head.getSeq[Row](0).map(r => (r.getLong(0), r.getInt(1))).toSet
    assert(got == Set((2019L, 4), (2020L, 4)), s"got=$got")
  }

  test("named catalog UDF as a SQL filter predicate over Connect (where/exists comprehension shape)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_nonempty", HofSpikeFns.nonEmptyFn)
    val got = spark.sql("SELECT filter(array('a','','bc'), x -> cql_nonempty(x)) AS f")
      .collect().head.getSeq[String](0).toSet
    assert(got == Set("a", "bc"), s"got=$got")
  }

  test("named catalog UDF in a SQL aggregate merge over a named_struct accumulator (tuple-fold linchpin)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_len", HofSpikeFns.lenFn)
    val agg = spark.sql(
      "SELECT aggregate(array('2019','20','202020'), " +
        "named_struct('n', 0, 'maxLen', 0), " +
        "(acc, x) -> named_struct('n', acc.n + 1, 'maxLen', greatest(acc.maxLen, cql_len(x))), " +
        "acc -> acc) AS agg").collect().head.getStruct(0)
    assert(agg.getInt(0) == 3 && agg.getInt(1) == 6, s"agg=$agg")
  }
}
