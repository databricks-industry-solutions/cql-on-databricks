package com.databricks.cql.it

import com.databricks.cql.service.{CompileRequest, CompileService, OssCompileHost}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.nio.charset.StandardCharsets

/** End-to-end gate for the LOCALLY-HOSTABLE compiler (PHASE-10, `BUILD-COMPILE-SERVICE`):
  * start the local host ([[OssCompileHost]], a zero-dependency JDK
  * HttpServer), POST over HTTP, and confirm it serves the compiled SQL script + the neutral library
  * graph — proving the whole compile path is reachable as a local OSS service with NO platform
  * (Databricks App) and NO external infrastructure. The host is SPARK-FREE (EXEC-SQL-BACKEND): it emits
  * SQL text; the Python client runs it in its own session (its own E2E, [[RuntimeCompileE2ESpec]]).
  * These endpoints are server-free, so this spec is always-on (no Connect server needed). */
@RunWith(classOf[JUnitRunner])
final class CompileHostSpec extends AnyFunSuite {

  private val http = HttpClient.newHttpClient()

  /** A data-free scalar fixture — no data/terminology adapter needed. */
  private val fixture = "Arithmetic"

  private def post(endpoint: String, path: String, body: String): (Int, ujson.Value) = {
    val req = HttpRequest.newBuilder(URI.create(s"$endpoint/$path"))
      .header("Content-Type", "application/json")
      .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
      .build()
    val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
    (resp.statusCode(), ujson.read(resp.body()))
  }

  test("OSS compile host serves a compiled SQL script + define metadata over HTTP (/compile-sql)") {
    val elm = Paths.read(Paths.elm(fixture))
    val service = new CompileService()
    val host = new OssCompileHost(service).start()
    try {
      // health check first (proves the JDK HttpServer is serving).
      val health = http.send(
        HttpRequest.newBuilder(URI.create(s"${host.endpoint}/health")).GET().build(),
        HttpResponse.BodyHandlers.ofString())
      assert(health.statusCode() == 200 && health.body().contains("ok"))

      val reqJson = ujson.write(ujson.Obj("elm" -> elm, "context" -> "Unfiltered"))
      val (code, out) = post(host.endpoint, "compile-sql", reqJson)
      assert(code == 200, s"compile-sql failed: $out")

      val statements = out("statements").arr
      assert(statements.nonEmpty, "the served script has statements")
      assert(out("target").str.nonEmpty, "the served script names a target object")
      val defines = out("defines").arr.map(_.str).toSeq
      assert(defines.nonEmpty, "metadata carries the compiled define names")
      // The host metadata must match the service's own compile of the same library (the host is faithful
      // to the compiler, not a re-implementation).
      assert(service.compileSql(CompileRequest(elm, context = Some("Unfiltered"))).defines == defines)
    } finally host.stop()
  }

  test("OSS compile host serves neutral library graph metadata over HTTP (/describe)") {
    val elm = Paths.read(Paths.elm(fixture))
    val service = new CompileService()
    val host = new OssCompileHost(service).start()
    try {
      val (code, out) = post(host.endpoint, "describe",
        ujson.write(ujson.Obj("elm" -> elm, "context" -> "Unfiltered")))
      assert(code == 200, s"describe failed: $out")

      val defines = out("defines").arr.map(_.str).toSeq
      assert(defines.nonEmpty, "the graph carries the define nodes")
      // Faithful to the compiler's own describe (the service is not a re-implementation).
      assert(service.describe(CompileRequest(elm, context = Some("Unfiltered"))).defines == defines)
      // The neutral shape is present: edges keyed by define, a boundary set, a pinned compiler version.
      assert(out.obj.contains("dependencies") && out.obj.contains("boundaries"))
      assert(out("compilerVersion").str.nonEmpty, "the compiler version is pinned")
    } finally host.stop()
  }

  test("compile of unsupported/garbage ELM fails loudly (400), never a silent empty result") {
    val host = new OssCompileHost(new CompileService()).start()
    try {
      val (code, out) = post(host.endpoint, "compile-sql", ujson.write(ujson.Obj("elm" -> "{ not valid elm }")))
      assert(code == 400, s"expected a loud 400, got $code: $out")
      assert(out.obj.contains("error"), "a compile failure reports an error (CORE-NO-SILENT-MISLOWER)")
    } finally host.stop()
  }

  test("OSS compile host serves the compile-once pipeline plan over HTTP (/compile-pipeline)") {
    val elm = Paths.read(Paths.elm(fixture))
    val service = new CompileService()
    val host = new OssCompileHost(service).start()
    try {
      val (code, out) = post(host.endpoint, "compile-pipeline",
        ujson.write(ujson.Obj("elm" -> elm, "context" -> "Unfiltered")))
      assert(code == 200, s"compile-pipeline failed: $out")
      // The neutral plan shape: a `datasets` array of recipes (PIPE-SQL-DAG-REUSE). Faithful to the
      // service's own compile (the host is not a re-implementation).
      assert(out.obj.contains("datasets"), "the served plan carries a datasets array")
      val served = out("datasets").arr.map(_("name").str).toSeq
      val direct = service.compilePipeline(CompileRequest(elm, context = Some("Unfiltered"))).datasets.map(_.name)
      assert(served == direct, s"host plan must match the service's own: host=$served service=$direct")
      out("datasets").arr.foreach { d =>
        assert(d.obj.contains("name") && d.obj.contains("target") && d.obj.contains("reads") &&
          d.obj.contains("statements") && d.obj.contains("query"),
          s"each recipe carries name/target/reads/query/statements: $d")
      }
    } finally host.stop()
  }
}
