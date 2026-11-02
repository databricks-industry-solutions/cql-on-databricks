package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.ir.Library
import com.databricks.cql.passes.Pipeline
import com.databricks.cql.spark.SystemColumns

import java.io.File

/** The single authoritative enumeration of differential fixtures — the "every measure" the
  * spec-faithfulness gate runs over (PROC-DRY): engine vs the Java reference on the OPTIMIZED plan
  * (CONF-GATE-MEASURE, [[SqlDifferentialSpec]]). Measures are DISCOVERED, never listed: every directory under
  * `data/measures/` with an `answer-key.json` is a measure, and adding one gates it with no code change
  * (CONF-MEASURE-DISCOVERY). The reference gate runs the optimized path.
  *
  * Each [[Entry]] carries a human label (with the requirement ids it exercises) and a THUNK that
  * builds the [[Differential.Case]] — deferred so `SparkConnect.session()` / file reads only happen
  * when a test actually runs, preserving the clean skip when no Connect server is reachable. */
object Corpus {

  /** A corpus fixture: the test label and a deferred [[Differential.Case]] builder. */
  final case class Entry(label: String, mk: () => Differential.Case)

  /** A measure the corpus discovered (CONF-MEASURE-DISCOVERY), shared by every measure gate (PROC-DRY): the
    * executable differential ([[entry]] -> [[Differential.Case]]), the hermetic link+normalize gate
    * ([[MeasureLinkSpec]]), and the answer-key gates ([[MeasureScoreSpec]], [[RefScoreSpec]]). Everything here
    * is read from the measure's own files, never written in code:
    *   - `library`, `version`, `measurementPeriod` and `populations` from `data/measures/<library>/answer-key.json`
    *     (`populations` maps each CQL population define to its measure-population code, in scoreboard order;
    *     the optional `supplemental` list adds defines reported beside them, and both are the prune roots);
    *   - `deps`, the FULL transitive `include` closure, FHIRHelpers included, from the translated ELM;
    *   - `resourceTypes`, one per `<Type>.ndjson` in the measure's data directory.
    * The value-set expansions are shared in `data/valuesets/` (`CONF-GATE-SCOREKEY`). */
  final case class MeasureDef(
      library: String,
      version: String,
      deps: Seq[String],
      dataSubdir: String,
      resourceTypes: Seq[String],
      populations: Seq[(String, String)],
      measurementPeriod: String,
      label: String,
      extraRoots: Seq[String] = Nil,
      deviations: Seq[Differential.Deviation] = Nil) {
    def roots: Set[String] = populations.map(_._1).toSet ++ extraRoots
    def dataDir: File = new File(measuresDataDir, dataSubdir)
    def entry: Entry = measure(this)
  }

  /** Where the measures' data lives, one directory per measure, named for its library. */
  def measuresDataDir: File = new File(Paths.dataDir.getParentFile, "measures")

  /** The value-set expansions the measures and QICore fixtures share, one `valueset-<lastUrlSegment>.ndjson` each. */
  def valueSetDir: File = new File(Paths.dataDir.getParentFile, "valuesets")

  /** The `(system, code)` expansion of a value set from [[valueSetDir]]. */
  def measureExpansion(valueSetId: String): Set[(String, String)] =
    Paths.read(new File(valueSetDir, s"valueset-${valueSetId.split("/").last}.ndjson"))
      .linesIterator.filter(_.trim.nonEmpty).map { l =>
        val o = ujson.read(l); (o("system").str, o("code").str)
      }.toSet

  /** A measure linked and pruned to its roots, with the measurement period bound, and NO data or
    * terminology provider: what every Spark-free measure spec lowers (`Differential.buildLibrary` on it gives
    * the normalized library). Loading the dependency ELM is the only I/O. */
  def offlineCase(md: MeasureDef): Differential.Case =
    Differential.Case(md.library, Context.Patient,
      link = Some(Differential.LinkSpec(md.deps.map(d => Pipeline().load(Paths.read(Paths.elm(d)))), md.roots)),
      parameters = Map("Measurement Period" -> md.measurementPeriod))

  /** The hand-written answer key (`CONF-GATE-SCOREKEY`): `patientId -> (populationCode -> 0|1)`. */
  def answerKey(md: MeasureDef): Map[String, Map[String, Int]] = {
    val patients = ujson.read(Paths.read(new File(md.dataDir, "answer-key.json")))("patients").obj
    patients.map { case (id, v) =>
      id -> md.populations.map { case (_, code) => code -> v(code).num.toInt }.toMap
    }.toMap
  }

  /** A scalar/Unfiltered fixture: no data, no terminology, a single synthetic subject. */
  private def scalar(library: String, label: String,
                     deviations: Seq[Differential.Deviation] = Nil): Entry =
    Entry(label, () => Differential.Case(library, Context.Unfiltered, deviations = deviations))

