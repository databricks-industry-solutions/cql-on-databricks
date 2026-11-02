package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.ir._
import com.databricks.cql.meta.LibraryDescribe
import com.databricks.cql.passes.{LibraryLinker, Optimize, Pipeline, RetrieveCatalog}
import com.databricks.cql.translate.CqlToElmCli
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The two BOUNDARY-POLICY gates over the corpus measures, both SPARK-FREE (pure lowering + metadata,
  * so they are always-on and need no Connect server):
  *
  *   1. `EXEC-MATERIALIZE-FILTER` — IF IT FILTERS, IT MATERIALIZES. Every define whose body prunes rows
  *      anywhere stages as a `CACHE TABLE` barrier; every define that prunes nothing (and is not a
  *      measure population) stays a logical view. Both directions are asserted, so the rule cannot decay
  *      into "materialize everything" either.
  *
  *   2. `BIND-DESCRIBE-RETRIEVES` — `describe` reports every scan the slice issues, and a scan that
  *      carries a code filter in the IR never surfaces without one.
  *
  * Deliberately run in the DEFAULT (`costly`) materialize mode — the mode `describe` and a production
  * compile use. The differential gate forces `materialize.mode=all` ([[Differential.withMaterialize]]),
  * which stages every define unconditionally and therefore exercises none of this. Before this gate
  * existed, that gap let the classifier miss 16 of 59 defines across the measures of the time unnoticed. */
@RunWith(classOf[JUnitRunner])
final class MaterializePolicySpec extends AnyFunSuite {

  /** Link + prune + normalize a measure to its population slice (no Spark, no data). */
  private def build(m: Corpus.MeasureDef): Library = Differential.buildLibrary(Corpus.offlineCase(m))

  /** The production/describe config: DEFAULT materialize mode so the semantic classifier actually
    * decides, everything else mirroring the gate wiring. */
  private def config(library: String): Config =
    Differential.withCteDefs(library,
      Differential.withConditionalUnion(library, Differential.withGrainFirst(library, Config.defaults)))

  /** An INDEPENDENT reimplementation of "does this define prune rows" — deliberately NOT a call into
    * `SqlFrameLowering.classify`, so the gate cross-checks the classifier instead of restating it. A
    * define prunes rows iff its body holds a scan-filtered `Retrieve` or a `Query` with a
    * `where`/`with`/`without`. An `ExpressionRef` to a sibling is a leaf (its filter belongs upstream). */
  private def prunesRows(e: Expr): Boolean = e.exists {
    case r: Retrieve =>
      r.codes.isDefined || r.dateRange.isDefined || r.periodScanFilter.isDefined || r.profileConstraint.isDefined
    case q: Query => q.where.isDefined || q.relationships.nonEmpty
    case _        => false
  }

  /** Populations always stage regardless of filtering — they are the requested outputs. */
  private def isPopulation(name: String): Boolean = {
    val n = name.toLowerCase
    Seq("initial population", "denominator", "numerator", "measure population", "eligible").exists(n.contains)
  }

