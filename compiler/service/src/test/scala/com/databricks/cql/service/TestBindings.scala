package com.databricks.cql.service

import com.databricks.cql.env.{DataBinding, RetrieveRelation, TerminologyBinding}

/** BIND-CALL-SITE: the bindings a test compile binds every call site with — data by rule to `acme.data.<type>`,
  * every value set from `acme.terminology.valueset`, each with its identity (`CLIENT-RESOLVER-IDENTITY`). */
private[service] object TestBindings {
  val Data: Option[DataBinding] = Some(DataBinding(Some(DataBinding.Rule("acme.data", Some("acme.data"))), Map.empty))

  val Terminology: Option[TerminologyBinding] = Some(TerminologyBinding(
    Some(Seq(RetrieveRelation("acme.terminology.valueset", Some("acme.terminology")))), Map.empty))
}
