package com.databricks.cql.sema

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Operator-level conformance for the 3VL primitives (LANG-3VL, Gate 1 seed
  * CONF-GATE-OPERATOR). These hand cases pin CQL's truth tables; the property-based
  * differential against the JS reference (Gate 2) expands coverage in Phase 3.
  */
@RunWith(classOf[JUnitRunner])
class Bool3Spec extends AnyFunSuite {
  import Bool3._

  test("not: null negates to null") {
    assert(not(True) == False)
    assert(not(False) == True)
    assert(not(Null) == Null)
  }

  test("and: false dominates, otherwise null propagates") {
    assert(and(True, True) == True)
    assert(and(True, False) == False)
    assert(and(True, Null) == Null)
    assert(and(False, Null) == False) // false dominates even over null
    assert(and(Null, False) == False)
    assert(and(Null, Null) == Null)
  }

  test("or: true dominates, otherwise null propagates") {
    assert(or(False, False) == False)
    assert(or(True, Null) == True) // true dominates even over null
    assert(or(Null, True) == True)
    assert(or(False, Null) == Null)
    assert(or(Null, Null) == Null)
  }

  test("xor: null is contagious") {
    assert(xor(True, False) == True)
    assert(xor(True, True) == False)
    assert(xor(Null, True) == Null)
    assert(xor(False, Null) == Null)
  }

  test("implies follows (not a) or b under 3VL") {
    // NB: our library is spec-correct. The stale JS reference
    // (cql-execution 2.1.0) returned null for all `implies` (no impl); the maintained Java
    // engine we now use as ground truth computes these correctly, so G4 is resolved.
    assert(implies(True, True) == True)
    assert(implies(True, False) == False)
    assert(implies(False, Null) == True) // false implies anything = true
    assert(implies(Null, True) == True)  // null implies true = true
    assert(implies(Null, False) == Null)
  }

  test("cqlTrue is true only for exactly true; the else-test catches false and null") {
    assert(cqlTrue(True))
    assert(!cqlTrue(False))
    assert(!cqlTrue(Null))
    // not cqlTrue(x) routes false AND null to the else branch (ARCH-CONDITIONAL)
    assert(!notCqlTrue(True))
    assert(notCqlTrue(False))
    assert(notCqlTrue(Null))
  }
}
