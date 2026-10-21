package com.databricks.cql.platform.compiler

import com.databricks.cql.service.CompileService
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SVC-COMPILE-CACHE — performance only: full keys, bounded memory, no cached errors/auth bypass. */
@RunWith(classOf[JUnitRunner])
final class CompileResponseCacheSpec extends AnyFunSuite {
  test("SVC-COMPILE-CACHE: canonical object keys hit; any input/build/operation change misses") {
    val cache = new CompileResponseCache()
    var calls = 0
    def run(body: String, version: String = "build-a", op: String = "/compile-pipeline") =
      cache.getOrCompile(version, op, body) { calls += 1; HttpResult(200, "result") }
    assert(!run("""{"cql":"one","deps":[{"cql":"helper"}],"parameters":{"P":"1"}}""")._2)
    assert(run("""{"parameters":{"P":"1"},"deps":[{"cql":"helper"}],"cql":"one"}""")._2)
    for (field <- Seq("cql", "deps", "parameters", "measurementPeriod", "dataSource", "sources",
      "overrides", "durable", "context", "target", "boundaries", "grainFirst", "sessionParameters", "libraries")) {
      val a = ujson.Obj(field -> "one")
      val b = ujson.Obj(field -> "two")
      assert(!run(ujson.write(a))._2)
      assert(!run(ujson.write(b))._2)
    }
    assert(!run("{}", version = "build-a")._2)
    assert(!run("{}", version = "build-b")._2)
    assert(!run("{}", op = "/compile-sql")._2)
    assert(calls == 32)
  }

  test("SVC-COMPILE-CACHE: LRU, byte bound, oversize bypass and errors remain correct") {
    val cache = new CompileResponseCache(maxEntries = 2, maxBytes = 200)
    def run(n: Int, status: Int = 200, value: String = "ok") =
      cache.getOrCompile("build", "compile", n.toString)(HttpResult(status, value))._2
    assert(!run(1) && !run(2) && run(1) && !run(3) && !run(2))
    assert(!run(4, value = "x" * 201) && !run(4, value = "x" * 201))
    assert(!run(5, status = 400) && !run(5, status = 400) && !run(5) && run(5))
    val small = new CompileResponseCache(maxEntries = 10, maxBytes = 70)
    def smallRun(n: Int) = small.getOrCompile("v", "op", n.toString)(HttpResult(200, "ok"))._2
    assert(!smallRun(1) && !smallRun(2) && !smallRun(1))
  }

  test("SVC-COMPILE-CACHE: real compiler hit is byte-identical and still requires authorization") {
    val router = new CompilerRouter(new CompileService(), Some("synthetic-token"), "build-a")
    val body = """{"cql":"library Synthetic version '1'\ndefine Output: 7", "durable":true}"""
    def run(auth: Option[String]) = router.handle("POST", "/compile-pipeline", auth, body)
    val first = run(Some("Bearer synthetic-token"))
    val second = run(Some("Bearer synthetic-token"))
    assert(first.status == 200, first.body)
    assert(first.body == second.body)
    assert(first.headers("X-CQL-Compile-Cache") == "MISS")
    assert(second.headers("X-CQL-Compile-Cache") == "HIT")
    assert(second.headers("Server-Timing").startsWith("compile;dur="))
    assert(run(None).status == 401)
    assert(ujson.read(first.body)("batchProtocol").str == "cql-batch-v1")
  }
}
