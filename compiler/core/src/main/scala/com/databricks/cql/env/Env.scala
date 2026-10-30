package com.databricks.cql.env

/** The immutable Environment threaded down the IR tree (ARCH-ENV, `02` section 4).
  *
  * This is the attribute-grammar split: INHERITED attributes flow DOWN through the Env
  * (context, lexical scope chain, parameter bindings, resolved terminology handles, and
  * the frozen engine config). SYNTHESIZED attributes — the lowered plan fragment, its
  * physical type, its encoding — flow UP as return values (see lowering, Phase 1+).
  *
  * Env updates are FUNCTIONAL: hand a child Env down (`inContext`, `withAlias`), never
  * mutate. That gives correct CQL scoping, thread-safety for concurrent plan building,
  * and sound memoization. No mutable visitor fields, no globals (ARCH-ENV, ARCH-PURITY).
  */
final case class Env(
    /** The active CQL context (Patient / Practitioner / Population / …). Context is
      * extensible and NOT hardcoded (LANG-CONTEXT); it maps to a grouping key. */
    context: Context,
    /** The lexical scope chain: library symbols → query source aliases → nested lets.
      * Innermost scope is the head (ARCH-ENV). */
    scopes: List[Scope],
    /** Parameter bindings in effect (resolved once in Resolve). */
    parameters: Map[String, ParamBinding],
    /** Resolved terminology handles — broadcast value-set references bound in Resolve;
      * they survive inlining and rewrites, never re-resolved per row (LANG-TERMINOLOGY,
      * LANG-FUNCTION-TERMINOLOGY). */
    terminology: Map[String, TerminologyHandle],
    /** The frozen engine configuration (TECH-CONFIG) — flows down like any other
      * inherited attribute, keeping plan-building pure. */
    config: Config
) {

  /** Descend into a new context (e.g. crossing into Population for an aggregate). */
  def inContext(c: Context): Env = copy(context = c)

  /** Push a new lexical scope (query enters, function body inlined, `let`). */
  def pushScope(scope: Scope): Env = copy(scopes = scope :: scopes)

  /** Bind a single query source alias into a fresh scope (`[Encounter] E`). */
  def withAlias(alias: String, info: AliasInfo): Env =
    pushScope(Scope(Map(alias -> info)))

  /** Resolve an alias by walking the scope chain inner→outer (correct CQL shadowing). */
  def lookupAlias(alias: String): Option[AliasInfo] =
    scopes.iterator.flatMap(_.aliases.get(alias)).nextOption()

  def lookupParameter(name: String): Option[ParamBinding] = parameters.get(name)

  def lookupTerminology(name: String): Option[TerminologyHandle] = terminology.get(name)
}

object Env {
  /** The root Env at build start: a context, no aliases, the resolved config frozen in. */
  def root(context: Context, config: Config): Env =
    Env(context = context, scopes = Nil, parameters = Map.empty,
        terminology = Map.empty, config = config)
}

/** A CQL evaluation context. Extensible (LANG-CONTEXT): Patient/Practitioner are not
  * privileged in the type system — a context is a name plus whether it groups by a
  * per-subject key. Population/Unfiltered has no per-subject grouping. */
final case class Context(name: String, hasSubjectGrouping: Boolean)
object Context {
  val Patient: Context      = Context("Patient", hasSubjectGrouping = true)
  val Practitioner: Context = Context("Practitioner", hasSubjectGrouping = true)
  val Unfiltered: Context   = Context("Unfiltered", hasSubjectGrouping = false)
  val Population: Context    = Context("Population", hasSubjectGrouping = false)

  /** Map a context name from ELM to a Context, defaulting unknown names to a
    * subject-grouped context (extensibility seam, not a hardcode). */
  def named(name: String): Context = name match {
    case "Patient"             => Patient
    case "Practitioner"        => Practitioner
    case "Unfiltered"          => Unfiltered
    case "Population"          => Population
    case other                 => Context(other, hasSubjectGrouping = true)
  }
}

/** One lexical scope: alias → info. */
final case class Scope(aliases: Map[String, AliasInfo])

/** What a query source alias refers to (filled in during lowering; opaque in Phase 0). */
final case class AliasInfo(sourceName: String)

/** A parameter binding (default value reference resolved in Resolve). */
final case class ParamBinding(name: String, typeName: Option[String])

/** A resolved, broadcast value-set reference. Source/expansion details are absorbed by
  * the TerminologyProvider adapter (DATA-TERMINOLOGY-ADAPTER); the engine carries only
  * the pinned identity here. */
final case class TerminologyHandle(valueSetId: String, version: Option[String])