  /** A FHIR Patient fixture: inject the sample-data `DataProvider` (+ optional terminology), and
    * the reference reads the SAME NDJSON resources + value-set expansions. */
  private def fhir(library: String, withTerminology: Boolean, label: String,
                   deviations: Seq[Differential.Deviation] = Nil): Entry =
    Entry(label, () => {
      val spark = SparkConnect.session()
      val dataDir = Paths.dataDir.getAbsolutePath
      val resources = Map(
        "Patient"           -> readLines("Patient.ndjson"),
        "Condition"         -> readLines("Condition.ndjson"),
        "Encounter"         -> readLines("Encounter.ndjson"),
        "Observation"       -> readLines("Observation.ndjson"),
        "MedicationRequest" -> readLines("MedicationRequest.ndjson"),
        // Context-less resource reached by a reference follow (FhirReferenceFollow, LANG-REFERENCE-FOLLOW).
        "Medication"        -> readLines("Medication.ndjson"))
      val expansions =
        if (withTerminology)
          Pipeline().normalize(Paths.read(Paths.elm(library))).valueSets
            .map(vs => vs.id -> readExpansion(vs.id)).toMap
        else Map.empty[String, Set[(String, String)]]
      Differential.Case(
        library,
        Context.Patient,
        data = Some(new SparkFhirData(spark, dataDir)),
        terminology = if (withTerminology) Some(new SparkTerminology(spark, dataDir)) else None,
        refEnv = GroundTruthRunner.RefEnv(resourcesByType = resources, expansions = expansions,
          extraLibrarySources = librarySources(library)),
        deviations = deviations)
    })

  /** A QICore fixture over `data/qicore/` (shared by the small QICore fixtures, kept apart from `data/fhir/`
    * so their rows cannot move another fixture's answers). Every resource file there feeds both sides; the
    * value sets come from [[valueSetDir]]. */
  private def qicore(library: String, label: String,
                     deviations: Seq[Differential.Deviation] = Nil): Entry =
    Entry(label, () => {
      val spark = SparkConnect.session()
      val dir = qicoreDataDir
      val resources = Option(dir.listFiles((_, n) => n.endsWith(".ndjson"))).getOrElse(Array.empty[File])
        .map(f => f.getName.stripSuffix(".ndjson") -> Paths.read(f).linesIterator.filter(_.trim.nonEmpty).toSeq)
        .toMap
      val expansions = Pipeline().normalize(Paths.read(Paths.elm(library))).valueSets
        .map(vs => vs.id -> measureExpansion(vs.id)).toMap
      Differential.Case(
        library,
        Context.Patient,
        data = Some(new SparkFhirData(spark, dir.getAbsolutePath)),
        terminology = if (expansions.nonEmpty) Some(new SparkTerminology(spark, valueSetDir.getAbsolutePath)) else None,
        refEnv = GroundTruthRunner.RefEnv(resourcesByType = resources, expansions = expansions,
          extraLibrarySources = librarySources(library)),
        deviations = deviations)
    })

  /** The data the small QICore fixtures share. */
  def qicoreDataDir: File = new File(Paths.dataDir.getParentFile, "qicore")

  /** A multi-library measure (CONF-GATE-MEASURE): flatten `md.library` with its `deps` (linked + pruned to
    * `md.roots`) and run the SAME differential. The dependency CQL sources are registered on both sides so
    * the measure compiles, and the measurement period is passed to both as a run-time parameter. */
  private def measure(md: MeasureDef): Entry =
    Entry(md.label, () => {
      val spark = SparkConnect.session()
      def lines(file: String): Seq[String] =
        Paths.read(new File(md.dataDir, file)).linesIterator.filter(_.trim.nonEmpty).toSeq

      val resources = md.resourceTypes.map(t => t -> lines(s"$t.ndjson")).toMap
      val rootLib = Pipeline().load(Paths.read(Paths.elm(md.library)))
      val depLibs = md.deps.map(d => Pipeline().load(Paths.read(Paths.elm(d))))
      // Expand the value sets declared in the ROOT measure AND its dependency libraries: a dependency arm's
      // value-set retrieve must be filtered on the reference side too. The reference's retrieve provider
      // treats an expansion it doesn't hold as "no filter" (returns everything), so a missing dependency
      // value set would silently over-match and diverge from the engine.
      val expansions = (rootLib.valueSets ++ depLibs.flatMap(_.valueSets))
        .map(vs => vs.id -> measureExpansion(vs.id)).toMap
      val depSources = md.deps.map(d => Paths.read(Paths.cql(d)))
      val parameters = Map("Measurement Period" -> md.measurementPeriod)

      Differential.Case(
        md.library,
        Context.Patient,
        data = Some(new SparkFhirData(spark, md.dataDir.getAbsolutePath)),
        terminology = Some(new SparkTerminology(spark, valueSetDir.getAbsolutePath)),
        refEnv = GroundTruthRunner.RefEnv(
          resourcesByType = resources, expansions = expansions, extraLibrarySources = depSources,
          parameters = parameters),
        link = Some(Differential.LinkSpec(depLibs, md.roots)),
        deviations = md.deviations,
        parameters = parameters)
    })

