package com.databricks.cql.transpiler.emit

/** Renders the SQL AST ([[SqlExpr]]/[[SqlRel]]/[[SqlStmt]]/[[SqlScript]]) to Spark SQL TEXT
  * (EMIT-SQL-EXECUTE). This is the ONLY place SQL strings are assembled, so identifier quoting, operator
  * precedence (every [[SqlExpr.Infix]] is parenthesized), and literal escaping are centralized — the
  * classes of bug that make hand-built SQL fragments unsafe.
  *
  * Output is READABLE by design (one projection per line, clause keywords at the margin), because
  * auditability is the point: the emitted script is the artifact a reviewer reads and can paste into any
  * SQL surface. It is pure — no Spark, no proto, no execution. */
object SqlRender {

  /** EMIT-UDF-QUALIFY: the active UDF schema qualifier, scoped by [[withUdfSchema]]. When set, every
    * `cql_*` function call is rendered as `schema.cql_fn(…)` instead of bare `cql_fn(…)`. */
  private val _udfSchema = new scala.util.DynamicVariable[Option[String]](None)

  /** Execute `body` with the given UDF schema qualifier active for all rendering within. */
  def withUdfSchema[A](schema: Option[String])(body: => A): A =
    if (schema.exists(_.nonEmpty)) _udfSchema.withValue(schema)(body) else body

  private val Indent = "  "

  // ---------------------------------------------------------------- script / statements

  /** Render a whole script: each statement terminated by `;`, blank line between, in the given order. */
  def render(script: SqlScript): String =
    script.statements.map(render).mkString("", ";\n\n", ";\n")

  def render(stmt: SqlStmt): String = stmt match {
    case SqlStmt.CreateView(name, rel) =>
      s"CREATE OR REPLACE TEMP VIEW ${quoteIdent(name)} AS\n" + renderRel(rel, 0)
    case SqlStmt.CacheTable(name, rel) =>
      s"CACHE TABLE ${quoteIdent(name)} AS\n" + renderRel(rel, 0)
    case SqlStmt.CreateFunction(name, params, returns, body) =>
      val ps = params.map { case (p, ty) => s"${quoteIdent(p)} $ty" }.mkString(", ")
      s"CREATE OR REPLACE TEMPORARY FUNCTION ${quoteIdent(name)}($ps)\nRETURNS $returns\nRETURN ${expr(body)}"
  }

  // ---------------------------------------------------------------- relations

  /** Render a relation as a statement body at the given base indent. Handles the `WITH` prefix. */
  def renderRel(rel: SqlRel, indent: Int): String = rel match {
    case SqlRel.With(ctes, body) =>
      val pad = Indent * indent
      val defs = ctes.map { case (n, r) =>
        s"$pad${Indent}${quoteIdent(n)} AS (\n${renderRel(r, indent + 2)}\n$pad$Indent)"
      }.mkString(",\n")
      s"${pad}WITH\n$defs\n${renderRel(body, indent)}"
    case s: SqlRel.Select   => renderSelect(s, indent)
    case SqlRel.UnionAll(parts) =>
      val pad = Indent * indent
      parts.map(renderRel(_, indent)).mkString("", s"\n${pad}UNION ALL\n", "")
    case SqlRel.TableRef(n) => Indent * indent + s"SELECT * FROM ${qualifiedIdent(n)}"
    case SqlRel.OneRow      => Indent * indent + "SELECT 1"
    case SqlRel.AliasRel(r, _) => renderRel(r, indent)
    case lv: SqlRel.LateralView => Indent * indent + "SELECT * FROM " + fromText(lv, indent)
    case j: SqlRel.Join     => Indent * indent + "SELECT * FROM " + fromText(j, indent)
    case tf: SqlRel.TableFunc => Indent * indent + "SELECT * FROM " + fromText(tf, indent)
  }

  private def renderSelect(s: SqlRel.Select, indent: Int): String = {
    val pad = Indent * indent
    val sb = new StringBuilder
    sb.append(pad).append("SELECT").append(if (s.distinct) " DISTINCT" else "").append('\n')
    val projs = if (s.projections.isEmpty) Seq("*") else s.projections.map(projText)
    sb.append(projs.map(p => s"$pad$Indent$p").mkString(",\n")).append('\n')
    sb.append(pad).append("FROM ").append(fromText(s.from, indent))
    s.where.foreach(w => sb.append('\n').append(pad).append("WHERE ").append(expr(w)))
    if (s.groupBy.nonEmpty) sb.append('\n').append(pad).append("GROUP BY ").append(s.groupBy.map(expr).mkString(", "))
    s.having.foreach(h => sb.append('\n').append(pad).append("HAVING ").append(expr(h)))
    if (s.orderBy.nonEmpty) sb.append('\n').append(pad).append("ORDER BY ").append(s.orderBy.map(sortKey).mkString(", "))
    s.limit.foreach(n => sb.append('\n').append(pad).append("LIMIT ").append(n))
    sb.toString
  }

