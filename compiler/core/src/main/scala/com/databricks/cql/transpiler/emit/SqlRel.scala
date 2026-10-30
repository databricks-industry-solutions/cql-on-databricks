package com.databricks.cql.transpiler.emit

/** The RELATIONAL node of the SQL AST (EMIT-SQL-EXECUTE) — the SQL-target analogue of the compiler's
  * `DataFrame` result (`FrameLowering`/`QueryLowering` relation encoding). [[SqlRender]] renders it to a
  * `SELECT … FROM …` statement (or a bare table reference), never to a Spark plan.
  *
  * A define lowers to a [[SqlRel]] and is emitted as one named view/table by [[SqlScript]]; sharing is
  * therefore native (a define is referenced by name via [[TableRef]], a local common subexpression via a
  * [[With]] CTE) — which is exactly the DAG the share-free Connect proto could not express. */
sealed trait SqlRel

object SqlRel {

  /** A bare reference to an already-defined view/table/CTE by name (`FROM <name>`). This is how
    * define-once / reference-many is realized — no subtree inlining. */
  final case class TableRef(name: String) extends SqlRel

  /** A CALL of a table-valued function in FROM position — `FROM [catalog.]fn(arg, …)`
    * (`BIND-RETRIEVE-UDF`). The SQL-back-end seam for a client-supplied SQL UDTF the compiler names but
    * never bodies (`ARCH-PURITY`): the retrieve mount lowers to a `retrieve_<Type>(…)` call whose EIGHT
    * explicit literal arguments (`BIND-EXPLICIT-PARAMS`) let Catalyst inline the SQL UDTF and fold its
    * non-matching source arms at analysis time. `catalog` is an optional dotted qualifier from the
    * binding table; the arguments are ordinary [[SqlExpr]]s (no relational subquery), so it is a FROM
    * atom like [[TableRef]]. */
  final case class TableFunc(fn: String, args: Seq[SqlExpr], catalog: Option[String] = None) extends SqlRel

  /** A single synthetic row with no columns (`SELECT 1`) — the FROM atom for an UNGROUPED-context base
    * relation (Unfiltered/Population), where the whole library is the degenerate one-subject case of the
    * set-at-a-time frame (ARCH-SET-EVAL). A base `SELECT '*' AS __subject_id FROM (SELECT 1)` seeds the
    * one synthetic subject key without reading any data. */
  case object OneRow extends SqlRel

  /** A `SELECT`. Absent clauses are `None`/empty. `distinct` emits `SELECT DISTINCT`. */
  final case class Select(
      projections: Seq[Proj],
      from: SqlRel,
      where: Option[SqlExpr] = None,
      groupBy: Seq[SqlExpr] = Nil,
      having: Option[SqlExpr] = None,
      distinct: Boolean = false,
      orderBy: Seq[SortKey] = Nil,
      limit: Option[Long] = None
  ) extends SqlRel

  /** A join. Carries EITHER an `on` condition OR `using` column names (never both). */
  final case class Join(
      left: SqlRel,
      right: SqlRel,
      kind: JoinKind,
      on: Option[SqlExpr] = None,
      using: Seq[String] = Nil
  ) extends SqlRel

  /** `UNION ALL` of ≥2 branches — the canonical conditional-over-relations realization
    * (ARCH-COND-UNION) and CQL list `union`. Branch projections must already align. */
  final case class UnionAll(parts: Seq[SqlRel]) extends SqlRel

  /** `input LATERAL VIEW [OUTER] gen(...) alias AS cols` — explode / posexplode of an array into rows
    * (grain-first list realization). `outer` keeps a row when the array is empty/null. */
  final case class LateralView(
      input: SqlRel,
      generator: SqlExpr,
      tableAlias: String,
      columnAliases: Seq[String],
      outer: Boolean = false
  ) extends SqlRel

  /** Names a relation so it can be referenced by alias in the enclosing statement (`(…) AS a`). */
  final case class AliasRel(rel: SqlRel, alias: String) extends SqlRel

  /** A `WITH <name> AS (<rel>), … <body>` — local common-subexpression sharing inside one statement
    * (the SQL-native replacement for the proto-tree CSE/hoist machinery). */
  final case class With(ctes: Seq[(String, SqlRel)], body: SqlRel) extends SqlRel

  /** A projected column: `expr [AS alias]`. */
  final case class Proj(expr: SqlExpr, alias: Option[String] = None)

  object Proj {
    def apply(expr: SqlExpr, alias: String): Proj = Proj(expr, Some(alias))
    val star: Proj = Proj(SqlExpr.Col("*"), None)
  }

  /** An `ORDER BY` / window `ORDER BY` key. */
  final case class SortKey(expr: SqlExpr, ascending: Boolean = true, nullsFirst: Option[Boolean] = None)

  /** Join flavors, each mapping to a SQL join keyword in [[SqlRender]]. */
  sealed trait JoinKind
  object JoinKind {
    case object Inner extends JoinKind
    case object LeftOuter extends JoinKind
    case object FullOuter extends JoinKind
    case object LeftSemi extends JoinKind
    case object LeftAnti extends JoinKind
    case object Cross extends JoinKind
  }
}
