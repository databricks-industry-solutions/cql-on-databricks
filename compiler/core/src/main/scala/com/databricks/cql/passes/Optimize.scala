package com.databricks.cql.passes

import com.databricks.cql.env.Config
import com.databricks.cql.ir.Library
import com.databricks.cql.rules.{Batch, DateRangePushdown, PeriodRangePushdown, Rule, RuleExecutor, Strategy, TerminologyPushdown}

/** The Optimize pass (ARCH-PIPELINE, ARCH-PUSHDOWN, `engine-design.md` section 7): the
  * domain-specific retrieve-pushdown optimizer, a kernel→kernel rewrite that runs between
  * Normalize and Lower — the `Optimize` slot in the documented pipeline.
  *
  * It is ADDITIVE by construction (ARCH-SEAMS): each optimization is a [[Rule]] in the pushdown
  * [[Batch]], run to fixpoint by the shared [[RuleExecutor]] (idempotent rules, capped iterations,
  * error on non-convergence — ARCH-HAZARDS), never an edit to a lowering monolith. Because lowering
  * stays pure (ARCH-PURITY), reordering/rewriting kernel nodes here is sound.
  *
  * Faithfulness gate. `config.optimizeRetrievePushdown` is the on/off switch:
  *   - OFF ⇒ execute the measure EXACTLY as written (the optimizer-faithfulness ground truth);
  *   - ON  ⇒ run the pushdown batch.
  * The two paths must produce identical results on the whole corpus; that empty self-diff is what
  * proves a rewrite rule introduced no bug (Gate 4). Gate 3 proves the optimized path is still
  * spec-faithful against the Java reference.
  *
  * Each rule in [[pushdownRules]] is gated two ways: spec-faithful vs the reference, and
  * optimizer-faithful vs the unoptimized self-diff.
  */
object Optimize {

  /** The retrieve-pushdown rule batch (ARCH-PUSHDOWN). Additive — rules join here one at a time
    * (ARCH-SEAMS); each must be idempotent so the `FixedPoint` batch converges (ARCH-HAZARDS). */
  private def pushdownRules: Seq[Rule] = Seq(TerminologyPushdown, DateRangePushdown, PeriodRangePushdown)

  /** Run the optimizer over a normalized library, gated by `config`. With pushdown OFF (or no rules
    * yet) this is the IDENTITY, so Lower sees the measure exactly as written — which is precisely
    * the unoptimized ground truth the self-diff gate compares against.
    *
    * `onRuleFired` receives `(batch, rule, iteration)` for plan-diff traceability (ARCH-IR) — the
    * trace you need when a measure's rate moves; the default discards it. */
  def run(lib: Library, config: Config,
          onRuleFired: (String, String, Int) => Unit = (_, _, _) => ()): Library = {
    val rules = pushdownRules
    if (!config.optimizeRetrievePushdown || rules.isEmpty) lib
    else {
      val executor = new RuleExecutor(
        batches = Seq(Batch("RetrievePushdown", Strategy.FixedPoint(maxIterations = 50), rules: _*)),
        onRuleFired = onRuleFired)
      executor.execute(lib).asInstanceOf[Library]
    }
  }
}
