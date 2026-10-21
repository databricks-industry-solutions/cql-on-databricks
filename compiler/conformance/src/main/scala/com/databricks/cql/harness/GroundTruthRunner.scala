package com.databricks.cql.harness

import ca.uhn.fhir.context.FhirContext
import com.databricks.cql.cqf.{InMemoryFhirRetrieveProvider, InMemoryTerminologyProvider}
import com.databricks.cql.env.Context
import com.databricks.cql.ir.ExpressionDef
import com.databricks.cql.load.ElmCodec
import com.databricks.cql.spark.SystemColumns
import org.cqframework.cql.cql2elm.{LibraryManager, ModelManager, StringLibrarySourceProvider}
import org.opencds.cqf.cql.engine.data.{CompositeDataProvider, DataProvider}
import org.opencds.cqf.cql.engine.execution.{CqlEngine, Environment, EvaluationExpressionRef, EvaluationParams}
import org.opencds.cqf.cql.engine.fhir.model.R4FhirModelResolver
import org.opencds.cqf.cql.engine.terminology.{TerminologyProvider => CqfTerminologyProvider}

import scala.jdk.CollectionConverters._

/** The ground-truth reference (CONF-GROUND-TRUTH, `06` section 3).
  *
  * Runs an authored measure through the **maintained Java CQL engine** (`engine-jvm`, the
  * successor to `org.opencds.cqf.cql:engine`, from cqframework/clinical-reasoning) — the
  * trusted reference whose outputs the engine's set-at-a-time results are diffed against
  * (`CONF-GATE-MEASURE`). It runs **in-process** (JVM): no Node, no subprocess.
  *
  * The reference compiles the SAME authored CQL with the SAME cqframework translator the
  * build uses (version-aligned via `cql-bom`), so the ELM cannot diverge from what the
  * engine `Load`s (TECH-ELM-INPUT). The injected [[RefEnv]] is the DI mirror of the engine's
  * [[com.databricks.cql.lower.EvalEnv]] (PROC-SOLID): the same sample resources and
  * value-set expansions feed both sides, so neither can diverge through the data.
  *
  * ONE evaluation entry for every context (PROC-DRY): `evaluate` returns the uniform
  * `subjectKey -> (defineName -> value)` shape — a subject-grouped context evaluates per
  * subject id; an ungrouped context evaluates once under the synthetic key
  * (`SystemColumns.UnfilteredSubject`), the degenerate one-subject case.
  *
  * GAP: this replaces the spec's named JS stack (`cql-execution` +
  * `cql-exec-fhir`), which is unmaintained. The Java engine is the actively-maintained
  * reference; the certification-vs-JS nuance is tracked there.
  */
object GroundTruthRunner {

  /** The injected reference inputs (the DI mirror of the engine's `EvalEnv`, PROC-SOLID): the
    * context that decides per-subject vs single evaluation, the sample FHIR resources, the
    * value-set expansions, and the subject ids to evaluate. Empty defaults describe a
    * System-model scalar library (no data, no terminology, single evaluation).
    *
    * @param resourcesByType FHIR resource type -> the NDJSON lines for that type (one resource each)
    * @param subjectIds      the context values to evaluate (ignored for an ungrouped context) */
  final case class RefEnv(
      contextName: String = Context.Unfiltered.name,
      resourcesByType: Map[String, Seq[String]] = Map.empty,
      expansions: Map[String, Set[(String, String)]] = Map.empty,
      subjectIds: Seq[String] = Seq.empty,
      /** Extra CQL library sources registered so a multi-library measure's `include`s resolve on
        * the reference side too (CONF-GATE-MEASURE) — the same dependency sources the engine links. */
      extraLibrarySources: Seq[String] = Seq.empty,
      /** Run-time parameter values, as CQL expression text by parameter name (`LANG-PARAMETER`,
        * `CONF-GATE-MEASURE`): the same text the engine side applies as an override, so both engines run
        * the same Measurement Period. Bound for every library in the run, not only the root. */
      parameters: Map[String, String] = Map.empty)

  /** Evaluate `defineNames` in `libraryName` (compiled from `cqlText`) through the reference
    * under the injected `env`, returning `subjectKey -> (defineName -> value)`. */
  def evaluate(cqlText: String, libraryName: String, defineNames: Seq[String],
               env: RefEnv = RefEnv()): Map[String, Map[String, ujson.Value]] =
    evaluateAcross(cqlText, libraryName, Map(libraryName -> defineNames), env)

