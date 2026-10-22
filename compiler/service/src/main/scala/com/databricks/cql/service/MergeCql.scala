package com.databricks.cql.service

import com.databricks.cql.translate.CqlFragments

/** SVC-NOTEBOOK-SESSION-SCOPE — the `merge-cql` wire op, shared by every host: `{fragments:[cql, ...]}` ->
  * `{cql, defines}`. The session library and a new fragment go in, in order; the merged library and the
  * defines the last fragment declares come out. Session-free and Spark-free, like every compile op. */
object MergeCql {
  def handle(body: String): ujson.Obj = {
    val request = ujson.read(if (body.isEmpty) "{}" else body)
    val fragments = request.obj.get("fragments").flatMap(_.arrOpt)
      .getOrElse(throw new IllegalArgumentException("merge-cql needs `fragments`, a list of CQL texts"))
      .map(_.str).toSeq
    val merged = CqlFragments.merge(fragments)
    ujson.Obj("cql" -> merged.cql, "defines" -> ujson.Arr.from(merged.defines))
  }
}
