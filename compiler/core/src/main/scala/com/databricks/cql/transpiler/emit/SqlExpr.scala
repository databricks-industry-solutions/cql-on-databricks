package com.databricks.cql.transpiler.emit

/** The SCALAR expression node of the SQL AST (EMIT-SQL-EXECUTE). The transpiler lowers a CQL/IR scalar
  * expression to a `SqlExpr` tree, and [[SqlRender]] renders it to Spark SQL TEXT — never to a Spark
  * `Column` and never to a Connect proto. It is the SQL-target analogue of the compiler's
  * `ColumnLowering` result (`org.apache.spark.sql.Column`), with the same shape (function calls, CASE,
  * struct/field, HOF lambdas, casts) so the semantic ALGORITHMS carry over unchanged — only the target
  * type differs.
  *
  * Kept deliberately small and total; grows as the node families are re-targeted (Phase 2). Every
  * construction goes through the smart constructors in the companion so callers do not hand-build raw
  * SQL fragments (which is where quoting/precedence bugs hide). */
sealed trait SqlExpr

object SqlExpr {

  /** A column / identifier reference (`__subject_id`, `E`, a define name). Rendered with identifier
    * quoting by [[SqlRender]] (backticks only when needed), so callers pass the bare name. */
  final case class Col(name: String) extends SqlExpr

  /** A field / struct navigation: `base`.`name` (FHIR `__resource.status`, a tuple element). */
  final case class Field(base: SqlExpr, name: String) extends SqlExpr

  /** A pre-formed SQL literal TOKEN (`5`, `true`, `NULL`, `'final'`, `DATE '2020-01-01'`). The token is
    * assembled by the smart constructors below so quoting/escaping is centralized; `raw` is never a
    * multi-token fragment. */
  final case class Lit(token: String) extends SqlExpr

  /** A function call `fn(arg, …)` — covers native scalar functions AND higher-order functions when an
    * argument is a [[Lambda]] (`transform(xs, x -> …)`), so no UDF is ever needed. */
  final case class Call(fn: String, args: Seq[SqlExpr]) extends SqlExpr

  /** A binary infix operator, always parenthesized so precedence is explicit and never wrong:
    * `(l op r)`. `op` is a SQL operator token (`+`, `=`, `AND`, `IS NOT DISTINCT FROM`, …). */
  final case class Infix(op: String, l: SqlExpr, r: SqlExpr) extends SqlExpr

  /** A prefix unary operator: `op e` (`NOT e`, `- e`). */
  final case class Prefix(op: String, e: SqlExpr) extends SqlExpr

  /** A postfix predicate: `e op` (`e IS NULL`, `e IS NOT NULL`). */
  final case class Postfix(e: SqlExpr, op: String) extends SqlExpr

  /** `CASE WHEN c1 THEN v1 … [ELSE e] END`. The canonical conditional target
    * (ARCH-CONDITIONAL's columnar specialization); list-valued conditionals lower relationally in
    * [[SqlRel]], not here. */
  final case class Case(whens: Seq[(SqlExpr, SqlExpr)], orElse: Option[SqlExpr]) extends SqlExpr

  /** `CAST(e AS ty)` — `ty` is a SQL type token (`DOUBLE`, `TIMESTAMP_NTZ`, `DECIMAL(38,8)`). */
  final case class Cast(e: SqlExpr, ty: String) extends SqlExpr

  /** A lambda argument to a higher-order function: `(p1, p2) -> body`. Only valid inside a [[Call]]. */
  final case class Lambda(params: Seq[String], body: SqlExpr) extends SqlExpr

  /** `named_struct('f1', v1, 'f2', v2, …)` — the struct constructor (Quantity, Choice discriminated
    * struct, tuple, the `__resource` handle). Field order is preserved. */
  final case class Struct(fields: Seq[(String, SqlExpr)]) extends SqlExpr

  /** A windowed expression: `fn OVER (PARTITION BY … ORDER BY …)` (grain-first ordinal/rank). */
  final case class Over(fn: SqlExpr, partitionBy: Seq[SqlExpr], orderBy: Seq[SqlRel.SortKey]) extends SqlExpr

  // --- smart constructors: the ONLY sanctioned way to build literals (centralized quoting/escaping) ---

  val Null: SqlExpr = Lit("NULL")
  def bool(b: Boolean): SqlExpr = Lit(if (b) "true" else "false")
  def int(n: Long): SqlExpr = Lit(n.toString)
  /** A decimal literal rendered exactly (no float formatting), cast to DECIMAL so Spark keeps precision. */
  def decimal(d: BigDecimal): SqlExpr = Cast(Lit(d.bigDecimal.toPlainString), "DECIMAL(38,8)")
  /** A single-quoted string literal with Spark SQL escaping. Spark processes backslash escapes inside
    * string literals by default (`\n`, `\t`, `\\`, and an unknown `\x` drops the backslash), so a literal
    * BACKSLASH must be doubled FIRST (else a regex like `Split`'s `\Q/\E` quote, or any data string with a
    * `\`, is silently mangled), then the single quote is doubled. */
  def str(s: String): SqlExpr = Lit("'" + s.replace("\\", "\\\\").replace("'", "''") + "'")

  def col(name: String): SqlExpr = Col(name)
  def call(fn: String, args: SqlExpr*): SqlExpr = Call(fn, args)
}
