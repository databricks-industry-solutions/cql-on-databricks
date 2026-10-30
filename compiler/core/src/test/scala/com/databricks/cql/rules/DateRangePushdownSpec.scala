package com.databricks.cql.rules

import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Isolated rule test for date-range retrieve pushdown (ARCH-PUSHDOWN; result-preservation gated
  * end-to-end by the `FhirTemporalWhere` self-diff). Pins the `[X] where X.date during <range>` →
  * date-filtered-retrieve fold and its safety properties. */
@RunWith(classOf[JUnitRunner])
class DateRangePushdownSpec extends AnyFunSuite {

  private val conditionType = "{http://hl7.org/fhir}Condition"
  private def retrieve(dateRange: Option[Expr] = None) =
    Retrieve(conditionType, None, None, None, if (dateRange.isDefined) Some("recordedDate") else None, dateRange)
  private def dt(y: Int) = DateTimeExpr(Seq(Literal("Integer", Some(y.toString))))
  private val litRange = IntervalExpr(Some(dt(2023)), Some(dt(2024)), lowClosed = true, highClosed = true)
  // `C.recordedDate during <interval>` as the translator emits it: In(FHIRHelpers.ToDateTime(nav), iv).
  private def during(alias: String, interval: Expr = litRange) =
    In(FunctionRef("ToDateTime", Some("FHIRHelpers"), Seq(Property(None, Some(alias), "recordedDate"))), interval)
  // A `date`-typed property `during <interval>`: the translator emits ToDate (not ToDateTime).
  private def duringDate(alias: String, interval: Expr = litRange) =
    In(FunctionRef("ToDate", Some("FHIRHelpers"), Seq(Property(None, Some(alias), "onsetDate"))), interval)
  private def query(where: Option[Expr], src: Expr = retrieve(), ret: Option[Expr] = Some(AliasRef("C"))) =
    Query(Seq(QuerySource("C", src)), where, ret)
  private def apply1(q: Query): Query = DateRangePushdown.apply(q).asInstanceOf[Query]

  test("folds a date-range where conjunct into the retrieve's date-filter slots and drops it") {
    val out = apply1(query(Some(during("C"))))
    val src = out.sources.head.expr.asInstanceOf[Retrieve]
    assert(src.dateProperty.contains("recordedDate"))
    assert(src.dateRange.contains(litRange))
    assert(src.datePointConverter.contains("ToDateTime")) // recorded so the scan reconstructs an identical predicate
    assert(out.where.isEmpty)
  }

  test("folds a ToDate (date-typed) point and records the ToDate converter for exact precision") {
    val out = apply1(query(Some(duringDate("C"))))
    val src = out.sources.head.expr.asInstanceOf[Retrieve]
    assert(src.dateProperty.contains("onsetDate"))
    assert(src.datePointConverter.contains("ToDate")) // not ToDateTime — Date precision (day) preserved
    assert(out.where.isEmpty)
  }

  test("idempotent: a retrieve that already has a date range is not re-folded") {
    val once = apply1(query(Some(during("C"))))
    assert(DateRangePushdown.apply(once) eq once)
  }

  test("does not fold a non-scan-safe range (one that references another define)") {
    val dataDependent = IntervalExpr(Some(ExpressionRef("Start", None)), Some(dt(2024)), lowClosed = true, highClosed = true)
    val q = query(Some(during("C", dataDependent)))
    assert(DateRangePushdown.apply(q) eq q, "a range needing the surrounding plan stays in the where")
  }

  test("leaves a non-date where untouched") {
    val q = query(Some(Equal(Property(None, Some("C"), "status"), Literal("String", Some("active")))))
    assert(DateRangePushdown.apply(q) eq q)
  }

  test("multi-conjunct: folds the date range, keeps the residual conjunct") {
    val residual = Equal(Property(None, Some("C"), "status"), Literal("String", Some("active")))
    val out = apply1(query(Some(And(during("C"), residual))))
    assert(out.sources.head.expr.asInstanceOf[Retrieve].dateRange.contains(litRange))
    assert(out.where.contains(residual))
  }
}
