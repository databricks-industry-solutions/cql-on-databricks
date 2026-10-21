package com.databricks.cql.transpiler.emit

import com.databricks.cql.ir.{CqlType, DateExpr, Literal, Predecessor, Successor}
import com.databricks.cql.transpiler.lower.SqlColumnLowering
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** EMIT-SQL-TEMPORAL-UDF: the pure emit-only tower fold. Two guards:
  *   1. INTERNAL — hand-built replica towers fold to the expected UDF calls, nest, and are idempotent;
  *      a non-tower script is untouched.
  *   2. REAL-EMIT — a tower produced by the actual `SqlColumnLowering` (a `Predecessor`/`Successor` of a
  *      date) folds, so the matcher tracks what the lowering really emits (not just a replica).
  * Server-free; the results-identical guarantee is validated on the Connect server (SQL differential). */
@RunWith(classOf[JUnitRunner])
class SqlTemporalFoldSpec extends AnyFunSuite {
  import SqlExpr._
  import SqlTemporalFold._

  // Replica tower shapes — MIRROR SqlColumnLowering.{tsOf, fmtTrunc, rankOfLen, addUnit}. The fold's own
  // body builders are the oracle; these prove the matcher recognizes that exact shape.
  private def tsOf(s: SqlExpr): SqlExpr =
    Cast(Call("concat", Seq(s, Call("substr", Seq(str("0000-01-01T00:00:00.000"),
      Infix("+", Call("length", Seq(s)), int(1)), int(23))))), "TIMESTAMP_NTZ")
  private def fmtTrunc(ts: SqlExpr, len: SqlExpr): SqlExpr =
    Call("substr", Seq(Call("replace", Seq(Call("date_format", Seq(ts, str("yyyy-MM-dd HH:mm:ss.SSS"))),
      str(" "), str("T"))), int(1), len))
  private def rankOfLen(len: SqlExpr): SqlExpr =
    Case(Seq(4 -> 0, 7 -> 1, 10 -> 2, 13 -> 3, 16 -> 4, 19 -> 5).map { case (n, r) =>
      (Infix("<=", len, int(n)): SqlExpr) -> (int(r): SqlExpr) }, Some(int(6)))
  private def addUnit(ts: SqlExpr, rank: SqlExpr, minus: Boolean): SqlExpr = {
    val one = int(if (minus) -1 else 1); val z = int(0)
    val ms = decimal(BigDecimal(if (minus) "-0.001" else "0.001"))
    def slot(r: Int): SqlExpr = Case(Seq(Infix("=", rank, int(r)) -> one), Some(z))
    val secs = Case(Seq(Infix("=", rank, int(5)) -> one, Infix("<", rank, int(5)) -> z), Some(ms))
    Cast(Infix("+", ts, Call("make_interval", Seq(slot(0), slot(1), z, slot(2), slot(3), slot(4), secs))), "TIMESTAMP_NTZ")
  }

  /** Fold `e` in isolation (wrap in a one-projection view) and return `(foldedExpr, preludeFnNames)`. */
  private def fold1(e: SqlExpr): (SqlExpr, Seq[String]) = {
    val in  = SqlScript(Seq(SqlStmt.CreateView("v", SqlRel.Select(Seq(SqlRel.Proj(e, "c")), SqlRel.TableRef("t")))), "v")
    val out = SqlTemporalFold(in)
    val fns = out.statements.collect { case f: SqlStmt.CreateFunction => f.name }
    val expr = out.statements.collectFirst {
      case SqlStmt.CreateView("v", s: SqlRel.Select) => s.projections.head.expr
    }.get
    (expr, fns)
  }

  test("cql_to_ts folds the pad+cast tower and emits its function") {
    val (e, fns) = fold1(tsOf(Col("x")))
    assert(e == Call(ToTs, Seq(Col("x"))))
    assert(fns.contains(ToTs))
  }

  test("cql_iso folds the date_format/replace/substr tower") {
    val (e, _) = fold1(fmtTrunc(Col("ts"), Col("len")))
    assert(e == Call(Iso, Seq(Col("ts"), Col("len"))))
  }

  test("cql_prec folds the precision-from-length ladder") {
    val (e, _) = fold1(rankOfLen(Call("length", Seq(Col("x")))))
    assert(e == Call(Prec, Seq(Col("x"))))
  }

  test("cql_add_unit and cql_sub_unit fold by the sign of the calendar step") {
    assert(fold1(addUnit(Col("ts"), Col("r"), minus = false))._1 == Call(AddUnit, Seq(Col("ts"), Col("r"))))
    assert(fold1(addUnit(Col("ts"), Col("r"), minus = true))._1 == Call(SubUnit, Seq(Col("ts"), Col("r"))))
  }

  test("nested towers collapse in one bottom-up pass") {
    val nested = fmtTrunc(addUnit(tsOf(Col("x")), rankOfLen(Call("length", Seq(Col("x")))), minus = true), Call("length", Seq(Col("x"))))
    val (e, fns) = fold1(nested)
    assert(e == Call(Iso, Seq(Call(SubUnit, Seq(Call(ToTs, Seq(Col("x"))), Call(Prec, Seq(Col("x"))))), Call("length", Seq(Col("x")))))
    )
    assert(Seq(ToTs, Iso, Prec, SubUnit).forall(fns.contains))
  }

