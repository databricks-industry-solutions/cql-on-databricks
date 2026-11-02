package com.databricks.cql.it

import com.databricks.cql.passes.Pipeline
import org.w3c.dom.{Element, Node}

import javax.xml.parsers.DocumentBuilderFactory
import scala.collection.mutable.ArrayBuffer

/** The vendored `cqframework` CQL operator test suite, parsed into cases
  * (`CONF-GATE-OPERATOR`). The suite ([cql-tests, Apache-2.0](src/test/resources/cql-tests)) is the
  * official per-operator conformance corpus: each `<test>` is a single CQL `<expression>` with the
  * spec-declared `<output>`. We turn each case into a `define` and run it through the SAME engine
  * path the whole-measure differential uses (PROC-DRY), giving Gate 1 systematic per-operator
  * breadth beyond the hand + property seed (`Bool3Spec`/`CqlIntervalSpec`/`PropertyDifferentialSpec`).
  *
  * This object is the pure parser + CQL-library generator (no Spark): [[OperatorTranslationSpec]]
  * uses it to gate translation/normalization (no server), and [[OperatorConformanceSpec]] uses it to
  * gate execution vs the CQF reference and the declared output. */
object OperatorTests {

  /** One operator test case lifted from the XML. `output` is itself a CQL expression (the declared
    * expected value); `invalid` marks cases the spec expects to fail to translate/evaluate. */
  final case class OpCase(
      file: String,
      group: String,
      name: String,
      expression: String,
      output: Option[String],
      capabilities: Set[String],
      invalid: Option[String]) {
    /** A stable, unique, CQL-quotable define id for the expression. group/name are alphanumeric in
      * the suite, so the quoted identifier is always well-formed. */
    def exprDefine: String = s"${group}__$name"
    def expectedDefine: String = s"${exprDefine}__exp"
  }

  /** The vendored files in this slice, aligned to operators already built + gated elsewhere
    * (arithmetic / comparison / logical / nullological / aggregate). Expanded file-by-file as the engine grows
    * (the doc's "iterate on UnsupportedNode" loop). */
  val Files: Seq[String] = Seq(
    "CqlLogicalOperatorsTest",
    "CqlNullologicalOperatorsTest",
    "CqlComparisonOperatorsTest",
    "CqlArithmeticFunctionsTest",
    "CqlAggregateFunctionsTest")

  /** Capabilities this slice does not yet claim: cases requiring them are skipped (not failed), so
    * the gate measures real coverage without false reds. Documented frontier, narrowed as support
    * lands (`system.long` = 64-bit Long; `tuple` = structural tuple equality/`~`). */
  val UnsupportedCapabilities: Set[String] = Set("system.long", "tuple")

  /** The engine's operator frontier WITHIN the vendored files: ELM node kinds that do not yet lower
    * to a kernel node, each with a reason. A case whose expression/output needs one of these is
    * SKIPPED (not failed) — it is a tracked Stage-4 engine-expansion backlog item, not a silent
    * fallback (CORE-NO-SILENT-MISLOWER). A normalize failure for ANY OTHER node kind is a real
    * failure: the set is the precise, machine-checked statement of what is not yet supported, so a
    * regression (a previously-supported node breaking) or a brand-new gap can never hide. Surfaced
    * by [[OperatorTranslationSpec]]; narrowed as operators land (then verified by the execution gate).
    *
    * NB: `Today`/`Now`/`TimeOfDay` read a runtime clock — even once lowered they cannot be
    * value-diffed deterministically against the reference, so they stay excluded by design. */
  val UnsupportedNodes: Map[String, String] = Map(
    "Today"        -> "runtime clock; nondeterministic, cannot be value-diffed",
    "Now"          -> "runtime clock; nondeterministic, cannot be value-diffed",
    "TimeOfDay"    -> "runtime clock; nondeterministic, cannot be value-diffed")

  /** The classification of a single case's translate+normalize attempt. */
  sealed trait Norm
  case object Ok extends Norm
  /** Fails solely because it needs a documented-unsupported node kind — skipped, not failed. */
  final case class Unsupported(node: String) extends Norm
  /** Fails for any other reason — a real failure the translation gate must surface. */
  final case class Broken(why: String) extends Norm

  private val UnknownExprRe = """UnknownExpr\(([A-Za-z0-9]+)\)""".r

  /** Translate + normalize one CQL library; classify the outcome against [[UnsupportedNodes]]. */
  private def normProbe(cqlText: String): Norm =
    try { Pipeline().normalizeCql(cqlText); Ok }
    catch {
      case t: Throwable =>
        val msg = Option(t.getMessage).getOrElse(t.getClass.getSimpleName)
        UnknownExprRe.findFirstMatchIn(msg).map(_.group(1)) match {
          case Some(node) if UnsupportedNodes.contains(node) => Unsupported(node)
          case Some(node) => Broken(s"unsupported node '$node' is not in UnsupportedNodes: $msg")
          case None        => Broken(msg)
        }
    }

