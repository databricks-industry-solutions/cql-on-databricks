package com.databricks.cql.rules

import com.databricks.cql.ir._

/** Date-range retrieve pushdown (ARCH-PUSHDOWN, `engine-design.md` section 7 — "Time → literal
  * range → file pruning"): fold a date-range `where` conjunct into its retrieve source, rewriting
  * `[X] C where C.<dateProperty> during MeasurementPeriod` into a retrieve carrying the
  * `dateProperty`/`dateRange` scan filter. The lowering applies it at the scan so Delta data
  * skipping / liquid clustering can prune (LANG-INTERVAL for the precision-aware comparison).
  *
  * Only a SCAN-SAFE range folds — a constant interval (a Measurement-Period parameter is inlined to
  * a literal by `ParameterResolver` before Optimize), so the folded `dateRange` lowers standalone at
  * the scan with no surrounding plan. The fold is EXACT for a single point `dateProperty` (the scan
  * filter IS `point in range`, the same lowering as the `where`), so the conjunct is removed — when
  * coarser Period-property (`dateLowProperty`/`dateHighProperty`) pushdown arrives, THAT will keep
  * the precise `where` downstream (section 7 step 6).
  *
  * Idempotent + convergent (ARCH-HAZARDS): only a retrieve with an empty `dateRange` is folded, one
  * conjunct per application. Result-preservation is gated by the optimized-vs-unoptimized self-diff
  * (CONF-GATE-SELFDIFF): the unoptimized lowering keeps the conjunct as a column `where` filter.
  */
object DateRangePushdown extends NodeRule {
  val name = "DateRangePushdown"

  def rule: PartialFunction[IrNode, IrNode] = {
    case q: Query => PushdownSupport.foldFirst(q)(foldDateRange).getOrElse(q)
  }

  /** Fold a date-range conjunct into a retrieve source (empty `dateRange`) whose alias it tests,
    * populating the date-filter slots. */
  private def foldDateRange(conjunct: Expr, source: QuerySource): Option[Retrieve] =
    for {
      (alias, path, interval, converter) <- RetrieveFilter.dateRange(conjunct)
      r <- source match {
        case QuerySource(`alias`, r: Retrieve) if r.dateRange.isEmpty => Some(r)
        case _                                                        => None
      }
    } yield r.copy(dateProperty = Some(path), dateRange = Some(interval), datePointConverter = Some(converter))
}
