package com.databricks.cql.load

import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Load (ELM JSON -> IR) and the ELM->IR->ELM round-trip, on inline ELM (no translator).
  * Covers the ELM->IR->ELM round-trip at the unit level and pins Load fidelity. */
@RunWith(classOf[JUnitRunner])
class ElmCodecSpec extends AnyFunSuite {

  private val sampleElm =
    """{"library":{
      |  "identifier":{"id":"Sample","version":"1.0.0"},
      |  "usings":{"def":[{"localIdentifier":"System","uri":"urn:hl7-org:elm-types:r1"}]},
      |  "statements":{"def":[
      |    {"type":"ExpressionDef","name":"One","context":"Unfiltered",
      |     "expression":{"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Integer","value":"1"}},
      |    {"type":"ExpressionDef","name":"Logic","context":"Unfiltered",
      |     "expression":{"type":"And","operand":[
      |        {"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Boolean","value":"true"},
      |        {"type":"ExpressionRef","name":"One"}]}}
      |  ]}
      |}}""".stripMargin

  test("Load lifts a library and its statements into the IR") {
    val lib = ElmCodec.loadString(sampleElm)
    assert(lib.id == "Sample")
    assert(lib.version.contains("1.0.0"))
    assert(lib.statements.size == 2)
    val one = lib.statements.collectFirst { case e: ExpressionDef if e.name == "One" => e }.get
    assert(one.expression == Literal("{urn:hl7-org:elm-types:r1}Integer", Some("1")))
    val logic = lib.statements.collectFirst { case e: ExpressionDef if e.name == "Logic" => e }.get
    assert(logic.expression.isInstanceOf[And])
  }

  test("ELM -> IR -> ELM -> IR is a fixed point (round-trip invariant)") {
    val ir1 = ElmCodec.loadString(sampleElm)
    val elm2 = ElmCodec.emitString(ir1)
    val ir2 = ElmCodec.loadString(elm2)
    assert(ir1 == ir2)
  }

  test("an unmodeled ELM node lifts to UnknownExpr and round-trips losslessly") {
    val elm =
      """{"library":{"identifier":{"id":"U","version":"1"},
        | "statements":{"def":[
        |   {"type":"ExpressionDef","name":"Tz","context":"Unfiltered",
        |    "expression":{"type":"TimezoneOffsetFrom","operand":{"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Decimal","value":"3.7"}}}
        | ]}}}""".stripMargin
    val ir1 = ElmCodec.loadString(elm)
    val unknown = ir1.collect { case u: UnknownExpr => u }
    assert(unknown.size == 1, "TimezoneOffsetFrom is not modeled and must become an UnknownExpr")
    assert(unknown.head.elmType == "TimezoneOffsetFrom")
    // round-trips losslessly
    val ir2 = ElmCodec.loadString(ElmCodec.emitString(ir1))
    assert(ir1 == ir2)
  }

  test("Load is total: malformed ELM fails loudly, never silently") {
    assertThrows[com.databricks.cql.error.ElmLoadError] {
      ElmCodec.loadString("""{"not a library":true}""")
    }
  }

  // LANG-INSTANCE / LANG-QUERY-SORT (a front-end gap): Instance + IdentifierRef load as kernel
  // nodes (not UnknownExpr) and round-trip losslessly.
  test("ELM Instance loads as InstanceExpr and round-trips") {
    val elm =
      """{"library":{"identifier":{"id":"I","version":"1"},
        | "statements":{"def":[
        |   {"type":"ExpressionDef","name":"Q","context":"Unfiltered",
        |    "expression":{"type":"Instance","classType":"{urn:hl7-org:elm-types:r1}Quantity",
        |     "element":[
        |       {"name":"value","value":{"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Decimal","value":"5"}},
        |       {"name":"unit","value":{"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}String","value":"mg"}}]}}
        | ]}}}""".stripMargin
    val ir1 = ElmCodec.loadString(elm)
    assert(ir1.collect { case _: UnknownExpr => 1 }.isEmpty, "Instance must not be UnknownExpr")
    val inst = ir1.collect { case i: InstanceExpr => i }.head
    assert(inst.classType == "{urn:hl7-org:elm-types:r1}Quantity")
    assert(inst.elements.map(_.name) == Seq("value", "unit"))
    assert(ElmCodec.loadString(ElmCodec.emitString(ir1)) == ir1)
  }

  // LANG-AGGREGATE-FUNCTIONS: each aggregate loads from its `source` into one node and round-trips; a `path`
  // (never emitted for CQL) is refused rather than dropped.
  test("ELM aggregate functions load as AggregateFunction and round-trip") {
    def lib(expr: String) =
      s"""{"library":{"identifier":{"id":"A","version":"1"},"statements":{"def":[
         |  {"type":"ExpressionDef","name":"X","context":"Unfiltered","expression":$expr}]}}}""".stripMargin
    val list = """{"type":"List","element":[{"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Integer","value":"1"}]}"""
    AggregateOp.all.foreach { op =>
      val ir1 = ElmCodec.loadString(lib(s"""{"type":"${op.elmType}","source":$list}"""))
      assert(ir1.collect { case a: AggregateFunction => a.op }.toList == List(op))
      assert(ElmCodec.loadString(ElmCodec.emitString(ir1)) == ir1)
    }
    assertThrows[com.databricks.cql.error.ElmLoadError] {
      ElmCodec.loadString(lib(s"""{"type":"Sum","source":$list,"path":"value"}"""))
    }
  }

  test("ELM IdentifierRef loads as IdentifierRef and round-trips") {
    val elm =
      """{"library":{"identifier":{"id":"R","version":"1"},
        | "statements":{"def":[
        |   {"type":"ExpressionDef","name":"E","context":"Unfiltered",
        |    "expression":{"type":"IdentifierRef","name":"effective"}}
        | ]}}}""".stripMargin
    val ir1 = ElmCodec.loadString(elm)
    assert(ir1.collect { case _: UnknownExpr => 1 }.isEmpty, "IdentifierRef must not be UnknownExpr")
    assert(ir1.collect { case r: IdentifierRef => r.name }.headOption.contains("effective"))
    assert(ElmCodec.loadString(ElmCodec.emitString(ir1)) == ir1)
  }

  // LANG-QUERY-LET: a query `let` clause loads into Query.lets (dependency-ordered) and round-trips.
  test("ELM query let clause loads into Query.lets and round-trips") {
    val elm =
      """{"library":{"identifier":{"id":"L","version":"1"},
        | "statements":{"def":[
        |   {"type":"ExpressionDef","name":"E","context":"Unfiltered",
        |    "expression":{"type":"Query",
        |     "source":[{"alias":"N","expression":{"type":"List","element":[
        |        {"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Integer","value":"1"}]}}],
        |     "let":[
        |       {"identifier":"doubled","expression":{"type":"Multiply","operand":[
        |          {"type":"AliasRef","name":"N"},
        |          {"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Integer","value":"2"}]}},
        |       {"identifier":"plusOne","expression":{"type":"Add","operand":[
        |          {"type":"QueryLetRef","name":"doubled"},
        |          {"type":"Literal","valueType":"{urn:hl7-org:elm-types:r1}Integer","value":"1"}]}}],
        |     "return":{"expression":{"type":"QueryLetRef","name":"plusOne"}}}}
        | ]}}}""".stripMargin
    val ir1 = ElmCodec.loadString(elm)
    val q = ir1.collect { case q: Query => q }.head
    assert(q.lets.map(_.identifier) == Seq("doubled", "plusOne"))
    assert(ElmCodec.loadString(ElmCodec.emitString(ir1)) == ir1)
  }
}
