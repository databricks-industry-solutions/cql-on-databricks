package com.databricks.cql.sema

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Operator-level conformance for the interval (Allen-relation) algebra over discrete integer
  * points (LANG-INTERVAL, Gate 1 seed CONF-GATE-OPERATOR). These hand cases pin the boundary
  * normalization and the Allen truth table that [[com.databricks.cql.lower.ColumnLowering]]
  * must reproduce on Spark columns; the property-based differential (Gate 2) expands coverage
  * against the reference. */
@RunWith(classOf[JUnitRunner])
class CqlIntervalSpec extends AnyFunSuite {
  import CqlInterval._

  private def ci(lo: Long, hi: Long): Closed = Closed(Some(lo), Some(hi))

  test("open integer boundaries normalize to closed via predecessor/successor") {
    // Interval(1,10) ≡ [2,9]; Interval(1,10] ≡ [2,10]
    assert(closed(Some(1), Some(10), lowClosed = false, highClosed = false) == ci(2, 9))
    assert(closed(Some(1), Some(10), lowClosed = false, highClosed = true) == ci(2, 10))
    assert(closed(Some(1), Some(10), lowClosed = true, highClosed = true) == ci(1, 10))
  }

  test("start / end / width over a closed interval") {
    val i = ci(1, 10)
    assert(start(i).contains(1L))
    assert(end(i).contains(10L))
    assert(width(i).contains(9L)) // end - start
  }

  test("point during interval (closed)") {
    assert(contains(ci(1, 10), Some(5)) == Bool3.True)
    assert(contains(ci(1, 10), Some(1)) == Bool3.True)  // inclusive boundary
    assert(contains(ci(1, 10), Some(11)) == Bool3.False)
    assert(contains(ci(1, 10), None) == Bool3.Null)     // null point → null
  }

  test("before / after with boundary adjacency") {
    assert(before(ci(1, 2), ci(5, 6)) == Bool3.True)
    assert(before(ci(1, 5), ci(5, 8)) == Bool3.False) // touch at 5 → overlap, not before
    assert(after(ci(5, 6), ci(1, 2)) == Bool3.True)
  }

  test("overlaps is symmetric and boundary-inclusive") {
    assert(overlaps(ci(1, 5), ci(3, 8)) == Bool3.True)
    assert(overlaps(ci(3, 8), ci(1, 5)) == Bool3.True)
    assert(overlaps(ci(1, 5), ci(5, 9)) == Bool3.True)  // share point 5
    assert(overlaps(ci(1, 4), ci(5, 9)) == Bool3.False) // gap between 4 and 5
  }

  test("includes / includedIn / starts / ends") {
    assert(includes(ci(1, 10), ci(3, 4)) == Bool3.True)
    assert(includes(ci(3, 4), ci(1, 10)) == Bool3.False)
    assert(includedIn(ci(3, 4), ci(1, 10)) == Bool3.True)
    assert(starts(ci(1, 4), ci(1, 10)) == Bool3.True)   // shared low
    assert(starts(ci(2, 4), ci(1, 10)) == Bool3.False)
    assert(ends(ci(7, 10), ci(1, 10)) == Bool3.True)    // shared high
  }

  test("a null boundary the relation reads makes it null (3VL)") {
    // `before` reads the left's HIGH and the right's LOW; a null in either → null.
    assert(before(Closed(Some(1), None), ci(5, 6)) == Bool3.Null)
    assert(before(ci(1, 2), Closed(None, Some(6))) == Bool3.Null)
    // but a null boundary the relation does NOT read leaves it decidable.
    assert(before(Closed(None, Some(2)), ci(5, 6)) == Bool3.True)
    assert(width(Closed(Some(1), None)).isEmpty)
  }
}