  /** Classify a case by probing BOTH its expression and its declared output (the execution gate
    * evaluates both): Broken wins over Unsupported wins over Ok. */
  def classify(c: OpCase): Norm = {
    val probes = Seq(
      singleExprLibrary(c),
      s"""library OpProbe version '1.0.0'\n\ndefine "${c.expectedDefine}": ${c.output.get}\n""")
      .map(normProbe)
    probes.collectFirst { case b: Broken => b }
      .orElse(probes.collectFirst { case u: Unsupported => u })
      .getOrElse(Ok)
  }

  /** Included cases that fully normalize (expression + output) — the breadth the execution gate
    * runs and diffs vs the reference. */
  def supported(file: String): Seq[OpCase] =
    included(load(file)).filter(c => classify(c) == Ok)

  /** Parse one vendored XML file (on the test classpath at `/cql-tests/<file>.xml`) into its cases.
    * Uses the JDK DOM parser (no scala-xml dependency); the parser decodes the `&lt;`/`&gt;` that
    * the operator expressions are full of, which a regex never could. */
  def load(file: String): Seq[OpCase] = {
    val res = s"/cql-tests/$file.xml"
    val is = Option(getClass.getResourceAsStream(res))
      .getOrElse(throw new IllegalStateException(s"vendored operator suite not on classpath: $res"))
    val doc =
      try {
        val f = DocumentBuilderFactory.newInstance()
        f.setNamespaceAware(false) // the suite declares a default xmlns; ignore it for simple lookups
        f.newDocumentBuilder().parse(is)
      } finally is.close()

    val out = ArrayBuffer.empty[OpCase]
    val groups = doc.getDocumentElement.getElementsByTagName("group")
    var gi = 0
    while (gi < groups.getLength) {
      val group = groups.item(gi).asInstanceOf[Element]
      val groupName = group.getAttribute("name")
      val groupCaps = childCapabilities(group)
      val tests = group.getElementsByTagName("test")
      var ti = 0
      while (ti < tests.getLength) {
        val test = tests.item(ti).asInstanceOf[Element]
        val exprEl = firstChild(test, "expression")
        exprEl.foreach { e =>
          val outputEl = firstChild(test, "output")
          out += OpCase(
            file = file,
            group = groupName,
            name = test.getAttribute("name"),
            expression = e.getTextContent.trim,
            output = outputEl.map(_.getTextContent.trim),
            capabilities = groupCaps ++ childCapabilities(test),
            invalid = Option(e.getAttribute("invalid")).map(_.trim).filter(_.nonEmpty))
        }
        ti += 1
      }
      gi += 1
    }
    out.toSeq
  }

  /** Cases included in this slice: a translatable expression with a declared output and no
    * unsupported capability. Invalid (expected-error) cases are a separate negative path, deferred. */
  def included(cases: Seq[OpCase]): Seq[OpCase] =
    cases.filter(c =>
      c.invalid.isEmpty &&
        c.output.isDefined &&
        c.capabilities.intersect(UnsupportedCapabilities).isEmpty)

  /** Build a single CQL library for a file's included cases: each case yields the expression define
    * and its declared-output define, so the engine and the reference both evaluate the SAME text. */
  def libraryText(file: String, cases: Seq[OpCase]): String = {
    val defines = cases.flatMap { c =>
      Seq(
        s"""define "${c.exprDefine}": ${c.expression}""",
        s"""define "${c.expectedDefine}": ${c.output.get}""")
    }
    s"library Op$file version '1.0.0'\n\n${defines.mkString("\n")}\n"
  }

  /** A minimal one-define library for isolating a single case's translation/normalization. */
  def singleExprLibrary(c: OpCase): String =
    s"""library OpProbe version '1.0.0'\n\ndefine "${c.exprDefine}": ${c.expression}\n"""

  /** A one-case library carrying BOTH the expression and its declared output — used by the
    * execution gate's per-case lower probe (so a single case's lowering-frontier failure is
    * isolated from the rest of the file's batch). */
  def caseLibrary(c: OpCase): String =
    s"""library OpProbe version '1.0.0'\n\ndefine "${c.exprDefine}": ${c.expression}\ndefine "${c.expectedDefine}": ${c.output.get}\n"""

