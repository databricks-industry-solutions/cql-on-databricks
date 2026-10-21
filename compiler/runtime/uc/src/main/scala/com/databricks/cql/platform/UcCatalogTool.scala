package com.databricks.cql.platform

/** `SVC-RUNTIME-UDF-UC` deploy helper. Prints the UC `CREATE OR REPLACE FUNCTION` DDL (from [[UcCatalog]],
  * i.e. from the engine's neutral manifest) as a **JSON array of statement strings**, for
  * `scripts/deploy/register-uc-udfs.sh` to submit one-by-one via the Databricks SQL Statement Execution API.
  *
  * It lives in the runtime jar on purpose: the DDL always derives from the engine manifest
  * (`EXEC-UC-MANIFEST`) at deploy time, never from a hand-copied list that could drift. Emits JSON (not raw
  * multi-line SQL) so the shell reads one array and hands each element straight into the API payload — no
  * fragile newline splitting.
  *
  * Usage: `./gradlew -q printUcDdl --args="--catalog C --schema S --jar /Volumes/.../runtime-<ver>.jar"` */
object UcCatalogTool {

  def main(args: Array[String]): Unit = {
    val m = parse(args)
    val stmts = UcCatalog.statements(req(m, "catalog"), req(m, "schema"), req(m, "jar"))
    // Minimal dependency-free JSON array of strings (mirrors UcFunctionManifest.toJson; no JSON lib here).
    def esc(s: String): String = s.flatMap {
      case '"'  => "\\\""
      case '\\' => "\\\\"
      case '\n' => "\\n"
      case '\r' => "\\r"
      case '\t' => "\\t"
      case c    => c.toString
    }
    println(stmts.map(s => "\"" + esc(s) + "\"").mkString("[", ",", "]"))
  }

  private def parse(args: Array[String]): Map[String, String] =
    args.grouped(2).collect { case Array(k, v) if k.startsWith("--") => k.drop(2) -> v }.toMap

  private def req(m: Map[String, String], k: String): String =
    m.getOrElse(k, throw new IllegalArgumentException(s"missing --$k (need --catalog --schema --jar)"))
}
