package com.databricks.cql.transpiler.emit

/** A compiled measure/library as an ORDERED Spark SQL script (EMIT-SQL-EXECUTE) — the whole artifact
  * the transpiler produces and the client executes via `spark.sql()`. This is the auditable,
  * measurable replacement for the opaque ~685 MB Connect proto: one statement per define, in dependency
  * order, readable and runnable standalone in any SQL surface.
  *
  * Statement KIND is chosen by the SAME pure `MaterializationPolicy` the proto path uses (unchanged
  * cost model) — only the realization differs (view vs cached/materialized table). */
final case class SqlScript(statements: Seq[SqlStmt], target: String)

/** One statement of a [[SqlScript]]. */
sealed trait SqlStmt {
  /** The object name this statement defines (for ordering / dependency wiring / teardown). */
  def name: String
}

object SqlStmt {

  /** `CREATE OR REPLACE TEMP VIEW <name> AS <rel>` — a NON-materialized define, a retrieve mount
    * (DATA-RETRIEVE-NO-BARRIER), or any logical CTE-def. The default statement kind. */
  final case class CreateView(name: String, rel: SqlRel) extends SqlStmt

  /** `CACHE TABLE <name> AS <rel>` — the materialization barrier (the SQL-native replacement for the
    * proto path's `localCheckpoint`: it cuts lineage and caches, session-scoped). The ONLY barrier
    * realization, identical for local and deploy — a session cache runs on any host, including a catalog
    * that cannot REPLACE-TABLE-AS-SELECT. */
  final case class CacheTable(name: String, rel: SqlRel) extends SqlStmt

  /** `CREATE OR REPLACE TEMPORARY FUNCTION <name>(<p> <ty>, …) RETURNS <ret> RETURN <body>` — a SQL-body
    * SCALAR function (EMIT-SQL-TEMPORAL-UDF). Emitted at the HEAD of a script (before the views that call
    * it) to give the partial-precision date towers a readable name. It is a SQL expression function, so
    * Catalyst INLINES the `body` into each caller's plan at analysis time — the analyzed plan (and thus
    * results / constant-folding) is unchanged; only the emitted SQL text is smaller. `params` are the
    * `(name, sqlType)` signature; `body` references each parameter by [[SqlExpr.Col]] of its name. */
  final case class CreateFunction(name: String, params: Seq[(String, String)], returns: String,
                                  body: SqlExpr) extends SqlStmt
}
