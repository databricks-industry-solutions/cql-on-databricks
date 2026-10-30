package com.databricks.cql.passes

import com.databricks.cql.ir._

import scala.collection.mutable

/** ARCH-RESOURCE-HANDLE step 1: STATIC demand analysis over the define DAG. For each
  * heterogeneous-`Choice` LIST define — result type `List<Choice<members>>` with a resource
  * (`NamedType`) member, the body-bearing encoding whose per-arm full-body struct blows the
  * analysis-time plan up (`LANG-CHOICE-RESOURCE`, blocker #8) — compute the DEMAND: the set of
  * top-level element fields any consumer NAVIGATES, plus whether some consumer needs a whole
  * rehydrated BODY.
  *
  * The handle then carries only `{__choice, identity, <demand set>}` instead of every arm's whole
  * body (`FrameLowering.tagChoiceArms`); a whole body is rehydrated by a late per-type identity
  * join ONLY where `needsBody` (`DATA-EXPORT`). Because the result-frame export renders a resource
  * list by `__resource_type`/`__resource_id` IDENTITY (never by struct shape, `ResourceStruct`), a
  * define merely exported "whole" needs identity only — NOT a body — so `needsBody` is rare.
  *
  * Model-typed and pure (no Spark), so it preserves `ARCH-NO-RUNTIME-SCHEMA`/`TYPE-OFFLINE-COMPILE`:
  * it narrows WHICH fields the offline model spells out, not the mechanism.
  *
  * CONSERVATIVE: any consumption it cannot prove is field-local navigation, a pure reduction
  * (`exists`/`Count`/`in`/`includes`), or an identity export escalates to `needsBody = true` (carry
  * + rehydrate the whole body — correct but unpruned) rather than prune a field and risk a runtime
  * `FIELD_NOT_FOUND` (CORE-NO-SILENT-MISLOWER).
  *
  * SCOPE: keyed by DEFINE NAME — a het-`Choice` value bound to a query/relationship alias through a
  * direct `ExpressionRef` to such a define, OR through a `union` of such
  * (`with ("Interventions Ordered" union "Interventions Performed") I`, where `Ordered`
  * is a het-`Choice` target and `Performed` a homogeneous non-target): a navigation on the union alias
  * demands the field on every target arm, and the bare target ref inside the union is a handle carrier,
  * not a whole-body escape. The union-SITE realization (the with-clause handle) is threaded into
  * `QueryLowering` separately; this pass supplies its demand set. */
object ChoiceDemand {

  /** The demand on one het-`Choice` list define. `fields` are the top-level element field names any
    * consumer navigates (the handle carries exactly these, each with its full model-derived type).
    * `needsBody` requests a rehydrated whole body at the boundary (a consumption not proven
    * field-local). Identity columns are ALWAYS carried (cheap), so they are not tracked here. */
  final case class Demand(fields: Set[String], needsBody: Boolean)

