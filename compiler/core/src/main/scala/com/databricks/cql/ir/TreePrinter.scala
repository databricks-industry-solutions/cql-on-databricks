package com.databricks.cql.ir

/** Renders an IR tree to a stable, indented, diff-friendly string.
  *
  * Two uses: (1) the Phase 0 golden-plan snapshot (CONF-GOLDEN-PLAN) — until Spark
  * lowering exists, the normalized IR shape IS the snapshot, so lowering/normalization
  * changes surface as a code-review diff; (2) plan-diff traceability when a rule fires
  * (ARCH-IR). Output is deterministic (children rendered in source order, no hashmap
  * iteration — TECH-DETERMINISM).
  */
object TreePrinter {

  def treeString(node: IrNode): String = {
    val sb = new StringBuilder
    render(node, 0, sb)
    sb.toString
  }

  private def render(n: IrNode, depth: Int, sb: StringBuilder): Unit = {
    sb.append("  " * depth).append(header(n)).append('\n')
    n.children.foreach(render(_, depth + 1, sb))
  }

  /** Strip the `{uri}` namespace prefix from an ELM type name for readability. */
  private def shortType(t: String): String = {
    val i = t.lastIndexOf('}')
    if (i >= 0 && i + 1 < t.length) t.substring(i + 1) else t
  }

  /** A node's one-line label: its kind plus the informative scalar fields (NOT its
    * children, which are rendered as the subtree). */
  private def header(n: IrNode): String = n match {
    case l: Library         => s"Library(${l.id}${l.version.map(v => s" v$v").getOrElse("")})"
    case e: ExpressionDef   => s"ExpressionDef(${e.name}${e.context.map(c => s", ctx=$c").getOrElse("")})"
    case f: FunctionDef     => s"FunctionDef(${f.name}(${f.operands.map(_.name).mkString(", ")}))"
    case p: ParameterDef    => s"ParameterDef(${p.name})"
    case u: UsingDef        => s"UsingDef(${u.localIdentifier.getOrElse(u.uri)})"
    case i: IncludeDef      => s"IncludeDef(${i.localIdentifier} -> ${i.path})"
    case v: ValueSetDef     => s"ValueSetDef(${v.name})"
    case c: ContextDef      => s"ContextDef(${c.name})"
    case u: UnknownStatement => s"UnknownStatement(${u.elmType}${u.name.map(n2 => s" '$n2'").getOrElse("")})"
    case d: Definition      => d.getClass.getSimpleName

    case Literal(vt, value) => s"Literal(${value.getOrElse("<null>")}: ${shortType(vt)})"
    case QuantityExpr(v, u) => s"Quantity($v${u.map(x => s" '$x'").getOrElse("")})"
    case ExpressionRef(name, _) => s"ExpressionRef($name)"
    case ParameterRef(name, _)  => s"ParameterRef($name)"
    case OperandRef(name)       => s"OperandRef($name)"
    case ValueSetRef(name, _)   => s"ValueSetRef($name)"
    case As(_, asType, strict, cts) =>
      val tgt = if (cts.nonEmpty) cts.map(shortType).mkString("Choice<", ",", ">") else shortType(asType)
      s"As($tgt${if (strict) ", strict" else ""})"
    case Is(_, isType)          => s"Is(${shortType(isType)})"
    case Property(_, scope, path) => s"Property(${scope.map(_ + ".").getOrElse("")}$path)"
    case r: Retrieve            => s"Retrieve(${shortType(r.dataType)})"
    case u: UnknownExpr         => s"UnknownExpr(${u.elmType})"
    case i: IntervalExpr        => s"Interval(${if (i.lowClosed) "[" else "("}..${if (i.highClosed) "]" else ")"})"
    case fr: FunctionRef        => s"FunctionRef(${fr.name})"
    case e: Expr                => e.elmType
  }
}
