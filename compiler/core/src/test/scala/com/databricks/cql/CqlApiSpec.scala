package com.databricks.cql

import com.databricks.cql.ir.ExpressionDef
import com.databricks.cql.passes.Pipeline
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The engine's PUBLIC API surface is CQL, not ELM (TECH-ELM-INPUT): this is CQL on Databricks.
  * The engine translates in-process and produces IR with no external service and no help from
  * `:conformance` — proving the SHIPPED artifact closes the CQL→IR path on its own (the
  * runtime gap that motivated moving translation into the engine). */
@RunWith(classOf[JUnitRunner])
class CqlApiSpec extends AnyFunSuite {

  test("normalizeCql translates and normalizes CQL text in-process (TECH-ELM-INPUT)") {
    val cql =
      """library TrivialApi version '1.0.0'
        |define "Yes": true
        |""".stripMargin
    val lib = Pipeline().normalizeCql(cql)
    val defines = lib.statements.collect { case e: ExpressionDef => e.name }
    assert(defines.contains("Yes"))
  }

  test("invalid CQL fails loudly with translator diagnostics, never fabricated ELM (CORE-NO-SILENT-MISLOWER)") {
    val notCql = "this is not valid CQL"
    val ex = intercept[IllegalArgumentException](Pipeline().normalizeCql(notCql))
    assert(ex.getMessage.contains("CQL translation produced errors"))
  }
}