  /** Demand per het-`Choice` list define name. Empty when the library has no such define. */
  def analyze(lib: Library, typeOf: Expr => CqlType): Map[String, Demand] = {
    val defines = lib.statements.collect { case e: ExpressionDef => e }
    val targets: Set[String] =
      defines.iterator.filter(d => isHetChoiceList(typeOf(d.expression))).map(_.name).toSet
    if (targets.isEmpty) return Map.empty

    val fields    = mutable.Map.empty[String, mutable.LinkedHashSet[String]]
    val needsBody = mutable.Set.empty[String]
    targets.foreach(t => fields(t) = mutable.LinkedHashSet.empty[String])

    // A source/relationship expr that binds an alias DIRECTLY to a target define — through the casts
    // the translator inserts around a heterogeneous source (`As`/`Convert` are transparent to the
    // binding). Returns the target name, or None.
    def sourceTarget(e: Expr): Option[String] = e match {
      case ExpressionRef(n, _) if targets.contains(n) => Some(n)
      case As(op, _, _, _)                            => sourceTarget(op)
      case Convert(op, _)                             => sourceTarget(op)
      case _                                          => None
    }

    // The SET of target defines an alias binds to — a single carrier (`sourceTarget`), OR a `union` of
    // carriers (`with ("Interventions Ordered" union "Interventions Performed") I`,
    // where `Ordered` is a het-`Choice` target and `Performed` is a homogeneous non-target). A navigation on
    // the alias demands the field on EVERY target arm; a non-target arm contributes nothing. Empty ⇒ not a
    // target-carrying binding (walk the expr normally). Recognizing the union binding is what stops the bare
    // target ref inside it from escalating to `needsBody` (it is a handle carrier, not a whole-body escape).
    def bindTargets(e: Expr): Set[String] = e match {
      case ExpressionRef(n, _) if targets.contains(n) => Set(n)
      case As(op, _, _, _)                            => bindTargets(op)
      case Convert(op, _)                             => bindTargets(op)
      case Union(a, b)                                => bindTargets(a) ++ bindTargets(b)
      case _                                          => Set.empty
    }

    // The query alias a Property is rooted at (scope-rooted `Property(None, Some(a), p)` OR the
    // `bindScopes`-rewritten source-rooted `Property(Some(AliasRef(a)), None, p)`), or None.
    def propRoot(p: Property): Option[String] = p.scope.orElse(p.source.collect {
      case AliasRef(a) => a
    })

    // Recurse, carrying the alias→targets bindings in scope. `aliasEnv` maps ONLY aliases bound to one or
    // more target defines (a `union` binding maps to several); other aliases are irrelevant to the demand.
    def walk(node: IrNode, aliasEnv: Map[String, Set[String]]): Unit = node match {
      case q: Query =>
        // Bindings introduced by THIS query: sources and relationship aliases whose expr carries a
        // target (directly or through a `union`). The binding exprs themselves are evaluated in the OUTER
        // scope; recurse into a NON-binding source expr (it may reference other targets or nest a query),
        // but a binding source expr is consumed here (a target ref — even inside a `union` — is NOT a
        // whole-body escape, it is the handle carrier), so it is not walked as a generic ref.
        val srcBinds = q.sources.flatMap(s => { val t = bindTargets(s.expr); if (t.nonEmpty) Some(s.alias -> t) else None })
        val relBinds = q.relationships.flatMap(r => { val t = bindTargets(r.expr); if (t.nonEmpty) Some(r.alias -> t) else None })
        val newEnv   = aliasEnv ++ srcBinds ++ relBinds

        q.sources.foreach(s => if (bindTargets(s.expr).isEmpty) walk(s.expr, aliasEnv))
        q.relationships.foreach { r =>
          if (bindTargets(r.expr).isEmpty) walk(r.expr, aliasEnv)
          walk(r.suchThat, newEnv)
        }
        q.where.foreach(walk(_, newEnv))
        q.returnExpr.foreach(walk(_, newEnv))
        q.sort.foreach(_.expr.foreach(walk(_, newEnv)))
        q.aggregate.foreach { a => walk(a.starting, newEnv); walk(a.body, newEnv) }

      case p @ Property(_, _, path) if propRoot(p).exists(aliasEnv.contains) =>
        // Navigation rooted directly at a bound alias: demand its FIRST path segment (the top-level
        // element field) on EVERY target the alias binds to (a `union` alias binds to several). A
        // dotted/nested path navigates WITHIN that field, still carried whole.
        val first = path.split('.').headOption.filter(_.nonEmpty)
        first.foreach(f => aliasEnv(propRoot(p).get).foreach(t => fields(t) += f))
        // Do not descend into `source` here — an `AliasRef(a)` root is the carrier, not an escape.

      case AliasRef(a) if aliasEnv.contains(a) =>
        // The WHOLE element used directly (not as a navigation root) — a body escape for every target.
        aliasEnv(a).foreach(needsBody += _)

      // Pure reductions over a target ref read cardinality/keys only (identity), never a field — do
      // not escalate to body. (`In`/`Includes` only when the target is the LIST side.)
      case Exists(op) if sourceTarget(op).isDefined                  => // identity only
      case Count(op) if sourceTarget(op).isDefined                   => // identity only
      case In(_, list) if sourceTarget(list).isDefined               => // identity only
      case Includes(_, right) if sourceTarget(right).isDefined       => // identity only

      // A direct reference to a target in any OTHER position (returned whole, an unclassified
      // operator, a First/Last/SingletonFrom extraction, …) conservatively needs a body.
      case ExpressionRef(n, _) if targets.contains(n) => needsBody += n

      case other => other.children.foreach(walk(_, aliasEnv))
    }

    defines.foreach(d => walk(d.expression, Map.empty[String, Set[String]]))

    targets.iterator.map { t =>
      t -> Demand(fields = fields(t).toSet, needsBody = needsBody.contains(t))
    }.toMap
  }

  /** A result type the handle encoding prunes: `List<Choice<members>>` with at least one resource
    * (`NamedType`) member — the whole-body arm that drives the type-width blow-up. A homogeneous list
    * or a scalar-only Choice is not body-bearing and is left unchanged. */
  private def isHetChoiceList(t: CqlType): Boolean = t match {
    case CqlType.ListType(CqlType.ChoiceType(members)) =>
      members.exists { case _: CqlType.NamedType => true; case _ => false }
    case _ => false
  }
}
