package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.harness.{GroundTruthRunner, JsonNormalize}
import com.databricks.cql.ir.Library
import com.databricks.cql.passes.{LibraryLinker, Pipeline}
import com.databricks.cql.retrieve.{DataProvider, TerminologyProvider}
import com.databricks.cql.runtime.ResultBuilder
import com.databricks.cql.service.CompileService
import org.scalatest.Assertions._

/** The SHARED differential harness (CONF-GATE-MEASURE, CONF-GROUND-TRUTH): the fixture descriptor, the
  * value renderer, the per-fixture config-wrapper stack, and the library builder — everything the
  * execution paths draw from. The EXECUTION itself is the SQL back end ([[SqlDifferential]]): lower a
  * fixture to a Spark SQL script, run it via `spark.sql`, and diff per (subject, define) against the Java
  * CQF reference.
  *
  * ONE path for every context, data model, and value type (PROC-DRY): a scalar/Unfiltered library and a
  * FHIR Patient measure are the same code differing only in the injected dependencies (PROC-SOLID). The
  * The SQL path (EXEC-SQL-BACKEND) is the only execution path; this object holds the parts it uses. */
object Differential {

  /** One differential case: the fixture plus the explicitly-injected engine and reference
    * environments. Both sides draw from the SAME sample data, so the two envs describe one world
    * seen from each side. */
  final case class Case(
      library: String,
      context: Context,
      data: Option[DataProvider] = None,
      terminology: Option[TerminologyProvider] = None,
      refEnv: GroundTruthRunner.RefEnv = GroundTruthRunner.RefEnv(),
      link: Option[LinkSpec] = None,
      /** Divergences from the reference this fixture is KNOWN and INTENDED to have (`CONF-REF-DEVIATION`). */
      deviations: Seq[Deviation] = Nil,
      /** Run-time parameter values as CQL expression text (`LANG-PARAMETER`), applied as overrides on the
        * engine side. The reference gets the same map through `refEnv.parameters`. */
      parameters: Map[String, String] = Map.empty)

  /** A DECLARED, JUSTIFIED divergence from the Java CQF reference (`CONF-REF-DEVIATION`).
    *
    * The reference is the oracle, so deviating from it must be a deliberate, reviewed act — never a
    * quietly-edited expectation. A declaration is therefore NARROW (it accepts one specific shape of
    * difference, not "this define may differ"), carries a REASON, and is CHECKED FOR STALENESS: a
    * declaration that no longer fires fails the gate, so a deviation cannot outlive the behaviour it
    * describes. Anything undeclared still fails loudly.
    *
    * Deviating is not free — matching CQF's all-null-interval collapse measured +15% to +40% emitted SQL
    * across the four measures, and matching its list order would need an ingestion-sequence column
    * threaded through every retrieve — so the reason field records the trade that was accepted. */
  sealed trait Deviation { def define: String; def reason: String }

  /** The same elements in a different ORDER. Accepted only if the two lists are permutations of each
    * other, so a genuine VALUE change on the same define still fails. */
  final case class OrderOnly(define: String, reason: String) extends Deviation

  /** A specific `(subject, define)` pair whose value differs by design. Narrow on purpose: a sibling
    * subject's divergence on the same define is still a failure. */
  final case class ValueFor(define: String, subject: String, reason: String) extends Deviation

  /** A multi-library measure to flatten before lowering (CONF-GATE-MEASURE): the loaded dependency
    * libraries to merge into `library`, and the population define names the slice prunes to. */
  final case class LinkSpec(deps: Seq[Library], roots: Set[String])

  /** Renders collected Spark cells as `ujson.Value` for the ground-truth diff — the harness's value
    * representation for the SQL-path collector ([[SqlDifferential.collect]]), compared against the Java
    * reference via [[JsonNormalize]]. */
  object UjsonResults extends ResultBuilder[ujson.Value] {
    def cell(raw: Any): ujson.Value = JsonNormalize.value(raw)
    def list(values: Seq[ujson.Value]): ujson.Value = ujson.Arr.from(values)
    def emptyList: ujson.Value = ujson.Arr()
    def nullValue: ujson.Value = ujson.Null
  }

