package com.databricks.cql.platform

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Spark-free guard for the UC Scala UDF DDL generator (`SVC-RUNTIME-UDF-UC`, `PROC-TDD`). Asserts the
  * generated `CREATE OR REPLACE FUNCTION … LANGUAGE SCALA` text against the engine's neutral manifest
  * (`EXEC-UC-MANIFEST`) — shape, full coverage, and that arg/return SQL types (incl. `STRUCT`/`MAP`) pass
  * through verbatim. Like `CqlDatabricksSpec` it also compiles a reference to the engine FQN, so an
  * engine package rename (`ACC-NAMESPACE-ALIGN`) or manifest change breaks THIS build loudly. No
  * `SparkSession` — the generator is pure text.
  *
  * `@RunWith(JUnitRunner)` for discovery under the JUnit Platform (mirrors compiler-app). */
@RunWith(classOf[JUnitRunner])
class UcCatalogSpec extends AnyFunSuite {

  private val catalog = "cql_catalog"
  private val schema  = "default"
  private val jar     = "/Volumes/cql_catalog/default/runtime/udf/runtime-0.0.0-phase0.jar"
  private val stmts   = UcCatalog.statements(catalog, schema, jar)

  test("emits one statement per engine manifest entry (no more, no fewer)") {
    // Compile-time reference to the engine FQN: an engine rename/removal stops this compiling (the earliest
    // signal), and the count keeps the generator honest if the catalog grows/shrinks.
    val entries = com.databricks.cql.runtime.udf.UcFunctionManifest.entries
    assert(entries.nonEmpty)
    assert(stmts.size == entries.size)
    // Every emitted SQL name from the manifest appears exactly once in the generated DDL.
    entries.foreach { e =>
      assert(stmts.count(_.contains(s"`${e.sqlName}`")) == 1, s"missing/duplicate DDL for ${e.sqlName}")
    }
  }

  test("cql_age_at scalar function renders the full validated shape") {
    val ddl = stmts.find(_.contains("`cql_age_at`")).getOrElse(fail("no cql_age_at statement"))
    assert(ddl.startsWith(s"CREATE OR REPLACE FUNCTION `$catalog`.`$schema`.`cql_age_at`(a0 STRING, a1 STRING)"))
    assert(ddl.contains("\nRETURNS INT\n"))
    assert(ddl.contains("\nLANGUAGE SCALA\n"))
    assert(ddl.contains("\nDETERMINISTIC\n"))
    assert(ddl.contains(s"""java_dependencies = '["$jar"]'"""))
    assert(ddl.contains("environment_version = '4'"))
    assert(ddl.endsWith("HANDLER 'com.databricks.cql.runtime.eval.CqlOps.ageAt';"))
  }

  test("STRUCT / MAP arg and return types pass through verbatim (cql_convert_quantity)") {
    val ddl = stmts.find(_.contains("`cql_convert_quantity`")).getOrElse(fail("no cql_convert_quantity statement"))
    // Positional params a0/a1/a2 carry the exact SQL types from the manifest, commas inside STRUCT<>/MAP<> intact.
    assert(ddl.contains("a0 STRUCT<value: DECIMAL(38,18), unit: STRING>"))
    assert(ddl.contains("a1 MAP<STRING, DECIMAL(38,18)>"))
    assert(ddl.contains("a2 STRING"))
    assert(ddl.contains("RETURNS STRUCT<value: DECIMAL(38,18), unit: STRING>"))
    assert(ddl.contains("HANDLER 'com.databricks.cql.runtime.eval.CqlOps.convertQuantity';"))
  }

  test("identifiers are backtick-quoted so the reserved `default` schema is legal") {
    assert(stmts.forall(_.contains(s"`$catalog`.`$schema`.`")))
  }
}
