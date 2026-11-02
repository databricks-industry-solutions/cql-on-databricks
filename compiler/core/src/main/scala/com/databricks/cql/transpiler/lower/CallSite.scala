package com.databricks.cql.transpiler.lower

import java.nio.charset.StandardCharsets
import java.security.MessageDigest

/** BIND-CALL-SITE: one place the compiled CQL reads data or a value set, and what a resolver needs to bind it.
  *
  * `kind` is `retrieve` or `valueset`. `library` is the name and version of the library the site is written in
  * and `define` the define it is in, both none for the context's own retrieve, which belongs to no define. Never
  * the root being compiled, so a define's SQL is the same whichever library includes it
  * (`EMIT-ROOT-INDEPENDENT`). `request` is a retrieve's [[RetrieveCall.requestJson]], or a value set's id and
  * version. The id hashes all of it, so two retrieves with the same request in one define are one site. */
final case class CallSite(kind: String, library: Option[(String, String)], define: Option[String],
                          request: ujson.Obj) {

  lazy val json: ujson.Obj = ujson.Obj(
    "kind" -> kind,
    "library" -> library.map(l => ujson.Str(l._1)).getOrElse(ujson.Null),
    "version" -> library.map(l => ujson.Str(l._2)).getOrElse(ujson.Null),
    "define" -> define.map(ujson.Str(_)).getOrElse(ujson.Null),
    "request" -> request)

  /** The site's id in the request's `sites`: its kind, `_`, and 12 hex digits of a hash of the site. */
  lazy val id: String = s"${kind}_${CallSite.sha256(ujson.write(json)).take(12)}"

  def describeJson: ujson.Obj = ujson.Obj.from(json.value ++ Seq("id" -> ujson.Str(id)))
}

object CallSite {
  val Retrieve = "retrieve"
  val ValueSet = "valueset"

  def retrieve(call: RetrieveCall, owner: Option[(String, String, String)]): CallSite =
    CallSite(Retrieve, owner.map(o => (o._1, o._2)), owner.map(_._3), call.requestJson)

  def valueSet(id: String, version: Option[String], owner: Option[(String, String, String)]): CallSite =
    CallSite(ValueSet, owner.map(o => (o._1, o._2)), owner.map(_._3),
      ujson.Obj("valueSet" -> id, "version" -> version.map(ujson.Str(_)).getOrElse(ujson.Null)))

  private def sha256(text: String): String =
    MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))
      .map(b => f"${b & 0xff}%02x").mkString
}
