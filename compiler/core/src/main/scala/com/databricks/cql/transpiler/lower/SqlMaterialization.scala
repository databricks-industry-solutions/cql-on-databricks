package com.databricks.cql.transpiler.lower

import com.databricks.cql.transpiler.emit.{SqlRel, SqlStmt}

/** Maps a materialize decision to a SQL STATEMENT KIND (EMIT-SQL-EXECUTE): a non-materialized define is
  * a logical `CREATE OR REPLACE TEMP VIEW` (Catalyst re-derives / shares it); a materialized one is a
  * `CACHE TABLE` barrier — the SQL-native replacement for the proto path's `localCheckpoint` (it cuts
  * lineage and caches, session-scoped). ONE barrier realization, identical for local and deploy: a
  * session cache runs on ANY host (including the OSS Spark Connect session catalog, which does not
  * support REPLACE-TABLE-AS-SELECT). (An earlier variant defaulted to a `CREATE OR REPLACE TABLE`
  * managed barrier — EMIT-SQL-BARRIER-TABLE, now RETIRED/reverted for exactly that host-parity reason.)
  *
  * The DECISION itself (materialize or not) is a simple SEMANTIC classification of the define, made in
  * [[SqlFrameLowering]] — NOT the proto path's structural cost × reuse model. Each decision carries a
  * single [[Decision.reason]] term (`population` | `valueset` | `date` | `filter` | `pushdown` |
  * `passthru` | `scalar` | `base`) so the diagnostics report reads at a glance. */
object SqlMaterialization {

  /** One materialization decision for the diagnostics report: the object, its statement KIND
    * (`view` | `cache_table`), and the single-term REASON. */
  final case class Decision(name: String, kind: String, reason: String)

  /** Build the statement + record its kind: a logical view when `materialize` is false, else a
    * `CACHE TABLE` barrier. */
  def statement(name: String, rel: SqlRel, materialize: Boolean): (SqlStmt, String) =
    if (!materialize) (SqlStmt.CreateView(name, rel), "view")
    else (SqlStmt.CacheTable(name, rel), "cache_table")
}
