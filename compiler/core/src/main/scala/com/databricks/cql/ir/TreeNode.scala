package com.databricks.cql.ir

import scala.collection.mutable

/** Catalyst-style immutable tree base (ARCH-IR, `engine-design.md` section 3).
  *
  * Provides structural recursion (`transformUp`/`transformDown`) so a rewrite rule
  * writes only the cases it matches and the framework passes everything else through
  * untouched. We deliberately mirror Spark's own `TreeNode`: we build on Spark and its
  * architecture is the proven reference for exactly this tree->tree problem.
  *
  * This is the "right kind" of rules engine substrate (term rewriting), explicitly NOT
  * a GoF Visitor (which wants void returns and mutable threaded state) and NOT a
  * Rete/forward-chaining inference engine.
  */
trait TreeNode[BaseType <: TreeNode[BaseType]] { self: BaseType =>

  /** The child nodes, in source order. Order is significant and preserved end to end
    * (TECH-DETERMINISM: no reliance on unstable ordering). */
  def children: Seq[BaseType]

  /** Reconstruct this node with new children of identical arity and order.
    * Implementations must not reorder or drop children. */
  def withNewChildren(newChildren: Seq[BaseType]): BaseType

  /** Apply `f` to each child, rebuilding only if some child actually changed
    * (reference inequality). Returns `this` unchanged otherwise so transforms that
    * touch nothing allocate nothing — and identity is preserved for fixpoint checks. */
  def mapChildren(f: BaseType => BaseType): BaseType = {
    if (children.isEmpty) return this
    var changed = false
    val newChildren = children.map { c =>
      val nc = f(c)
      if (!(nc eq c)) changed = true
      nc
    }
    if (changed) withNewChildren(newChildren) else this
  }

  /** Pre-order rewrite: apply `rule` to this node, then recurse into the result's
    * children. Use for rewrites that should see the un-rewritten subtree first. */
  def transformDown(rule: PartialFunction[BaseType, BaseType]): BaseType = {
    val afterSelf = rule.applyOrElse(this, identity[BaseType])
    afterSelf.mapChildren(_.transformDown(rule))
  }

  /** Post-order rewrite: recurse into children first, then apply `rule` to the
    * rebuilt node. Use for rewrites that depend on already-rewritten children
    * (e.g. constant folding, desugaring that composes bottom-up). */
  def transformUp(rule: PartialFunction[BaseType, BaseType]): BaseType = {
    val afterChildren = mapChildren(_.transformUp(rule))
    rule.applyOrElse(afterChildren, identity[BaseType])
  }

  /** Default transform is top-down, matching Catalyst's `transform`. */
  def transform(rule: PartialFunction[BaseType, BaseType]): BaseType = transformDown(rule)

  /** Pre-order read-only walk (verifiers, DAG scans). No mutation, no rebuild. */
  def foreach(f: BaseType => Unit): Unit = {
    f(this)
    children.foreach(_.foreach(f))
  }

  /** Collect over a pre-order walk. */
  def collect[A](pf: PartialFunction[BaseType, A]): Seq[A] = {
    val buf = mutable.ArrayBuffer.empty[A]
    foreach(n => pf.lift(n).foreach(buf += _))
    buf.toSeq
  }

  /** True if any node in the subtree (inclusive) satisfies `p`. */
  def exists(p: BaseType => Boolean): Boolean = {
    var found = false
    foreach(n => if (p(n)) found = true)
    found
  }
}
