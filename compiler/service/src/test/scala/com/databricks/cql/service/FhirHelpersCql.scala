package com.databricks.cql.service

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path, Paths}

/** LANG-FHIRHELPERS-FROM-CONTENT — the FHIRHelpers CQL a test supplies with a library that includes it: the
  * conformance corpus's unmodified copy. The compiler bundles none. */
object FhirHelpersCql {

  lazy val text: String = {
    var dir: Path = Paths.get("").toAbsolutePath
    while (dir != null && !Files.isRegularFile(dir.resolve("settings.gradle"))) dir = dir.getParent
    require(dir != null, "repository root not found (no settings.gradle above the working directory)")
    new String(Files.readAllBytes(dir.resolve("compiler/conformance/src/test/resources/cql/fhir/FHIRHelpers.cql")),
      StandardCharsets.UTF_8)
  }
}
