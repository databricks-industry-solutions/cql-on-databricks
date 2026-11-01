package com.databricks.cql.env

/** BIND-RELATION, BIND-PROVIDER-REQUEST: what a compile request binds one retrieve, or one terminology layer,
  * to. A relation NAMES where the rows are; it never carries a field mapping. The client turns a volume or
  * DataFrame relation into a temporary view before it compiles, so the compiler only ever sees a table (or
  * view) name or a SQL function name. `identity` is the identity of the provider that answered
  * (`CLIENT-PROVIDER-IDENTITY`); a kept result's fingerprint covers it. The compiler reads no catalog
  * (`ARCH-PURITY`): a function's parameters come in the request. */
sealed trait RetrieveRelation {
  def identity: Option[String]
}

object RetrieveRelation {

  /** A Delta table or UC view holding the contract `(__subject_id, __resource)` (`BIND-CONTRACT`), or, for
    * terminology, `(valueset_id, version, system, code)`. */
  final case class Table(name: String, identity: Option[String] = None) extends RetrieveRelation

  /** A UC SQL table function on the contract, called with only the parameters it declares, by name
    * (`BIND-NAMED-ARGS`). Each parameter names a retrieve request field. */
  final case class Function(name: String, parameters: Seq[String], identity: Option[String] = None)
      extends RetrieveRelation

  /** Parse one relation off the wire: `{"table": name}` or `{"function": name, "parameters": [...]}`, each
    * with an optional `identity`. Anything else fails loudly, naming what was sent. */
  def fromJson(v: ujson.Value): RetrieveRelation = {
    val o = v.obj
    val identity = o.get("identity").filterNot(_.isNull).map(_.str)
    (o.get("table").filterNot(_.isNull), o.get("function").filterNot(_.isNull)) match {
      case (Some(t), None) => Table(t.str, identity)
      case (None, Some(f)) =>
        Function(f.str, o.get("parameters").filterNot(_.isNull).map(_.arr.map(_.str).toSeq).getOrElse(Nil), identity)
      case _ => throw new IllegalArgumentException(
        s"a relation names a table or a function, not ${ujson.write(v)} (BIND-PROVIDER-REQUEST)")
    }
  }
}
