package com.databricks.cql.it

import com.databricks.cql.runtime.eval.{CqlInterval, CqlOps}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** EXEC-SELFDIFF (compiler/docs/execution-model.md) / EVAL-OPS-CORE (compiler/docs/execution-model.md section
  * 11.3) — the PURE, Spark-FREE per-operator gate on the shared semantic core ([[CqlOps]], the temporal
  * family, EMIT-SQL-TEMPORAL-UDF). These ports must reproduce `SqlColumnLowering`'s ISO-string precision
  * semantics EXACTLY; this spec pins the three-valued edges (precision-uncertain -> null, open-bound
  * infinity, calendar stepping) by hand so a regression fails WITHOUT needing a Connect server. Because
  * `CqlRuntimeUdfs` now DELEGATES into these same functions, this is the operator gate for both the SQL
  * catalog UDFs and any future coarse evaluator. The end-to-end on==off==reference gate is
  * [[TemporalUdfSelfDiffSpec]] + the default-on fixtures in [[SqlDifferentialSpec]]. */
@RunWith(classOf[JUnitRunner])
final class TemporalOpsSpec extends AnyFunSuite {

  private def cmp(a: String, b: String, op: String): java.lang.Boolean = CqlOps.temporalCmp(a, 0, b, 0, op)

  test("cql_temporal_cmp: equal at equal precision, uncertain across precisions") {
    assert(cmp("2013", "2013", "eq") == true)
    assert(cmp("2013", "2014", "eq") == false)
    assert(cmp("2013", "2013-01", "eq") == null)   // prefix ties, precisions differ -> uncertain
    assert(cmp(null, "2013", "eq") == null)
  }

  test("cql_temporal_cmp: inequality tri-state (Lt tie=false, Le tie=true)") {
    assert(cmp("2013", "2014", "lt") == true)
    assert(cmp("2014", "2013", "lt") == false)
    assert(cmp("2013", "2013", "lt") == false)     // equal, same precision -> tie(false)
    assert(cmp("2013", "2013-05", "lt") == null)   // prefix ties, precisions differ -> null
    assert(cmp("2013", "2013", "le") == true)      // tie(true)
    assert(cmp("2013-06-15", "2013-06-20", "lt") == true)
  }

  test("cql_temporal_cmp: equiv is null-safe and precision-lenient") {
    assert(cmp("2013", "2013-05", "equiv") == true) // compare common (shorter) prefix
    assert(cmp(null, "2013", "equiv") == false)
    assert(cmp(null, null, "equiv") == true)
    assert(cmp("2013-06", "2013-07", "equiv") == false)
  }

  test("cql_temporal_cmp: open-bound infinity (cmpPoints)") {
    // a.high = +inf (null, +1) vs finite b.low -> a.high < b.low is false
    assert(CqlOps.temporalCmp(null, +1, "2015", 0, "lt") == false)
    // a.low = -inf (null, -1) vs finite b -> a.low < b is true
    assert(CqlOps.temporalCmp(null, -1, "2015", 0, "lt") == true)
    // -inf <= +inf
    assert(CqlOps.temporalCmp(null, -1, null, +1, "le") == true)
    // finite operand with tracking on but not null -> falls to the precision-aware core
    assert(CqlOps.temporalCmp("2015", -1, "2016", 0, "lt") == true)
  }

  test("cql_temporal_same_prec: aligned to an explicit precision") {
    assert(CqlOps.temporalSamePrec("2013-06-15", "2013-06-20", 7) == true)  // to month
    assert(CqlOps.temporalSamePrec("2013-06-15", "2013-07-01", 7) == false)
    assert(CqlOps.temporalSamePrec("2013", "2013-06", 7) == null)           // a shorter than precLen
    assert(CqlOps.temporalSamePrec(null, "2013-06", 7) == null)
  }

  test("cql_temporal_step: successor/predecessor at native precision") {
    assert(CqlOps.temporalStep("2013", minus = false) == "2014")
    assert(CqlOps.temporalStep("2013-01", minus = false) == "2013-02")
    assert(CqlOps.temporalStep("2013-01-31", minus = false) == "2013-02-01")
    assert(CqlOps.temporalStep("2013-01-01", minus = true) == "2012-12-31")
    assert(CqlOps.temporalStep(null, minus = false) == null)
  }

  test("cql_date_add: date ± calendar quantity, truncated to precision") {
    assert(CqlOps.dateAdd("2013-01-15", 1, "month") == "2013-02-15")
    assert(CqlOps.dateAdd("2013-01-15", -1, "day") == "2013-01-14")
    assert(CqlOps.dateAdd("2013", 5, "year") == "2018")
    assert(CqlOps.dateAdd("2013-01-15", 2, "week") == "2013-01-29")
    assert(CqlOps.dateAdd("2013-01-15", null, "day") == null)
    assert(CqlOps.dateAdd("2013-01-15", 1, "1") == null) // dimensionless -> null (make_interval(null,…))
  }

  test("cql_age_at: whole years, birthday not yet reached") {
    assert(CqlOps.ageAt("1980-06-15", "2020-06-15") == 40)
    assert(CqlOps.ageAt("1980-06-15", "2020-06-14") == 39)
    assert(CqlOps.ageAt("1980-06-15", "2020-06-16") == 40)
    assert(CqlOps.ageAt(null, "2020-06-16") == null)
  }

  test("cql_temporal_boundary: low truncates, high is the largest sharing value") {
    assert(CqlOps.temporalBoundary("2013", 2, high = false) == "2013-01-01") // low of year 2013, to day
    assert(CqlOps.temporalBoundary("2013", 2, high = true) == "2013-12-31")  // high of year 2013, to day
    assert(CqlOps.temporalBoundary("2013-02", 2, high = true) == "2013-02-28")
    assert(CqlOps.temporalBoundary(null, 2, high = false) == null)
  }

  test("cql_to_interval: {low,high} normalized endpoints, always a non-null struct") {
    assert(CqlOps.toInterval("2013-01-01", "2013-12-31") == CqlInterval("2013-01-01", "2013-12-31"))
    assert(CqlOps.toInterval(null, "2013-12-31") == CqlInterval(null, "2013-12-31"))
    // a timezone suffix beyond millisecond width is capped at 23 (fhirTemporal)
    assert(CqlOps.toInterval("2013-01-01T00:00:00.000+05:00", null) ==
      CqlInterval("2013-01-01T00:00:00.000", null))
  }
}
