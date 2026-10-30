package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** ARCH-RESOURCE-HANDLE step 1: static demand analysis over the define DAG (PROC-TDD — proves the
  * demand set / body escalation the handle encoding consumes, before it consumes it). Uses an
  * injected `typeOf` so the analysis is exercised independently of full `TypeEncoding` inference: a
  * `union` define is typed as the body-bearing het-`Choice` list, everything else non-target. */
@RunWith(classOf[JUnitRunner])
class ChoiceDemandSpec extends AnyFunSuite {

  // A heterogeneous resource union (`[Observation] union [MedicationRequest]`), the body-bearing
  // het-Choice the encoding prunes. Retrieves stand in for the arms; only the DEFINE-body type drives
  // target detection, so `typeOf` returns the het-Choice list for the union and Any for the rest.
  private def obs: Retrieve = Retrieve("Observation", None, None, None, None, None)
  private def med: Retrieve = Retrieve("MedicationRequest", None, None, None, None, None)
  private val mixed: Union = Union(obs, med)

  private val hetChoiceList: CqlType =
    CqlType.ListType(CqlType.ChoiceType(Seq(CqlType.NamedType("Observation"), CqlType.NamedType("MedicationRequest"))))
  private val typeOf: Expr => CqlType = { case _: Union => hetChoiceList; case _ => CqlType.AnyType }

  private def analyze(l: Library) = ChoiceDemand.analyze(l, typeOf)

  test("no het-Choice define → empty demand map") {
    assert(analyze(lib(edef("A", obs))).isEmpty)
  }

  test("navigation on a bound alias collects the demand set; identity export needs no body") {
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Statuses", Query(Seq(QuerySource("I", ref("Mixed"))), None,
        Some(Property(None, Some("I"), "status"))))))
    assert(m.keySet == Set("Mixed"))
    assert(m("Mixed").fields == Set("status"))
    assert(!m("Mixed").needsBody, "a define exported/consumed only by field-nav needs identity, not a body")
  }

  test("multiple consumers union their demanded fields") {
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Statuses", Query(Seq(QuerySource("I", ref("Mixed"))), None,
        Some(Property(None, Some("I"), "status")))),
      edef("Codes", Query(Seq(QuerySource("J", ref("Mixed"))), None,
        Some(Property(Some(AliasRef("J")), None, "code"))))))  // the bindScopes-rewritten source form
    assert(m("Mixed").fields == Set("status", "code"))
  }

  test("a nested navigation demands only the TOP-LEVEL element field") {
    // `I.performed.performedSpan()` → the handle carries `performed` whole; `authoredOn` too.
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Interval", Query(Seq(QuerySource("I", ref("Mixed"))), None,
        Some(Coalesce(Seq(
          Property(Some(Property(None, Some("I"), "performed")), None, "end"),
          Property(None, Some("I"), "authoredOn"))))))))
    assert(m("Mixed").fields == Set("performed", "authoredOn"))
  }

  test("a pure reduction (exists/Count/in) needs identity only — no fields, no body") {
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Any", Exists(ref("Mixed"))),
      edef("N", Count(ref("Mixed")))))
    assert(m("Mixed").fields.isEmpty && !m("Mixed").needsBody)
  }

  test("the whole element used directly (returned) escalates to needsBody") {
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Whole", Query(Seq(QuerySource("I", ref("Mixed"))), None, Some(AliasRef("I"))))))
    assert(m("Mixed").needsBody, "a whole-element return is a body escape → rehydrate")
  }

  test("a direct whole reference (not a query source, not a reduction) escalates to needsBody") {
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Passthrough", ref("Mixed"))))
    assert(m("Mixed").needsBody)
  }

  test("a union-bound alias collects demand for the target arm; the bare ref is a carrier, not a body escape") {
    // `… with ("Mixed" union "Homogeneous") I such that I.performed …`. `Mixed` is a het-Choice
    // target; `Homogeneous` is a non-target. `I.performed`/`I.authoredOn` demand those fields on `Mixed`,
    // and `Mixed`'s bare ref inside the union does NOT escalate to needsBody (it is the handle carrier).
    val homogeneous = ref("Homogeneous")
    val m = analyze(lib(
      edef("Mixed", mixed),
      edef("Consumer", Query(
        sources = Seq(QuerySource("Base", ref("Homogeneous"))),
        where = None, returnExpr = Some(AliasRef("Base")),
        relationships = Seq(RelationshipClause("I", Union(ref("Mixed"), homogeneous),
          And(
            Property(Some(Property(None, Some("I"), "performed")), None, "end"),
            Property(None, Some("I"), "authoredOn")),
          negated = false))))))
    assert(m("Mixed").fields == Set("performed", "authoredOn"))
    assert(!m("Mixed").needsBody, "a union-carried target navigated field-locally needs identity, not a body")
  }
}
