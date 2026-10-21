package com.databricks.cql

import com.databricks.cql.ir._

/** Small IR builders for unit tests. */
object TestFixtures {
  def lib(statements: Statement*): Library =
    Library("Test", Some("1.0.0"), Nil, Nil, Nil, Nil, Nil, Nil, Nil, Nil, statements.toList)

  def edef(name: String, e: Expr, context: String = "Unfiltered"): ExpressionDef =
    ExpressionDef(name, Some(context), e)

  def int(v: String): Literal = Literal("{urn:hl7-org:elm-types:r1}Integer", Some(v))
  def bool(v: Boolean): Literal = Literal("{urn:hl7-org:elm-types:r1}Boolean", Some(v.toString))
  def ref(name: String): ExpressionRef = ExpressionRef(name, None)
}