  /** Every differential fixture, in suite order. Labels carry the realized requirement ids. */
  private val allFixtures: Seq[Entry] = Seq(
    // --- Scalar / Unfiltered (LANG-3VL, LANG-FUNCTION, LANG-LIST, LANG-QUERY, LANG-PARAMETER) ---
    scalar("Logic3VL", "Logic3VL: 3VL truth tables diff clean (LANG-3VL)"),
    scalar("Comparison", "Comparison: = vs ~ and 3VL comparisons diff clean (LANG-EQUIV)",
      // CONF-REF-DEVIATION: the reference's `EquivalentEvaluator` compares strings with a bare
      // `equalsIgnoreCase` and does NO whitespace normalization, so it answers false where the CQL
      // Reference ("all whitespace characters are treated as equivalent") says a tab and a space are
      // equivalent. We follow the SPEC and declare the divergence rather than match an upstream bug.
      // The sibling `RunNotCollapsed`/`NotTrimmed` defines are NOT declared: both sides say false
      // there, which is what pins that the fix normalizes per CHARACTER and not per run.
      Seq(Differential.ValueFor("StringEquivTabIsSpace", SystemColumns.UnfilteredSubject,
        "CQF 4.8.0 EquivalentEvaluator uses equalsIgnoreCase — no whitespace normalization (LANG-EQUIV)"))),
    scalar("Conditionals", "Conditionals: if/case with null-condition-goes-else diff clean (ARCH-CONDITIONAL)"),
    scalar("Nullological", "Nullological: Coalesce/IsNull diff clean"),
    scalar("Arithmetic", "Arithmetic: arithmetic incl. runtime conditions (÷0 → null) diff clean"),
    scalar("Functions", "Functions: user-defined functions inline and diff clean (LANG-FUNCTION)"),
    scalar("TrivialBoolean", "TrivialBoolean: scalar define DAG (ExpressionRef) diffs clean end-to-end"),
    scalar("Parameters", "Parameters: defaulted parameters bind as broadcast scalars and diff clean (LANG-PARAMETER)"),
    scalar("Lists", "Lists: ordered list operators diff clean (LANG-LIST)"),
    scalar("Queries", "Queries: single-source where/return queries diff clean (LANG-QUERY)"),
    scalar("LetQueryInline", "LetQueryInline: correlated singleton-source let-query inlines (body[a:=e]) in Normalize (LANG-QUERY/ARCH-TYPE-ATTR)"),
    scalar("QueryLet", "QueryLet: query `let` clause — iteration-scoped named bindings (source-alias + earlier-let deps) feeding where/return incl. a singleton conditional-return chain (LANG-QUERY-LET)"),
    // NOTE: ConceptResolution is a COMPILE-ONLY (Normalize-stage) fixture — a scalar concept value has no
    // lowering, so it is intentionally NOT in the executable Corpus; its transform is validated by the IR
    // assertion in TransformationCatalogSpec section 5 (LANG-TERMINOLOGY).
    scalar("SingletonSource", "SingletonSource: a single-valued query source binds the scalar (no posexplode/collect_list); a failing where -> null (LANG-QUERY)"),
    scalar("Intervals", "Intervals: interval construction + Allen-relation algebra diff clean (LANG-INTERVAL)"),
    scalar("IntervalUnbounded", "IntervalUnbounded: null interval bound reads as a +/-inf sentinel in the Allen algebra (LANG-INTERVAL)"),
    scalar("DecimalStep", "DecimalStep: Predecessor, Successor and HighBoundary over Decimal apply the 10^-8 step in the 8th decimal place (EXEC-SQL-DECIMAL-STEP)"),
    scalar("IntervalOpenClosed", "IntervalOpenClosed: open bounds normalize to closed via discrete successor/predecessor (LANG-INTERVAL)"),
    scalar("SetUnion", "SetUnion: list union has set semantics (dedup by identity), not UNION ALL (LANG-LIST)"),
    scalar("AggregateFold", "AggregateFold: query aggregate is a left fold -> Spark aggregate HOF (LANG-QUERY-AGGREGATE)"),
    scalar("AggregateFunctions", "AggregateFunctions: Sum/Min/Max/Avg/Median/Mode/Variance/StdDev/Product/GeometricMean/AllTrue/AnyTrue ignore nulls, are null over no non-null element, order dates precision-aware and convert quantity units (LANG-AGGREGATE-FUNCTIONS)",
      // CONF-REF-DEVIATION. The CQL Reference defines Product over the non-null elements; the reference's
      // ProductEvaluator returns null as soon as it meets a null element.
      Seq(Differential.ValueFor("ProductIgnoresNulls", SystemColumns.UnfilteredSubject,
        "CQF 4.8.0 ProductEvaluator returns null on a null element; the CQL Reference ignores nulls (Product)")) ++
      // The reference squares a quantity's unit through UCUM, which normalizes to SI base units (`0.0001 'm2'`);
      // the engine keeps the operands' unit (`1.0 'cm2'`), as its quantity Multiply does (`Multiply1CMBy2CM`).
      Seq(Differential.ValueFor("VarianceQuantity", SystemColumns.UnfilteredSubject,
        "CQF reference normalizes the squared unit to SI base (m2); the engine keeps cm2, as quantity Multiply does"))),
    scalar("Quantities", "Quantities: UCUM cross-unit comparison + arithmetic diff clean (LANG-QUANTITY)"),
    scalar("Temporal", "Temporal: precision-aware date comparison + Measurement-Period math diff clean (LANG-INTERVAL)"),
    scalar("TemporalPartialPrecision", "TemporalPartialPrecision: partial-precision DateTime comparison truth table vs a static bound — coarse prefix-equal -> NULL (alignment-moot), equal-precision decided (LANG-INTERVAL)"),
    scalar("ChoiceTypes", "ChoiceTypes: heterogeneous if/case + as/is over a discriminated struct diff clean (LANG-CHOICE)"),
    scalar("QueryClauses", "QueryClauses: list set-ops + sort/distinct/aggregate diff clean (LANG-QUERY/LANG-LIST)"),
    scalar("MultiSource", "MultiSource: multi-source Cartesian queries + tuple encoding diff clean (LANG-QUERY-MULTISOURCE)"),

    // --- FHIR Patient (DATA-RETRIEVER, LANG-TERMINOLOGY, LANG-QUERY, DATA-LIST-ENCODING) ---
    fhir("FhirRetrieve", withTerminology = false, "FhirRetrieve: exists [Condition] per patient diffs clean"),
    fhir("FhirHelpersFromContent", withTerminology = false, "FhirHelpersFromContent: an included FHIRHelpers resolves from the corpus beside the fixture, and its conversion diffs clean (LANG-FHIRHELPERS-FROM-CONTENT)"),
    fhir("FhirValueSet", withTerminology = true, "FhirValueSet: value-set-filtered retrieve diffs clean (LANG-TERMINOLOGY)"),
    fhir("FhirValueSetQuery", withTerminology = true, "FhirValueSetQuery: value-set-filtered retrieve as a general query source diffs clean (LANG-TERMINOLOGY/LANG-QUERY)"),
    fhir("FhirValueSetWhere", withTerminology = true, "FhirValueSetWhere: membership where conjunct + terminology pushdown self-diff clean (ARCH-PUSHDOWN/LANG-TERMINOLOGY)"),
    fhir("FhirTemporalWhere", withTerminology = false, "FhirTemporalWhere: precision-aware temporal where over a FHIR retrieve date diffs clean (LANG-INTERVAL)"),
    fhir("FhirNumerator", withTerminology = true, "FhirNumerator: combined value-set + date pushdown on one retrieve self-diffs clean (ARCH-PUSHDOWN)"),
    fhir("FhirWhere", withTerminology = false, "FhirWhere: where on a FHIR retrieve diffs clean (LANG-QUERY)"),
    fhir("FhirVsFunction", withTerminology = true, "FhirVsFunction: value set passed into a function carries through inlining (LANG-FUNCTION-TERMINOLOGY)"),
    fhir("FhirReturnList", withTerminology = false, "FhirReturnList: per-subject ordered return list diffs clean (DATA-LIST-ENCODING)"),
    fhir("FhirListWholeListOp", withTerminology = false, "FhirListWholeListOp: list define consumed by whole-list ops (in/Count) boxes back identically (DATA-LIST-ENCODING/DATA-DEFINE-DF)"),
    fhir("FhirResourceExport", withTerminology = false, "FhirResourceExport: raw-resource result exports by __resource_type/__resource_id identity and diffs clean (DATA-EXPORT)"),
    fhir("FhirBareRetrieveList", withTerminology = false, "FhirBareRetrieveList: a bare unfiltered-retrieve define realizes as a Relation view and diffs clean (DATA-DEFINE-DF/DATA-SKIP-UNFILTERED)"),
    fhir("FhirListDefineConsumed", withTerminology = false, "FhirListDefineConsumed: a bare-retrieve list define consumed as a Relation source folds the where per-consumer and diffs clean (DATA-SKIP-UNFILTERED/DATA-DEFINE-DF)"),
    fhir("FhirListDefineVsFilter", withTerminology = true, "FhirListDefineVsFilter: value-set membership folds per-consumer over a Relation define source (ARCH-PUSHDOWN/LANG-TERMINOLOGY/DATA-SKIP-UNFILTERED)"),
    fhir("FhirExistsCompound", withTerminology = true, "FhirExistsCompound: a compound where with a membership conjunct inside exists lowers via the general path and self-diffs clean (ARCH-PUSHDOWN/LANG-TERMINOLOGY)"),
    fhir("FhirDisjunctiveMembership", withTerminology = true, "FhirDisjunctiveMembership: a value-set membership BURIED in an `or` (not a top-level conjunct) is lifted to a per-row boolean flag column and referenced by the residual predicate — the `reasonCode in \"VS\" or …` shape and the SQL-backend InValueSet blocker (EXEC-SELFDIFF/LANG-TERMINOLOGY)"),
    fhir("FhirRetrieveRelation", withTerminology = true, "FhirRetrieveRelation: each retrieve reads the relation bound to its call site, the map's entry over the rule (BIND-RELATION/BIND-CALL-SITE)"),
    fhir("FhirRetrieveParameters", withTerminology = false, "FhirRetrieveParameters: a retrieve's request carries the constant parameters its define uses, read before parameters are inlined (BIND-RETRIEVE-REQUEST)"),
    fhir("FhirTerminologyLayers", withTerminology = true, "FhirTerminologyLayers: a value set is read from the first terminology relation that holds it (BIND-CALL-SITE/CLIENT-RESOLVER-OVERLAY)"),
    fhir("FhirDatePushdown", withTerminology = false, "FhirDatePushdown: date-range pushdown over a FHIR date-typed property (ToDate) folds at Date precision and self-diffs clean (ARCH-PUSHDOWN/LANG-INTERVAL)"),
    fhir("FhirDateArithmetic", withTerminology = false, "FhirDateArithmetic: minimal isolation of a measure's temporal-arithmetic tower (birthDate + years / end-of-interval boundary) — the make_interval/date_format tower diffs clean (LANG-INTERVAL/ARCH-TEMPORAL-CSE)"),
    fhir("FhirPeriodWhere", withTerminology = false, "FhirPeriodWhere: a FHIR Period property in a temporal where (ToInterval -> Allen during) diffs clean (LANG-INTERVAL)"),
    fhir("Relationships", withTerminology = false, "Relationships: with/without semi-/anti-joins over retrieves diff clean (LANG-QUERY-WITH)"),
    fhir("FhirMultiSource", withTerminology = false, "FhirMultiSource: multi-source FHIR + correlated where/such-that + with/without lists diff clean (LANG-QUERY-MULTISOURCE)"),
    fhir("FhirRelationClauses", withTerminology = false, "FhirRelationClauses: sort + aggregate over Relation lists diff clean (LANG-QUERY-SORT/-AGGREGATE)"),
    fhir("FhirConditional", withTerminology = false, "FhirConditional: if/case over different-source retrieve branches diff clean (ARCH-CONDITIONAL)"),
    fhir("FhirConditionalUnion", withTerminology = false, "FhirConditionalUnion: list-valued if/case lowers to a relation UNION (no collect_list/posexplode) and diffs clean vs the boxed self + Java reference (ARCH-COND-UNION)"),
    fhir("FhirNullableList", withTerminology = false, "FhirNullableList: list-valued if with a NULL else branch keeps the null/empty/non-empty tri-state via the per-subject nullity sidecar (no collect to subject grain) and diffs clean vs the boxed self + Java reference (ARCH-LIST-NULL-SIDECAR/LANG-LIST-NULL-STATE)"),
    fhir("FhirAggregateMostRecent", withTerminology = false, "FhirAggregateMostRecent: Max over the date-times a query over a retrieve returns, inline and through a list define; a patient with none is null (LANG-AGGREGATE-FUNCTIONS)"),
    fhir("FhirListCount", withTerminology = false, "FhirListCount: a grain-first list define consumed by Count is reduced at SUBJECT GRAIN (countDistinct, no collect_list box) and diffs clean vs the Java reference (ARCH-RELATION-REDUCE)"),
    fhir("FhirConditionalUnionHet", withTerminology = false, "FhirConditionalUnionHet: HETEROGENEOUS list-valued if/case unions into a Choice-tagged relation (no boxing) and diffs clean vs the Java reference (ARCH-COND-UNION Phase B)"),
    fhir("FhirChoiceRetrieve", withTerminology = false, "FhirChoiceRetrieve: FHIR value[x] choice projection/test (as/is) diff clean (LANG-CHOICE)"),
    fhir("FhirChoiceGate", withTerminology = false, "FhirChoiceGate: as/is over a MONOMORPHIC FHIR element is navigation/presence-test, not value[x] slot projection (LANG-CHOICE-MODEL)"),
    fhir("FhirChoiceUnionResources", withTerminology = false, "FhirChoiceUnionResources: union of two DIFFERENT resource types ([Encounter] union [Condition]) aligns whole resource bodies into one Choice-tagged relation and diffs clean vs the Java reference (LANG-CHOICE-RESOURCE)"),
    fhir("FhirChoiceConditionalResources", withTerminology = false, "FhirChoiceConditionalResources: list-valued if/case over different resource-type branches (Encounter vs Condition) unions into a Choice-tagged relation and diffs clean vs the Java reference (LANG-CHOICE-RESOURCE/ARCH-COND-UNION)"),
    fhir("FhirChoiceResourcePrimitive", withTerminology = false, "FhirChoiceResourcePrimitive: list-valued if mixing a resource body with a primitive/tuple (Choice<Encounter,Integer|String|Tuple>) aligns a deep resource schema against a flat scalar and diffs clean vs the Java reference (LANG-CHOICE-RESOURCE)"),
    fhir("FhirChoiceUnionLabsMeds", withTerminology = false, "FhirChoiceUnionLabsMeds: union/conditional over a second resource-type pair (Observation/MedicationRequest) diffs clean vs the Java reference (LANG-CHOICE-RESOURCE)"),
    fhir("FhirChoiceUnionNav", withTerminology = false, "FhirChoiceUnionNav: PERF/plan-shape reproducer — NAVIGATING a field on a heterogeneous resource union (( [Observation] union [MedicationRequest] ) I where I.status) forces per-arm dispatch over the full-resource discriminated Choice struct (LANG-CHOICE-RESOURCE)"),
    fhir("FhirHetUnionValueX", withTerminology = false, "FhirHetUnionValueX: an INLINE heterogeneous union query source navigating a value[x] on ONLY ONE arm + a plain field on the other, Coalesced — exercises aliasedSource.hetUnionAlias (demand-pruned discriminated struct) + rewriteHetUnionNav (per-member value[x]/field dispatch); the Coalesce of a performed and an authored interval across the arms (LANG-CHOICE-RESOURCE)"),
    fhir("FhirDisjunctiveTemporalExists", withTerminology = false, "FhirDisjunctiveTemporalExists: a boolean define ORing several correlated temporal exists — the six-arm exclusion plan shape whose final barrier localCheckpoint once OOMed the server. Each exists stages as its own barrier leaf (FrameLowering.presenceRef); inspect with -Dcqldatabricks.barrierTrace"),
    fhir("FhirReferenceFollow", withTerminology = false, "FhirReferenceFollow: a `with [Medication] M such that M.id = <ref>` reference-follow join over a CONTEXT-LESS resource — correlates on the reference id, not the patient subject (LANG-REFERENCE-FOLLOW); a medication-reference arm isolated"),
    fhir("FhirReferenceFollowValue", withTerminology = false, "FhirReferenceFollowValue: a `singleton from ([Encounter] E where E.id = <ref>)` read for its VALUE (a followed resource's field navigated in a return/where) lowers as a correlated projecting LEFT JOIN — the value cousin of the with-join; an `encounterFor().class.code` shape isolated to a plain-field follow (LANG-REFERENCE-FOLLOW)"),
    fhir("FhirScalarDefInWhere", withTerminology = false, "FhirScalarDefInWhere: a RELATION query whose `where` references a per-subject SCALAR sibling define (`where \"ObsCount\" >= 2`) LEFT-joins that define's per-subject view and resolves the reference to its value column — the `… same day as \"Most Recent Day\"` shape (LANG-QUERY-WHERE over a define DAG)"),
    fhir("FhirFluentSingletonQuery", withTerminology = false, "FhirFluentSingletonQuery: a fluent function whose SINGLETON-resource param body is a QUERY, called on an outer query alias — the inner query's source is an AliasRef to the outer singleton alias; combined's scope-aware `typeOf` augmentation types it as a singleton source (not a list `transform`), a medication-request period helper's shape (LANG-CHOICE-MODEL)"),
    fhir("FhirDeepNavigation", withTerminology = false, "FhirDeepNavigation: navigation the old depth cap dropped — a value in an extension nested inside a resource extension, a dose bound five levels down, and a read through the Identifier.assigner cut (DATA-MODEL-SCHEMA/DATA-MODEL-CUTS)"),
    fhir("FhirNestedChoiceValue", withTerminology = false, "FhirNestedChoiceValue: a value[x] choice reached through a `let`/nav CHAIN (`comp.value as Quantity` where `comp` = `singleton from R.component`) has no alias root — rewriteNestedChoiceValueX resolves the base type via the query scope and dispatches to the `valueQuantity` slot; the nested-value[x] shape of a medication-request period helper (LANG-CHOICE-MODEL)"),
    fhir("FhirNestedQuantityValue", withTerminology = false, "FhirNestedQuantityValue: `( comp.value as Quantity ).value` (comp a let off R.component) — scope-aware body typing + `.value` reading a System.Quantity's numeric FIELD + `as Quantity` normalizing an already-quantity slot to {value,unit}; a medication-request period helper's `dose.value`/`quantity.value` shape (LANG-CHOICE-MODEL/LANG-QUANTITY)"),
    fhir("FhirCorrelatedExistsDisjunction", withTerminology = false, "FhirCorrelatedExistsDisjunction: a correlated `exists` in a DISJUNCTIVE where whose inner where repeats a value[x] Case referencing the inner alias; the outer CSE/hoist must not cross into the exists's alias scope (LANG-QUERY-WHERE)"),
    fhir("FhirPartialPrecision", withTerminology = false, "FhirPartialPrecision: a PARTIAL FHIR date compares at its own per-row precision; year vs day dates flip membership by the window's precision (LANG-INTERVAL)"),
    fhir("FhirTemporalDynamic", withTerminology = false, "FhirTemporalDynamic: TWO runtime operands (Condition.recordedDate during Encounter.period) — precision-aware comparison with no static bound diffs clean vs the Java reference (LANG-INTERVAL)"),
    // --- QICore (`data/qicore/`) ---
    qicore("FhirProfileOnlyRetrieve", "FhirProfileOnlyRetrieve: a profile-only retrieve keeps the resources that claim the profile or claim none, and drops one that claims only another (LANG-RETRIEVE-PROFILE)"),
    qicore("FhirNegationRationale", "FhirNegationRationale: a procedure not done, with a reason in a value set and a QI-Core recorded time in the period (LANG-RETRIEVE-PROFILE/LANG-TERMINOLOGY)"),
    qicore("FhirDayPrecisionDuring", "FhirDayPrecisionDuring: `during day of` admits a visit (and a visit's start, a point) on a boundary day outside the window's time of day; plain `during` does not (LANG-INTERVAL-PRECISION)"),
    qicore("FhirChoiceValueOffDefine", "FhirChoiceValueOffDefine: `is T`, `is null` and `as T` on the value[x] of a define holding one resource dispatch to the typed slots; an absent value is a null quantity (LANG-CHOICE-MODEL)",
      // CONF-REF-DEVIATION: v4's and q3's results have no value. The CQL spec says `null is T` is false ("the
      // expression null is Integer returns false", CQL Reference, Types Operators, "Is"); the reference answers null.
      deviations = for (d <- Seq("Result Is Quantity", "Result Is String"); subject <- Seq("v4", "q3"))
        yield Differential.ValueFor(d, subject,
          "CQF 4.8.0 answers null for `is` on a null value; the CQL spec says false (Types Operators, Is)")),
    qicore("FhirSubjectReferenceFields", "FhirSubjectReferenceFields: Coverage keys by `beneficiary` and Immunization by `patient` in the Patient context; a list of Concepts renders alike on both sides (DATA-SUBJECT-KEY)"),
    qicore("FhirAgeIntervalBoundaries", "FhirAgeIntervalBoundaries: an age-coded onset is birthDate + the age, with the unit read from the data, and lands on the period boundaries (LANG-CHOICE-MODEL/LANG-QUANTITY)",
      deviations = Seq(
        Differential.ValueFor("Onset Spans", "a5", "a5 has no birthDate, so both age bounds are null. We return the "
        + "struct `{low: null, high: null}`; the reference collapses it to null. We keep the struct: collapsing is "
        + "non-injective — it destroys the difference between NO interval and an interval whose bounds are both "
        + "UNKNOWN — and this repo made the same call one type over, where `LANG-LIST-NULL-STATE`/"
        + "`ARCH-LIST-NULL-SIDECAR` keep null and empty distinct. ELM does not settle it: type specifiers carry no "
        + "nullability. Matching the reference measured +15% to +40% emitted SQL across the measures of the time "
        + "(a guard on EVERY interval build)."),
        Differential.ValueFor("Onset Overlaps Period", "a5",
          "Follows from the `Onset Spans` a5 deviation: we read the null bounds of the kept struct as unbounded, "
            + "so it overlaps the period; the reference has no interval at all, so it does not."))))

