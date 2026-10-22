package com.databricks.cql.platform.compiler

import com.databricks.cql.service.CompileService
import java.io.ByteArrayInputStream
import java.nio.charset.StandardCharsets.UTF_8
import java.util.Base64
import java.util.zip.GZIPInputStream

/** SVC-COMPILER-UC — the existing compiler protocol hosted inside a UC Scala UDF.
  * No HTTP, credentials, Spark session or SQL execution. Request compression is transport only:
  * a nine-library measure's resolved ELM closure exceeds the Statement Execution API's 1 MiB parameter limit. */
object UcCompiler {
  private val MaxRequestBytes = 64 * 1024 * 1024
  private val Operations = Set("translate", "compile-sql", "compile-pipeline", "describe",
                               "resource-schema", "merge-cql", "capabilities")

  /** SVC-COMPILER-UC — artifact-derived, independent of environment and sandbox lifetime. */
  lazy val version: String = {
    val stream = Option(getClass.getResourceAsStream("/compiler-uc.version"))
      .getOrElse(throw new IllegalStateException("missing compiler-uc.version build resource"))
    val value = try new String(stream.readAllBytes(), UTF_8).trim finally stream.close()
    require(value.matches("uc-sha256-[0-9a-f]{64}"), "invalid compiler build identity")
    value
  }

  private lazy val router = new CompilerRouter(
    new CompileService(Some(MountedTerminology)), None, version)

  /** SVC-COMPILER-UC — one base64(gzip(UTF-8 JSON)) request returns unchanged protocol JSON. */
  def invoke(operation: String, requestGzipBase64: String): String = {
    require(Operations.contains(operation), "unsupported compiler operation")
    require(requestGzipBase64 != null, "compiler request is required")
    val input = new GZIPInputStream(new ByteArrayInputStream(Base64.getDecoder.decode(requestGzipBase64)))
    val bytes = try input.readNBytes(MaxRequestBytes + 1) finally input.close()
    require(bytes.length <= MaxRequestBytes, "compiler request exceeds 64 MiB decoded limit")
    val method = if (operation == "capabilities") "GET" else "POST"
    val result = router.handle(method, "/" + operation, None, new String(bytes, UTF_8))
    require(result.status == 200, result.body)
    result.body
  }
}