  Corpus.linkMeasures.foreach { m =>
    test(s"${m.library}: a define that filters is materialized, one that does not is a view (EXEC-MATERIALIZE-FILTER)") {
      val cfg = config(m.library)
      assert(cfg.materializeMode == Config.MaterializeMode.Costly,
        "the policy gate must run the semantic classifier, not a forced materialize mode")

      val optimized = Optimize.run(build(m), cfg)
      val lowered = SqlFrameLowering.lower(optimized, SqlEvalEnv(Context.Patient, cfg).describing)
      val bodies = optimized.statements.collect { case d: ExpressionDef => d.name -> d.expression }.toMap
      val staged = lowered.materializedDefines

      val missed = lowered.names.filter(n => bodies.get(n).exists(prunesRows) && !staged.contains(n))
      assert(missed.isEmpty,
        s"these defines prune rows but were left as logical views (EXEC-MATERIALIZE-FILTER): ${missed.mkString("; ")}")

      // The other direction: staging a define that prunes NOTHING re-stages rows for free. Only a
      // population may do so, or the plan-SIZE gate (a huge inlined scalar tower is its own reason).
      // Decision names are SANITIZED object names, so pair them with defines POSITIONALLY instead —
      // statements are emitted in `names` order, after the base view and minus any nullity sidecars.
      val reasonOf = lowered.names.zip(
        lowered.decisions.filterNot(d => d.reason == "base" || d.reason == "null_sidecar")).toMap
      val overStaged = lowered.names.filter { n =>
        staged.contains(n) && !isPopulation(n) && !bodies.get(n).exists(prunesRows) &&
          !reasonOf.get(n).exists(_.reason == "size")
      }
      assert(overStaged.isEmpty,
        s"these defines prune no rows yet were staged as barriers: " +
          overStaged.map(n => s"$n (${reasonOf.get(n).map(_.reason).getOrElse("?")})").mkString("; "))

      // The rule must be doing the work, not the size backstop: `describe` publishes the boundary set,
      // so a size-decided boundary would couple the contract to `-Dcqldatabricks.sqlBarrierBytes`.
      val sized = lowered.names.filter(reasonOf.get(_).exists(_.reason == "size"))
      assert(sized.isEmpty,
        s"barriers attributed to the plan-SIZE gate rather than to filtering: ${sized.mkString("; ")}")

      // The rule must not be a no-op restatement of "populations only".
      assert(staged.exists(n => !isPopulation(n)), "expected non-population defines among the barriers")
    }

    test(s"${m.library}: describe reports every scan, and a filtered scan is never reported unfiltered (BIND-DESCRIBE-RETRIEVES)") {
      val optimized = Optimize.run(build(m), config(m.library))
      val catalog = RetrieveCatalog.analyze(optimized)

      // Every Retrieve in the IR is reported (modulo the per-define dedup of identical scans).
      val irRetrieves = optimized.statements.collect { case d: ExpressionDef => d }
        .flatMap(d => d.expression.collect { case r: Retrieve => (d.name, r) })
      assert(catalog.retrieves.nonEmpty, "a measure issues scans")
      assert(irRetrieves.map(_._1).toSet.subsetOf(catalog.retrieves.map(_.define).toSet),
        "every define that scans appears in the catalog")

      // THE invariant: a retrieve carrying `codes` in the IR always surfaces with a codeFilter
      // (CORE-NO-SILENT-MISLOWER — a filtered scan must never read as a full one).
      val filteredInIr = irRetrieves.filter(_._2.codes.isDefined).map(_._1).toSet
      val unfilteredInCatalog = catalog.retrieves.filter(_.codeFilter.isEmpty).map(_.define).toSet
      val lying = filteredInIr.filter { d =>
        val forDefine = catalog.retrieves.filter(_.define == d)
        forDefine.forall(_.codeFilter.isEmpty) && unfilteredInCatalog.contains(d)
      }
      assert(lying.isEmpty, s"code-filtered scans reported with no codeFilter: ${lying.mkString("; ")}")

      // Value sets are keyed by CANONICAL id, one entry per id, and every id is really declared.
      val ids = catalog.valueSets.map(_.id)
      assert(ids.distinct == ids, s"value sets must be keyed by canonical id, one entry each: $ids")
      val declared = optimized.valueSets.map(_.id).toSet
      assert(ids.forall(declared.contains), s"reported ids must be declared: ${ids.filterNot(declared.contains)}")
      assert(catalog.valueSets.forall(_.names.nonEmpty), "every value set carries the local alias(es) naming it")

      // Every value set a reported scan binds is in the coverage list — that list IS the mount contract.
      val bound = catalog.retrieves.flatMap(_.codeFilter).collect { case v: RetrieveCatalog.ValueSet => v.id }.toSet
      assert(bound.subsetOf(ids.toSet), s"scans bind value sets absent from the coverage list: ${bound -- ids.toSet}")
    }
  }