  /** Confirmed value divergences between the engine and the spec/reference that are TRACKED ENGINE
    * BUGS, not capability gaps — excluded from the execution diff so the gate is green, but recorded
    * here LOUDLY (not silently) so they are not forgotten. Each is a real correctness defect the
    * operator gate caught; fixing the engine then removes the entry and the case starts gating.
    * Empty: no engine value divergence is open. */
  val KnownDivergences: Map[(String, String, String), String] = Map.empty

  /** Cases the engine lowers to a STRUCTURALLY INVALID Spark plan — they pass normalize and the
    * Column-building lower, but Spark rejects the plan at analysis time (`AnalysisException`). These
    * are tracked engine LOWERING bugs the operator gate caught: the engine should either handle the
    * operation or fail loudly with `UnsupportedNode`, not emit a plan Spark can't analyze
    * (`CORE-NO-SILENT-MISLOWER`). Excluded from the diff so the gate is green, enumerated so a NEW
    * invalid-plan case fails the gate rather than hiding. Each is keyed by (file, group, name).
    *
    * Quantity unary minus (`-1'cm'`) lowers to `negative(struct(value, unit))`, and Quantity-by-
    * Integer (`10.0'g' / 5`) to a struct/int binary op — both reach Spark as a struct where a numeric
    * is required. The fix is quantity-aware unary/mixed arithmetic lowering (the quantity unit algebra
    * deferred from Phase 3). */
  val KnownInvalidPlan: Map[(String, String, String), String] = Map.empty // quantity unit-algebra landed

  /** Cases the engine lowers to a plan that passes Spark analysis but throws at EXECUTION (a runtime
    * error), tracked like [[KnownInvalidPlan]]. `DateTime(null)` builds a partial-timestamp STRING
    * with an empty year (`'01-01 00:00:00'`) that fails the timestamp cast at runtime — the null
    * year should short-circuit the whole constructor to null. A tracked engine bug the gate caught
    * (`CORE-NO-SILENT-MISLOWER`); fixing the DateTime/Date constructor null-component handling removes
    * the entry. */
  val KnownRuntimeError: Map[(String, String, String), String] =
    Map.empty // DateTime(null) null-component handling landed (per-row precision in dateStruct)

