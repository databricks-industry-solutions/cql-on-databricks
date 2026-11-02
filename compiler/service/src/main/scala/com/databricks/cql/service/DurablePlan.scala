package com.databricks.cql.service

import java.nio.charset.StandardCharsets
import java.security.MessageDigest

import scala.collection.mutable
import com.databricks.cql.ir._
import com.databricks.cql.load.ElmCodec
import com.databricks.cql.passes.ParameterScope
import com.databricks.cql.transpiler.emit.SqlNames

/** RUN-CLIENT-RESULT-IDENTITY, PIPE-DURABLE-IDENTITY — one compiler-owned identity per materialized recipe.
  * `fingerprint` is the compiler's part of the result's identity (`DEC-RESULT-IDENTITY`): the identity format,
  * the recipe's emitted SQL, the parameter values it uses, its upstream recipes' fingerprints and the identities
  * of the resolvers bound. `valueSets` lists every value set the recipe's define, or any define it reads, uses;
  * the client completes the fingerprint with a digest of their codes, since the compiler reads no catalog
  * (`CLIENT-RESOLVER-IDENTITY`). Where it is stored, who runs it and the compiler version are not part of it.
  * `reusable` is false for a non-deterministic define and every define that reads one. No SQL is parsed to
  * establish identity. */
final case class DurableIdentity(format: String, fingerprint: String, reusable: Boolean,
                                 parameters: ujson.Obj = ujson.Obj(),
                                 valueSets: Seq[(String, Option[String])] = Nil) {
  def toJson: ujson.Obj = ujson.Obj(
    "format" -> format, "fingerprint" -> fingerprint, "reusable" -> reusable, "parameters" -> parameters,
    "valueSets" -> ujson.Arr.from(valueSets.map { case (id, version) =>
      ujson.Obj("id" -> id, "version" -> version.map(ujson.Str(_)).getOrElse(ujson.Null)) }))
}

object DurablePlan {
  val Format = "cql-result-identity-v1"

  /** CLIENT-RESOLVER-IDENTITY: the sorted, distinct identities of the data and terminology resolvers whose
    * relations the compile binds, hashed into every recipe's fingerprint. */
  final case class Providers(data: Seq[String], terminology: Seq[String]) {
    def input: (String, ujson.Value) =
      "providers" -> ujson.Obj("data" -> ujson.Arr.from(data), "terminology" -> ujson.Arr.from(terminology))
  }

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

  /** CLIENT-RESOLVER-IDENTITY — every value set each definition's dependency closure uses, `(id, version)`, sorted.
    * A superset of what the recipe's own SQL reads, since its closure spans the upstream recipes it reads: so a
    * changed value set changes every result downstream of it, through the client's digest. */
  def valueSetClaims(lib: Library, context: SqlNames.DatasetNameContext): Map[String, Seq[(String, Option[String])]] = {
    val graph = com.databricks.cql.verify.DefineGraph.dependencies(lib)
    val statements = lib.statements.collect {
      case d: ExpressionDef => d.name -> d.expression
      case f: FunctionDef => f.name -> f.body
    }.toMap
    val declared = lib.valueSets.map(v => v.name -> (v.id, v.version)).toMap
    def closure(name: String, visited: Set[String] = Set.empty): Set[String] =
      if (visited(name)) Set.empty
      else statements.get(name).toSet.flatMap((e: Expr) => e.collect { case r: ValueSetRef => r.name }.toSet) ++
        graph.getOrElse(name, Set.empty).flatMap(closure(_, visited + name))
    statements.keys.map { name =>
      SqlNames.datasetName(name, context) -> closure(name).toSeq.flatMap(declared.get).distinct.sortBy(_._1)
    }.toMap
  }

  /** RUN-CLIENT-RESULT-IDENTITY, CLIENT-RESOLVER-IDENTITY — retain the original SQL and wire defaults; annotate
    * only on opt-in. `boundTo` is what the compile bound its data to ([[Providers]]). It is hashed
    * as its own input, though each relation is already named in the SQL, so the identity does not depend on
    * how a recipe happens to reach its data. */
  def annotate(plan: PipelinePlan, nonReusable: Set[String],
               parameters: Map[String, ujson.Obj] = Map.empty, boundTo: Providers = Providers(Nil, Nil),
               valueSets: Map[String, Seq[(String, Option[String])]] = Map.empty): PipelinePlan = {
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
        boundTo.input))
      visiting -= name
      DurableIdentity(Format, fingerprint, !nonReusable(name) && upstream.forall(_._2.reusable), bound,
        valueSets.getOrElse(name, Nil))
    })

    plan.copy(datasets = plan.datasets.map(recipe => recipe.copy(durable = Some(identity(recipe.name)))))
  }
}
