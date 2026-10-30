package com.databricks.cql.rules

import com.databricks.cql.ir._

/** Shared skeleton for retrieve-pushdown rules (ARCH-PUSHDOWN, PROC-DRY): every such rule folds the
  * first `where` conjunct it can absorb into a retrieve source and drops that conjunct, leaving the
  * rest of the `where` in place. The rules differ only in WHICH conjunct/source pair folds and into
  * which retrieve slot — that variation is the `fold` callback; the conjunct-splitting,
  * first-match, source-replacement, and where-reassembly are identical and live here once. Folding
  * one conjunct per application keeps each rule idempotent so the FixedPoint batch converges
  * (ARCH-HAZARDS). */
private[rules] object PushdownSupport {

  /** Fold the first `(conjunct, source)` for which `fold` yields a rewritten retrieve: replace that
    * source's expression and remove the conjunct from the `where`. `None` if nothing folds (the
    * caller returns the query unchanged, signalling no-op to the fixpoint). */
  def foldFirst(q: Query)(fold: (Expr, QuerySource) => Option[Retrieve]): Option[Query] = {
    val conjuncts = q.where.toSeq.flatMap(RetrieveFilter.conjuncts)
    val target = conjuncts.iterator.flatMap { c =>
      q.sources.zipWithIndex.iterator.collect(Function.unlift { case (s, idx) =>
        fold(c, s).map(r => (c, idx, r))
      })
    }.nextOption()
    target.map { case (c, idx, filtered) =>
      q.copy(
        sources = q.sources.updated(idx, q.sources(idx).copy(expr = filtered)),
        where = conjuncts.filterNot(_ eq c).reduceOption(And(_, _)))
    }
  }
}
