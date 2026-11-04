package com.databricks.cql.it

import com.databricks.cql.sema.{FhirModel, FhirShape, ResourceShape}

/** DATA-MODEL-SUBSET: every modeled resource's stock shape, the row a stock view returns (`resource` as the stock
  * struct), as the data shapes of a compile that binds the stock views without running them. */
object StockShapes {
  lazy val all: Map[String, ResourceShape.Checked] = FhirModel.resourceTypes.toSeq.map(rt =>
    rt -> ResourceShape.parse(rt, s"struct<${ResourceShape.ResourceColumn}:${FhirShape.typeText(rt)}>")).toMap
}
