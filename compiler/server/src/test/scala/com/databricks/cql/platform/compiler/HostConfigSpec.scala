package com.databricks.cql.platform.compiler

import com.databricks.cql.error.ConfigError
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The host's settings resolve by explicit lookups (`HostConfig.fromSettings`). A value that is not an integer
  * fails, naming its setting (CORE-NO-SILENT-MISLOWER); the default is never taken in its place. */
@RunWith(classOf[JUnitRunner])
class HostConfigSpec extends AnyFunSuite {

  private def settings(props: Map[String, String] = Map.empty, env: Map[String, String] = Map.empty): HostConfig =
    HostConfig.fromSettings(props.get, env.get)

  test("CORE-NO-SILENT-MISLOWER: a PORT that is not an integer fails, naming PORT and its value") {
    val err = intercept[ConfigError](settings(env = Map("PORT" -> "eighty")))
    assert(err.key == "PORT" && err.value == "eighty", err.getMessage)
  }

  test("CORE-NO-SILENT-MISLOWER: cqldatabricks.hostPort wins over PORT, and a bad one fails naming itself") {
    assert(settings(props = Map("cqldatabricks.hostPort" -> "9001"), env = Map("PORT" -> "9002")).port == 9001)
    val err = intercept[ConfigError](settings(props = Map("cqldatabricks.hostPort" -> "x"), env = Map("PORT" -> "9002")))
    assert(err.key == "cqldatabricks.hostPort" && err.value == "x", err.getMessage)
  }

  test("CORE-NO-SILENT-MISLOWER: CQL_COMPILER_THREADS that is not an integer fails, naming it") {
    val err = intercept[ConfigError](settings(env = Map("CQL_COMPILER_THREADS" -> "many")))
    assert(err.key == "CQL_COMPILER_THREADS" && err.value == "many", err.getMessage)
  }

  test("CORE-NO-SILENT-MISLOWER: CQL_COMPILER_THREADS given as an integer is used") {
    assert(settings(env = Map("CQL_COMPILER_THREADS" -> " 6 ")).threads == 6)
  }

  test("a host with no settings takes its defaults") {
    val c = settings()
    assert(c.port == 8080)
    assert(c.bindHost == "0.0.0.0")
    assert(c.threads >= 2)
  }
}
