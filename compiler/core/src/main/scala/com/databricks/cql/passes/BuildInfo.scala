package com.databricks.cql.passes

/** The engine version recorded with materialized output for reproducibility (DATA-MATERIALIZE,
  * TECH-DETERMINISM). Read from the packaged JAR manifest's implementation version when present,
  * falling back to `"dev"` for an unpackaged (test/IDE) classpath. A build that needs an exact pin
  * can stamp the manifest (Gradle `jar { manifest { attributes('Implementation-Version': version) } }`)
  * or replace this with a generated constant; the recording seam does not change. */
object BuildInfo {
  val version: String =
    Option(getClass.getPackage.getImplementationVersion).filter(_.nonEmpty).getOrElse("0.1.0-dev")
}
