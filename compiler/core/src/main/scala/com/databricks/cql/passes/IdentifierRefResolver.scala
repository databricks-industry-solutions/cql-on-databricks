package com.databricks.cql.passes

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._

/** Normalize step: resolve a bare ELM [[IdentifierRef]] to a scope-rooted [[Property]] (LANG-QUERY-SORT).
  *
  * The translator emits `IdentifierRef(name)` for an identifier it cannot bind to a concrete ref — in
  * the corpus, only inside a `sort by <expr>` key, where CQL exposes no alias for the element being
  * sorted (e.g. `sort by end of effective.effectiveSpan()` — `effective` is a property of the
  * sort element, the result of a single-source, no-`return` query). The sort element of such a query IS
  * the source alias's element, so `IdentifierRef(effective)` means `O.effective`:
  * rewrite it to `Property(scope = Some(<source alias>), path = name)`, which the existing sort-key
  * lowering resolves against the bound element (the alias → element mapping in `SqlQueryLowering`).
  *
  * Scope is the enclosing query's sort clause only — an `IdentifierRef` anywhere else, or in a
  * multi-source query's sort (where the element is a tuple, not a single alias's value), is rejected
  * loudly (`UnsupportedNode`, CORE-NO-SILENT-MISLOWER) rather than silently mis-bound. */
object IdentifierRefResolver {

  def normalize(lib: Library): Library =
    lib.copy(statements = lib.statements.map {
      case e: ExpressionDef => e.copy(expression = rewrite(e.expression))
      case f: FunctionDef   => f.copy(body = rewrite(f.body))
      case other            => other
    })

  private def rewrite(expr: Expr): Expr = expr match {
    case q: Query =>
      // Rewrite children first (nested queries resolve THEIR own sort identifiers against their own
      // aliases); then bind any IdentifierRef remaining directly in THIS query's sort keys.
      val q2 = Query(
        sources        = q.sources.map(s => s.copy(expr = rewrite(s.expr))),
        where          = q.where.map(rewrite),
        returnExpr     = q.returnExpr.map(rewrite),
        returnDistinct = q.returnDistinct,
        sort           = q.sort.map(si => si.copy(expr = si.expr.map(rewrite))),
        aggregate      = q.aggregate.map(a => a.copy(starting = rewrite(a.starting), body = rewrite(a.body))),
        relationships  = q.relationships.map(r => r.copy(expr = rewrite(r.expr), suchThat = rewrite(r.suchThat))),
        lets           = q.lets.map(l => l.copy(expr = rewrite(l.expr))))
      bindSortIdentifiers(q2)
    case other =>
      other.withNewChildren(other.children.map {
        case e: Expr => rewrite(e)
        case c       => c
      }).asInstanceOf[Expr]
  }

  /** Bind an [[IdentifierRef]] appearing in this query's sort keys to the single source alias. Nested
    * identifiers were already resolved by the recursion, so only this query's remain. */
  private def bindSortIdentifiers(q: Query): Query = {
    val hasSortIdentifier = q.sort.exists(_.expr.exists(_.exists { case _: IdentifierRef => true; case _ => false }))
    if (!hasSortIdentifier) q
    else q.sources match {
      case Seq(single) =>
        def bind(e: Expr): Expr = Expr.coerce(e.transformDown {
          case IdentifierRef(name) => Property(None, Some(single.alias), name)
        })
        q.copy(sort = q.sort.map(si => si.copy(expr = si.expr.map(bind))))
      case _ =>
        throw UnsupportedNode("IdentifierRef",
          "sort-key identifier is only resolvable over a single-source query (the sort element is that " +
            s"alias's value); got ${q.sources.size} sources")
    }
  }
}