  /** Evaluate defines across SEVERAL libraries of one linked measure in ONE reference run
    * (`CONF-GATE-MEASURE`): `byLibrary` maps a library id to the defines wanted from THAT library, and
    * `rootLibrary` says which of them is the measure root.
    *
    * WHY THIS EXISTS. `LibraryLinker` flattens a measure's include closure and, per its own contract,
    * leaves the root's defines BARE while qualifying every dependency define as `{depId}.{name}`. The
    * reference has no such flattened library — it runs the original multi-library CQL — so a linked name
    * like `Helpers.Has Condition in Year Before or During Measurement Period`
    * cannot be looked up at all, and the differential could only ever diff the ROOT's own defines. That is
    * why a defect in a dependency-library define (which is where multi-library measure logic overwhelmingly
    * lives — 19 of 23 defines in one measured case) was invisible to the result gate and surfaced only as a production error.
    *
    * The `EvaluationParams.Builder.library(...)` call is repeatable and `EvaluationResults` is keyed by
    * library, so one request covers the whole closure. Results are re-qualified on the way out to the
    * SAME `{depId}.{name}` form the linker produces, so the caller can diff by linked name directly.
    *
    * Run-time parameters (`env.parameters`) are passed to the engine as evaluation parameters, never
    * written into any library's CQL, so a dependency library's `Measurement Period` is the same value
    * the root's is. */
  def evaluateAcross(cqlText: String, rootLibrary: String, byLibrary: Map[String, Seq[String]],
                     env: RefEnv = RefEnv()): Map[String, Map[String, ujson.Value]] = {
    val libraryName = rootLibrary
    val context = Context.named(env.contextName)

    val libraryManager = newLibraryManager(cqlText, env.extraLibrarySources)
    val (dataProvider, terminologyProvider) = buildProviders(env)
    val dataProviders =
      if (dataProvider == null) null
      else Map("http://hl7.org/fhir" -> dataProvider).asJava
    val engine = new CqlEngine(new Environment(libraryManager, dataProviders, terminologyProvider))
    val parameterValues = runtimeParameters(env.parameters)

    // Evaluate the requested defines once, optionally pinning the context to `contextId`.
    def evalDefines(contextId: Option[String]): Map[String, ujson.Value] = {
      val builder = new EvaluationParams.Builder()
      byLibrary.foreach { case (lib, names) =>
        builder.library(lib, new EvaluationParams.LibraryParams(names.map(new EvaluationExpressionRef(_)).asJava))
      }
      contextId.foreach(id =>
        builder.setContextParameter(new kotlin.Pair[String, Object](env.contextName, id)))
      if (parameterValues.nonEmpty) builder.setParameters(parameterValues.asJava)
      val results = engine.evaluate(builder.build())
      if (results.hasExceptions)
        throw new RuntimeException(
          s"ground-truth evaluation raised exceptions${contextId.fold("")(id => s" for '$id'")}: ${results.getExceptions}")
      // Re-qualify each dependency library's defines to the linker's `{depId}.{name}` form so the caller
      // diffs by the SAME name the engine emitted; the root's own defines stay bare, as the linker leaves them.
      results.getResults.asScala.iterator.flatMap { case (vid, res) =>
        val libId = vid.getId
        res.getExpressionResults.asScala.iterator.map { case (name, er) =>
          val key = if (libId == rootLibrary) name else s"$libId.$name"
          key -> JsonNormalize.value(er.getValue)
        }
      }.toMap
    }

    if (context.hasSubjectGrouping) {
      // A subject whose reference evaluation raises has NO in-process ground truth, so it is OMITTED
      // from the differential rather than aborting the whole run. This is sound: the CQF reference is
      // INDEPENDENT of the engine under test, so a subject it cannot evaluate can never mask an engine
      // bug on the OTHER subjects. The trigger in practice is a `Message('Error')` from spec-strict
      // FHIRHelpers on malformed test data — an `Observation` whose UCUM system is
      // `https://ucum.org` instead of the canonical `http://unitsofmeasure.org` — which makes the
      // reference throw for that one patient while the scorekey gate ([[MeasureScoreSpec]]) still checks
      // it against the measure's hand-written answer key. GUARD: if EVERY subject fails, the cause
      // is systemic (a library-load / config error, not per-subject poison data) — re-throw the first,
      // so a broken reference can never yield a vacuously-empty (falsely passing) differential.
      val (ok, failed) = env.subjectIds.foldLeft(
        (Map.empty[String, Map[String, ujson.Value]], Vector.empty[(String, Throwable)])) {
        case ((acc, errs), id) =>
          try (acc + (id -> evalDefines(Some(id))), errs)
          catch { case e: RuntimeException => (acc, errs :+ (id -> e)) }
      }
      if (failed.nonEmpty && ok.isEmpty) throw failed.head._2
      if (failed.nonEmpty)
        System.err.println(s"[GroundTruthRunner] reference could not evaluate ${failed.size} of " +
          s"${env.subjectIds.size} subject(s) (no ground truth; excluded from the differential):\n" +
          failed.map { case (id, e) => s"  - $id: ${e.getMessage}" }.mkString("\n"))
      ok
    } else
      Map(SystemColumns.UnfilteredSubject -> evalDefines(None))
  }

