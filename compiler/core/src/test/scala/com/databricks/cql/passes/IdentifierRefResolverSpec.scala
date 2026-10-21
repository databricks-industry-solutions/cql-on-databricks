package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** IdentifierRefResolver (LANG-QUERY-SORT): a bare `IdentifierRef` in a `sort by` key binds to the
  * enclosing single-source query's alias as a scope-rooted `Property` — a front-end gap
  * (`sort by end of effective.effectiveSpan()`). Anything but a single-source query rejects loudly. */
@RunWith(classOf[JUnitRunner])
class IdentifierRefResolverSpec extends AnyFunSuite {

  private def sortQuery(sources: Seq[QuerySource], key: Expr): Query =
    Query(sources = sources, where = None, returnExpr = None, sort = Seq(SortItem("asc", expr = Some(key))))

  test("a sort-key IdentifierRef binds to the single source alias as a scope-rooted Property") {
    // sort by end of effective.effectiveSpan()  — `effective` is the IdentifierRef leaf.
    val key = End(FunctionRef("effectiveSpan", Some("CommonHelpers"), Seq(IdentifierRef("effective"))))
    val q   = sortQuery(Seq(QuerySource("O", Retrieve("Observation", None, None, None, None, None))), key)
    val out = IdentifierRefResolver.normalize(lib(edef("D", q)))

    val identifiers = out.collect { case r: IdentifierRef => r }
    assert(identifiers.isEmpty, "no IdentifierRef should survive")
    val props = out.collect { case p @ Property(None, Some("O"), "effective") => p }
    assert(props.size == 1, s"expected one O.effective Property, got ${out.collect { case p: Property => p }}")
  }

  test("an IdentifierRef sort key over a multi-source query is rejected loudly") {
    val q = sortQuery(
      Seq(QuerySource("A", Retrieve("Observation", None, None, None, None, None)),
          QuerySource("B", Retrieve("Condition",  None, None, None, None, None))),
      IdentifierRef("effective"))
    assertThrows[UnsupportedNode](IdentifierRefResolver.normalize(lib(edef("D", q))))
  }

  test("no sort identifier is a no-op") {
    val q = sortQuery(Seq(QuerySource("A", Retrieve("Observation", None, None, None, None, None))),
      Property(None, Some("A"), "effective"))
    assert(IdentifierRefResolver.normalize(lib(edef("D", q))) == lib(edef("D", q)))
  }
}