  /** Every differential fixture the executing suites iterate (SqlDifferentialSpec, the primary gate): the
    * fixtures above, then every discovered measure ([[measureDefs]]).
    *
    * A run with no Connect server skips every executing fixture (the `requireConnect` pattern), the measures
    * included, so the no-server compiler runs stay green. Set `-Dcqldatabricks.skipMeasures=true` to drop the
    * measures for a fast focused iteration. */
  lazy val all: Seq[Entry] =
    if (skipMeasures) allFixtures else allFixtures ++ measureDefs.map(_.entry)

  /** True when `-Dcqldatabricks.skipMeasures=true` drops the measures from every measure gate. */
  def skipMeasures: Boolean = sys.props.get("cqldatabricks.skipMeasures").contains("true")

  /** Every measure under [[measuresDataDir]], in name order (CONF-MEASURE-DISCOVERY). An empty corpus is an
    * error, not a vacuous pass: every measure gate iterates this. */
  lazy val measureDefs: Seq[MeasureDef] = {
    val dirs = Option(measuresDataDir.listFiles(f => f.isDirectory && new File(f, "answer-key.json").isFile))
      .getOrElse(Array.empty[File]).sortBy(_.getName).toSeq
    require(dirs.nonEmpty, s"no measure under $measuresDataDir (a directory with an answer-key.json)")
    dirs.map(discover)
  }

