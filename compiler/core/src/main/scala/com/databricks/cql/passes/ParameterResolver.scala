package com.databricks.cql.passes

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._

/** Normalize step: bind `ParameterRef`s to values (TECH-CONFIG, LANG-PARAMETER). CQL parameters
  * are constants for a run — "literal/parameter scalars as broadcasts" (Phase 1). Phase 1 binds
  * each `ParameterRef` to its declared default; engine-supplied overrides are a later seam. A
  * parameter with neither a bound value nor a default fails loudly (CORE-NO-SILENT-MISLOWER).
  * Runs after function inlining so parameters referenced inside inlined bodies are bound too. */
object ParameterResolver {

  def normalize(lib: Library): Library = {
    if (lib.parameters.isEmpty) lib
    else {
      val defaults: Map[String, Expr] =
        lib.parameters.collect { case ParameterDef(name, _, Some(default)) => name -> default }.toMap
      val rewritten = lib.statements.map {
        case e: ExpressionDef => e.copy(expression = substitute(e.expression, defaults))
        case other            => other
      }
      lib.copy(statements = rewritten)
    }
  }

  private def substitute(expr: Expr, defaults: Map[String, Expr]): Expr = expr match {
    case ParameterRef(name, _) =>
      // Recurse into the default so a default that itself references a parameter resolves too
      // (acyclic — CQL forbids parameter cycles).
      substitute(
        defaults.getOrElse(name,
          throw UnsupportedNode("ParameterRef",
            s"parameter '$name' has no default and no bound value (Phase 1 supports defaulted parameters)")),
        defaults)
    case other =>
      other.withNewChildren(other.children.map {
        case e: Expr => substitute(e, defaults)
        case c       => c
      }).asInstanceOf[Expr]
  }
}
