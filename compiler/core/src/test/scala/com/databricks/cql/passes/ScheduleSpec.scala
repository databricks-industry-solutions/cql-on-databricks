package com.databricks.cql.passes

import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Schedule (`ARCH-PIPELINE`, `ARCH-SCHEDULE-ACYCLIC`): the define-DAG topological sort follows only
  * INTRA-library reference edges — the exact set `DefineGraph.assertAcyclic` checks — and marks a node the
  * moment it is entered, so it cannot be driven into unbounded recursion by a mis-fed (un-flattened)
  * library. This is the regression guard for the multi-library eCQM `StackOverflowError`: real measures
  * re-export a Supplemental Data Element as a same-named local define, which the old `ExpressionRef(n, _)`
  * edge mistook for a self-cycle. */
@RunWith(classOf[JUnitRunner])
class ScheduleSpec extends AnyFunSuite {

  private def edef(name: String, e: Expr): ExpressionDef = ExpressionDef(name, Some("Patient"), e)

  test("a qualified cross-library ref whose name matches a local define is not a self-edge (ARCH-SCHEDULE-ACYCLIC)") {
    // A supplemental-data-element self-export: `define "Supplemental Sex": SDE."Supplemental Sex"`. The local define
    // "Supplemental Sex" references a SAME-NAMED define in another library (alias "SDE"). Counting that qualified ref
    // as a local edge forged a self-cycle the acyclicity check never saw, and the post-order visit then
    // recursed forever. It must schedule cleanly, dependency-first.
    val defs = Seq(
      edef("Supplemental Sex", ExpressionRef("Supplemental Sex", Some("SDE"))),      // the qualified re-export
      edef("Initial Population", ExpressionRef("Supplemental Sex", None)))  // a real intra-library dependent
    val ordered = Schedule.order(defs).map(_.name)

    assert(ordered.toSet == Set("Supplemental Sex", "Initial Population"))
    // the qualified ref is NOT an edge, so the local define is a leaf; the intra-library dependent follows it
    assert(ordered.indexOf("Supplemental Sex") < ordered.indexOf("Initial Population"))
  }

  test("an intra-library reference cycle terminates (marked on entry), never overflows") {
    // A direct A <-> B cycle (which Resolve rejects upstream) must degrade to a terminating best-effort
    // order here — defense in depth so a mis-fed library errors loudly downstream, never StackOverflows.
    val defs = Seq(edef("A", ExpressionRef("B", None)), edef("B", ExpressionRef("A", None)))
    val ordered = Schedule.order(defs).map(_.name)

    assert(ordered.toSet == Set("A", "B"))
  }
}
