package com.databricks.cql.service

import com.databricks.cql.sema.{FhirModel, FhirShape, ResourceShape}
import com.databricks.cql.env.{DataBinding, RetrieveRelation, TerminologyBinding}

/** BIND-CALL-SITE: the bindings a test compile binds every call site with — data by rule to `acme.data.<type>`,
  * every value set from `acme.terminology.valueset`, each with its identity (`CLIENT-RESOLVER-IDENTITY`). */
private[service] object TestBindings {
  /** DATA-MODEL-SUBSET: every modeled resource's stock shape, as the stock relations report it. */
  lazy val StockShapes: Map[String, ResourceShape.Checked] = FhirModel.resourceTypes.toSeq.map(rt =>
    rt -> ResourceShape.parse(rt, s"struct<${ResourceShape.ResourceColumn}:${FhirShape.typeText(rt)}>")).toMap

  val Data: Option[DataBinding] =
    Some(DataBinding(Some(DataBinding.Rule("acme.data", Some("acme.data"))), Map.empty, StockShapes))

  val Terminology: Option[TerminologyBinding] = Some(TerminologyBinding(
    Some(Seq(RetrieveRelation("acme.terminology.valueset", Some("acme.terminology")))), Map.empty))
}
