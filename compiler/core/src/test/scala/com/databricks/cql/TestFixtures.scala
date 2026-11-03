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

  /** DATA-MODEL-SUBSET: the stock shape of `rt`, the row a stock relation returns (its `resource` the stock struct),
    * as Spark prints its type. */
  def stockShapeText(rt: String): String = s"struct<resource:${com.databricks.cql.sema.FhirShape.typeText(rt)}>"

  /** DATA-MODEL-SUBSET: every modeled resource's stock shape, checked, as a request's data shapes. */
  lazy val stockShapes: Map[String, com.databricks.cql.sema.ResourceShape.Checked] =
    com.databricks.cql.sema.FhirModel.resourceTypes.toSeq.map(rt =>
      rt -> com.databricks.cql.sema.ResourceShape.parse(rt, stockShapeText(rt))).toMap
}
