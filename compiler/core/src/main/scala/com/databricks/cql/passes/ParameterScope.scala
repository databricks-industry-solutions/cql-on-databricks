package com.databricks.cql.passes

import com.databricks.cql.ir._
import com.databricks.cql.verify.DefineGraph

/** EMIT-ROOT-INDEPENDENT, PIPE-DURABLE-IDENTITY — the parameters each define's dependency closure uses, keyed
  * by the name its OWN library gives them. HL7 CQL 1.5, Library > Parameters: a parameter belongs to the
  * library that declares it. When the library being compiled declares the same name, the linker binds the
  * included library's parameter to it ([[LibraryLinker.link]]); otherwise it keeps its own default.
  *
  * So an included define sees the same keys alone and included: its own library's parameter is keyed by its
  * local name (`Measurement Period`), and another library's by its merged name (`Helpers.Flag`). A parameter
  * the define never reaches is not in its scope, so a parameter only the including library declares changes
  * nothing about an included define. */
object ParameterScope {

  /** Every statement's `(key, parameter)` pairs, sorted by key. */
  def of(lib: Library): Map[String, Seq[(String, ParameterDef)]] = {
    val graph = DefineGraph.dependencies(lib)
    val statements = lib.statements.collect {
      case d: ExpressionDef => d.name -> d.expression
      case f: FunctionDef => f.name -> f.body
    }.toMap
    val parameters = lib.parameters.map(p => p.name -> p).toMap
    def referenced(expr: Expr): Set[String] = expr.collect { case p: ParameterRef => p.name }.toSet
    def closure(name: String, visited: Set[String] = Set.empty): Set[String] =
      if (visited(name)) Set.empty
      else statements.get(name).toSet.flatMap(referenced) ++
        graph.getOrElse(name, Set.empty).flatMap(closure(_, visited + name))
    def expand(names: Set[String]): Set[String] = {
      val more = names ++ names.flatMap(n => parameters(n).default.toSet.flatMap(referenced))
      if (more == names) names else expand(more)
    }
    statements.keys.map { name =>
      val owner = lib.ownerOf(name).library
      name -> expand(closure(name)).toSeq.map { parameter =>
        val declared = lib.ownerOf(parameter)
        (if (declared.library == owner) declared.name else parameter) -> parameters(parameter)
      }.sortBy(_._1)
    }.toMap
  }
}
