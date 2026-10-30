package com.databricks.cql.sema

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Operator-level conformance for UCUM unit conversion (LANG-QUANTITY, Gate 1 seed
  * CONF-GATE-OPERATOR). Pins the factors the lowering bakes into the plan; the property-based
  * differential (Gate 2) exercises the combinatorial unit space against the reference. */
@RunWith(classOf[JUnitRunner])
class UcumSpec extends AnyFunSuite {
  import Ucum._

  test("commensurability is by dimension; same unit is always commensurable") {
    assert(commensurable("kg", "g"))
    assert(commensurable("mg", "kg"))
    assert(commensurable("m", "cm"))
    assert(!commensurable("kg", "m"))   // mass vs length
    assert(!commensurable("g", "s"))    // mass vs time
    assert(commensurable("foo", "foo")) // identical unmodeled unit
    assert(!commensurable("foo", "g"))  // unmodeled vs modeled
  }

  test("conversion factors are exact and round-trip") {
    assert(conversion("kg", "g").contains(BigDecimal(1000)))
    assert(conversion("g", "kg").contains(BigDecimal("0.001")))
    assert(conversion("mg", "g").contains(BigDecimal("0.001")))
    assert(conversion("cm", "m").contains(BigDecimal("0.01")))
    assert(conversion("m", "m").contains(BigDecimal(1)))       // identity
    assert(conversion("kg", "m").isEmpty)                       // incommensurable
    assert(conversion("foo", "foo").contains(BigDecimal(1)))    // identity even when unmodeled
    assert(conversion("foo", "g").isEmpty)
  }

  test("cross-unit magnitudes match after normalizing to a common base") {
    // 1 kg = 1000 g : 1 * factor(kg→g) == 1000 * factor(g→g)
    assert(BigDecimal(1) * conversion("kg", "g").get == BigDecimal(1000))
    // 500 mg < 1 g
    assert(BigDecimal(500) * factor("mg").get < BigDecimal(1) * factor("g").get)
    // 2 m > 150 cm  (compare in base metre)
    assert(BigDecimal(2) * factor("m").get > BigDecimal(150) * factor("cm").get)
  }
}
