package com.databricks.cql.passes

import com.databricks.cql.ir._
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.lower.Encoding

/** Normalize step: eliminate a CORRELATED scalar-source "let-query" by substitution (LANG-QUERY).
  *
  * CQL uses a single-source query with only a `return` as a let-binding: `(e) a return body`. When the
  * source `e` is SINGULAR (not a list), it binds `a = e` once and yields `body` — exactly `body[a := e]`.
  * A helper fluent that builds an onset-to-abatement interval uses this idiom (`(end of condition.abatementSpan())
  * abatementDate return …`); after [[FunctionInliner]] substitutes `condition` with the CALLER's query
  * alias `C`, the let-query's source references that OUTER alias, so lowering the nested query fails
  * (`AliasRef 'C' referenced outside a query`). Inlining the let removes the nested query: the
  * abatement/onset expressions land directly in the enclosing clause, scope-bound to `C`.
  *
  * Fires ONLY when BOTH hold: (1) the source is non-list (`encodingOf` scalar — a list source is a
  * genuine map/filter and must not collapse); (2) the source references an ENCLOSING query alias (it is
  * CORRELATED — the only case that escapes). A self-contained scalar let-query lowers fine as-is, so we
  * leave it untouched to keep every existing plan byte-identical (no golden churn). Type-/scope-driven
  * (ARCH-TYPE-ATTR), threading the alias scope exactly as [[TypeEncoding.typeOf]] does. */
object LetQueryInliner {

  import TypeEncoding.{TypeCtx, typeOf, encodingOf, ctxOf}

  def normalize(lib: Library): Library = {
    val ctx = ctxOf(lib)
    lib.copy(statements = lib.statements.map {
      case e: ExpressionDef => e.copy(expression = rewrite(e.expression, ctx))
      case other            => other
    })
  }

  /** The element type an alias binds over a source: a list source binds the ELEMENT, a singular
    * source binds the value itself (mirrors `typeOf`'s alias-scope rule). */
  private def aliasType(sourceType: CqlType): CqlType = sourceType match {
    case ListType(e) => e
    case t           => t
  }

  private def rewrite(expr: Expr, ctx: TypeCtx): Expr = expr match {
    case q: Query =>
      // Sources type in the ENCLOSING scope; the rest see the aliases this query introduces.
      val innerCtx = q.sources.foldLeft(ctx) { (c, s) => c.withAlias(s.alias, aliasType(typeOf(s.expr, c))) }
      val relCtx   = q.relationships.foldLeft(innerCtx) { (c, r) => c.withAlias(r.alias, aliasType(typeOf(r.expr, innerCtx))) }
      // `let` bindings see the source/relationship aliases + earlier lets (dependency-ordered); the
      // remaining clauses see the lets too. `bodyCtx == relCtx` when there are no lets (no behaviour
      // change for let-free queries). Threading them PRESERVES the clause — a rebuild that dropped
      // `lets` would silently discard the bindings (they default to Nil).
      val (newLets, bodyCtx) = q.lets.foldLeft((Vector.empty[LetClause], relCtx)) { case ((acc, c), l) =>
        (acc :+ l.copy(expr = rewrite(l.expr, c)), c.withAlias(l.identifier, typeOf(l.expr, c)))
      }
      val q2 = Query(
        sources        = q.sources.map(s => s.copy(expr = rewrite(s.expr, ctx))),
        where          = q.where.map(rewrite(_, bodyCtx)),
        returnExpr     = q.returnExpr.map(rewrite(_, bodyCtx)),
        returnDistinct = q.returnDistinct,
        sort           = q.sort.map(si => si.copy(expr = si.expr.map(rewrite(_, bodyCtx)))),
        aggregate      = q.aggregate.map(a => a.copy(starting = rewrite(a.starting, ctx), body = rewrite(a.body, bodyCtx))),
        relationships  = q.relationships.map(r => r.copy(expr = rewrite(r.expr, innerCtx), suchThat = rewrite(r.suchThat, bodyCtx))),
        lets           = newLets)
      inlineLet(q2, ctx).getOrElse(q2)
    case other =>
      other.withNewChildren(other.children.map {
        case e: Expr => rewrite(e, ctx)
        case c       => c
      }).asInstanceOf[Expr]
  }

  /** If `q` is a CORRELATED scalar-source let-query, return `body[alias := source]`; else `None`. */
  private def inlineLet(q: Query, ctx: TypeCtx): Option[Expr] =
    q.returnExpr.filter(_ =>
      q.sources.size == 1 && q.where.isEmpty && q.sort.isEmpty && q.aggregate.isEmpty &&
        q.relationships.isEmpty && q.lets.isEmpty)
      .flatMap { body =>
        val src = q.sources.head
        val correlated = referencesEnclosingAlias(src.expr, ctx.aliases.keySet)
        encodingOf(typeOf(src.expr, ctx)) match {
          case Encoding.Scalar if correlated => Some(substituteAlias(body, src.alias, src.expr))
          case _                             => None
        }
      }

  /** True iff `e` references any alias in `names` (the enclosing-query scope) — i.e. the let-query is
    * correlated and would escape its enclosing alias if lowered as a nested query. */
  private def referencesEnclosingAlias(e: Expr, names: Set[String]): Boolean =
    names.nonEmpty && e.exists { case AliasRef(n) => names.contains(n); case _ => false }

  /** Replace `AliasRef(alias)` with `replacement`, stopping at any nested query that re-binds `alias`. */
  private def substituteAlias(expr: Expr, alias: String, replacement: Expr): Expr = expr match {
    case AliasRef(`alias`) => replacement
    case q: Query if (q.sources.map(_.alias) ++ q.relationships.map(_.alias)).contains(alias) => q
    case other =>
      other.withNewChildren(other.children.map {
        case e: Expr => substituteAlias(e, alias, replacement)
        case c       => c
      }).asInstanceOf[Expr]
  }
}
