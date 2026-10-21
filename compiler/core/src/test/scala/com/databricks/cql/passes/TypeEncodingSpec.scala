package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.error.TypeError
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.ir._
import com.databricks.cql.lower.Encoding
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The recomputable Type/Encoding attribute (ARCH-PIPELINE, ARCH-IR). Proves typing is
  * correct on the existing scalar/list/query kernel before Phase 3 interval/quantity
  * lowering builds on it (PROC-TDD). */
@RunWith(classOf[JUnitRunner])
class TypeEncodingSpec extends AnyFunSuite {

  private val ctx = TypeEncoding.TypeCtx(Map.empty, Map.empty)
  private def typeOf(e: Expr, c: TypeEncoding.TypeCtx = ctx): CqlType = TypeEncoding.typeOf(e, c)

  private def quantity(v: Int, unit: Option[String]) = QuantityExpr(BigDecimal(v), unit)
  private def dec(v: String) = Literal("{urn:hl7-org:elm-types:r1}Decimal", Some(v))
  private def str(v: String) = Literal("{urn:hl7-org:elm-types:r1}String", Some(v))

  // -- CqlType.parse --
  test("parse accepts qualified, System-dotted, and bare type names") {
    assert(CqlType.parse("{urn:hl7-org:elm-types:r1}Integer") == IntegerType)
    assert(CqlType.parse("System.Decimal") == DecimalType)
    assert(CqlType.parse("Boolean") == BooleanType)
    assert(CqlType.parse("Quantity") == QuantityType(None))
    assert(CqlType.parse("FHIR.Condition") == NamedType("Condition"))
  }

  // -- CqlType.join (lattice) --
  test("join widens numerics, treats Any as identity, and falls to Any on a mismatch") {
    assert(CqlType.join(IntegerType, DecimalType) == DecimalType)
    assert(CqlType.join(IntegerType, LongType) == LongType)
    assert(CqlType.join(IntegerType, AnyType) == IntegerType)
    assert(CqlType.join(AnyType, BooleanType) == BooleanType)
    assert(CqlType.join(BooleanType, StringType) == AnyType)
    assert(CqlType.join(ListType(IntegerType), ListType(DecimalType)) == ListType(DecimalType))
  }

  // -- Leaves --
  test("literals, quantities, and null type from their declared form") {
    assert(typeOf(int("1")) == IntegerType)
    assert(typeOf(bool(true)) == BooleanType)
    assert(typeOf(str("x")) == StringType)
    assert(typeOf(quantity(5, Some("mg"))) == QuantityType(Some("mg")))
    assert(typeOf(NullExpr()) == AnyType)
  }

  // -- ELM Instance (LANG-INSTANCE) — types as its declared classType, not a Tuple --
  test("Instance types as its declared System/class type") {
    val q = InstanceExpr("{urn:hl7-org:elm-types:r1}Quantity",
      Seq(TupleElement("value", dec("5")), TupleElement("unit", str("mg"))))
    assert(typeOf(q) == QuantityType(None))
    assert(typeOf(InstanceExpr("{urn:hl7-org:elm-types:r1}Ratio", Nil)) == RatioType)
    assert(typeOf(InstanceExpr("{http://hl7.org/fhir}Reference",
      Seq(TupleElement("reference", str("r"))))) == NamedType("Reference"))
  }

  // -- Boolean-valued operators --
  test("logical, comparison, and membership operators are Boolean") {
    assert(typeOf(And(bool(true), bool(false))) == BooleanType)
    assert(typeOf(Equal(int("1"), int("2"))) == BooleanType)
    assert(typeOf(Less(int("1"), int("2"))) == BooleanType)
    assert(typeOf(IsNull(int("1"))) == BooleanType)
    assert(typeOf(Exists(ListExpr(Seq(int("1"))))) == BooleanType)
  }

  // -- Arithmetic --
  test("arithmetic widens operands; CQL division is always Decimal") {
    assert(typeOf(Add(int("1"), int("2"))) == IntegerType)
    assert(typeOf(Add(int("1"), dec("2.0"))) == DecimalType)
    assert(typeOf(Divide(int("1"), int("2"))) == DecimalType)
    assert(typeOf(Negate(int("1"))) == IntegerType)
  }