  /** Grain-first opt-in (ARCH-GRAIN). The fixtures whose
    * lowering runs grain-first (list defines carried as `Relation`s, NOT boxed onto a wide frame).
    * Every OTHER fixture lowers byte-unchanged with the flag off. Membership also flips the simplified
    * subject key on (`Corpus`/`SparkFhirData`). Extra names can be opted in for a one-off run via
    * `-Dcqldatabricks.grainFirst=<comma-separated>`. All are EXECUTABLE-verified (diff-clean vs the Java
    * reference); grain-first is per-fixture, so none of this affects any other fixture's gates. */
  val grainFirstFixtures: Set[String] =
    Set("FhirListDefineConsumed", "FhirMultiSource", "FhirPeriodWhere",
      "FhirConditionalUnion", "FhirConditionalUnionHet", "FhirNullableList",
      "FhirReturnList", "FhirListCount", "FhirRelationClauses",
      "FhirChoiceUnionResources", "FhirChoiceConditionalResources", "FhirChoiceResourcePrimitive",
      "FhirChoiceUnionLabsMeds", "FhirChoiceUnionNav", "FhirHetUnionValueX",
      // Plain list defines (bare/filtered retrieve, where/return, multi-source temporal, value[x]
      // projection, list-define-as-source) — their per-subject `collect_list`/`posexplode` box is
      // UNNECESSARY: the rows stay at natural grain and assemble driver-side (ARCH-GRAIN).
      "FhirDisjunctiveTemporalExists", "FhirReferenceFollow", "FhirCorrelatedExistsDisjunction",
      "FhirBareRetrieveList", "FhirChoiceGate", "FhirChoiceRetrieve", "FhirNumerator",
      "FhirResourceExport", "FhirTemporalDynamic", "FhirTemporalWhere", "FhirValueSetQuery",
      "FhirValueSetWhere", "FhirListDefineVsFilter", "FhirConditional")

  /** ARCH-COND-UNION opt-in (Phase A same-type + Phase C nested): the fixtures whose list-valued
    * `if`/`case` lowers via the relation-UNION rewrite (`optimize.conditional_union`). These are
    * grain-first (the union is a `Relation` define). Extra names: `-Dcqldatabricks.conditionalUnion=<csv>`. */
  val conditionalUnionFixtures: Set[String] = Set("FhirConditionalUnion", "FhirNullableList", "FhirConditional")

  /** ARCH-COND-UNION Phase B opt-in (heterogeneous branches → `Choice`-tagged union): a heterogeneous
    * *list* conditional has NO boxed baseline, so these are differential-only, gated vs the Java reference. */
  val conditionalUnionHetFixtures: Set[String] =
    Set("FhirConditionalUnionHet",
      "FhirChoiceUnionResources", "FhirChoiceConditionalResources", "FhirChoiceResourcePrimitive",
      "FhirChoiceUnionLabsMeds",
      // Navigates a field on the het union element (the SQL path lowers it via the opaque
      // `cql_choice_nav` UDF, EXEC-OPAQUE-UDF).
      "FhirChoiceUnionNav",
      // An INLINE het-union query source with per-member value[x] navigation.
      "FhirHetUnionValueX",
      // Its 5th arm is a temporal exists over a het union (a shared exclusion library's diagnosis arm).
      "FhirDisjunctiveTemporalExists")

  /** Apply the conditional-union flag to `config` iff `library` is opted in (either curated set, or the
    * `-Dcqldatabricks.conditionalUnion` override). */
  def withConditionalUnion(library: String, config: Config): Config = {
    val extra = sys.props.get("cqldatabricks.conditionalUnion").toSeq
      .flatMap(_.split(",")).map(_.trim).filter(_.nonEmpty).toSet
    val on = conditionalUnionFixtures.contains(library) ||
      conditionalUnionHetFixtures.contains(library) || extra.contains(library)
    config.copy(optimizeConditionalUnion = on)
  }

  /** Apply the grain-first flag to `config` iff `library` is opted in (the curated set, or the
    * `-Dcqldatabricks.grainFirst` override). `-Dcqldatabricks.noGrainFirst=<csv>` force-OFF an opted-in fixture. */
  def withGrainFirst(library: String, config: Config): Config = {
    def csv(key: String): Set[String] =
      sys.props.get(key).toSeq.flatMap(_.split(",")).map(_.trim).filter(_.nonEmpty).toSet
    val extra = csv("cqldatabricks.grainFirst")
    val off = csv("cqldatabricks.noGrainFirst")
    config.copy(grainFirst = (grainFirstFixtures.contains(library) || extra.contains(library)) && !off.contains(library))
  }

