package com.databricks.cql.passes

import com.databricks.cql.env.Config
import com.databricks.cql.ir._

/** The materialization DECISION layer (DATA-MATERIALIZE, DATA-SKIP-UNFILTERED, DATA-MATERIALIZE-POLICY,
  * `03` section 6 / `04` section 8). A PURE planning step over the define DAG + the frozen [[Config]]:
  * it records the inputs a re-run reproduces from, and — crucially under the unified cost-driven policy
  * (`data-format.md` section 6.2) — it is the SINGLE SOURCE of the policy INPUTS: the per-name reuse
  * (refcount) map and the skip-unfiltered set. It performs NO Spark action and writes nothing — the
  * physical writer (Delta/Iceberg) is a host-side seam (see [[Plan]]), kept out of engine-core so the
  * decision is unit-testable and storage-agnostic (ARCH-SEAMS).
  *
  * Three modes (`materialize.mode`, TECH-CONFIG, DATA-MATERIALIZE-POLICY): `none` (never), `all`
  * (debug/audit — every define), and `costly` (default — `Cost.score × reuse` over a threshold,
  * decided per-candidate at the `env.materialize` SEAM where the lowered frame exists, since the cost
  * is the lowered-frame structural cost, not a pure-IR estimate; `selective` is a deprecated alias).
  * [[plan]] therefore reports the part it CAN decide purely (none → ∅; all → every define; costly →
  * the reuse-shared set, the reuse component of the seam decision) and exposes [[reuseCounts]] /
  * [[skipUnfilteredNames]] which the seam policy (`lower.MaterializationPolicy`) consumes — so the
  * pass and the seam share ONE set of inputs, never a parallel decision. Every mode honors
  * `materialize.skip_unfiltered_retrieves` (default on): a bare unfiltered-retrieve define
  * (`define E: [Encounter]`, no value-set/date scan filter) is a VIEW over the source frame the
  * Retriever already exposes (now that list defines are `Relation`-encoded), so
  * materializing it is wasted I/O — it is skipped even in `all`.
  */
object Materialize {

  /** A materialization plan: the defines to materialize (in Schedule order) and the trivial
    * unfiltered-retrieve pass-throughs deliberately skipped (logged, never silently dropped —
    * CORE-NO-SILENT-MISLOWER / `DATA-SKIP-UNFILTERED`). `mode` is the resolved `materialize.mode`.
    * A host materializer consumes `materialize` to stage those define frames; `skippedUnfiltered`
    * documents what was treated as a view. */
  final case class Plan(materialize: Seq[String], skippedUnfiltered: Seq[String], mode: String)

  /** Decide the materialization plan for a library under `config` — the part decidable PURELY (the
    * cost-driven `costly` refinement happens per-candidate at the `env.materialize` seam over the
    * lowered frame, see [[reuseCounts]]). `none` → ∅; `all` → every define; `costly` → the reuse-shared
    * set (the reuse component of the seam decision). All modes minus the skip-unfiltered set. */
  def plan(lib: Library, config: Config): Plan = {
    val defines = orderedDefines(lib)
    val unfiltered = skipUnfilteredNames(lib, config)
    val candidates = config.materializeMode match {
      case Config.MaterializeMode.None   => Seq.empty[String]
      case Config.MaterializeMode.All    => defines.map(_.name)
      case Config.MaterializeMode.Costly => sharedDefines(defines)
    }
    Plan(
      materialize = candidates.filterNot(unfiltered.contains),
      skippedUnfiltered = defines.map(_.name).filter(unfiltered.contains),
      mode = config.materializeMode.key)
  }

  /** The skip-unfiltered set: the bare unfiltered-retrieve defines (DATA-SKIP-UNFILTERED) — a VIEW
    * over the source frame, never materialized, in EVERY mode. Empty when the flag is off. One source
    * shared by [[plan]] and the seam policy. */
  def skipUnfilteredNames(lib: Library, config: Config): Set[String] =
    if (config.materializeSkipUnfilteredRetrieves)
      orderedDefines(lib).filter(d => isUnfilteredRetrieve(d.expression)).map(_.name).toSet
    else Set.empty[String]

