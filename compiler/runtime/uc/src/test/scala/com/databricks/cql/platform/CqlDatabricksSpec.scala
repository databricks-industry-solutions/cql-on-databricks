package com.databricks.cql.platform

import org.apache.spark.sql.SparkSession
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Spark-free guard for the CqlDatabricks session entry point (`SVC-RUNTIME-UDF`, `PROC-TDD`). It compiles a
  * reference to `CqlDatabricks.init` and to the engine catalog `registerAll` it delegates to (`ACC-ENGINE-CLIENT`),
  * so an engine package rename (`ACC-NAMESPACE-ALIGN`) or a signature change breaks THIS build loudly —
  * the earliest possible signal — instead of surfacing only at runtime in a notebook. No live
  * `SparkSession` is created; the checks are pure type/reflection.
  *
  * `@RunWith(JUnitRunner)` is required for discovery under the JUnit Platform (mirrors compiler-app). */
@RunWith(classOf[JUnitRunner])
class CqlDatabricksSpec extends AnyFunSuite {

  test("CqlDatabricks.init is a SparkSession => Unit session entry point") {
    val entry: SparkSession => Unit = CqlDatabricks.init
    assert(entry != null)
  }

  test("the engine opaque-UDF catalog is reachable with the expected registerAll(spark)") {
    // Compile-time reference: if the engine renames CqlRuntimeUdfs / registerAll, this stops compiling.
    val register: SparkSession => Unit = com.databricks.cql.runtime.udf.CqlRuntimeUdfs.registerAll
    assert(register != null)
    // Reflection double-checks the runtime class carries the static forwarder CqlDatabricks.init calls into.
    val m = Class
      .forName("com.databricks.cql.runtime.udf.CqlRuntimeUdfs")
      .getMethod("registerAll", classOf[SparkSession])
    assert(m != null)
  }
}