  // -- Conditionals join their branches --
  test("If/Coalesce result is the join of branch types") {
    assert(typeOf(If(bool(true), int("1"), dec("2.0"))) == DecimalType)
    assert(typeOf(If(bool(true), int("1"), NullExpr())) == IntegerType)
    assert(typeOf(Coalesce(Seq(NullExpr(), int("1")))) == IntegerType)
  }

  // -- Lists & intervals --
  test("lists, element accessors, and intervals type structurally") {
    assert(typeOf(ListExpr(Seq(int("1"), int("2")))) == ListType(IntegerType))
    assert(typeOf(First(ListExpr(Seq(int("1"))))) == IntegerType)
    assert(typeOf(Count(ListExpr(Seq(int("1"))))) == IntegerType)
    assert(typeOf(IntervalExpr(Some(int("1")), Some(int("10")), lowClosed = true, highClosed = true))
      == IntervalType(IntegerType))
  }

  // -- Phase 3: interval operators + quantity arithmetic (LANG-INTERVAL/LANG-QUANTITY) --
  private def iv(lo: Int, hi: Int) =
    IntervalExpr(Some(int(lo.toString)), Some(int(hi.toString)), lowClosed = true, highClosed = true)

  test("interval accessors yield the point type; Allen relations are Boolean") {
    assert(typeOf(Start(iv(1, 10))) == IntegerType)
    assert(typeOf(End(iv(1, 10))) == IntegerType)
    assert(typeOf(Width(iv(1, 10))) == IntegerType)
    assert(typeOf(Before(iv(1, 2), iv(5, 6))) == BooleanType)
    assert(typeOf(Overlaps(iv(1, 5), iv(3, 8))) == BooleanType)
    assert(typeOf(Includes(iv(1, 10), iv(3, 4))) == BooleanType)
    assert(typeOf(IncludedIn(iv(3, 4), iv(1, 10))) == BooleanType)
    assert(typeOf(In(int("5"), iv(1, 10))) == BooleanType)
  }

  test("Date/DateTime carry the precision implied by their component count") {
    import CqlType.DateTimePrecision._
    assert(typeOf(DateExpr(Seq(int("2012")))) == DateType(Some(Year)))
    assert(typeOf(DateExpr(Seq(int("2012"), int("6")))) == DateType(Some(Month)))
    assert(typeOf(DateExpr(Seq(int("2012"), int("1"), int("15")))) == DateType(Some(Day)))
    assert(typeOf(DateTimeExpr(Seq(int("2012"), int("1"), int("15"), int("10"), int("30"), int("0"))))
      == DateTimeType(Some(Second)))
    // temporal comparison + a date interval type structurally
    assert(typeOf(Less(DateExpr(Seq(int("2012"))), DateExpr(Seq(int("2013"))))) == BooleanType)
    assert(typeOf(IntervalExpr(Some(DateExpr(Seq(int("2012"), int("1"), int("1")))),
      Some(DateExpr(Seq(int("2012"), int("12"), int("31")))), lowClosed = true, highClosed = true))
      == IntervalType(DateType(Some(Day))))
  }

  test("quantity arithmetic keeps a quantity result; comparison is Boolean") {
    assert(typeOf(Add(quantity(1, Some("kg")), quantity(500, Some("g")))) == QuantityType(Some("kg")))
    assert(typeOf(Subtract(quantity(2, Some("g")), quantity(1, Some("mg")))) == QuantityType(Some("g")))
    assert(typeOf(Equal(quantity(1, Some("kg")), quantity(1000, Some("g")))) == BooleanType)
    assert(typeOf(Less(quantity(500, Some("mg")), quantity(1, Some("g")))) == BooleanType)
  }

  test("As to a ChoiceTypeSpecifier constructs a Choice; a named As/Is project/test (LANG-CHOICE)") {
    val toChoice = As(int("1"), "", strict = false, choiceTypes = Seq("Integer", "String"))
    assert(typeOf(toChoice) == ChoiceType(Seq(IntegerType, StringType)))
    // a heterogeneous if (branches cast to the choice) types as the Choice
    val hetIf = If(bool(true), toChoice, As(str("x"), "", strict = false, choiceTypes = Seq("Integer", "String")))
    assert(typeOf(hetIf) == ChoiceType(Seq(IntegerType, StringType)))
    assert(typeOf(As(hetIf, "System.Integer", strict = false)) == IntegerType) // projection
    assert(typeOf(Is(hetIf, "System.String")) == BooleanType)
  }

