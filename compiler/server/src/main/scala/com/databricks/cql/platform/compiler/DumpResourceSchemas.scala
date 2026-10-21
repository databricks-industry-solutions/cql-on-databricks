package com.databricks.cql.platform.compiler

import com.databricks.cql.retrieve.ResourceStruct
import com.databricks.cql.sema.{FhirModel, FhirSchema}

/** CLI utility: emit the canonical `__resource` mount DDL for every modeled FHIR resource type as a
  * JSON object to stdout (`SVC-RESOURCE-SCHEMA`). The ingest component vendors this output so it can
  * generate conformant `retrieve_<Type>` SQL UDTFs without a running Compiler Service.
  *
  * Usage: `./gradlew dumpResourceSchemas` (or `java -cp <classpath> ...DumpResourceSchemas [outfile]`).
  *
  * Output shape (same as the `/resource-schema` HTTP endpoint, `schemas` key):
  * {{{
  *   { "Patient": { "modeled": true, "patientContext": true, "ddl": "STRUCT<...>" }, ... }
  * }}}
  */
object DumpResourceSchemas {
  def main(args: Array[String]): Unit = {
    val types = FhirModel.resourceTypes.toSeq.sorted
    val entries = types.map { t =>
      val ddl = ResourceStruct.structType(t).toDDL
      val pc = FhirModel.isPatientContext(t)
      s"""  "$t": {"modeled": true, "patientContext": $pc, "ddl": ${ujson.Str(ddl).render()}}"""
    }
    val json = entries.mkString("{\n", ",\n", "\n}\n")

    args.headOption match {
      case Some(path) =>
        val f = new java.io.File(path)
        f.getParentFile match { case null => () ; case d => d.mkdirs() }
        val w = new java.io.PrintWriter(f, "UTF-8")
        try w.print(json) finally w.close()
        System.err.println(s"wrote ${types.size} resource schemas to $path")
      case None =>
        print(json)
    }
  }
}