  private def projText(p: SqlRel.Proj): String =
    p.alias match { case Some(a) => s"${expr(p.expr)} AS ${quoteIdent(a)}"; case None => expr(p.expr) }

  /** Render whatever sits after `FROM` (a table, a subquery, a join, a lateral view). Subqueries are
    * parenthesized and indented; an [[SqlRel.AliasRel]] supplies the required derived-table alias. */
  private def fromText(rel: SqlRel, indent: Int): String = rel match {
    case SqlRel.TableRef(n)      => qualifiedIdent(n)
    case SqlRel.TableFunc(fn, args, catalog) => s"${qualifiedName(catalog, fn)}(${args.map(expr).mkString(", ")})"
    case SqlRel.AliasRel(r, a)   => s"${fromAtom(r, indent)} AS ${quoteIdent(a)}"
    case SqlRel.Join(l, r, kind, on, using) =>
      val onTxt = on.map(c => s" ON ${expr(c)}")
        .orElse(if (using.nonEmpty) Some(using.map(quoteIdent).mkString(" USING (", ", ", ")")) else None)
        .getOrElse("")
      s"${fromText(l, indent)} ${joinKeyword(kind)} ${fromText(r, indent)}$onTxt"
    case SqlRel.LateralView(input, gen, tableAlias, cols, outer) =>
      val o = if (outer) "OUTER " else ""
      val colList = if (cols.isEmpty) "" else cols.map(quoteIdent).mkString(" AS ", ", ", "")
      s"${fromText(input, indent)} LATERAL VIEW $o${expr(gen)} ${quoteIdent(tableAlias)}$colList"
    case other => fromAtom(other, indent) // bare subquery — caller should have wrapped in AliasRel
  }

  /** A relation used as a FROM atom: parenthesized, indented subquery. */
  private def fromAtom(rel: SqlRel, indent: Int): String = rel match {
    case SqlRel.TableRef(n) => qualifiedIdent(n)
    case tf: SqlRel.TableFunc => fromText(tf, indent)
    case _ =>
      val pad = Indent * indent
      s"(\n${renderRel(rel, indent + 1)}\n$pad)"
  }

  private def joinKeyword(k: SqlRel.JoinKind): String = k match {
    case SqlRel.JoinKind.Inner     => "JOIN"
    case SqlRel.JoinKind.LeftOuter => "LEFT OUTER JOIN"
    case SqlRel.JoinKind.FullOuter => "FULL OUTER JOIN"
    case SqlRel.JoinKind.LeftSemi  => "LEFT SEMI JOIN"
    case SqlRel.JoinKind.LeftAnti  => "LEFT ANTI JOIN"
    case SqlRel.JoinKind.Cross     => "CROSS JOIN"
  }

  private def sortKey(k: SqlRel.SortKey): String = {
    val dir = if (k.ascending) " ASC" else " DESC"
    val nulls = k.nullsFirst.map(nf => if (nf) " NULLS FIRST" else " NULLS LAST").getOrElse("")
    s"${expr(k.expr)}$dir$nulls"
  }

  // ---------------------------------------------------------------- expressions

