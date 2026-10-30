package com.databricks.cql.it

import org.apache.spark.sql.{functions => F, DataFrame, Row, SparkSession}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.{BufferedOutputStream, File, FileOutputStream}
import java.nio.file.Files
import java.util.zip.{ZipEntry, ZipOutputStream}

/** SPIKE (EVAL-DEFINE-EVALUATOR return-type feasibility): what can the coarse `cql_define` node
  * actually RETURN over Spark Connect, given (a) the runtime jar is predeployed ahead of any CQL
  * (a register-by-name UDF's return type is frozen at deploy time) and (b) results are MATERIALIZED
  * frequently for audit (must survive write→read and stay field-queryable)? Fixed-struct return
  * (`CanonTemporal`) and any-shape struct INPUT are already proven (`UdfFeasibilitySpec`). This probes
  * the open options through a materialize→read-back→query cycle:
  *   A. UDF returns `array<struct>` (a List of a FIXED tuple type).
  *   B. UDF returns `binary` (the opaque envelope) — materializes but is NOT field-queryable.
  *   C. Option 2 — bespoke `array<struct>` built IN-PLAN by `named_struct`/`array` (no UDF return).
  *   D. VARIANT — evaluator emits a JSON string, plan `parse_json` → `variant` (one deploy-fixed
  *      type, yet self-describing + `variant_get`-queryable after materialization).
  * Throwaway probe — the run itself is the report. */
object RetSpikeFns {
  // Object-held `val`s so each lambda's class lives HERE (no spec capture) — the proven recipe.
  val listStructFn: Seq[String] => Seq[CanonTemporal] =
    ss => if (ss == null) null
    else ss.filter(_ != null).map(s => CanonTemporal(s.filter(_.isDigit).take(4).toLong, s.length))
  val toBinaryFn: String => Array[Byte] =
    s => if (s == null) null else s.getBytes("UTF-8")
  // Evaluator-emits-JSON pattern: a bespoke-shape result rendered as a JSON string the plan wraps.
  val toJsonFn: Row => String = r => {
    val status = r.getAs[String]("status")
    val n = r.getAs[Int]("n")
    s"""{"status":"$status","n":$n}"""
  }
}

@RunWith(classOf[JUnitRunner])
final class ReturnTypeFeasibilitySpec extends AnyFunSuite {

  private def shipTestClasses(spark: SparkSession): Unit = {
    val classesDir = new File("build/classes/scala/test")
    assert(classesDir.isDirectory, s"classes dir missing: ${classesDir.getAbsolutePath}")
    val jar = File.createTempFile("retspike", ".jar")
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

  /** Write to parquet and read back — the "materialized for audit" round-trip. */
  private def matRoundTrip(spark: SparkSession, df: DataFrame, name: String): DataFrame = {
    val dir = Files.createTempDirectory(s"retspike_$name").toFile
    dir.deleteOnExit()
    val path = new File(dir, "t.parquet").getAbsolutePath
    df.write.mode("overwrite").parquet(path)
    spark.read.parquet(path)
  }

  test("A. UDF returns array<struct> (List<fixed-tuple>), materializes, field-queryable") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_list", RetSpikeFns.listStructFn)
    val df = spark.sql("SELECT cql_list(array('2019','20')) AS xs")
    val rt = matRoundTrip(spark, df, "A")
    val r = rt.selectExpr("xs[0].instant", "xs[0].precision", "xs[1].precision").collect().head
    assert(r.getLong(0) == 2019L && r.getInt(1) == 4 && r.getInt(2) == 2, s"r=$r")
  }

  test("B. UDF returns binary, materializes (opaque — no field query)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_bin", RetSpikeFns.toBinaryFn)
    val df = spark.sql("SELECT cql_bin('hello') AS b")
    val rt = matRoundTrip(spark, df, "B")
    val bytes = rt.collect().head.getAs[Array[Byte]](0)
    assert(new String(bytes, "UTF-8") == "hello", s"bytes=${bytes.mkString(",")}")
  }

  test("C. Option 2 — bespoke array<struct> built IN-PLAN (named_struct), materializes, field-queryable") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    val df = spark.sql(
      "SELECT array(named_struct('status','finished'," +
        "'span',named_struct('lo','2019','hi','2020'))) AS result")
    val rt = matRoundTrip(spark, df, "C")
    val r = rt.selectExpr("result[0].status", "result[0].span.lo").collect().head
    assert(r.getString(0) == "finished" && r.getString(1) == "2019", s"r=$r")
  }

  test("D. VARIANT — UDF emits JSON string, plan parse_json → variant, materializes, variant_get-queryable") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_json", RetSpikeFns.toJsonFn)
    val df = spark.sql(
      "SELECT parse_json(cql_json(named_struct('status','finished','n',3))) AS v")
    val rt = matRoundTrip(spark, df, "D")
    val r = rt.selectExpr("variant_get(v,'$.status','string')", "variant_get(v,'$.n','int')")
      .collect().head
    assert(r.getString(0) == "finished" && r.getInt(1) == 3, s"r=$r")
  }
}
