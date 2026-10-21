package com.databricks.cql.rules

import com.databricks.cql.ir._

/** Period-property date-range pushdown (ARCH-PUSHDOWN, `engine-design.md` section 7 step 6 — the
  * coarser Period twin of [[DateRangePushdown]]). For `[X] C where C.<period> during <literal
  * interval>` it folds a COARSE, non-lossy scan PRE-FILTER onto the retrieve — a half-open YEAR window
  * on the Period's raw `start`/`end` ISO strings (`Retrieve.periodScanFilter`) — so Delta/Parquet
  * min/max stats prune files BEFORE the expensive precision-aware `during` ever runs.
  *
  * UNLIKE the exact point [[DateRangePushdown]], this KEEPS the `during` conjunct in the `where`: a
  * Period spans two instants, so the scan box is only the `overlaps` SUPERSET of `during` (`E ⊆ MP ⟹
  * E ∩ MP ≠ ∅`); the precise containment still runs downstream and removes the slack. Because the
  * pre-filter is a pure superset, the plan produces identical results with the rule on or off — that
  * is exactly what the optimized-vs-unoptimized self-diff proves (CONF-GATE-SELFDIFF).
  *
  * Why a YEAR window of raw STRINGS (not a `timestamp` cast): FHIR dates are stored as zero-padded ISO
  * strings, whose lexicographic order IS chronological, so a string `>=`/`<` prunes directly on the
  * stored column's min/max stats (a cast would defeat that). And a bare-year prefix sorts BEFORE its
  * dated values (`"2023" < "2023-06-15" < "2024"`), so the half-open `['2023','2024')` box includes a
  * partial `"2023"` correctly — year is the coarsest precision a partial can carry, hence the only
  * non-lossy granularity. The match/window extraction lives in [[RetrieveFilter.periodDuring]].
  *
  * Idempotent + convergent (ARCH-HAZARDS): only a retrieve with an empty `periodScanFilter` folds, and
  * the conjunct stays put, so a second application is a no-op and the FixedPoint batch converges. */
object PeriodRangePushdown extends NodeRule {
  val name = "PeriodRangePushdown"

  def rule: PartialFunction[IrNode, IrNode] = {
    case q: Query => foldPeriodRange(q).getOrElse(q)
  }

  /** Fold the first Period-`during` conjunct's scan box onto the retrieve source it tests, LEAVING the
    * `where` intact (the precise `during` is non-lossy-superset-gated, never removed). `None` if none
    * folds (signals no-op to the fixpoint). */
  private def foldPeriodRange(q: Query): Option[Query] = {
    val conjuncts = q.where.toSeq.flatMap(RetrieveFilter.conjuncts)
    conjuncts.iterator.flatMap { c =>
      q.sources.zipWithIndex.iterator.collect(Function.unlift { case (s, idx) =>
        foldInto(c, s).map(r => q.copy(sources = q.sources.updated(idx, s.copy(expr = r))))
      })
    }.nextOption()
  }

  /** Set the period scan-filter on a retrieve source (empty `periodScanFilter`) whose alias the
    * conjunct tests. */
  private def foldInto(conjunct: Expr, source: QuerySource): Option[Retrieve] =
    for {
      (alias, path, lowYr, hiExcl) <- RetrieveFilter.periodDuring(conjunct)
      r <- source match {
        case QuerySource(`alias`, r: Retrieve) if r.periodScanFilter.isEmpty => Some(r)
        case _                                                               => None
      }
    } yield r.copy(periodScanFilter = Some((path, lowYr, hiExcl)))
}