  /** The per-name REUSE (refcount) map — how many other defines reference each name, INCLUDING the
    * context name (the context resource is referenced by ~every define, so it weighs heavily). This is
    * the reuse component the seam policy multiplies the per-candidate `Cost.score` by under `costly`
    * (DATA-MATERIALIZE-POLICY), and the same count `sharedDefines` thresholds at ≥2. Deterministic. */
  def reuseCounts(lib: Library): Map[String, Int] = {
    val defines = lib.statements.collect { case e: ExpressionDef => e }
    val known = defines.map(_.name).toSet
    val refCount = scala.collection.mutable.Map.empty[String, Int].withDefaultValue(0)
    defines.foreach(_.expression.collect {
      case ExpressionRef(n, _) if known.contains(n) => refCount(n) += 1
    })
    refCount.toMap
  }

  /** The per-resource-TYPE Retrieve CONSUMER count (DATA-RETRIEVE-SHARE-BARRIER): how many Retrieve nodes
    * across the library target each resource type (by `localName(dataType)`). This is the reuse signal the
    * retrieve-mount barrier decision threshold ([[QueryLowering.RetrieveBarrierMinReuse]]): a type read by many
    * consumers (the conditional/choice-union pathology — `[Encounter]` re-emitted per branch per define)
    * is promoted to a scan-once `localCheckpoint` mount instead of a re-scanned logical view. Counts every
    * Retrieve occurrence (each is one mount reference), not distinct filters. Deterministic. */
  def retrieveCounts(lib: Library): Map[String, Int] = {
    val count = scala.collection.mutable.Map.empty[String, Int].withDefaultValue(0)
    lib.statements.foreach {
      case e: ExpressionDef => e.expression.collect { case r: Retrieve => count(localName(r.dataType)) += 1 }
      case f: FunctionDef   => f.body.collect { case r: Retrieve => count(localName(r.dataType)) += 1 }
      case _                => ()
    }
    count.toMap
  }

  /** Bare local name of a (possibly namespaced) FHIR type — mirrors `QueryLowering.localName`
    * (`substring after the last '}'`) so the count keys match the FHIR type keys the retrieve seam
    * binds by (`{urn:hl7-org:elm-types:r1}Encounter` → `Encounter`; a bare name is unchanged). */
  private def localName(dataType: String): String = dataType.substring(dataType.lastIndexOf('}') + 1)

  /** True if a define's expression is a BARE unfiltered retrieve — no value-set (`codes`) and no
    * date-range scan filter (`DATA-SKIP-UNFILTERED`). Such a define is an alias of the source frame. */
  def isUnfilteredRetrieve(e: Expr): Boolean = e match {
    case Retrieve(_, _, _, codes, _, dateRange, _, _) => codes.isEmpty && dateRange.isEmpty
    case _                                      => false
  }

  /** The reproducibility record written alongside materialized output (DATA-MATERIALIZE,
    * TECH-DETERMINISM): the engine version, the resolved config (`Config.recordedView`), and the
    * value-set id→version pins. A re-run from these inputs reproduces the result. Deterministic order. */
  final case class RunRecord(engineVersion: String,
                             config: Seq[(String, String)],
                             valueSets: Seq[(String, Option[String])])

  def record(lib: Library, config: Config, engineVersion: String = BuildInfo.version): RunRecord =
    RunRecord(
      engineVersion = engineVersion,
      config = config.recordedView,
      valueSets = lib.valueSets.map(vs => vs.id -> vs.version).distinct.sortBy(_._1))

  /** The (context-define-excluded) defines in Schedule order — the same set [[FrameLowering]] lowers. */
  private def orderedDefines(lib: Library): Seq[ExpressionDef] = {
    val contextNames = lib.contexts.map(_.name).toSet
    Schedule.order(lib.statements.collect { case e: ExpressionDef => e })
      .filterNot(d => contextNames.contains(d.name))
  }

  /** Defines referenced by ≥2 other defines (intra-library) — the shared cut-points selective
    * materialization stages (the cache/statistics payoff; `03` section 6). Deterministic by name. */
  private def sharedDefines(defines: Seq[ExpressionDef]): Seq[String] = {
    val known = defines.map(_.name).toSet
    val refCount = scala.collection.mutable.Map.empty[String, Int].withDefaultValue(0)
    defines.foreach(_.expression.collect {
      case ExpressionRef(n, _) if known.contains(n) => refCount(n) += 1
    })
    defines.map(_.name).filter(n => refCount(n) >= 2)
  }
}
