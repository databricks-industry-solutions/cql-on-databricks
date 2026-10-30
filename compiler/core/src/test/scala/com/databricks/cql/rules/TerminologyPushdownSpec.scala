package com.databricks.cql.rules

import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Isolated rule test for the terminology retrieve pushdown (ARCH-PUSHDOWN, CONF-GATE-SELFDIFF:
  * "every new optimizer rule ships with an isolated rule test"). The result-preservation of the
  * rewrite is gated end-to-end by the optimized-vs-unoptimized self-diff over `FhirValueSetWhere`;
  * this spec pins the rewrite SHAPE (the `[X] where code in VS` → `[X: VS]` fold) and its safety
  * properties (idempotence, residual preservation, no over-firing). */
@RunWith(classOf[JUnitRunner])
class TerminologyPushdownSpec extends AnyFunSuite {

  private val conditionType = "{http://hl7.org/fhir}Condition"
  private def retrieve(codes: Option[Expr] = None, codeProp: Option[String] = None) =
    Retrieve(conditionType, None, codeProp, codes, None, None)
  // `C.code in "VS"` as the translator emits it: InValueSet over a FHIRHelpers.ToConcept(scope nav).
  private def memberOf(alias: String, vs: String) =
    InValueSet(FunctionRef("ToConcept", Some("FHIRHelpers"), Seq(Property(None, Some(alias), "code"))),
      ValueSetRef(vs, None))
  private def query(where: Option[Expr], src: Expr = retrieve(), ret: Option[Expr] = Some(AliasRef("C"))) =
    Query(Seq(QuerySource("C", src)), where, ret)

  private def apply1(q: Query): Query = TerminologyPushdown.apply(q).asInstanceOf[Query]

  test("folds a membership where conjunct into the retrieve source and drops the conjunct") {
    val out = apply1(query(Some(memberOf("C", "Diabetes"))))
    val src = out.sources.head.expr.asInstanceOf[Retrieve]
    assert(src.codeProperty.contains("code"))
    assert(src.codes.contains(ValueSetRef("Diabetes", None)))
    assert(out.where.isEmpty, "the folded membership conjunct is removed (exact fold)")
  }

  test("idempotent: a second application is a no-op (an already-filtered source is not re-folded)") {
    val once = apply1(query(Some(memberOf("C", "Diabetes"))))
    val twice = TerminologyPushdown.apply(once)
    assert(twice eq once, "no change on the second pass → the FixedPoint batch converges")
  }

  test("a non-membership where is left untouched") {
    val q = query(Some(Equal(Property(None, Some("C"), "status"), Literal("String", Some("active")))))
    assert(TerminologyPushdown.apply(q) eq q)
  }

  test("multi-conjunct where: folds the membership, keeps the residual conjunct as the where") {
    val residual = Equal(Property(None, Some("C"), "status"), Literal("String", Some("active")))
    val out = apply1(query(Some(And(memberOf("C", "Diabetes"), residual))))
    assert(out.sources.head.expr.asInstanceOf[Retrieve].codes.contains(ValueSetRef("Diabetes", None)))
    assert(out.where.contains(residual), "the non-membership conjunct stays in the where")
  }

  test("does not fold when the retrieve already carries a code filter (no double-fold)") {
    val filtered = retrieve(codes = Some(ValueSetRef("Diabetes", None)), codeProp = Some("code"))
    val q = query(Some(memberOf("C", "Other")), src = filtered)
    assert(TerminologyPushdown.apply(q) eq q)
  }
}