  /** Convenience for the System-model reference sanity checks (CONF-GROUND-TRUTH): derive the
    * define names from generated ELM, evaluate once (ungrouped), and return the flat map. */
  def evaluateFromElm(cqlText: String, elmJson: String): Map[String, ujson.Value] = {
    val lib = ElmCodec.loadString(elmJson)
    val names = lib.statements.collect { case e: ExpressionDef => e.name }
    evaluate(cqlText, lib.id, names).values.head
  }

  /** The library manager wired with the authored CQL, the bundled FHIRHelpers source, and any
    * dependency library sources a multi-library measure includes (CONF-GATE-MEASURE). */
  private def newLibraryManager(cqlText: String, extraSources: Seq[String]): LibraryManager = {
    val libraryManager = new LibraryManager(new ModelManager()) // default-loads the System model
    // The same strict provider the engine uses (`CqlToElmCli`): an include resolves to the source declaring
    // exactly its name and version, so both sides load the same sources (CONF-GATE-MEASURE, TECH-ELM-INPUT).
    libraryManager.getLibrarySourceLoader.registerProvider(
      new StringLibrarySourceProvider((Seq(
        cqlText, // as written: the oracle adds no include either (LANG-NO-IMPLICIT-INCLUDE)
        com.databricks.cql.translate.FhirHelpersSource.cql) ++
        extraSources).asJava))
    libraryManager
  }

  /** Evaluate each run-time parameter's CQL expression text to the reference engine's own runtime value
    * (`LANG-PARAMETER`): one throwaway library with a define per parameter, evaluated once. Going through
    * the engine means every CQL literal (an interval of DateTimes, a quantity, a code) gets the exact
    * runtime type the engine expects, with no hand-built value. A text that does not translate fails. */
  private def runtimeParameters(parameters: Map[String, String]): Map[String, Object] =
    if (parameters.isEmpty) Map.empty
    else {
      val names = parameters.keys.toSeq.sorted
      val cql = "library RunTimeParameters version '1.0.0'\n\n" +
        names.zipWithIndex.map { case (n, i) => s"define \"p$i\": ${parameters(n)}" }.mkString("\n")
      val libraryManager = new LibraryManager(new ModelManager())
      libraryManager.getLibrarySourceLoader.registerProvider(new StringLibrarySourceProvider(Seq(cql).asJava))
      val engine = new CqlEngine(new Environment(libraryManager, null, null))
      val builder = new EvaluationParams.Builder()
      builder.library("RunTimeParameters",
        new EvaluationParams.LibraryParams(names.indices.map(i => new EvaluationExpressionRef(s"p$i")).asJava))
      val results = engine.evaluate(builder.build())
      if (results.hasExceptions)
        throw new IllegalArgumentException(s"run-time parameter value(s) did not evaluate: ${results.getExceptions}")
      val byDefine = results.getResults.asScala.values.flatMap(_.getExpressionResults.asScala).toMap
      names.zipWithIndex.map { case (n, i) => n -> byDefine(s"p$i").getValue }.toMap
    }

  /** Build the reference's FHIR data + terminology providers from the injected sample data, or
    * `(null, null)` for a System-model library with no resources/expansions. */
  private def buildProviders(env: RefEnv): (DataProvider, CqfTerminologyProvider) = {
    val dataProvider: DataProvider =
      if (env.resourcesByType.isEmpty) null
      else {
        val parser = FhirContext.forR4().newJsonParser()
        val resources = env.resourcesByType.values.flatten.map(parser.parseResource).toSeq
        new CompositeDataProvider(new R4FhirModelResolver(),
          new InMemoryFhirRetrieveProvider(resources, env.expansions))
      }
    val terminologyProvider =
      if (env.expansions.nonEmpty) new InMemoryTerminologyProvider(env.expansions) else null
    (dataProvider, terminologyProvider)
  }
}
