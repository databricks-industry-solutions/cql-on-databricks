package com.databricks.cql.passes

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._

/** Normalize step: eliminate user-defined functions by INLINING (LANG-FUNCTION). CQL forbids
  * recursion ("a function reference must not result in a circular reference", enforced as a
  * cycle check in Resolve), so every `FunctionRef` rewrites to the resolved body with its
  * `OperandRef`s bound to the (already-inlined) argument expressions — never a runtime call
  * mechanism. Nested and argument-position calls inline by structural recursion.
  *
  * OVERLOADS (LANG-FUNCTION): CQL permits several functions to share a name, differing by operand
  * count/types (e.g. `ToDaily(Integer, Quantity)` and `ToDaily(Code)`). The ELM `FunctionRef`
  * does not name which overload, so the table keys by name → ALL same-named defs, and each call site
  * selects by ARITY (operand count). Arity disambiguates the corpus overloads; a genuinely ambiguous
  * same-arity/type overload set is rejected loudly rather than silently picking one. */
object FunctionInliner {

  /** Inline every `FunctionRef` in `expr`. `functions` maps a name to its overload set (all
    * same-named `FunctionDef`s); `bindings` maps an enclosing function's operand names to their
    * bound argument expressions. */
  def inline(expr: Expr, functions: Map[String, Seq[FunctionDef]],
             bindings: Map[String, Expr] = Map.empty): Expr = expr match {
    case OperandRef(name) =>
      bindings.getOrElse(name,
        throw UnsupportedNode("OperandRef", s"unbound operand '$name' (not inside an inlined function)"))

    // Cross-library calls (e.g. FHIRHelpers.ToString) are NOT inlined — they are lowered
    // directly (FHIR primitive conversions). Recurse into their args, preserve the call.
    case fr @ FunctionRef(_, Some(_), _) =>
      fr.withNewChildren(fr.children.map {
        case e: Expr => inline(e, functions, bindings)
        case c       => c
      }).asInstanceOf[Expr]

    case FunctionRef(name, _, args) =>
      val overloads = functions.getOrElse(name,
        throw UnsupportedNode("FunctionRef", s"unresolved function '$name' (external or cross-library not yet supported)"))
      // Resolve the overload by arity (the ELM FunctionRef does not carry the resolved target).
      val fn = overloads.filter(_.operands.size == args.size) match {
        case Seq(one) => one
        case Seq()    => throw UnsupportedNode("FunctionRef",
          s"arity mismatch calling '$name': no ${args.size}-operand overload (have ${overloads.map(_.operands.size).sorted.mkString("/")})")
        case many     => throw UnsupportedNode("FunctionRef",
          s"ambiguous overload calling '$name': ${many.size} defs with ${args.size} operands (type-based overload resolution not yet supported)")
      }
      if (fn.external)
        throw UnsupportedNode("FunctionRef", s"external function '$name' has no inlinable body")
      // Arguments are in the CALLER's scope: inline them under the current bindings first,
      // then bind by operand name and inline the body under those fresh bindings.
      val inlinedArgs = args.map(a => inline(a, functions, bindings))
      val newBindings = fn.operands.map(_.name).zip(inlinedArgs).toMap
      inline(fn.body, functions, newBindings)

    case other =>
      // Structural recursion: inline every Expr child, preserving node shape.
      other.withNewChildren(other.children.map {
        case e: Expr => inline(e, functions, bindings)
        case c       => c
      }).asInstanceOf[Expr]
  }

  /** Inline functions across a whole library and DROP the now-eliminated `FunctionDef`s
    * (they are not kernel nodes; the Normalize verifier `OnlyKernelNodes` would reject a
    * residual `FunctionRef`). `ExpressionDef`s keep their order. */
  def normalize(lib: Library): Library = {
    val functions = lib.statements.collect { case f: FunctionDef => f }.groupBy(_.name)
    if (functions.isEmpty) lib
    else {
      val rewritten = lib.statements.flatMap {
        case e: ExpressionDef => Some(e.copy(expression = inline(e.expression, functions)))
        case _: FunctionDef   => None // eliminated by inlining
        case other            => Some(other)
      }
      lib.copy(statements = rewritten)
    }
  }
}
