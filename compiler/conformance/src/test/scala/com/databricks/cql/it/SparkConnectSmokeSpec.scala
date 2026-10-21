package com.databricks.cql.it

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Proves the Spark Connect lowering target is reachable from the conformance JVM
  * (TECH-LOWERING-TARGET, CONF-GATE-MEASURE). The test JVM is a pure Connect client; the
  * endpoint is `cqldatabricks.sparkRemote` (default a local OSS server, or a Databricks Connect
  * instance). Skips cleanly when no server is reachable. This is the smoke test the Phase 1
  * lowered-plan differentials build on. */
@RunWith(classOf[JUnitRunner])
final class SparkConnectSmokeSpec extends AnyFunSuite {

  test("a Spark Connect session executes a trivial plan") {
    val spark = SparkConnect.session()
    assert(spark.range(0, 5).count() == 5L)
  }
}
