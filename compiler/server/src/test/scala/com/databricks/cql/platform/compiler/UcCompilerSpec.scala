package com.databricks.cql.platform.compiler

import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets.UTF_8
import java.util.Base64
import java.util.zip.GZIPOutputStream
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SVC-COMPILER-UC — real compiler in the UDF handler, without sockets or Spark. */
@RunWith(classOf[JUnitRunner])
class UcCompilerSpec extends AnyFunSuite {
  private def encoded(body: String): String = {
    val output = new ByteArrayOutputStream()
    val gzip = new GZIPOutputStream(output)
    gzip.write(body.getBytes(UTF_8))
    gzip.close()
    Base64.getEncoder.encodeToString(output.toByteArray)
  }

  private def call(operation: String, body: ujson.Value): ujson.Value =
    ujson.read(UcCompiler.invoke(operation, encoded(ujson.write(body))))

  private val cql = "library Synthetic version '1'\nusing FHIR version '4.0.1'\ndefine X: 1 + 1"

  test("SVC-COMPILER-UC: translate and durable plan equal the App protocol") {
    val router = new CompilerRouter(new com.databricks.cql.service.CompileService(Some(MountedTerminology)),
                                    None, UcCompiler.version)
    for ((operation, body) <- Seq(
      "translate" -> ujson.Obj("cql" -> cql),
      "compile-pipeline" -> ujson.Obj("cql" -> cql, "durable" -> true,
                                      "overrides" -> ujson.Obj("materialize.mode" -> "all")),
      "resource-schema" -> ujson.Obj(),
      "describe" -> ujson.Obj("cql" -> cql))) {
      val expected = router.handle("POST", "/" + operation, None, ujson.write(body))
      assert(expected.status == 200, expected.body)
      assert(call(operation, body) == ujson.read(expected.body))
    }
  }

  test("SVC-COMPILER-UC: packaged identity is stable and used by every operation") {
    val version = call("capabilities", ujson.Obj())("compilerVersion").str
    assert(version.matches("uc-sha256-[0-9a-f]{64}"))
    assert(call("describe", ujson.Obj("cql" -> cql))("compilerVersion").str == version)
    assert(call("compile-pipeline", ujson.Obj("cql" -> cql))("compilerVersion").str == version)
  }

  test("SVC-COMPILER-UC: reject unsupported operations, invalid compression and compile failures") {
    intercept[IllegalArgumentException] { UcCompiler.invoke("execute", encoded("{}")) }
    intercept[IllegalArgumentException] { UcCompiler.invoke("translate", "not base64!") }
    intercept[IllegalArgumentException] { call("compile-pipeline", ujson.Obj("cql" -> "invalid CQL")) }
    val diagnostics = call("translate", ujson.Obj("cql" -> "invalid CQL"))
    assert(!diagnostics("ok").bool)
    assert(diagnostics("diagnostics").arr.nonEmpty)
  }
}
