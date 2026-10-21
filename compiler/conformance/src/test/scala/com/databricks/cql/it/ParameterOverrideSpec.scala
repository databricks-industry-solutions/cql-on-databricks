package com.databricks.cql.it

import com.databricks.cql.service.{CompileRequest, CompileService}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SERVER-FREE gate for the two `BUILD-CLIENT-SESSION` service additions (two-layer-eval P1): the compile
  * service accepts (a) RAW CQL TEXT (translated to ELM in-process, `TECH-ELM-INPUT`) and (b) parameter-VALUE
  * overrides inlined as literals (the engine-supplied override seam of `ParameterResolver`). Exercised
  * through the session-free `CompileService.compileSql` object entry — no Spark, so it runs everywhere. */
@RunWith(classOf[JUnitRunner])
final class ParameterOverrideSpec extends AnyFunSuite {

  /** A tiny parameterized library: the scalar define just returns the parameter, so the parameter's bound
    * value appears VERBATIM as a literal in the emitted SQL — a clean observable for the override. */
  private val cql =
    """library ParamProbe version '1.0.0'
      |
      |parameter "Threshold" Integer default 4242
      |
      |define "Val": "Threshold"
      |""".stripMargin

  private def compile(parameters: Map[String, String]): String =
    CompileService.compileSql(
      CompileRequest(cql = Some(cql), parameters = parameters), hasTerminology = false).script

  test("raw CQL text is translated and compiled to SQL (BUILD-CLIENT-SESSION)") {
    val script = compile(Map.empty)
    assert(script.contains("4242"), s"the defaulted parameter value should be inlined; got:\n$script")
  }

  test("a parameter-value override replaces the declared default (ParameterResolver seam)") {
    val script = compile(Map("Threshold" -> "777"))
    assert(script.contains("777"), s"the OVERRIDE value should be inlined; got:\n$script")
    assert(!script.contains("4242"), s"the default should NOT survive the override; got:\n$script")
  }

  test("overriding an undeclared parameter fails loudly (CORE-NO-SILENT-MISLOWER)") {
    val e = intercept[IllegalArgumentException](compile(Map("Nope" -> "1")))
    assert(e.getMessage.contains("undeclared"), s"expected an undeclared-parameter error, got: ${e.getMessage}")
  }

  test("an interval override (the measurement-period shape) translates and inlines") {
    val cqlWithPeriod =
      """library PeriodProbe version '1.0.0'
        |
        |parameter "Measurement Period" Interval<DateTime> default Interval[@2000-01-01, @2000-12-31]
        |
        |define "Start": start of "Measurement Period"
        |""".stripMargin
    val script = CompileService.compileSql(
      CompileRequest(cql = Some(cqlWithPeriod),
        parameters = Map("Measurement Period" -> "Interval[@2026-01-01, @2026-12-31]")),
      hasTerminology = false).script
    assert(script.contains("2026"), s"the override interval bound should be inlined; got:\n$script")
    assert(!script.contains("2000"), s"the default interval should NOT survive; got:\n$script")
  }

  private def compileWith(source: String, parameters: Map[String, String],
                          session: Map[String, String]): String =
    CompileService.compileSql(
      CompileRequest(cql = Some(source), parameters = parameters, sessionParameters = session),
      hasTerminology = false).script

  test("a session parameter binds a library that declares it (BUILD-CLIENT-SESSION-PARAMETERS)") {
    val script = compileWith(cql, Map.empty, Map("Threshold" -> "555"))
    assert(script.contains("555"), s"the session value should be inlined; got:\n$script")
    assert(!script.contains("4242"), s"the default should NOT survive the session value; got:\n$script")
  }

  test("a session parameter the library does not declare is not this library's (BUILD-CLIENT-SESSION-PARAMETERS)") {
    val script = compileWith(cql, Map.empty, Map("Measurement Period" -> "Interval[@2026-01-01, @2026-12-31]"))
    assert(script.contains("4242"), s"an undeclared session value must leave the library as it is; got:\n$script")
  }

  test("an explicit parameter wins over the session's (BUILD-CLIENT-SESSION-PARAMETERS)") {
    val script = compileWith(cql, Map("Threshold" -> "777"), Map("Threshold" -> "555"))
    assert(script.contains("777") && !script.contains("555"), s"the explicit value should win; got:\n$script")
  }

  test("an explicit undeclared parameter still fails beside session values (CORE-NO-SILENT-MISLOWER)") {
    val e = intercept[IllegalArgumentException](compileWith(cql, Map("Nope" -> "1"), Map("Threshold" -> "5")))
    assert(e.getMessage.contains("undeclared"), s"expected an undeclared-parameter error, got: ${e.getMessage}")
  }
}
