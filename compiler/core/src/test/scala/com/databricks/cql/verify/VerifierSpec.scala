package com.databricks.cql.verify

import com.databricks.cql.TestFixtures._
import com.databricks.cql.error.{ReferenceCycle, VerifierError}
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Verifier checkpoints (CONF-VERIFIER) and DAG cycle detection (ARCH-PIPELINE). */
@RunWith(classOf[JUnitRunner])
class VerifierSpec extends AnyFunSuite {

  test("OnlyKernelNodes passes when no unmodeled nodes remain") {
    val l = lib(edef("A", And(bool(true), bool(false))))
    Verifier.OnlyKernelNodes.verify(l) // does not throw
  }

  test("OnlyKernelNodes fails loudly when an UnknownExpr is present (CORE-NO-SILENT-MISLOWER)") {
    val l = lib(edef("A", UnknownExpr("ToDecimal", "{\"type\":\"ToDecimal\"}")))
    val ex = intercept[VerifierError](Verifier.OnlyKernelNodes.verify(l))
    assert(ex.getMessage.contains("ToDecimal"))
  }

  test("WellTyped passes on a well-formed kernel library") {
    val l = lib(edef("A", And(bool(true), bool(false))), edef("B", If(ref("A"), int("1"), int("2"))))
    Verifier.WellTyped.verify(l) // does not throw
  }

  test("WellTyped fails loudly, naming the define, when a node cannot be typed") {
    val l = lib(edef("Bad", UnknownExpr("SomeOp", "{\"type\":\"SomeOp\"}")))
    val ex = intercept[VerifierError](Verifier.WellTyped.verify(l))
    assert(ex.getMessage.contains("Type/Encoding") && ex.getMessage.contains("Bad"))
  }

  test("WellFormedLibrary rejects an unnamed statement") {
    val l = lib(edef("", int("1")))
    assertThrows[VerifierError](Verifier.WellFormedLibrary.verify(l))
  }

  test("DefineGraph accepts an acyclic define DAG") {
    val l = lib(edef("A", int("1")), edef("B", ref("A")), edef("C", And(ref("A"), ref("B"))))
    DefineGraph.assertAcyclic(l) // does not throw
  }

  test("DefineGraph rejects a circular define reference (CQL forbids it)") {
    val l = lib(edef("A", ref("B")), edef("B", ref("A")))
    val ex = intercept[ReferenceCycle](DefineGraph.assertAcyclic(l))
    assert(ex.getMessage.contains("A") && ex.getMessage.contains("B"))
  }

  test("DefineGraph detects a self-cycle") {
    val l = lib(edef("A", ref("A")))
    assertThrows[ReferenceCycle](DefineGraph.assertAcyclic(l))
  }
}
