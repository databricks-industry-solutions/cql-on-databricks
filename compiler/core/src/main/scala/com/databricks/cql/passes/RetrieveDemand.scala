package com.databricks.cql.passes

import com.databricks.cql.ir._
import com.databricks.cql.sema.FhirModel

import scala.collection.mutable

/** Demand-driven projection pushdown analysis (`ARCH-RETRIEVE-PROJECT`, the general form of
  * `ARCH-RESOURCE-HANDLE`): for each FHIR resource TYPE a retrieve produces, compute which top-level
  * physical fields are actually NAVIGATED anywhere in the (linked, normalized) library — the demand
  * set — so a retrieve's `__resource` struct can be built with ONLY those fields (+ identity lineage +
  * the scan-filter fields) instead of the whole ~40-top-level / ~1000–1500-leaf body.
  *
  * Keyed by resource type (not per-occurrence): a type's `__resource` view is shared, so the union of
  * every consumer's navigation is the safe projection. Runs over the SAME normalized library
  * `FrameLowering` lowers, so fluent-function navigations (e.g., `.isFinished()` → `R.status`) are
  * already inlined as explicit `Property` nodes.
  *
  * Pure and model-typed (`FhirModel` only, no Spark) — preserves `ARCH-NO-RUNTIME-SCHEMA` /
  * `TYPE-OFFLINE-COMPILE`; it narrows WHICH modeled fields are spelled out, not the mechanism.
  *
  * CONSERVATIVE: a type escalates to [[Full]] (keep the whole body) whenever the analysis cannot prove
  * every use is a field navigation, a reduction, or an identity export — e.g. a bare whole-resource
  * value flowing into an untracked position. Pruning a field that is actually navigated would be a
  * runtime `FIELD_NOT_FOUND` (CORE-NO-SILENT-MISLOWER), so ambiguity always widens, never narrows. A
  * `value[x]` navigation expands to ALL of that base's physical slots (`FhirModel.choiceSlots`), since
  * the slot the value lands in is per-row. */
object RetrieveDemand {

  /** The projection demand on one resource type. [[Full]] keeps the whole modeled body (conservative /
    * un-prunable). [[Narrow]] keeps only `fields` (top-level PHYSICAL field names) — identity lineage
    * (`__resource_type`/`__resource_id`) is always added by the struct builder, so it is not tracked. */
  sealed trait Demand
  case object Full extends Demand
  final case class Narrow(fields: Set[String]) extends Demand