  test("one canonical URL declared by both the root and a dependency is ONE coverage entry") {
    // Root and dependency declare the same canonical URL under different local names; linking keeps both
    // `ValueSetDef`s (the dependency's qualified). Keyed by name that is two requirements where there is one
    // expansion — hence the id-keyed contract with `names` as aliases.
    val dep =
      """library SharedVisits version '1.0.0'
        |using QICore version '6.0.0'
        |include FHIRHelpers version '4.0.1' called FHIRHelpers
        |valueset "RoutineEncounter": 'http://example.org/fhir/ValueSet/cql-test-office-visit'
        |context Patient
        |define "Has RoutineEncounter": exists [Encounter: "RoutineEncounter"]
        |""".stripMargin
    val rootCql =
      """library SharedVisitsRoot version '1.0.0'
        |using QICore version '6.0.0'
        |include FHIRHelpers version '4.0.1' called FHIRHelpers
        |include SharedVisits version '1.0.0' called Visits
        |valueset "SpecializedEncounter": 'http://example.org/fhir/ValueSet/cql-test-office-visit'
        |context Patient
        |define "Initial Population": Visits."Has RoutineEncounter" and exists [Encounter: "SpecializedEncounter"]
        |""".stripMargin
    val closure = CqlToElmCli.translateClosure(rootCql, Seq(dep, Paths.read(Paths.cql("FHIRHelpers"))))
    val root = Pipeline().load(closure.root.elmJson.getOrElse(fail(closure.root.errors.mkString("; "))))
    val deps = closure.dependencies.map(e => Pipeline().load(e))
    val lib = Pipeline().normalize(LibraryLinker.prune(
      LibraryLinker.link(root, deps.map(d => d.id -> d).toMap), Set("Initial Population")))
    val catalog = RetrieveCatalog.analyze(Optimize.run(lib, config(root.id)))
    val office = catalog.valueSets.filter(_.id.endsWith("/cql-test-office-visit"))
    assert(office.sizeIs == 1, s"expected ONE entry for the shared canonical URL, got: $office")
    assert(office.head.names.toSet == Set("SpecializedEncounter", "SharedVisits.RoutineEncounter"),
      s"both local aliases travel with the id: ${office.head.names}")
  }

  Corpus.linkMeasures.foreach { m =>
    test(s"${m.library}: the scan catalog agrees with the field-demand contract on which types are touched") {
      // The catalog is compiler-INTERNAL for now (deliberately not on the `/describe` wire — see
      // BIND-DESCRIBE-RETRIEVES), so this pins it against the contract that IS published: the two
      // describe the same binding from different angles (which rows will be asked for vs which fields).
      val lib = build(m)
      val cfg = config(m.library)
      val catalog = RetrieveCatalog.analyze(Optimize.run(lib, cfg))
      val g = LibraryDescribe.describe(lib, Context.Patient,
        overrides = Map.empty, grainFirst = cfg.grainFirst, terminologyVersion = None)
      assert(catalog.retrieves.nonEmpty, "a measure issues scans")
      assert(catalog.valueSets.map(_.id).forall(_.startsWith("http")), "coverage is keyed by canonical URL")
      // EMIT-DESCRIBE-RETRIEVE-TYPES lists every retrieved type, including one that reads no field
      // (`exists [Condition]`), which the field demand leaves out.
      assert(catalog.retrieves.map(_.resourceType).toSet.subsetOf(g.retrieveTypes.toSet),
        s"scanned types ${catalog.retrieves.map(_.resourceType).distinct} vs described ${g.retrieveTypes}")
      assert(g.retrieveDemand.keySet.subsetOf(g.retrieveTypes.toSet),
        s"demanded types ${g.retrieveDemand.keySet} vs described ${g.retrieveTypes}")
    }
  }
}
