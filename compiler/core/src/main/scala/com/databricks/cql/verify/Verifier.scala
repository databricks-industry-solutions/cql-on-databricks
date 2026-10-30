package com.databricks.cql.verify

import com.databricks.cql.error.{ReferenceCycle, TypeError, VerifierError}
import com.databricks.cql.ir._
import com.databricks.cql.passes.TypeEncoding

/** A verifier pass: a cheap assertion of a post-condition at a pass boundary, run in
  * test/debug builds (CONF-VERIFIER, ARCH-ROBUSTNESS pillar 4; cf. LLVM's verifier and
  * Catalyst's `CheckAnalysis`).
  *
  * The point is locality: a verifier failure points at the pass boundary that produced
  * it, so bugs are caught where they originate, not three passes downstream as a wrong
  * rate. Verifiers throw `VerifierError` (a CompileError) on failure.
  */
trait Verifier {
  /** The pass this verifier guards (used in the failure message). */
  def pass: String
  def verify(node: IrNode): Unit
}

object Verifier {

  /** Run a sequence of verifiers; the first failure throws. */
  def runAll(node: IrNode, verifiers: Seq[Verifier]): Unit =
    verifiers.foreach(_.verify(node))

  /** Post-Normalize (ARCH-PIPELINE / CONF-VERIFIER): only kernel nodes remain — no
    * unmodeled ELM lingering as `UnknownExpr`/`UnknownStatement`. This is the loud gate
    * that prevents an unsupported construct reaching Lower (CORE-NO-SILENT-MISLOWER). */
  object OnlyKernelNodes extends Verifier {
    val pass = "Normalize"
    def verify(node: IrNode): Unit = {
      val offenders = node.collect {
        case u: UnknownExpr      => s"UnknownExpr(${u.elmType})"
        case u: UnknownStatement => s"UnknownStatement(${u.elmType}${u.name.fold("")(n => s" '$n'")})"
      }
      if (offenders.nonEmpty)
        throw VerifierError(pass,
          s"non-kernel nodes still present after Normalize: ${offenders.distinct.mkString(", ")}")
    }
  }

  /** Post-Type/Encoding (ARCH-TYPE-ATTR / CONF-VERIFIER / ARCH-ROBUSTNESS pillar 4): every
    * expression in the kernel types successfully. Types are recomputable (see
    * [[com.databricks.cql.passes.TypeEncoding]]), so this asserts the invariant on the
    * kernel that Lower consumes; it moves ahead of Normalize once type-directed desugaring
    * (Phase 4) begins consuming types. A genuinely untypeable node surfaces as a `TypeError`,
    * re-raised here as a `VerifierError` pointing at the offending define. Runs after
    * [[OnlyKernelNodes]], so unmodeled constructs trip that gate first. */
  object WellTyped extends Verifier {
    val pass = "Type/Encoding"
    def verify(node: IrNode): Unit = node match {
      case lib: Library =>
        val ctx = TypeEncoding.ctxOf(lib)
        lib.statements.foreach {
          case e: ExpressionDef =>
            try { TypeEncoding.typeOf(e.expression, ctx); () }
            catch { case t: TypeError => throw VerifierError(pass, s"define '${e.name}': ${t.getMessage}") }
          case f: FunctionDef =>
            try { TypeEncoding.typeOf(f.body, ctx); () }
            catch { case t: TypeError => throw VerifierError(pass, s"function '${f.name}': ${t.getMessage}") }
          case _: UnknownStatement => () // OnlyKernelNodes is the gate for these
        }
      case _ => () // verifier runs on the library root
    }
  }

  /** Global (CONF-VERIFIER): every `ExpressionDef`/`FunctionDef` declares a context, and
    * functions referenced are not obviously dangling. Phase 0 checks the structural
    * post-condition that defines are named and statements are well-formed. */
  object WellFormedLibrary extends Verifier {
    val pass = "Load"
    def verify(node: IrNode): Unit = node match {
      case lib: Library =>
        val unnamed = lib.statements.collect {
          case e: ExpressionDef if e.name.trim.isEmpty => "ExpressionDef"
          case f: FunctionDef if f.name.trim.isEmpty   => "FunctionDef"
        }
        if (unnamed.nonEmpty)
          throw VerifierError(pass, s"unnamed statements: ${unnamed.mkString(", ")}")
      case _ => () // verifier runs on the library root
    }
  }
}

/** DAG verification (CONF-VERIFIER "DAG: no reference cycles"). Builds the inter-define
  * dependency graph and rejects cycles — CQL forbids circular define/function references
  * (LANG-FUNCTION, ARCH-PIPELINE). Properly a Resolve-pass responsibility; exposed as a
  * verifier so the checkpoint is reusable.
  */
object DefineGraph {

  /** Map each statement name to the define/function names it references. */
  def dependencies(lib: Library): Map[String, Set[String]] = {
    val definedNames: Set[String] = lib.statements.collect {
      case e: ExpressionDef => e.name
      case f: FunctionDef   => f.name
    }.toSet

    lib.statements.collect {
      case e: ExpressionDef =>
        e.name -> refsIn(e.expression, definedNames)
      case f: FunctionDef =>
        f.name -> refsIn(f.body, definedNames)
    }.toMap
  }

  private def refsIn(expr: Expr, defined: Set[String]): Set[String] =
    expr.collect {
      case r: ExpressionRef if r.libraryName.isEmpty && defined.contains(r.name) => r.name
      case r: FunctionRef   if r.libraryName.isEmpty && defined.contains(r.name) => r.name
    }.toSet

  /** Throw `ReferenceCycle` if the dependency graph has a cycle. DFS with a recursion
    * stack; deterministic iteration order (TECH-DETERMINISM: names sorted, never hashmap
    * order). */
  def assertAcyclic(lib: Library): Unit = {
    val deps = dependencies(lib)
    val visited  = scala.collection.mutable.Set.empty[String]
    val onStack  = scala.collection.mutable.LinkedHashSet.empty[String]

    def dfs(name: String): Unit = {
      if (onStack.contains(name)) {
        val cycle = onStack.dropWhile(_ != name).toSeq :+ name
        throw ReferenceCycle(cycle)
      }
      if (!visited.contains(name)) {
        onStack += name
        deps.getOrElse(name, Set.empty).toSeq.sorted.foreach(dfs)
        onStack -= name
        visited += name
      }
    }

    deps.keys.toSeq.sorted.foreach(dfs)
  }
}
