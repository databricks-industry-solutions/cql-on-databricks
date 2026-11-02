package com.databricks.cql.it

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** THE result-equivalence gate (EMIT-SQL-EXECUTE, EXEC-SQL-BACKEND): every fixture in the shared
  * [[Corpus]], lowered to a Spark SQL SCRIPT ([[SqlDifferential]]) and executed via `spark.sql`, must
  * produce the SAME per-(subject, define) values as the Java CQF reference (`CONF-GATE-MEASURE`,
  * `CONF-GROUND-TRUTH`).
  *
  * The SQL emitter is the SOLE execution substrate (two-layer eval,
  * `compiler/docs/execution-model.md`/9), so this suite runs by default (no flag). A fixture
  * whose SQL lowering is not yet ported is CANCELLED (skipped).  Skips the whole suite when no Spark
  * Connect server is reachable. */
@RunWith(classOf[JUnitRunner])
final class SqlDifferentialSpec extends AnyFunSuite {
  Corpus.all.foreach { e =>
    test("SQL: " + e.label)(SqlDifferential.run(SparkConnect.session(), e.mk()))
  }
}
