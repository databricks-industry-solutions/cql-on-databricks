package com.databricks.cql.service

/** SVC-COMPILE-BATCH — period typing belongs with the compiler. */
private[service] object BatchInputs {
  def prepare(req: CompileRequest): CompileRequest = {
    val period = req.measurementPeriod.map { bounds =>
      require(req.libraries.isEmpty, "measurementPeriod requires a single root and its dependencies")
      require(!req.parameters.contains("Measurement Period") && !req.sessionParameters.contains("Measurement Period"),
        "give measurementPeriod or a Measurement Period parameter, not both")
      periodParameters(req.elmJson, bounds)
    }.getOrElse(Map.empty[String, String])
    req.copy(measurementPeriod = None, parameters = req.parameters ++ period)
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