  /** Cases where the engine matches the spec's declared `<output>` but the **CQF reference** does
    * NOT — i.e. the reference engine itself diverges from the spec. For these the spec output is
    * authoritative (the official suite is ground truth), so the gate keeps the hard
    * engine-vs-declared-output anchor and only relaxes the reference cross-check. These are NOT
    * engine bugs — they are evidence the engine is MORE spec-faithful than the reference, and a
    * caution that the in-process reference is not infallible.
    *
    * Found by this gate: the reference WRAPS on 32-bit integer overflow where the spec says null
    * (`Ceiling(2147483647.2)` → spec null, engine null, reference -2147483648), and it rounds ties
    * toward zero where the spec (and the engine) round half away from zero (`Round(-0.5)` → spec
    * -1.0, engine -1.0, reference 0). */
  val KnownReferenceDivergence: Map[(String, String, String), String] = {
    val refOverflow = "CQF reference wraps on 32-bit integer overflow; spec (and engine) yield null"
    val refRounding = "CQF reference rounds ties toward zero; spec (and engine) round half away from zero"
    // The engine is zoneless (TIMESTAMP_NTZ, so the differential never drifts with the server
    // timezone); the reference renders a UTC DateTime with an explicit `+00:00` offset. Same instant,
    // and the engine matches the declared spec `<output>` literal exactly — only the reference's
    // offset-rendering cross-check is relaxed.
    val refZone = "engine is zoneless (TIMESTAMP_NTZ); reference renders +00:00 — same instant, engine matches the declared spec output"
    Map(
      ("CqlArithmeticFunctionsTest", "MinValue", "DateTimeMinValue") -> refZone,
      ("CqlArithmeticFunctionsTest", "MaxValue", "DateTimeMaxValue") -> refZone,
      ("CqlArithmeticFunctionsTest", "LowBoundary", "LowBoundaryDateTimeMillisecond")   -> refZone,
      ("CqlArithmeticFunctionsTest", "HighBoundary", "HighBoundaryDateTimeMillisecond") -> refZone,
      ("CqlArithmeticFunctionsTest", "Ceiling", "CeilingMaxIntegerAsDecimalWhereDecimalIsNonZero") -> refOverflow,
      ("CqlArithmeticFunctionsTest", "Ceiling", "CeilingDecimalGreaterThanMaxInteger")             -> refOverflow,
      ("CqlArithmeticFunctionsTest", "Ceiling", "CeilingDecimalLessThanMinInteger")                -> refOverflow,
      ("CqlArithmeticFunctionsTest", "Floor",   "FloorDecimalGreaterThanMaxInteger")               -> refOverflow,
      ("CqlArithmeticFunctionsTest", "Floor",   "FloorMinIntegerAsDecimalWhereDecimalIsNonZero")   -> refOverflow,
      ("CqlArithmeticFunctionsTest", "Floor",   "FloorDecimalLessThanMinInteger")                  -> refOverflow,
      ("CqlArithmeticFunctionsTest", "Round",   "RoundNeg0D5")                                      -> refRounding,
      ("CqlArithmeticFunctionsTest", "Round",   "RoundNeg1D5")                                      -> refRounding,
      // Reference Ln rounds the 8th decimal down where the spec (and engine) round half up:
      // ln(1000)=6.90775527898…, Round(…,8) = 6.90775528 (spec/engine) vs 6.90775527 (reference).
      ("CqlArithmeticFunctionsTest", "Ln", "Ln1000")  -> "reference Ln/Round yields 6.90775527; spec and engine yield 6.90775528 (correct half-up)",
      ("CqlArithmeticFunctionsTest", "Ln", "Ln1000D") -> "reference Ln/Round yields 6.90775527; spec and engine yield 6.90775528 (correct half-up)") ++ {
      // Cross-magnitude duration comparison: the spec's declared <output> is `true`, but the CQF
      // reference does not convert across duration magnitudes — it yields `null` for the precise `=`
      // chain (`1 day = 24 hours`) and `false` for the imprecise `~` relations (`1 year ~ 365 days`).
      // The engine matches the spec; only the reference cross-check is relaxed.
      val refDur = "CQF reference does not convert across duration magnitudes; spec (and engine) yield true"
      Map(
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestSecondEqualMilliseconds") -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestMinuteEqualSeconds")      -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestHourEqualMinutes")        -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestDayEqualHours")           -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestWeekEqualDays")           -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestYearEquivalentMonths")    -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestYearEquivalentDays")      -> refDur,
        ("CqlComparisonOperatorsTest", "Unit Comparison", "TestMonthEquivalentDays")     -> refDur,
        // Compound-unit product: the spec's declared <output> is `2.0'cm2'`; the reference normalizes
        // the result to the SI base (`2.0E-4 'm2'`). Same physical quantity; the engine keeps the
        // operands' unit (cm2) matching the spec, the reference cross-check is relaxed.
        ("CqlArithmeticFunctionsTest", "Multiply", "Multiply1CMBy2CM") ->
          "CQF reference normalizes the compound unit to SI base (m2); spec (and engine) keep cm2")
    }
  }

  /** Cases the **CQF reference cannot evaluate at all** — it raises an exception where the spec
    * declares a value — so there is no reference result to cross-check. The engine produces the
    * spec's declared `<output>`, so it is the more robust of the two; these are excluded from the
    * differential (no reference anchor exists) but enumerated loudly. NOT engine bugs.
    *
    * `Floor(2147483648)` / `Floor(-2147483649)`: the integer literal is one past the 32-bit range,
    * which the reference's parser/runtime rejects with an exception; the engine lowers it as a `long`
    * and `Floor`'s `toInt32OrNull` yields the spec's `null` (out-of-Integer-range result). */
  val KnownReferenceError: Map[(String, String, String), String] = {
    val refReject = "CQF reference raises on the out-of-Int32 literal; engine yields the spec's declared null"
    Map(
      ("CqlArithmeticFunctionsTest", "Floor", "FloorIntegerGreaterThanMaxInteger") -> refReject,
      ("CqlArithmeticFunctionsTest", "Floor", "FloorIntegerLessThanMinInteger")    -> refReject)
  }

  /** All cases excluded from the execution diff with a reason (value divergence, invalid plan, or
    * runtime error) — each a tracked, enumerated engine bug the operator gate has already caught. */
  def excluded: Map[(String, String, String), String] =
    KnownDivergences ++ KnownInvalidPlan ++ KnownRuntimeError ++ KnownReferenceError

  private def firstChild(e: Element, tag: String): Option[Element] = {
    val nl = e.getElementsByTagName(tag)
    var i = 0
    while (i < nl.getLength) {
      val n = nl.item(i)
      // getElementsByTagName is recursive; keep only DIRECT children of `e`.
      if (n.getParentNode eq e) return Some(n.asInstanceOf[Element])
      i += 1
    }
    None
  }

  private def childCapabilities(e: Element): Set[String] = {
    val nl = e.getChildNodes
    val caps = ArrayBuffer.empty[String]
    var i = 0
    while (i < nl.getLength) {
      val n = nl.item(i)
      if (n.getNodeType == Node.ELEMENT_NODE && n.getNodeName == "capability") {
        val code = n.asInstanceOf[Element].getAttribute("code")
        if (code.nonEmpty) caps += code
      }
      i += 1
    }
    caps.toSet
  }
}
