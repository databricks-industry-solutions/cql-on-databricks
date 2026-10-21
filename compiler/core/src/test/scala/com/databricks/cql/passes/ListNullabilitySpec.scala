package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Static list-nullability (ARCH-LIST-NULL-SIDECAR gate 1, LANG-LIST-NULL-STATE): a list-valued body
  * is nullable iff it can evaluate to null rather than empty-or-more. Proves the attribute before the
  * sidecar consumes it (PROC-TDD). */
@RunWith(classOf[JUnitRunner])
class ListNullabilitySpec extends AnyFunSuite {

  private val ctx = ListNullability.NullCtx(Map.empty)
  private def nullable(e: Expr, c: ListNullability.NullCtx = ctx): Boolean =
    ListNullability.isNullableList(e, c)

  private def enc: Retrieve = Retrieve("Encounter", None, None, None, None, None)
  private def cond: Retrieve = Retrieve("Condition", None, None, None, None, None)
  private val nullInt = Literal("{urn:hl7-org:elm-types:r1}Integer", None)

  // -- Null introducers --
  test("a null literal and NullExpr are nullable") {
    assert(nullable(NullExpr()))
    assert(nullable(nullInt))
  }

  // -- Provably non-null list producers (LANG-LIST: empty-or-more, never null) --
  test("retrieve, union, set ops, ToList, list literal, and a query are non-nullable") {
    assert(!nullable(enc))
    assert(!nullable(Union(enc, cond)))
    assert(!nullable(Intersect(enc, cond)))
    assert(!nullable(Except(enc, cond)))
    assert(!nullable(ToList(int("1"))))
    assert(!nullable(ListExpr(Seq(int("1"), int("2")))))
    assert(!nullable(Query(Seq(QuerySource("E", enc)), None, Some(AliasRef("E")))))
  }

  // -- Conditionals: nullable iff any reachable branch is nullable --
  test("if/case are nullable iff a branch is nullable") {
    assert(nullable(If(bool(true), enc, NullExpr())))            // `if C then [Enc] else null`
    assert(!nullable(If(bool(true), enc, cond)))                 // both branches non-null lists
    assert(nullable(If(bool(true), enc, NullExpr())))            // `if C then [Enc]` (no-else → NullExpr)
    assert(nullable(Case(None, Seq(CaseItem(bool(true), enc)), NullExpr())))
    assert(!nullable(Case(None, Seq(CaseItem(bool(true), enc)), cond)))
  }

  // -- Coalesce: null only when EVERY operand can be null --
  test("Coalesce is nullable only if all operands are nullable") {
    assert(nullable(Coalesce(Seq(NullExpr(), nullInt))))
    assert(!nullable(Coalesce(Seq(NullExpr(), enc))))           // a non-null operand makes it non-null
  }

  // -- A cast does not introduce null; a null operand stays null --
  test("As follows its operand's nullability") {
    assert(nullable(As(NullExpr(), "List<Encounter>", strict = false)))
    assert(!nullable(As(Union(enc, cond), "List<Encounter>", strict = false)))
  }

  // -- References: resolve in-library; conservatively nullable when unresolvable --
  test("ExpressionRef resolves to the referenced define's nullability") {
    val c = ListNullability.ctxOf(lib(
      edef("A", If(bool(true), enc, NullExpr())),
      edef("B", ref("A")),
      edef("C", enc)))
    assert(nullable(ref("A"), c))   // A is nullable
    assert(nullable(ref("B"), c))   // B inherits A's nullability
    assert(!nullable(ref("C"), c))  // C is a plain retrieve
  }

  test("an unresolved / cross-library reference is conservatively nullable") {
    assert(nullable(ref("Missing")))                          // not in context
    assert(nullable(ExpressionRef("X", Some("OtherLib"))))    // qualified cross-library
  }

  // -- perDefine produces the per-name map FrameLowering threads --
  test("perDefine maps every define to its nullability") {
    val l = lib(
      edef("Statuses", Query(Seq(QuerySource("E", enc)), None, Some(Property(Some(AliasRef("E")), None, "status")))),
      edef("MaybeEnc", If(bool(true), enc, NullExpr())))
    val m = ListNullability.perDefine(l)
    assert(m("Statuses") == false)
    assert(m("MaybeEnc") == true)
  }
}
