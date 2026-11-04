package com.databricks.cql.env

import com.databricks.cql.sema.ResourceShape

/** BIND-RELATION, BIND-CALL-SITE: what a compile request binds one call site, or one terminology layer, to: a
  * Delta table or UC view, by name. A relation NAMES where the rows are; it never carries a field mapping, and
  * there is no function relation. The client turns a volume or DataFrame relation into a temporary view before
  * it compiles, so the compiler only ever sees a table or view name. `identity` is the identity of the resolver
  * that answered (`CLIENT-RESOLVER-IDENTITY`); a kept result's fingerprint covers it. The compiler reads no
  * catalog (`ARCH-PURITY`). */
final case class RetrieveRelation(table: String, identity: Option[String] = None)

object RetrieveRelation {

  /** Parse one relation off the wire: `{"table": name}`, with an optional `identity`. Anything else fails
    * loudly, naming what was sent. */
  def fromJson(v: ujson.Value): RetrieveRelation = {
    val o = v.objOpt.getOrElse(throw new IllegalArgumentException(
      s"a relation is {\"table\": name}, not ${ujson.write(v)} (BIND-CALL-SITE)"))
    val table = o.get("table").filterNot(_.isNull).map(_.str).getOrElse(throw new IllegalArgumentException(
      s"a relation names a table or view, not ${ujson.write(v)}; there is no function relation (BIND-RELATION)"))
    RetrieveRelation(table, o.get("identity").filterNot(_.isNull).map(_.str))
  }

  /** A list of relations, as a terminology rule or site entry carries it. */
  def listFromJson(v: ujson.Value): Seq[RetrieveRelation] = v.arrOpt.getOrElse(throw new IllegalArgumentException(
    s"a terminology entry is a list of relations, not ${ujson.write(v)} (BIND-CALL-SITE)")).map(fromJson).toSeq
}

/** BIND-CALL-SITE: how a compile request binds the data retrieves: a `rule`, the schema whose views serve every
  * type, a `sites` map from call-site id to relation, or both. For each site the map's entry wins over the rule.
  * The rule binds a retrieve of type `T` to the view `<schema>.<T in lower case>` (`DATA-ENVIRONMENT-LAYOUT`).
  *
  * `DATA-MODEL-SUBSET`: `shapes` holds, per resource type, the `__resource` type its relations return, read by
  * the caller from the bound relations (the compiler reads no catalog). Each is checked against FHIR R4 when the
  * request is read ([[ResourceShape]]). A retrieved type with no shape fails the compile. */
final case class DataBinding(rule: Option[DataBinding.Rule], sites: Map[String, RetrieveRelation],
                             shapes: Map[String, ResourceShape.Checked] = Map.empty) {

  /** The relation call site `siteId`, a retrieve of `fhirType`, reads: its map entry, else the rule's view. */
  def relationFor(siteId: String, fhirType: String): Option[RetrieveRelation] =
    sites.get(siteId).orElse(rule.map(r => RetrieveRelation(s"${r.schema}.${fhirType.toLowerCase}", r.identity)))

  /** Every identity the binding names, for a kept result's fingerprint (`CLIENT-RESOLVER-IDENTITY`). */
  def relations: Seq[RetrieveRelation] =
    rule.map(r => RetrieveRelation(r.schema, r.identity)).toSeq ++ sites.values
}

object DataBinding {
  final case class Rule(schema: String, identity: Option[String] = None)

  def fromJson(v: ujson.Value): DataBinding = {
    val o = v.objOpt.getOrElse(throw new IllegalArgumentException(
      s"data is {\"rule\": …, \"sites\": …}, not ${ujson.write(v)} (BIND-CALL-SITE)"))
    unknownKeys(o.keys, "data", Set("shapes"))
    DataBinding(
      rule = o.get("rule").filterNot(_.isNull).map { r =>
        val ro = r.obj
        Rule(ro.get("schema").filterNot(_.isNull).map(_.str).getOrElse(throw new IllegalArgumentException(
          s"a data rule names a schema, not ${ujson.write(r)} (BIND-CALL-SITE)")),
          ro.get("identity").filterNot(_.isNull).map(_.str))
      },
      sites = o.get("sites").filterNot(_.isNull).map(_.obj.map { case (id, rel) => id -> RetrieveRelation.fromJson(rel) }
        .toMap).getOrElse(Map.empty),
      shapes = o.get("shapes").filterNot(_.isNull).map { v =>
        v.objOpt.getOrElse(throw new IllegalArgumentException(
          s"data shapes are {\"<FHIR type>\": \"<Spark type>\"}, not ${ujson.write(v)} (DATA-MODEL-SUBSET)"))
          .map { case (t, ddl) =>
            val checked = ResourceShape.parse(t, ddl.strOpt.getOrElse(throw new IllegalArgumentException(
              s"the data shape for $t is a Spark type string, not ${ujson.write(ddl)} (DATA-MODEL-SUBSET)")))
            checked.resourceType -> checked
          }.toMap
      }.getOrElse(Map.empty))
  }

  private[env] def unknownKeys(keys: Iterable[String], role: String, extra: Set[String] = Set.empty): Unit = {
    val known = Set("rule", "sites") ++ extra
    val unknown = keys.filterNot(known).toSeq.sorted
    require(unknown.isEmpty,
      s"$role carries only ${known.toSeq.sorted.mkString(", ")}, not ${unknown.mkString(", ")} (BIND-CALL-SITE)")
  }
}

/** BIND-CALL-SITE: how a compile request binds value sets: a `rule`, the relations every value set is read
  * from, a `sites` map from value-set site id to relations, or both, the map winning. Each list is in precedence
  * order: a value set is read from the first relation that holds it. */
final case class TerminologyBinding(rule: Option[Seq[RetrieveRelation]], sites: Map[String, Seq[RetrieveRelation]]) {

  def relationsFor(siteId: String): Option[Seq[RetrieveRelation]] = sites.get(siteId).orElse(rule)

  def relations: Seq[RetrieveRelation] = rule.toSeq.flatten ++ sites.values.flatten
}

object TerminologyBinding {
  def fromJson(v: ujson.Value): TerminologyBinding = {
    val o = v.objOpt.getOrElse(throw new IllegalArgumentException(
      s"terminology is {\"rule\": […], \"sites\": …}, not ${ujson.write(v)} (BIND-CALL-SITE)"))
    DataBinding.unknownKeys(o.keys, "terminology")
    TerminologyBinding(
      rule = o.get("rule").filterNot(_.isNull).map(RetrieveRelation.listFromJson),
      sites = o.get("sites").filterNot(_.isNull).map(_.obj.map { case (id, rels) => id -> RetrieveRelation.listFromJson(rels) }
        .toMap).getOrElse(Map.empty))
  }
}
