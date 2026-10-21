package com.databricks.cql.passes

import com.databricks.cql.ir._

/** Static list-nullability (ARCH-LIST-NULL-SIDECAR, LANG-LIST-NULL-STATE): can a list-valued
  * expression evaluate to NULL (as opposed to always empty-or-more)?
  *
  * Like [[TypeEncoding.typeOf]] this is a RECOMPUTABLE synthesized attribute (ARCH-TYPE-ATTR), not
  * state on the IR: [[isNullableList]] folds a node bottom-up on demand, resolving an in-library
  * `ExpressionRef` through its define body, so it survives Normalize rewrites for free.
  *
  * It is the FIRST gate of the nullity sidecar (`list-null-tristate.md` section 4 gate 1): a define
  * whose body is provably non-null (a retrieve, a `union`, a `Query` with a return, `ToList`, a list
  * literal — all yield empty-or-more per LANG-LIST, never null) emits NO `(__subject_id, __is_null)`
  * sidecar at all. Only a body that can be null — a `NullExpr`/null literal, an `if`/`case` with a
  * nullable branch (an `if` with no `else` is `If(c, then, NullExpr())`, so it counts), an all-null
  * `Coalesce`, or an unresolved/cross-library reference (conservatively nullable so a real null is
  * never rendered as empty) — is tracked.
  *
  * Phase 0 (`list-null-tristate.md` section 6) ships this attribute INERT: it is computed and threaded
  * into [[com.databricks.cql.lower.EvalEnv]] but nothing consumes it until the sidecar
  * production lands. (PROC-CITE-REQ) */
object ListNullability {

  /** Inherited context (ARCH-ENV): the library's define bodies, for resolving an `ExpressionRef`'s
    * nullability — mirrors [[TypeEncoding.TypeCtx.defines]]. */
  final case class NullCtx(defines: Map[String, Expr])

  def ctxOf(lib: Library): NullCtx =
    NullCtx(lib.statements.collect { case e: ExpressionDef => e.name -> e.expression }.toMap)

  /** True iff `expr` (a list-valued expression) can evaluate to NULL rather than empty-or-more. */
  def isNullableList(expr: Expr, ctx: NullCtx): Boolean = infer(expr, ctx, Set.empty)

  /** Per-define nullability — the map [[com.databricks.cql.lower.FrameLowering]] threads via
    * `EvalEnv.listNullability` (the form parallel to `Materialize.reuseCounts`). Deterministic. */
  def perDefine(lib: Library): Map[String, Boolean] = {
    val ctx = ctxOf(lib)
    ctx.defines.map { case (name, body) => name -> isNullableList(body, ctx) }
  }

  // `visiting` guards an `ExpressionRef` chain against runaway recursion; Resolve already rejects
  // true cycles (DefineGraph), so this only bounds the walk defensively (mirrors TypeEncoding).
  private def infer(expr: Expr, ctx: NullCtx, visiting: Set[String]): Boolean = {
    def go(e: Expr): Boolean = infer(e, ctx, visiting)
    expr match {
      // -- Null introducers --
      case NullExpr()          => true
      case Literal(_, None)    => true              // a null literal
      // -- Conditionals: nullable iff ANY reachable branch is nullable (an `else`-less `if` carries
      //    a NullExpr else from ElmCodec, so it is caught here). --
      case If(_, t, e)         => go(t) || go(e)
      case Case(_, items, els) => items.exists(i => go(i.thenExpr)) || go(els)
      // Coalesce yields null only when EVERY operand can be null (the first non-null wins).
      case Coalesce(os)        => os.nonEmpty && os.forall(go)
      // A cast does not introduce null; a null operand stays null (As-to-choice over a list too).
      case As(o, _, _, _)      => go(o)
      // `distinct` neither introduces nor removes null — a null list dedupes to null.
      case Distinct(o)         => go(o)
      // -- References: resolve in-library; conservatively nullable when unresolvable. --
      case ExpressionRef(name, None) =>
        ctx.defines.get(name) match {
          case Some(_) if visiting.contains(name) => false
          case Some(body)                         => infer(body, ctx, visiting + name)
          case None                               => true   // cross-library / external → conservative
        }
      case _: ExpressionRef    => true              // qualified cross-library ref → conservative
      case _: FunctionRef      => true              // an un-inlined / stdlib list-returning fn → conservative
      // -- Provably NON-null list producers (LANG-LIST: empty-or-more, never null). --
      case _: Retrieve                         => false
      case _: Union | _: Intersect | _: Except => false
      case _: ToList | _: ListExpr             => false
      case _: Query                            => false   // a query is a list (empty if no rows); aggregate is scalar
      // Any other node is not a null-introducing list producer (a scalar literal, arithmetic, etc.).
      case _                                   => false
    }
  }
}
