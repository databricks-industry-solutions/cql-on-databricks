package com.databricks.cql.transpiler.lower

/** BIND-RETRIEVE-REQUEST, BIND-CALL-SITE: one retrieve the compiled SQL makes, and what it asks for.
  *
  * The single home for a retrieve's request. `describe` publishes it in the retrieve's call site
  * ([[CallSite]]); a resolver that binds per site picks a relation by it. A relation takes no arguments: the
  * compiled plan applies the request's filters above it (`BIND-PUSHDOWN`). An absent value is NULL or an empty
  * array or map. No field names a library (`EMIT-ROOT-INDEPENDENT`). */
final case class RetrieveCall(fhirType: String,
                              valueSet: Option[String] = None,
                              codes: Seq[(String, String)] = Nil,
                              dateProperty: Option[String] = None,
                              dateLow: Option[String] = None,
                              dateHigh: Option[String] = None,
                              parameters: Seq[(String, RetrieveCall.Value)] = Nil) {
  import RetrieveCall._

  /** The request as `describe` publishes it. Every key is present, so the id hashes one fixed shape. */
  lazy val requestJson: ujson.Obj = ujson.Obj(
    "fhirType" -> fhirType,
    "valueSet" -> opt(valueSet),
    "codes" -> ujson.Arr.from(codes.map { case (s, c) => ujson.Obj("system" -> s, "code" -> c) }),
    "dateProperty" -> opt(dateProperty),
    "dateLow" -> opt(dateLow),
    "dateHigh" -> opt(dateHigh),
    "parameters" -> ujson.Obj.from(parameters.sortBy(_._1).map { case (k, v) => k -> v.json }))
}

object RetrieveCall {

  /** A parameter's literal value, as the request carries it. */
  sealed trait Value {
    def json: ujson.Value
  }

  final case class Scalar(value: String) extends Value {
    def json: ujson.Value = ujson.Str(value)
  }

  final case class Interval(low: String, lowClosed: Boolean, high: String, highClosed: Boolean) extends Value {
    def json: ujson.Value =
      ujson.Obj("low" -> low, "lowClosed" -> lowClosed, "high" -> high, "highClosed" -> highClosed)
  }

  private def opt(v: Option[String]): ujson.Value = v.map(ujson.Str(_)).getOrElse(ujson.Null)

}
