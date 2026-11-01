package com.databricks.cql.transpiler.lower

import java.nio.charset.StandardCharsets
import java.security.MessageDigest

import com.databricks.cql.transpiler.emit.SqlExpr
import com.databricks.cql.transpiler.emit.SqlExpr._

/** BIND-RETRIEVE-REQUEST, BIND-PROVIDER-REQUEST: one retrieve the compiled SQL makes, and what it asks for.
  *
  * The single home for a retrieve's request. `describe` publishes it ([[requestJson]], [[id]]); the client
  * asks its data provider with it; the compiler then embeds the relation bound to [[id]] and, for a function,
  * passes the declared fields as named arguments ([[namedArgs]], `BIND-NAMED-ARGS`). Every value is a
  * compile-time literal, so Catalyst folds a guard on it (`BIND-PUSHDOWN`). An absent value is NULL or an
  * empty array or map. No field names a library (`EMIT-ROOT-INDEPENDENT`).
  *
  * `codeProperty` is not a request field. It is kept only for the positional call of a `sources` binding
  * ([[positionalArgs]]). */
final case class RetrieveCall(fhirType: String,
                              valueSet: Option[String] = None,
                              codeProperty: Option[String] = None,
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

  /** The retrieve's id in the request's `data`: the FHIR type, `_`, and 12 hex digits of a hash of the
    * request, so two retrieves with the same request share one relation. */
  lazy val id: String = s"${fhirType}_${sha256(ujson.write(requestJson)).take(12)}"

  /** The argument a function relation's declared parameter `field` takes. Only request fields exist. */
  def arg(field: String): SqlExpr = field match {
    case "fhir_type"     => str(fhirType)
    case "value_set"     => optStr(valueSet)
    case "codes"         => codesArg
    case "date_property" => optStr(dateProperty)
    case "date_low"      => optStr(dateLow)
    case "date_high"     => optStr(dateHigh)
    case "parameters"    => parametersArg
    case other => throw new IllegalArgumentException(
      s"retrieve function parameter '$other' names no retrieve request field (BIND-NAMED-ARGS): give it a " +
        s"default, and the client leaves it out")
  }

  /** BIND-NAMED-ARGS: `fn(value_set => …, date_low => …)` with only the parameters the function declares. */
  def namedArgs(declared: Seq[String]): Seq[SqlExpr] = declared.map(p => NamedArg(p, arg(p)))

  /** The seven positional arguments of a `sources` binding: value_set, code_property, codes, date_property,
    * date_low, date_high, parameters. */
  def positionalArgs: Seq[SqlExpr] =
    Seq(optStr(valueSet), optStr(codeProperty), codesArg, optStr(dateProperty), optStr(dateLow), optStr(dateHigh),
      parametersArg)

  private def codesArg: SqlExpr =
    Call("array", codes.map { case (system, code) => Struct(Seq("system" -> str(system), "code" -> str(code))) })

  /** A `MAP<STRING, VARIANT>`: a literal interval as a `{low, lowClosed, high, highClosed}` VARIANT object, a
    * scalar as a VARIANT scalar. The closure flags are carried, not assumed: a half-open Measurement Period
    * read as inclusive would over-select a whole instant at its boundary. */
  private def parametersArg: SqlExpr =
    Call("map", parameters.flatMap { case (n, v) => Seq(str(n), v.sql) })

  private def optStr(v: Option[String]): SqlExpr = v.map(str).getOrElse(Null)
}

object RetrieveCall {

  /** A parameter's literal value, as the request carries it. */
  sealed trait Value {
    def json: ujson.Value
    def sql: SqlExpr
  }

  final case class Scalar(value: String) extends Value {
    def json: ujson.Value = ujson.Str(value)
    def sql: SqlExpr = Cast(str(value), "VARIANT")
  }

  final case class Interval(low: String, lowClosed: Boolean, high: String, highClosed: Boolean) extends Value {
    def json: ujson.Value =
      ujson.Obj("low" -> low, "lowClosed" -> lowClosed, "high" -> high, "highClosed" -> highClosed)
    def sql: SqlExpr = Call("to_variant_object", Seq(Struct(Seq(
      "low" -> str(low), "lowClosed" -> bool(lowClosed), "high" -> str(high), "highClosed" -> bool(highClosed)))))
  }

  private def opt(v: Option[String]): ujson.Value = v.map(ujson.Str(_)).getOrElse(ujson.Null)

  private def sha256(text: String): String =
    MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))
      .map(b => f"${b & 0xff}%02x").mkString
}