  def expr(e: SqlExpr): String = e match {
    case SqlExpr.Col("*")          => "*"
    case SqlExpr.Col(name)         => quoteIdent(name)
    case SqlExpr.Field(base, name) => s"${expr(base)}.${quoteIdent(name)}"
    case SqlExpr.Lit(token)        => token
    case SqlExpr.Call(fn, args)    =>
      val qfn = SqlNames.qualifyUdf(_udfSchema.value, fn)
      s"$qfn(${args.map(expr).mkString(", ")})"
    case SqlExpr.Infix(op, l, r)   => s"(${expr(l)} $op ${expr(r)})"
    case SqlExpr.Prefix(op, x)     => s"$op ${expr(x)}"
    case SqlExpr.Postfix(x, op)    => s"${expr(x)} $op"
    case SqlExpr.Case(whens, orElse) =>
      val ws = whens.map { case (c, v) => s"WHEN ${expr(c)} THEN ${expr(v)}" }.mkString(" ")
      val el = orElse.map(e0 => s" ELSE ${expr(e0)}").getOrElse("")
      s"CASE $ws$el END"
    case SqlExpr.Cast(x, ty)       => s"CAST(${expr(x)} AS $ty)"
    case SqlExpr.Lambda(ps, body)  =>
      val hd = if (ps.size == 1) ps.head else ps.mkString("(", ", ", ")")
      s"$hd -> ${expr(body)}"
    case SqlExpr.Struct(fields) =>
      val args = fields.flatMap { case (n, v) => Seq(s"'${n.replace("'", "''")}'", expr(v)) }
      s"named_struct(${args.mkString(", ")})"
    case SqlExpr.Over(fn, part, ord) =>
      val p = if (part.isEmpty) "" else part.map(expr).mkString("PARTITION BY ", ", ", "")
      val o = if (ord.isEmpty) "" else ord.map(sortKey).mkString("ORDER BY ", ", ", "")
      s"${expr(fn)} OVER (${Seq(p, o).filter(_.nonEmpty).mkString(" ")})"
  }

  // ---------------------------------------------------------------- size estimation (the blowup gate)

  /** A CHEAP structural byte-size estimate of a statement's rendered SQL, walking the AST and summing
    * token lengths WITHOUT materializing the (potentially huge) string — so the emit-size gate
    * ([[SqlFrameLowering]]) can reject a define whose within-body inlining exploded (the value[x] /
    * temporal-tower duplication the proto path also hit) BEFORE a hundreds-of-MB script is built or
    * shipped to Spark. Approximate (ignores indentation/keywords), but monotone in the real size, which
    * is all the gate needs. */
  def sizeOf(stmt: SqlStmt): Long = stmt match {
    case SqlStmt.CreateView(n, r)     => n.length + 30L + sizeOf(r)
    case SqlStmt.CacheTable(n, r)     => n.length + 20L + sizeOf(r)
    case SqlStmt.CreateFunction(n, params, returns, body) =>
      n.length + 40L + returns.length + params.map { case (p, ty) => p.length + ty.length + 4L }.sum + sizeOf(body)
  }

  def sizeOf(rel: SqlRel): Long = rel match {
    case SqlRel.With(ctes, body)   => ctes.map { case (n, r) => n.length + 10L + sizeOf(r) }.sum + sizeOf(body)
    case s: SqlRel.Select          =>
      s.projections.map(p => sizeOf(p.expr) + p.alias.map(_.length + 6L).getOrElse(3L)).sum +
        sizeOf(s.from) + s.where.map(sizeOf).getOrElse(0L) + s.groupBy.map(sizeOf).sum +
        s.having.map(sizeOf).getOrElse(0L) + s.orderBy.map(k => sizeOf(k.expr) + 8L).sum + 12L
    case SqlRel.UnionAll(parts)    => parts.map(sizeOf).sum + parts.size * 12L
    case SqlRel.TableRef(n)        => n.length + 16L
    case SqlRel.TableFunc(fn, args, catalog) => fn.length + catalog.map(_.length + 1L).getOrElse(0L) + args.map(a => sizeOf(a) + 2L).sum + 20L
    case SqlRel.OneRow             => 10L
    case SqlRel.AliasRel(r, a)     => sizeOf(r) + a.length + 6L
    case SqlRel.LateralView(in, g, ta, cols, _) => sizeOf(in) + sizeOf(g) + ta.length + cols.map(_.length + 4L).sum + 20L
    case SqlRel.Join(l, r, _, on, using) => sizeOf(l) + sizeOf(r) + on.map(sizeOf).getOrElse(0L) + using.map(_.length + 4L).sum + 18L
  }

  def sizeOf(e: SqlExpr): Long = e match {
    case SqlExpr.Col(n)            => n.length + 2L
    case SqlExpr.Field(b, n)       => sizeOf(b) + n.length + 3L
    case SqlExpr.Lit(t)            => t.length.toLong
    case SqlExpr.Call(fn, args)    => fn.length + 2L + args.map(a => sizeOf(a) + 2L).sum
    case SqlExpr.Infix(op, l, r)   => sizeOf(l) + sizeOf(r) + op.length + 4L
    case SqlExpr.Prefix(op, x)     => sizeOf(x) + op.length + 2L
    case SqlExpr.Postfix(x, op)    => sizeOf(x) + op.length + 2L
    case SqlExpr.Case(ws, orElse)  => ws.map { case (c, v) => sizeOf(c) + sizeOf(v) + 12L }.sum + orElse.map(sizeOf).getOrElse(0L) + 10L
    case SqlExpr.Cast(x, ty)       => sizeOf(x) + ty.length + 10L
    case SqlExpr.Lambda(ps, body)  => ps.map(_.length + 2L).sum + sizeOf(body) + 6L
    case SqlExpr.Struct(fields)    => fields.map { case (n, v) => n.length + sizeOf(v) + 16L }.sum + 14L
    case SqlExpr.Over(fn, p, o)    => sizeOf(fn) + p.map(sizeOf).sum + o.map(k => sizeOf(k.expr) + 8L).sum + 20L
  }

  // ---------------------------------------------------------------- structural dependency scan

  /** PIPE-SQL-DAG-REUSE — every object NAME a statement reads (`FROM <name>` / `JOIN <name>` — the
    * [[SqlRel.TableRef]]s in its relational tree), so the pipeline-plan cut can partition the one-lowering
    * DAG at the materialized boundaries. Structural (like [[sizeOf]]) and total; a [[SqlStmt.CreateFunction]]
    * has no relational body, so it reads nothing. Table references live ONLY in [[SqlRel]] (an [[SqlExpr]]
    * carries no subquery — a membership test lowers to a relational semi-join), so a `SqlRel` walk is
    * exhaustive. */
  def tableRefs(stmt: SqlStmt): Set[String] = stmt match {
    case SqlStmt.CreateView(_, r)  => tableRefs(r)
    case SqlStmt.CacheTable(_, r)  => tableRefs(r)
    case _: SqlStmt.CreateFunction => Set.empty
  }

  def tableRefs(rel: SqlRel): Set[String] = rel match {
    case SqlRel.TableRef(n)                     => Set(n)
    case SqlRel.TableFunc(fn, _, _)             => Set(fn)
    case SqlRel.OneRow                          => Set.empty
    case s: SqlRel.Select                       => tableRefs(s.from)
    case SqlRel.UnionAll(parts)                 => parts.flatMap(tableRefs).toSet
    case SqlRel.AliasRel(r, _)                  => tableRefs(r)
    case SqlRel.LateralView(in, _, _, _, _)     => tableRefs(in)
    case SqlRel.Join(l, r, _, _, _)             => tableRefs(l) ++ tableRefs(r)
    case SqlRel.With(ctes, body)                => ctes.flatMap { case (_, r) => tableRefs(r) }.toSet ++ tableRefs(body)
  }

  // ---------------------------------------------------------------- identifier quoting

  private val SimpleIdent = "^[A-Za-z_][A-Za-z0-9_]*$".r

  /** Backtick-quote an identifier only when it is not a simple word or collides with a reserved keyword,
    * so the emitted SQL stays readable (`__subject_id`, `status`) but arbitrary CQL define names
    * (`Visits During Period`) and keywords are safely quoted. Embedded backticks are doubled. */
  def quoteIdent(name: String): String =
    if (SimpleIdent.pattern.matcher(name).matches() && !Reserved.contains(name.toUpperCase)) name
    else "`" + name.replace("`", "``") + "`"

  /** A possibly-dotted identifier: each segment is independently quoted, so `acme.bindings.valuesets`
    * renders as `acme.bindings.valuesets` (each simple) or `` `my catalog`.bindings.valuesets `` (only the
    * non-simple segments backticked). A bare simple name like `valuesets` passes through unquoted. */
  private def qualifiedIdent(name: String): String =
    name.split('.').map(quoteIdent).mkString(".")

  /** A (possibly catalog-qualified) function name for a [[SqlRel.TableFunc]] call: each dotted segment
    * of the optional `catalog` plus the function name is independently quoted, so a namespaced binding
    * (`acme.fhir.retrieve_observation`) renders safely and a bare convention name (`retrieve_Encounter`)
    * stays unquoted. */
  private def qualifiedName(catalog: Option[String], fn: String): String =
    (catalog.toSeq.flatMap(_.split('.').toSeq) :+ fn).map(quoteIdent).mkString(".")

  private val Reserved: Set[String] = Set(
    "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "BY", "HAVING", "JOIN", "INNER", "OUTER", "LEFT",
    "RIGHT", "FULL", "CROSS", "SEMI", "ANTI", "ON", "USING", "UNION", "ALL", "DISTINCT", "AS", "AND",
    "OR", "NOT", "NULL", "TRUE", "FALSE", "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "IN", "IS",
    "LIKE", "BETWEEN", "EXISTS", "TABLE", "VIEW", "CREATE", "REPLACE", "TEMP", "CACHE", "LIMIT",
    "LATERAL", "OVER", "PARTITION", "WITH", "ASC", "DESC", "NULLS", "FIRST", "LAST", "USING", "INTERVAL"
  )
}
