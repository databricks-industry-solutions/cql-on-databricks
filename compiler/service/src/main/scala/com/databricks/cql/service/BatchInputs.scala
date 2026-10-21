package com.databricks.cql.service

import com.databricks.cql.env.SourceBinding
import com.databricks.cql.sema.FhirModel

import java.util.Locale

/** SVC-COMPILE-BATCH — catalog metadata supplied by the caller, never fetched by the compiler. */
final case class DataSourceRoutines(catalog: String, schema: String, routines: Seq[String])

/** SVC-COMPILE-BATCH — model validation and period typing belong with the compiler. */
private[service] object BatchInputs {
  def prepare(req: CompileRequest): CompileRequest = {
    val sources = req.dataSource.map { d =>
      require(req.sources.isEmpty, "give dataSource or explicit source bindings, not both")
      bindings(d)
    }.getOrElse(req.sources)
    val period = req.measurementPeriod.map { bounds =>
      require(req.libraries.isEmpty, "measurementPeriod requires a single root and its dependencies")
      require(!req.parameters.contains("Measurement Period") && !req.sessionParameters.contains("Measurement Period"),
        "give measurementPeriod or a Measurement Period parameter, not both")
      periodParameters(req.elmJson, bounds)
    }.getOrElse(Map.empty[String, String])
    req.copy(sources = sources, dataSource = None, measurementPeriod = None, parameters = req.parameters ++ period)
  }

  private def bindings(d: DataSourceRoutines): Map[String, SourceBinding] = {
    require(d.catalog.nonEmpty && d.schema.nonEmpty, "dataSource requires catalog and schema")
    val types = FhirModel.resourceTypes.map(t => t.toLowerCase(Locale.ROOT) -> t).toMap
    val prefix = "retrieve_"
    val parsed = d.routines.filter(_.toLowerCase(Locale.ROOT).startsWith(prefix)).map { name =>
      val suffix = name.substring(prefix.length).toLowerCase(Locale.ROOT)
      val scoped = suffix.endsWith("_ids") && types.contains(suffix.stripSuffix("_ids"))
      val t = types.getOrElse(if (scoped) suffix.stripSuffix("_ids") else suffix,
        throw new IllegalArgumentException(s"$name names no FHIR type the compiler models"))
      (t, scoped)
    }
    val base = parsed.collect { case (t, false) => t }.toSet
    require(base.nonEmpty, "no retrieve_<Type> functions")
    parsed.collect { case (t, true) => t }.foreach { t =>
      require(base(t), s"retrieve_${t}_ids has no retrieve_$t")
    }
    // Match the existing binding contract, including canonical FHIR case in emitted SQL.
    base.toSeq.sorted.map(t => t -> SourceBinding(prefix + t, Some(s"${d.catalog}.${d.schema}"))).toMap
  }

  /** RUN-EVAL-ID-INPUTS, LANG-PARAM-INSTANT — preserve existing closed-interval precision rules.
    * HL7 CQL 1.5 sections 9.2 (Date/Time) and 9.6 (Intervals); the ELM declaration supplies the type. */
  private def periodParameters(elm: String, bounds: (String, String)): Map[String, String] = {
    val lib = ujson.read(elm)("library")
    val parameters = lib.obj.get("parameters").toSeq.flatMap(_.obj.get("def").toSeq.flatMap(_.arr))
    parameters.find(_("name").str == "Measurement Period").map { p =>
      val spec = p.obj.get("parameterTypeSpecifier").orElse(p.obj.get("resultTypeSpecifier"))
      val point = spec.filter(_.obj.get("type").contains(ujson.Str("IntervalTypeSpecifier")))
        .flatMap(_.obj.get("pointType")).flatMap(_.obj.get("name")).map(_.str)
      def bound(value: String, end: Boolean): String = point match {
        case Some("{urn:hl7-org:elm-types:r1}DateTime") =>
          if (value.contains("T")) value else value + (if (end) "T23:59:59.999" else "T00:00:00.000")
        case Some("{urn:hl7-org:elm-types:r1}Date") => value.takeWhile(_ != 'T')
        case _ => throw new IllegalArgumentException(
          "Measurement Period must be declared Interval<DateTime> or Interval<Date>")
      }
      Map("Measurement Period" -> s"Interval[@${bound(bounds._1, false)}, @${bound(bounds._2, true)}]")
    }.getOrElse(Map.empty)
  }
}