  /** Override the CTE-def flag (ARCH-CTE-DEF) from `-Dcqldatabricks.cteDefs`, which OVERRIDES the config
    * default (now ON): absent → keep the default; `true`/`false` → force; a comma-separated list → on
    * for the named fixtures only. */
  def withCteDefs(library: String, config: Config): Config =
    sys.props.get("cqldatabricks.cteDefs").map(_.trim).filter(_.nonEmpty) match {
      case None         => config // absent → keep the config default (ON)
      case Some("true") => config.copy(cteDefs = true)
      case Some(raw)    => config.copy(cteDefs = raw.split(",").map(_.trim).filter(_.nonEmpty).contains(library))
    }

  /** The materialization policy for the conformance harness (DATA-MATERIALIZE-POLICY). Every define is a
    * barrier by default (`MaterializeMode.All`) for per-define auditability — a HARNESS override only; the
    * compiler/production default stays `Costly`. Overridable per run with
    * `-Dcqldatabricks.materialize.mode=all|costly|none` and `-Dcqldatabricks.materialize.cost_threshold=<long>`.
    *
    * `DATA-RETRIEVE-BARRIER-OPT` rides the same seam: `-Dcqldatabricks.materialize.retrieves=all` re-runs the
    * WHOLE corpus with every filtered read promoted to its own barrier object. The barrier is a pure
    * restructuring — the object holds exactly the rows the inlined form produced — so a full-corpus run
    * under `all` must diff CLEAN against the reference, exactly as the optimized-vs-unoptimized self-diff
    * does. That run is the correctness gate for the feature; the compiler-level
    * specs only prove the emit SHAPE. Defaults to the production `none`, so an ordinary run is unchanged. */
  def withMaterialize(config: Config): Config =
    config.copy(
      materializeMode = sys.props.get("cqldatabricks.materialize.mode")
        .flatMap(Config.MaterializeMode.fromKey).getOrElse(Config.MaterializeMode.All),
      materializeCostThreshold = sys.props.get("cqldatabricks.materialize.cost_threshold")
        .flatMap(s => scala.util.Try(s.toLong).toOption).getOrElse(config.materializeCostThreshold),
      materializeRetrieves = sys.props.get("cqldatabricks.materialize.retrieves")
        .flatMap(Config.RetrieveMode.fromKey).getOrElse(config.materializeRetrieves),
      materializeRetrieveMinReuse = sys.props.get("cqldatabricks.materialize.retrieve_min_reuse")
        .flatMap(s => scala.util.Try(s.toInt).toOption).filter(_ >= 1)
        .getOrElse(config.materializeRetrieveMinReuse))

  /** The per-CQL keyword filter shared by every gate: `-Dcqldatabricks.only=<substring>` runs only fixtures
    * whose library name contains the (case-insensitive) substring; the rest cancel cleanly. */
  def onlyFilter(library: String): Unit =
    sys.props.get("cqldatabricks.only").map(_.trim.stripSuffix(".cql").stripSuffix(".CQL"))
      .filter(_.nonEmpty).foreach { kw =>
        assume(library.toLowerCase.contains(kw.toLowerCase), s"[$library] skipped: != '$kw'")
      }

  /** The default evaluation config for a fixture (PROC-DRY): grain-first, materialization policy (every
    * define a barrier), conditional-union, cte-defs. All perf enhancements (opaque temporal catalog UDFs,
    * temporal-CSE, retrieve projection) are ON unconditionally in the compiler now. Shared so an
    * out-of-band driver (e.g. the measure scale harnesses) exercises byte-identical wiring to the gate. */
  def evalConfig(library: String): Config =
    withCteDefs(library, withMaterialize(withConditionalUnion(library, withGrainFirst(library, Config.defaults))))

  /** Build the normalized engine library for a case (PROC-DRY): a multi-library measure is flattened
    * (link) and pruned to its population slice before the single-library pipeline runs; a self-contained
    * fixture loads its ELM directly. */
  def buildLibrary(c: Case): Library = Pipeline().normalize(loadedLibrary(c))

  /** `c`'s library loaded, linked and with its parameter overrides applied, BEFORE `normalize` inlines
    * parameters: the library a parameter scope is read from (`BIND-RETRIEVE-REQUEST`). */
  def loadedLibrary(c: Case): Library = {
    val loaded = c.link match {
      case Some(ls) =>
        val root = Pipeline().load(Paths.read(Paths.elm(c.library)))
        LibraryLinker.prune(LibraryLinker.link(root, ls.deps.map(d => d.id -> d).toMap), ls.roots)
      case None =>
        Pipeline().load(Paths.read(Paths.elm(c.library)))
    }
    CompileService.applyParameterOverrides(loaded, c.parameters)
  }
}
