package com.databricks.cql.passes

import com.databricks.cql.ir.{ExpressionDef, ExpressionRef}

/** The Schedule pass (ARCH-PIPELINE, `ARCH-SCHEDULE-ACYCLIC`): order the define DAG so each define is
  * lowered after the defines it references. Edges are INTRA-LIBRARY `ExpressionRef`s only — a QUALIFIED
  * cross-library ref (`ExpressionRef(n, Some(lib))`) is NOT a local edge, exactly as
  * [[com.databricks.cql.verify.DefineGraph.assertAcyclic]] (the acyclicity precondition this pass
  * relies on) counts them. Matching that filter is load-bearing: a library may re-export a supplemental
  * data-element define with a SAME-NAMED local define (`define "DemographicAttribute": SDE."DemographicAttribute"`), so counting
  * the qualified ref as a local edge would forge a self-cycle the acyclicity check never saw. Ties are
  * broken by name for determinism (TECH-DETERMINISM).
  *
  * The traversal is CYCLE-SAFE regardless (defense in depth, `ARCH-SCHEDULE-ACYCLIC`): a node is marked the
  * moment it is entered, so a back edge is skipped rather than recursed into. That means a mis-fed library
  * (e.g. one lowered WITHOUT `LibraryLinker` flattening, so its cross-library refs are still dangling)
  * degrades to a loud downstream error (`CORE-NO-SILENT-MISLOWER`) instead of a `StackOverflowError`. */
object Schedule {

  def order(defines: Seq[ExpressionDef]): Seq[ExpressionDef] = {
    val byName = defines.map(d => d.name -> d).toMap
    val known = byName.keySet
    // INTRA-LIBRARY edges only (`libraryName.isEmpty`): a qualified cross-library ref is resolved in a
    // later phase and never contributes a local ordering edge — see the class doc for why `_` is wrong.
    val deps: Map[String, Seq[String]] = defines.map { d =>
      d.name -> d.expression.collect { case ExpressionRef(n, None) if known.contains(n) => n }.distinct.sorted
    }.toMap

    val entered = scala.collection.mutable.HashSet.empty[String] // marked on ENTRY → back edges are skipped
    val order   = scala.collection.mutable.LinkedHashSet.empty[String] // post-order = dependency-first
    def visit(n: String): Unit =
      if (entered.add(n)) {
        deps.getOrElse(n, Nil).foreach(visit)
        order += n
      }
    defines.map(_.name).sorted.foreach(visit)
    order.toSeq.map(byName)
  }
}
