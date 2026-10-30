package com.databricks.cql.it

import org.apache.spark.sql.{functions => F, Row, SparkSession}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.{BufferedOutputStream, File, FileOutputStream}
import java.util.zip.{ZipEntry, ZipOutputStream}

/** SPIKE (branch `udf`, EXEC-OPAQUE-UDF / EXEC-UDF-LIBRARY feasibility): can this Spark Connect
  * client register and EXECUTE an opaque Scala (JVM lambda) UDF against the OSS Connect server —
  * including a UDF whose input is a struct `Row` (what `cql_choice_nav` needs)? The first run
  * proved the server rejects the closure with SparkClassNotFoundException + "install the artifact
  * by calling session.addArtifact". This version PROVES THE REMEDY: package the compiled classes
  * into a jar and `spark.addArtifact` it (the client uploads to the server at runtime), then run
  * the same UDFs. Green here == opaque UDFs are viable over Connect via runtime artifact upload
  * (no manual pre-install on OSS).
  *
  * Throwaway probe — delete once the read is definitive. */
/** UDFs defined in a STANDALONE top-level object so their closures capture no enclosing spec
  * (which would drag scalatest onto the server). This is how the real `runtime` library
  * would be structured: a clean object with minimal transitive deps. */
/** A canonical typed return (the `EXEC-UDF-CATALOG` shape): a fixed struct type a catalog UDF
  * returns — proving a UDF can return an EXACT custom type, not one generalized type. */
final case class CanonTemporal(instant: Long, precision: Int)

object UdfSpikeFns {
  // DataFrame-path UDFs (Column API + addArtifact).
  val upper = F.udf((s: String) => if (s == null) null else s.toUpperCase)
  val pick = F.udf { (r: Row) =>
    val tag = r.getAs[String]("tag")
    if (tag == "a") r.getAs[String]("a_val") else r.getAs[String]("b_val")
  }
  // SQL-path functions (registered by NAME via spark.udf.register, referenced from emitted SQL).
  // Held as object-level `val`s so the lambda's class lives in THIS object (no spec capture) — the
  // catalog UDFs must be defined this way, never eta-expanded at the registration call site.
  val upperFn: String => String = s => if (s == null) null else s.toUpperCase
  val toCanon: String => CanonTemporal =
    s => if (s == null) null else CanonTemporal(s.filter(_.isDigit).take(4).toLong, s.length)
}

@RunWith(classOf[JUnitRunner])
final class UdfFeasibilitySpec extends AnyFunSuite {

  /** Zip this module's compiled test classes into a temp jar and upload it to the Connect server. */
  private def shipTestClasses(spark: SparkSession): Unit = {
    val classesDir = new File("build/classes/scala/test")
    assert(classesDir.isDirectory, s"classes dir missing: ${classesDir.getAbsolutePath}")
    val jar = File.createTempFile("udfspike", ".jar")
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

  test("opaque scalar Scala UDF executes over Connect (after addArtifact)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    import spark.implicits._
    val got = Seq("a", "bc").toDF("x").select(UdfSpikeFns.upper(F.col("x")).as("u"))
      .collect().map(_.getString(0)).toSet
    assert(got == Set("A", "BC"), s"got=$got")
  }

  test("opaque Scala UDF over a STRUCT Row executes over Connect (cql_choice_nav shape)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    import spark.implicits._
    val got = Seq(("a", "x1", "y1"), ("b", "x2", "y2")).toDF("tag", "a_val", "b_val")
      .select(UdfSpikeFns.pick(F.struct(F.col("tag"), F.col("a_val"), F.col("b_val"))).as("picked"))
      .collect().map(_.getString(0)).toSet
    assert(got == Set("x1", "y2"), s"got=$got")
  }

  // --- SQL-path proof: the mechanism EXEC-SQL-BACKEND depends on — a NAMED opaque Scala UDF,
  // registered via spark.udf.register, called from emitted SQL text over Connect. ---

  test("named scalar Scala UDF is callable from emitted SQL over Connect (EXEC-SQL-BACKEND)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_upper", UdfSpikeFns.upperFn)
    val got = spark.sql("SELECT cql_upper('ab') AS u").collect().head.getString(0)
    assert(got == "AB", s"got=$got")
  }

  test("named STRUCT-returning Scala UDF callable from SQL — exact typed return (EXEC-UDF-CATALOG)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    spark.udf.register("cql_canon", UdfSpikeFns.toCanon)
    val c = spark.sql("SELECT cql_canon('2020') AS c").collect().head.getStruct(0)
    assert(c.getLong(0) == 2020L && c.getInt(1) == 4, s"c=$c")
  }

  // --- THE Phase-2 linchpin: a STRUCT(Row)-INPUT opaque UDF, registered BY NAME, called from emitted
  // SQL text over Connect (the `cql_choice_nav(choiceStruct, field)` shape). The struct-Row spike above
  // proved Row input via the DataFrame `F.udf` path; the SQL-path spikes proved name+SQL for SCALAR
  // input. This closes the ONE untested combination Phase 2 rides on: Row input AND name AND SQL. ---

  test("named STRUCT-INPUT Scala UDF callable from emitted SQL over Connect (cql_choice_nav SQL path)") {
    val spark = SparkConnect.session()
    shipTestClasses(spark)
    // Register the SAME Row-input UserDefinedFunction the DataFrame spike used, by NAME.
    spark.udf.register("cql_pick", UdfSpikeFns.pick)
    // Build the discriminated-struct input INSIDE emitted SQL text and navigate through the opaque UDF.
    val got = spark.sql(
      "SELECT cql_pick(struct('a' AS tag, 'x1' AS a_val, 'y1' AS b_val)) AS p " +
      "UNION ALL SELECT cql_pick(struct('b' AS tag, 'x2' AS a_val, 'y2' AS b_val))")
      .collect().map(_.getString(0)).toSet
    assert(got == Set("x1", "y2"), s"got=$got")
  }
}
