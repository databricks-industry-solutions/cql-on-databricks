package com.databricks.cql.rules

import com.databricks.cql.ir.IrNode

/** A term-rewriting rule: `KernelNode => KernelNode` (ARCH-IR, `02` section 3).
  *
  * Each rule is unit-testable in isolation (a TDD line item, PROC-TDD) and should be
  * idempotent — applying it twice equals applying it once — so batches reach a fixpoint
  * instead of ping-ponging (ARCH-HAZARDS). A rule rewrites only the cases it matches via
  * `transformUp`/`transformDown`; everything else passes through untouched.
  */
abstract class Rule {
  /** A stable name, logged when the rule fires (plan-diff traceability, ARCH-IR). */
  def name: String

  /** Apply the rule across the tree. Implementations choose `transformUp`/`transformDown`.
    * Returning the same tree (by value) signals "no change" to the fixpoint loop. */
  def apply(plan: IrNode): IrNode
}

/** A rule that fires on individual nodes via a partial function, applied bottom-up.
  * The common case — you write only the cases you rewrite. */
abstract class NodeRule extends Rule {
  def rule: PartialFunction[IrNode, IrNode]
  final def apply(plan: IrNode): IrNode = plan.transformUp(rule)
}

/** How many times a batch may iterate before we declare non-convergence. */
sealed trait Strategy { def maxIterations: Int }
object Strategy {
  /** Run the batch's rules exactly once. */
  case object Once extends Strategy { val maxIterations = 1 }
  /** Run to a fixpoint, capped. Exceeding the cap is an ERROR, never a silent stop
    * (ARCH-HAZARDS: rule fixpoint non-termination). */
  final case class FixedPoint(maxIterations: Int = 100) extends Strategy
}

/** An ordered group of rules with a run strategy (ARCH-IR). Batches give ordering
  * control so rewrites compose (constant-fold → dead-branch elim → pushdown). */
final case class Batch(name: String, strategy: Strategy, rules: Rule*)

/** A Catalyst-style fixpoint batch runner (ARCH-IR, TECH-SPARK-MAP "rule engine").
  *
  * Runs each batch's rules in order, iterating per the batch strategy until the tree
  * stops changing (by value) or the iteration cap is hit. Hitting the cap throws — we
  * never silently stop on a non-converged batch (ARCH-HAZARDS). Optionally logs which
  * rule fired on which iteration (the plan-diff traceability you need when a rate moves).
  */
class RuleExecutor(batches: Seq[Batch], onRuleFired: (String, String, Int) => Unit = (_, _, _) => ()) {

  def execute(initial: IrNode): IrNode = {
    var current = initial
    for (batch <- batches) {
      var iteration = 0
      var converged = false
      while (!converged && iteration < batch.strategy.maxIterations) {
        iteration += 1
        val before = current
        for (rule <- batch.rules) {
          val afterRule = rule.apply(current)
          if (!(afterRule eq current)) onRuleFired(batch.name, rule.name, iteration)
          current = afterRule
        }
        // Converged when an entire pass over the batch changed nothing (by value).
        converged = current == before
      }
      if (!converged && batch.strategy.isInstanceOf[Strategy.FixedPoint]) {
        throw new IllegalStateException(
          s"Rule batch '${batch.name}' did not converge within ${batch.strategy.maxIterations} " +
            "iterations (ARCH-HAZARDS: possible non-terminating/oscillating rules).")
      }
    }
    current
  }
}