  /** Every measure, for the HERMETIC link+prune+normalize gate ([[MeasureLinkSpec]]) and the other Spark-free
    * measure specs. The same set as [[measureDefs]]: every discovered measure has an answer key. */
  lazy val linkMeasures: Seq[MeasureDef] = measureDefs

  /** Library name -> [[MeasureDef]] for the parameterized scale harnesses ([[MeasureScaleSpec]] and the
    * others): every discovered measure is selectable by name via `-Dcqldatabricks.scaleMeasure=<library>`. */
  lazy val scaleMeasures: Map[String, MeasureDef] = measureDefs.map(md => md.library -> md).toMap

  /** The measure a single-measure harness runs when `-Dcqldatabricks.scaleMeasure` is unset: the first by name. */
  def defaultScaleMeasure: String = measureDefs.head.library

  /** One measure from its data directory. The directory is named for the library, and the answer key must
    * agree, so a rename that misses one of them fails here rather than gating the wrong library. */
  private def discover(dir: File): MeasureDef = {
    val key = ujson.read(Paths.read(new File(dir, "answer-key.json")))
    val library = key("measure").str
    require(library == dir.getName, s"${dir.getName}/answer-key.json names measure '$library'; the directory " +
      "must be named for the library")
    val populations = key("populations").obj.toSeq.map { case (define, code) => define -> code.str }
    val supplemental = key.obj.get("supplemental").map(_.arr.map(_.str).toSeq).getOrElse(Nil)
    val resourceTypes = Option(dir.listFiles((_, n) => n.endsWith(".ndjson"))).getOrElse(Array.empty[File])
      .map(_.getName.stripSuffix(".ndjson")).sorted.toSeq
    val patients = key("patients").obj.size
    MeasureDef(library,
      version = key("version").str,
      deps = includeClosure(library),
      dataSubdir = dir.getName,
      resourceTypes = resourceTypes,
      populations = populations,
      measurementPeriod = key("measurementPeriod").str,
      label = s"$library: ${populations.map(_._1).mkString(", ")} over $patients synthetic patients (CONF-GATE-MEASURE)",
      extraRoots = supplemental)
  }

