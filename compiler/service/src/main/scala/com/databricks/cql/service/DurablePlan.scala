package com.databricks.cql.service

import java.nio.charset.StandardCharsets
import java.security.MessageDigest

import scala.collection.mutable
import com.databricks.cql.ir._
import com.databricks.cql.load.ElmCodec
import com.databricks.cql.passes.ParameterScope
import com.databricks.cql.transpiler.emit.SqlNames

/** RUN-CLIENT-RESULT-IDENTITY, PIPE-DURABLE-IDENTITY — one compiler-owned identity per materialized recipe.
  * `fingerprint` is the result's whole identity (`DEC-RESULT-IDENTITY`): the identity format, the recipe's
  * emitted SQL, the parameter values it uses, its upstream recipes' fingerprints and the bound data source's
  * address. Where it is stored, who runs it and the compiler version are not part of it. `reusable` is false
  * for a non-deterministic define and every define that reads one. No SQL is parsed to establish identity. */
final case class DurableIdentity(format: String, fingerprint: String, reusable: Boolean,
                                 parameters: ujson.Obj = ujson.Obj()) {
  def toJson: ujson.Obj = ujson.Obj(
    "format" -> format, "fingerprint" -> fingerprint, "reusable" -> reusable, "parameters" -> parameters)
}

object DurablePlan {
  val Format = "cql-result-identity-v1"

  private def digest(value: ujson.Value): String =
    MessageDigest.getInstance("SHA-256").digest(ujson.write(value).getBytes(StandardCharsets.UTF_8))
      .map(byte => f"${byte & 0xff}%02x").mkString

  /** PIPE-DURABLE-IDENTITY, EMIT-ROOT-INDEPENDENT — bound ELM parameters, scoped to each definition's
    * dependency closure and keyed by its own library's names ([[ParameterScope]]), so an included define's
    * claims do not depend on the library being compiled. ELM's typed expressions preserve interval endpoints,
    * precision and inclusivity (HL7 CQL 1.5, Appendix B, ParameterDef and Interval). No parameter is parsed
    * from emitted SQL by the client. */
  def parameterClaims(lib: Library, context: SqlNames.DatasetNameContext): Map[String, ujson.Obj] =
    ParameterScope.of(lib).map { case (name, scoped) =>
      val claims = scoped.map { case (key, p) =>
        val value = p.default.getOrElse(throw new IllegalArgumentException(s"unbound durable parameter $key"))
        key -> ujson.Obj("type" -> p.parameterTypeName.getOrElse(s"ELM.${value.elmType}"),
          "value" -> ElmCodec.emitExpr(value))
      }
      SqlNames.datasetName(name, context) -> ujson.Obj.from(claims)
    }

  /** RUN-CLIENT-RESULT-IDENTITY — retain the original SQL and wire defaults; annotate only on opt-in.
    * `source` is the bound data source's address (`catalog.schema`), or None for CQL that retrieves nothing.
    * It is hashed as its own input, though a bound retrieve already names it in the SQL, so the identity
    * does not depend on how a recipe happens to reach its data. */
  def annotate(plan: PipelinePlan, nonReusable: Set[String],
               parameters: Map[String, ujson.Obj] = Map.empty, source: Option[String] = None): PipelinePlan = {
    val recipes = plan.datasets.map(recipe => recipe.name -> recipe).toMap
    require(recipes.size == plan.datasets.size, "duplicate durable dataset names")
    val identities = mutable.Map.empty[String, DurableIdentity]
    val visiting = mutable.Set.empty[String]

    def identity(name: String): DurableIdentity = identities.getOrElseUpdate(name, {
      require(!visiting(name), s"durable dataset dependency cycle at $name")
      val recipe = recipes.getOrElse(name, throw new IllegalArgumentException(s"unknown durable dataset $name"))
      visiting += name
      val upstream = recipe.reads.sorted.map(read => read -> identity(read))
      val bound = parameters.getOrElse(name, ujson.Obj())
      val fingerprint = digest(ujson.Obj(
        "format" -> Format,
        "query" -> recipe.query,
        "parameters" -> bound,
        "upstream" -> ujson.Arr.from(upstream.map { case (read, claim) => ujson.Arr(read, claim.fingerprint) }),
        "source" -> source.map(ujson.Str(_)).getOrElse(ujson.Null)))
      visiting -= name
      DurableIdentity(Format, fingerprint, !nonReusable(name) && upstream.forall(_._2.reusable), bound)
    })

    plan.copy(datasets = plan.datasets.map(recipe => recipe.copy(durable = Some(identity(recipe.name)))))
  }
}
