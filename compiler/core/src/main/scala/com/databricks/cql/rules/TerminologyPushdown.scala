package com.databricks.cql.rules

import com.databricks.cql.ir._

/** Terminology retrieve pushdown (ARCH-PUSHDOWN, `engine-design.md` section 7 — the highest-value
  * case for quality measures, which are code-dominated): fold a value-set-membership `where` conjunct into its
  * retrieve source, rewriting `[X] C where C.code in "VS"` into the value-set-filtered retrieve
  * `[X: "VS"] C`. The lowering then realizes the retrieve's code filter as a broadcast semi-join
  * over the pre-expanded value-set table (LANG-TERMINOLOGY).
  *
  * The fold is EXACT — the retrieve code filter IS the membership predicate — so the conjunct is
  * removed entirely; no precise per-consumer filter need remain downstream (contrast the date-range
  * case, `engine-design.md` section 7 step 6, where the pushed range is coarser and the `where`
  * stays). This is the sound rewrite for the (a) self-attribute + value-set class.
  *
  * Idempotent + convergent (ARCH-HAZARDS): only an UNFILTERED retrieve source (`codes` empty) whose
  * alias a top-level membership conjunct tests is folded, ONE conjunct per application; the folded
  * source then carries `codes`, and the conjunct is gone, so no further match — the FixedPoint batch
  * converges. Result-preservation is gated by the optimized-vs-unoptimized self-diff
  * (CONF-GATE-SELFDIFF): the unoptimized lowering realizes the same membership as a semi-join.
  */
object TerminologyPushdown extends NodeRule {
  val name = "TerminologyPushdown"

  def rule: PartialFunction[IrNode, IrNode] = {
    case q: Query => PushdownSupport.foldFirst(q)(foldMembership).getOrElse(q)
  }

  /** Fold a membership conjunct into an UNFILTERED retrieve source (empty `codes`) whose alias it
    * tests, populating the code-filter slots. */
  private def foldMembership(conjunct: Expr, source: QuerySource): Option[Retrieve] =
    for {
      (alias, path, vs) <- RetrieveFilter.membership(conjunct)
      r <- source match {
        case QuerySource(`alias`, r: Retrieve) if r.codes.isEmpty => Some(r)
        case _                                                    => None
      }
    } yield r.copy(codeProperty = Some(path), codes = Some(vs))
}