  /** The CQL of every library `library` includes, transitively: the sources the reference is given so its
    * includes resolve, FHIRHelpers among them (LANG-FHIRHELPERS-FROM-CONTENT). */
  def librarySources(library: String): Seq[String] = includeClosure(library).map(d => Paths.read(Paths.cql(d)))

  /** The transitive `include` closure of a library, read from its translated ELM, FHIRHelpers included: it is
    * content like any other library (LANG-FHIRHELPERS-FROM-CONTENT). Dependencies come before the libraries
    * that include them. */
  private def includeClosure(library: String): Seq[String] = {
    val seen = scala.collection.mutable.LinkedHashSet.empty[String]
    def includes(name: String): Seq[String] =
      ujson.read(Paths.read(Paths.elm(name)))("library").obj.get("includes")
        .map(_("def").arr.map(_("path").str).toSeq).getOrElse(Nil)
    def visit(name: String): Unit = includes(name).foreach { dep =>
      if (!seen.contains(dep)) { visit(dep); seen += dep }
    }
    visit(library)
    seen.toSeq
  }

  private def readLines(file: String): Seq[String] =
    Paths.read(new File(Paths.dataDir, file)).linesIterator.filter(_.trim.nonEmpty).toSeq

  /** Read a value set's `(system, code)` expansion from `valueset-<lastUrlSegment>.ndjson`. */
  private def readExpansion(valueSetId: String): Set[(String, String)] = {
    val seg = valueSetId.split("/").last
    readLines(s"valueset-$seg.ndjson").map { line =>
      val o = ujson.read(line)
      (o("system").str, o("code").str)
    }.toSet
  }
}