  test("a non-tower script is returned unchanged, with no prelude") {
    val in = SqlScript(Seq(SqlStmt.CreateView("v",
      SqlRel.Select(Seq(SqlRel.Proj(Infix("+", Col("a"), int(1)), "c")), SqlRel.TableRef("t")))), "v")
    assert(SqlTemporalFold(in) eq in)
  }

  test("the fold is idempotent") {
    val in = SqlScript(Seq(SqlStmt.CreateView("v",
      SqlRel.Select(Seq(SqlRel.Proj(tsOf(Col("x")), "c")), SqlRel.TableRef("t")))), "v")
    val once = SqlTemporalFold(in)
    assert(SqlTemporalFold(once) == once)
  }

  test("REAL EMIT: a Predecessor/Successor of a date lowered by SqlColumnLowering folds to nested calls") {
    def lit(v: String): Literal = Literal("{urn:hl7-org:elm-types:r1}Integer", Some(v))
    val date = DateExpr(Seq(lit("2020"), lit("1"), lit("1")))
    val dateType: com.databricks.cql.ir.Expr => CqlType = _ => CqlType.DateType()

    for (mk <- Seq(Predecessor(date), Successor(date))) {
      val emitted = SqlColumnLowering.lower(mk, typeOf = dateType)
      val (folded, fns) = fold1(emitted)
      // strStep = cql_iso( cql_(add|sub)_unit( cql_to_ts(s), cql_prec(s) ), length(s) )
      assert(fns.contains(Iso) && fns.contains(ToTs) && fns.contains(Prec), s"emitted did not fold: $emitted")
      folded match {
        case Call(Iso, Seq(Call(step, Seq(Call(ToTs, _), Call(Prec, _))), _)) =>
          assert(step == (if (mk.isInstanceOf[Predecessor]) SubUnit else AddUnit))
        case other => fail(s"unexpected fold shape: $other")
      }
    }
  }

  test("preludeDdl renders all functions with CREATE TEMPORARY FUNCTION") {
    val ddl = SqlTemporalFold.preludeDdl
    Seq(ToTs, Iso, Prec, AddUnit, SubUnit, Cmp, Equiv).foreach(fn =>
      assert(ddl.contains(s"CREATE OR REPLACE TEMPORARY FUNCTION $fn("), s"missing $fn in:\n$ddl"))
  }

  // Replica of temporalCompare's partial-precision comparator core (SqlColumnLowering.temporalCompare).
  private def commonLen(a: SqlExpr, b: SqlExpr): SqlExpr =
    Call("least", Seq(Call("length", Seq(a)), Call("length", Seq(b))))
  private def pfx(s: SqlExpr, l: SqlExpr): SqlExpr = Call("substr", Seq(s, int(1), l))
  private def cmpShape(a: SqlExpr, b: SqlExpr, op1: String, op2: String, tie: Boolean): SqlExpr = {
    val l = commonLen(a, b); val ta = pfx(a, l); val tb = pfx(b, l)
    Case(Seq(Infix(op1, ta, tb) -> bool(true), Infix(op2, ta, tb) -> bool(false)),
      Some(Case(Seq(Infix("=", Call("length", Seq(a)), Call("length", Seq(b))) -> bool(tie)), Some(Cast(Null, "BOOLEAN")))))
  }

  test("cql_cmp folds the Lt/Le/Gt/Ge partial-precision comparator cores to the tri-state mapping") {
    val x = Col("x"); val y = Col("y")
    assert(fold1(cmpShape(x, y, "<", ">", tie = false))._1 == Infix("=",  Call(Cmp, Seq(x, y)), int(-1))) // Lt
    assert(fold1(cmpShape(x, y, "<", ">", tie = true))._1  == Infix("<=", Call(Cmp, Seq(x, y)), int(0)))  // Le
    assert(fold1(cmpShape(x, y, ">", "<", tie = false))._1 == Infix("=",  Call(Cmp, Seq(x, y)), int(1)))  // Gt
    assert(fold1(cmpShape(x, y, ">", "<", tie = true))._1  == Infix(">=", Call(Cmp, Seq(x, y)), int(0)))  // Ge
    assert(fold1(cmpShape(x, y, "<", ">", tie = false))._2.contains(Cmp))
  }

  test("cql_equiv folds the null-safe partial-precision equivalence core") {
    val x = Col("x"); val y = Col("y"); val l = commonLen(x, y)
    val (e, fns) = fold1(Infix("<=>", pfx(x, l), pfx(y, l)))
    assert(e == Call(Equiv, Seq(x, y)))
    assert(fns.contains(Equiv))
  }

  test("a plain <=> that is not a partial-precision prefix compare is left untouched") {
    val plain = Infix("<=>", Col("a"), Col("b"))
    assert(fold1(plain)._1 == plain)
  }
}