  /** Demand per resource type name (localized, e.g. "Observation"). A type with no retrieve / no
    * navigation does not appear. */
  def analyze(lib: Library, typeOf: Expr => CqlType): Map[String, Demand] = {
    val defines = lib.statements.collect { case e: ExpressionDef => e }

    val fields  = mutable.Map.empty[String, mutable.LinkedHashSet[String]]
    val full    = mutable.Set.empty[String]
    def demandType(t: String): mutable.LinkedHashSet[String] =
      fields.getOrElseUpdate(t, mutable.LinkedHashSet.empty[String])

    // The resource type an expression's ELEMENT is (a retrieve, a list of resources, or a define /
    // union that resolves to one) — or None for a scalar / heterogeneous-Choice / unmodeled result
    // (a het-Choice union body is discriminated per arm downstream; a scalar has no body to prune).
    def elemResourceType(e: Expr): Option[String] = typeOf(e) match {
      case CqlType.NamedType(n)                        => Some(FhirModel.localName(n)).filter(FhirModel.isModeled)
      case CqlType.ListType(CqlType.NamedType(n))      => Some(FhirModel.localName(n)).filter(FhirModel.isModeled)
      case _                                           => None
    }

    // Map a navigated top-level path segment to the PHYSICAL field(s) it needs on `rt`: a `value[x]`
    // choice base (`value`, `effective`, `performed`, …) expands to all its typed slots (the slot is
    // per-row); a monomorphic element is itself.
    def physicalFields(rt: String, seg: String): Seq[String] =
      if (FhirModel.isChoiceElement(seg)) {
        val slots = FhirModel.choiceSlots(rt, seg)
        if (slots.nonEmpty) slots else Seq(seg)
      } else Seq(seg)

    def record(rt: String, path: String): Unit =
      path.split('.').headOption.filter(_.nonEmpty).foreach(seg => demandType(rt) ++= physicalFields(rt, seg))

    // The query alias a Property is rooted at (scope-rooted, or the bindScopes source-rooted form).
    def propRoot(p: Property): Option[String] = p.scope.orElse(p.source.collect { case AliasRef(a) => a })

    def flattenUnion(e: Expr): Seq[Expr] = e match {
      case Union(a, b) => flattenUnion(a) ++ flattenUnion(b)
      case other       => Seq(other)
    }

    // `aliasEnv` maps a query alias to the resource type its element carries (only resource-typed
    // aliases; others are irrelevant to projection).
    def walk(node: IrNode, aliasEnv: Map[String, String]): Unit = node match {
      case q: Query =>
        val binds =
          (q.sources.flatMap(s => elemResourceType(s.expr).map(s.alias -> _)) ++
           q.relationships.flatMap(r => elemResourceType(r.expr).map(r.alias -> _)))
        val newEnv = aliasEnv ++ binds
        // ALWAYS walk the source / relationship exprs (in the OUTER scope): binding an alias does not make
        // the source opaque — a value-set/date-filtered retrieve source carries scan-filter fields
        // (codeProperty/dateProperty/periodScanFilter, recorded in the `Retrieve` case) that lowering
        // navigates even with no explicit CQL `Property`, and a nested-query source needs its own walk.
        q.sources.foreach(s => walk(s.expr, aliasEnv))
        q.relationships.foreach { r => walk(r.expr, aliasEnv); walk(r.suchThat, newEnv) }
        q.where.foreach(walk(_, newEnv))
        q.returnExpr.foreach(walk(_, newEnv))
        q.sort.foreach(_.expr.foreach(walk(_, newEnv)))
        q.aggregate.foreach { a => walk(a.starting, newEnv); walk(a.body, newEnv) }

      case p: Property if propRoot(p).exists(aliasEnv.contains) =>
        record(aliasEnv(propRoot(p).get), p.path)
        // Do not descend into an `AliasRef` carrier root; a nested-Property source is walked below.
        p.source.foreach { case _: AliasRef => (); case s => walk(s, aliasEnv) }

      case p: Property =>
        // Source-rooted navigation on any resource-typed expression (`[Encounter]` inlined, a
        // define ref, …): `elemResourceType(source)` names the type; record then recurse.
        p.source.foreach { s => elemResourceType(s).foreach(rt => record(rt, p.path)); walk(s, aliasEnv) }

      case AliasRef(a) if aliasEnv.contains(a) =>
        // Whole element used directly (not a navigation root) — a body escape; keep the full body.
        full += aliasEnv(a)

      // A `Choice`-construction cast (`As(retrieve, choiceTypes)`) marks its operand type as a
      // heterogeneous-Choice ARM: its width is owned by the Choice path (whole-body slots
      // `choiceStructOfTyped`, or a handle under ARCH-RESOURCE-HANDLE), whose `dataTypeOf` assumes the
      // FULL body. Narrowing it here would misalign `unionByName`, so leave it Full.
      case As(op, _, _, cts) if cts.nonEmpty =>
        elemResourceType(op).foreach(full += _)
        walk(op, aliasEnv)

      // A HETEROGENEOUS union (arms of different resource types) is Choice-tagged the same way — mark
      // every arm's type Full. A homogeneous union (one type, e.g. a union of encounter retrieves) is a
      // plain relation union and stays narrowable.
      case u @ Union(_, _) =>
        val armTypes = flattenUnion(u).flatMap(elemResourceType)
        if (armTypes.distinct.size > 1) full ++= armTypes
        u.children.foreach(walk(_, aliasEnv))

      // A bare retrieve returned/used whole exports by identity (needs no body). A whole resource
      // flowing elsewhere is caught as a non-root AliasRef above; retrieve nodes contribute their
      // scan-filter fields (below) but not a Full escalation.
      case r: Retrieve =>
        val rt = FhirModel.localName(r.dataType)
        if (FhirModel.isModeled(rt)) {
          r.codeProperty.foreach(cp => demandType(rt) ++= physicalFields(rt, cp.split('.').head))
          r.dateProperty.foreach(dp => demandType(rt) ++= physicalFields(rt, dp.split('.').head))
          r.periodScanFilter.foreach { case (path, _, _) => demandType(rt) ++= physicalFields(rt, path.split('.').head) }
          // LANG-RETRIEVE-PROFILE: a profile-constrained retrieve tests `meta.profile` at the scan, so
          // `meta` must survive projection pruning even though no CQL `Property` ever navigates it.
          r.profileConstraint.foreach(_ => demandType(rt) += "meta")
        }
        r.children.foreach(walk(_, aliasEnv))

      case other => other.children.foreach(walk(_, aliasEnv))
    }

    defines.foreach(d => walk(d.expression, Map.empty))

    // Assemble: a type marked Full stays Full; otherwise Narrow over its collected fields (possibly
    // empty → identity-only, valid for an identity-export-only retrieve).
    (fields.keySet ++ full).iterator.map { rt =>
      rt -> (if (full.contains(rt)) Full else Narrow(fields.get(rt).map(_.toSet).getOrElse(Set.empty)))
    }.toMap
  }
}