  // -- Conversions carry their target type --
  test("Convert/As adopt the declared target type") {
    assert(typeOf(Convert(int("1"), "{urn:hl7-org:elm-types:r1}Decimal")) == DecimalType)
    assert(typeOf(As(int("1"), "System.Long", strict = false)) == LongType)
  }

  // -- Inherited context: define refs, parameters, query aliases --
  test("ExpressionRef resolves to the referenced define's result type") {
    val c = TypeEncoding.ctxOf(lib(edef("A", Add(int("1"), int("2"))), edef("B", ref("A"))))
    assert(typeOf(ref("B"), c) == IntegerType)
  }

  test("ParameterRef resolves to its declared type") {
    val c = TypeEncoding.TypeCtx(Map("Threshold" -> DecimalType), Map.empty)
    assert(typeOf(ParameterRef("Threshold", None), c) == DecimalType)
  }

  test("a query binds its source alias and types the return shape") {
    val q = Query(Seq(QuerySource("E", ListExpr(Seq(int("1"), int("2"))))), None, Some(AliasRef("E")))
    assert(typeOf(q) == ListType(IntegerType))
  }

  test("list set-ops type as a list; aggregate types as the accumulator (LANG-QUERY/LANG-LIST)") {
    val a = ListExpr(Seq(int("1"), int("2"))); val b = ListExpr(Seq(int("3")))
    assert(typeOf(Union(a, b)) == ListType(IntegerType))
    assert(typeOf(Intersect(a, b)) == ListType(IntegerType))
    assert(typeOf(Except(a, b)) == ListType(IntegerType))
    // sort/distinct keep the list type; aggregate folds to the accumulator (Integer here)
    val sorted = Query(Seq(QuerySource("N", a)), None, Some(AliasRef("N")), sort = Seq(SortItem("asc")))
    assert(typeOf(sorted) == ListType(IntegerType))
    val agg = Query(Seq(QuerySource("N", a)), None, None,
      aggregate = Some(AggregateClause("R", int("0"), Add(QueryLetRef("R"), AliasRef("N")))))
    assert(typeOf(agg) == IntegerType)
  }

  test("a retrieve is a list of its named resource type") {
    assert(typeOf(Retrieve("Condition", None, None, None, None, None)) == ListType(NamedType("Condition")))
  }

  test("a tuple types as a record of its slots; navigation projects a slot (LANG-QUERY-MULTISOURCE)") {
    val tup = TupleExpr(Seq(TupleElement("x", int("1")), TupleElement("y", str("a"))))
    assert(typeOf(tup) == TupleType(Seq("x" -> IntegerType, "y" -> StringType)))
    assert(typeOf(Property(Some(tup), None, "x")) == IntegerType)
    assert(typeOf(Property(Some(tup), None, "missing")) == AnyType)
  }

  test("a multi-source query is a list of the returned tuple shape (LANG-QUERY-MULTISOURCE)") {
    val q = Query(
      Seq(QuerySource("X", ListExpr(Seq(int("1"), int("2")))),
          QuerySource("Y", ListExpr(Seq(str("a"))))),
      where = None,
      returnExpr = Some(TupleExpr(Seq(TupleElement("X", AliasRef("X")), TupleElement("Y", AliasRef("Y"))))))
    assert(typeOf(q) == ListType(TupleType(Seq("X" -> IntegerType, "Y" -> StringType))))
  }

  // -- Encoding (DATA-LIST-ENCODING: Relation is the default for lists; Array is a contextual
  //    boundary boxing realized in lowering, not a property of the type) --
  test("encodingOf is Relation for list types and Scalar otherwise") {
    assert(TypeEncoding.encodingOf(ListType(IntegerType)) == Encoding.Relation)
    assert(TypeEncoding.encodingOf(IntegerType) == Encoding.Scalar)
  }

  // -- Loud failure on an unmodeled node --
  test("typing an unmodeled node fails loudly (CORE-NO-SILENT-MISLOWER)") {
    val ex = intercept[TypeError](typeOf(UnknownExpr("SomeOp", "{\"type\":\"SomeOp\"}")))
    assert(ex.getMessage.contains("SomeOp"))
  }
}
