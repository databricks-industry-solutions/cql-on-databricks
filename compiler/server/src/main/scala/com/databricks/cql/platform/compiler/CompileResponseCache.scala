package com.databricks.cql.platform.compiler

import java.nio.charset.StandardCharsets.UTF_8
import java.security.MessageDigest
import java.util.LinkedHashMap

/** SVC-COMPILE-CACHE — bounded, process-local memoization of successful, data-free compile responses.
  * Serialized values cannot be mutated by callers. Auth is checked by the router before lookup.
  * Duplicate concurrent misses may compile concurrently; they never hold a global lock while compiling. */
private[compiler] final class CompileResponseCache(maxEntries: Int = 128, maxBytes: Long = 64L * 1024 * 1024) {
  require(maxEntries >= 0 && maxBytes >= 0, "cache limits must be nonnegative")
  private val entries = new LinkedHashMap[String, (HttpResult, Long)](16, 0.75f, true)
  private var bytes = 0L

  def getOrCompile(version: String, operation: String, body: String)(compile: => HttpResult): (HttpResult, Boolean) = {
    // Sort object keys only. Array order and every request field remain part of the key.
    def canonical(v: ujson.Value): ujson.Value = v match {
      case o: ujson.Obj => ujson.Obj.from(o.obj.toSeq.sortBy(_._1).map { case (k, x) => k -> canonical(x) })
      case a: ujson.Arr => ujson.Arr.from(a.arr.map(canonical))
      case other => other
    }
    val key = MessageDigest.getInstance("SHA-256")
      .digest(ujson.write(ujson.Arr(version, operation, canonical(ujson.read(body)))).getBytes(UTF_8))
      .map(b => f"${b & 0xff}%02x").mkString
    val hit = synchronized { Option(entries.get(key)).map(_._1) }
    hit match {
      case Some(value) => (value, true)
      case None =>
        val value = compile
        val weight = value.body.getBytes(UTF_8).length.toLong + key.length
        if (value.status == 200 && maxEntries > 0 && weight <= maxBytes) synchronized {
          Option(entries.remove(key)).foreach { case (_, size) => bytes -= size }
          entries.put(key, (value, weight))
          bytes += weight
          while (entries.size > maxEntries || bytes > maxBytes) {
            val oldest = entries.entrySet.iterator
            bytes -= oldest.next().getValue._2
            oldest.remove()
          }
        }
        (value, false)
    }
  }
}
