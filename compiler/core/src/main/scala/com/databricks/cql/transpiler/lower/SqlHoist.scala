package com.databricks.cql.transpiler.lower

import com.databricks.cql.transpiler.emit.SqlExpr._
import com.databricks.cql.transpiler.emit.{SqlExpr, SqlRel}

import java.util.concurrent.atomic.AtomicInteger
import scala.collection.mutable

/** WITHIN-DEFINE common-subexpression elimination for the SQL target (ARCH-SQL-CSE, EMIT-SQL-EXECUTE).
  *
  * WHY: SQL views share ACROSS defines (a define = a named view), but a repeated scalar subexpression
  * INSIDE one `SELECT` is still textually duplicated — the transpiler emits a [[SqlExpr]] TREE, not a
  * DAG. The dominant duplicate is the precision-aware temporal interval operand
  * `named_struct('low', substr(period.start,1,23), 'high', substr(period.`end`,1,23))`: every Allen
  * relation reads `.low`/`.high` of both operands and the precision-aware compare references each bound
  * several more times, so one `during` re-inlines the whole struct 10–50x. Combined with OR-ed `exists`
  * and value[x] CASE ladders this exploded one measure's three dominant defines to 60–158 MB each (the
  * emit-size gate in [[SqlFrameLowering]]). This is the SAME temporal-tower blowup the proto path hit;
  * the compiler collapses it with `QueryLowering.hoisted` (a projected-column binder), and this is its
  * SQL twin.
  *
  * MECHANISM (mirrors `QueryLowering.hoisted`): the duplication-prone construction points in
  * [[SqlColumnLowering]] are ALREADY marked with `hoist(...)` / `hoistT(...)` calls (defaulted to
  * `noHoist = identity`). [[hoisted]] wires those to a binder that records each hoisted sub-expression as
  * a fresh projected column `__h<N>` over the define's relation and returns a `Col(__h<N>)` reference, so
  * the operand is emitted ONCE and every use reads it by name. The dedup is BY CONSTRUCTION — the marked
  * points call `hoist` once per operand site (e.g. `ivOperand` hoists `go(e)` once, then both `.low` and
  * `.high` read `__hN.low`/`__hN.high`) — plus a structural [[SqlExpr]]-keyed cache that collapses
  * identical operands hoisted at separate sites (e.g. `Width`'s separate `lo`/`hi`).
  *
  * WHERE IT APPLIES: only at the RELATION-PROJECTION level (the `where` predicate and `return`/element
  * projection lowered over the combined relation, and a scalar define's body). It must NOT reach inside
  * the `array` higher-order-function path (System-list lambdas) — those lower with `noHoist`, because
  * `SqlQueryLowering.array` never threads a hoister, so a temporal op over a lambda variable stays inline
  * (the marked hoist points are all alias-rooted STRUCT columns, which are lift-able relation columns). */
private[lower] object SqlHoist {
  import SqlColumnLowering.Hoister

  /** Bind each duplication-prone sub-expression the `build` closure hoists to a fresh projected column
    * over `rel`, returning `(relWithHoistCols, builtExpr)`: the built expression references the columns by
    * name, and `relWithHoistCols` is `SELECT *, <sub0> AS __h0, … FROM rel` (the caller then wraps
    * `SELECT … <builtExpr> … FROM relWithHoistCols`). `counter` supplies the `__h<N>` numbers UNIQUE
    * across every (possibly nested) hoist scope of the current define ([[SqlEvalEnv.hoistCounter]]) — a
    * hoisted select propagates its prior `__h<N>` through `*`, so an outer level must not collide.
    *
    * COLUMN CHAINING (the one subtlety): a hoisted column may reference an EARLIER hoisted column (nested
    * temporal ops — e.g. `CalculateAgeAt`'s `tsOf(hoist(birth))`, whose inner `hoist` already returned
    * `Col(__hK)`). The projection emits them in insertion order, relying on Spark's LATERAL COLUMN ALIAS
    * resolution (Spark 3.4+/DBR 18 floor): `SELECT *, <sub0> AS __h0, <sub1 uses __h0> AS __h1 FROM rel`.
    *
    * No hoist => `rel` is returned unchanged (byte-identical to the pre-CSE emission), so a define with no
    * duplication-prone operand is untouched. */
  def hoisted(rel: SqlRel, counter: AtomicInteger, alias: String = "__hoist")
             (build: Hoister => SqlExpr): (SqlRel, SqlExpr) = {
    val cols   = mutable.ArrayBuffer.empty[(String, SqlExpr)]
    val byExpr = mutable.HashMap.empty[SqlExpr, SqlExpr] // operand -> its Col(__hN) (structural dedup)
    val hoist: Hoister = e => byExpr.getOrElseUpdate(e, {
      val n = s"__h${counter.getAndIncrement()}"; cols += (n -> e); Col(n)
    })
    val out = build(hoist)
    if (cols.isEmpty) (rel, out)
    else {
      val projs = SqlRel.Proj.star +: cols.toSeq.map { case (n, e) => SqlRel.Proj(e, n) }
      (SqlRel.Select(projs, from = asFrom(rel, alias)), out)
    }
  }

  /** Present a relation as a FROM atom: a bare table stays a `TableRef`; anything else is aliased as a
    * derived table (matches the local `asFrom` in [[SqlQueryLowering]]/[[SqlFrameLowering]]). */
  private def asFrom(rel: SqlRel, alias: String): SqlRel = rel match {
    case t: SqlRel.TableRef => t
    case other              => SqlRel.AliasRel(other, alias)
  }
}
