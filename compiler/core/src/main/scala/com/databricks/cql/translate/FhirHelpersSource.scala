package com.databricks.cql.translate

import scala.io.Source

/** The bundled **FHIRHelpers 4.0.1** CQL library (TECH-ELM-INPUT, LANG-FUNCTION, LANG-NO-IMPLICIT-INCLUDE).
  *
  * A LIBRARY SOURCE, and nothing more: registered with every translation so that a library which
  * writes `include FHIRHelpers version '4.0.1'` resolves it with no network or service. The CQL
  * specification leaves supplying a referenced library to the implementation environment (Developer's
  * Guide, Libraries: "it is up to the implementation environment to provide the most appropriate version
  * of the referenced library").
  *
  * It is NEVER added to a library that did not include it. Until 2026-10-06 `inject` inserted
  * `include FHIRHelpers` after any `using FHIR` line, which the reference translator does not do; it
  * rewrote the author's CQL, made a FHIR conversion compile here that fails everywhere else, and
  * broke every unnamed library (the reference translator refuses an include from one). Conformance is
  * what the reference translator accepts, so a library that converts FHIR primitives includes
  * FHIRHelpers itself, as every published eCQM does (`LANG-NO-IMPLICIT-INCLUDE`). */
object FhirHelpersSource {

  /** Resource path of the bundled FHIRHelpers CQL (extracted from `org.cqframework:quick`). */
  private val ResourcePath = "/cql/FHIRHelpers-4.0.1.cql"

  /** The FHIRHelpers CQL text, read from the classpath. */
  lazy val cql: String = {
    val in = getClass.getResourceAsStream(ResourcePath)
    require(in != null, s"bundled FHIRHelpers source $ResourcePath not found on the classpath")
    try Source.fromInputStream(in, "UTF-8").mkString finally in.close()
  }
}
