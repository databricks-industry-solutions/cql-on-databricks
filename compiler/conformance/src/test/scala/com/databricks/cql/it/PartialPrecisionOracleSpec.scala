package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.harness.GroundTruthRunner
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** Pins the precision-aware temporal comparison truth table to the cqframework Java reference
  * (CONF-GATE-MEASURE) WITHOUT Spark — so the semantics stay gated even when no Connect server is up,
  * and a future length-dispatch lexical emitter that drifts from the oracle fails here before the
  * (Spark-dependent) differential ever runs. The companion executable differential over the SAME two
  * fixtures (engine vs reference) is registered in [[Corpus]] (TemporalPartialPrecision, FhirTemporalDynamic).
  *
  * The reference is COMPONENT-WISE-NULL (verified): a coarse operand whose prefix equals the bound at
  * the operand's precision is NULL unless both operands share precision, and bound ALIGNMENT does not
  * make it decidable — refuting the "totality under alignment" / successor-normalization shortcuts. */
@RunWith(classOf[JUnitRunner])
class PartialPrecisionOracleSpec extends AnyFunSuite {

  // ---- Static cross-product (System model; no Spark, no data) ----
  private lazy val static: Map[String, ujson.Value] =
    GroundTruthRunner.evaluateFromElm(
      cqlText = Paths.read(Paths.cql("TemporalPartialPrecision")),
      elmJson = Paths.read(Paths.elm("TemporalPartialPrecision")))

  private def opt(name: String): Option[Boolean] = static(name) match {
    case ujson.Null  => None
    case ujson.True  => Some(true)
    case ujson.False => Some(false)
    case other       => fail(s"$name is not a Boolean: $other")
  }

  test("a coarse operand prefix-equal to the bound is NULL — regardless of bound alignment") {
    val nullCells = Seq(
      // year operand vs a full bound, prefix-equal at year — every operator
      "Yr_Eq_Lt", "Yr_Eq_Le", "Yr_Eq_Gt", "Yr_Eq_Ge", "Yr_Eq_Eq",
      // SAME year operand vs a CALENDAR-ALIGNED full bound — still NULL (alignment is moot)
      "Yr_AlignedEq_Lt", "Yr_AlignedEq_Le", "Yr_AlignedEq_Gt", "Yr_AlignedEq_Ge",
      // month/day operands prefix-equal to a finer bound
      "Mo_Eq_Lt", "Mo_Eq_Ge", "Day_Eq_Lt", "Day_Eq_Ge")
    for (n <- nullCells) assert(opt(n).isEmpty, s"$n must be NULL (insufficient precision to decide)")
  }

  test("equality at the lesser precision is DECIDED only when both operands share precision") {
    // day vs day-precision bound
    assert(opt("DayVsDay_Eq_Lt").contains(false))
    assert(opt("DayVsDay_Eq_Le").contains(true))
    assert(opt("DayVsDay_Eq_Gt").contains(false))
    assert(opt("DayVsDay_Eq_Ge").contains(true))
    assert(opt("DayVsDay_Eq_Eq").contains(true))
    // full vs full bound
    assert(opt("Full_Eq_Lt").contains(false))
    assert(opt("Full_Eq_Le").contains(true))
    assert(opt("Full_Eq_Gt").contains(false))
    assert(opt("Full_Eq_Ge").contains(true))
    assert(opt("Full_Eq_Eq").contains(true))
  }

  test("clearly-ordered coarse operands are decided (prefix differs)") {
    assert(opt("Yr_Below_Lt").contains(true));  assert(opt("Yr_Below_Gt").contains(false))
    assert(opt("Yr_Below_Le").contains(true));  assert(opt("Yr_Below_Ge").contains(false))
    assert(opt("Yr_Below_Eq").contains(false))
    assert(opt("Yr_Above_Lt").contains(false)); assert(opt("Yr_Above_Ge").contains(true))
    assert(opt("Mo_Below_Lt").contains(true));  assert(opt("Mo_Above_Ge").contains(true))
    assert(opt("Day_Below_Lt").contains(true)); assert(opt("Day_Above_Ge").contains(true))
    assert(opt("Full_Below_Lt").contains(true)); assert(opt("Full_Above_Gt").contains(true))
  }

  test("aligned-bound boundary cases are NULL (alignment does NOT make a coarse operand total)") {
    // The disputed cells: a day/year operand whose prefix equals an ALIGNED MP bound. cqframework
    // returns NULL; the engine must match (the differential gate proves engine==reference here too).
    assert(opt("DayEdge_Lo_Le").isEmpty)             // @2023-01-01T00:00:00 <= @2023-01-01
    assert(opt("DayEdge_Hi_Le").isEmpty)             // @2023-12-31 <= @2023-12-31T23:59:59
    assert(opt("IvDayEdge_During_MPfull").isEmpty)   // day interval on the MP edge during full MP
    assert(opt("PtDayEdge_In_MPfull").isEmpty)       // day point at the lower MP edge
  }

  test("interval 'during' / point 'in' over a full-precision Measurement Period") {
    assert(opt("IvYr_During_MPfull").isEmpty)            // year interval spills sub-precision past MP -> NULL
    assert(opt("IvDay_During_MPfull").contains(true))    // day interval decidably inside
    assert(opt("IvYr_During_MPyr").contains(true))       // same (year) precision both sides -> decided
    assert(opt("PtYr_In_MPfull").isEmpty)                // year point vs full bounds -> NULL
    assert(opt("PtDay_In_MPfull").contains(true))        // day point decidably inside
  }

  // ---- Dynamic: two RUNTIME operands (Condition.recordedDate during Encounter.period) ----
  private def lines(file: String): Seq[String] =
    Paths.read(new File(Paths.dataDir, file)).linesIterator.filter(_.trim.nonEmpty).toSeq

  private def strSet(v: ujson.Value): Set[String] = v match {
    case ujson.Arr(xs) => xs.map(_.str).toSet
    case other         => fail(s"expected a list, got $other")
  }

  test("dynamic condition-during-encounter is precision-aware per row (vs the Java reference)") {
    val refEnv = GroundTruthRunner.RefEnv(
      contextName = Context.Patient.name,
      resourcesByType = Map(
        "Patient"   -> lines("Patient.ndjson"),
        "Condition" -> lines("Condition.ndjson"),
        "Encounter" -> lines("Encounter.ndjson")),
      subjectIds = Seq("p1", "p2", "p3", "p4", "p5", "p6", "p7"),
      extraLibrarySources = Corpus.librarySources("FhirTemporalDynamic"))

    val r = GroundTruthRunner.evaluate(
      cqlText = Paths.read(Paths.cql("FhirTemporalDynamic")),
      libraryName = "FhirTemporalDynamic",
      defineNames = Seq("CondDatesDuringEnc", "HasCondDuringEnc"),
      env = refEnv)

    // p6: ep6 = June-2023 full window. c6a 2023-06-15 (day) decidably inside -> kept;
    //     c6b "2023" (year vs full) undecidable -> dropped; c6c 2024-06-15 outside -> dropped.
    assert(strSet(r("p6")("CondDatesDuringEnc")) == Set("2023-06-15"))
    // p7: ep7 = all-of-2023 window. c7a "2023-06" (month) decidably inside -> kept;
    //     c7b "2023" (year vs full) undecidable -> dropped.
    assert(strSet(r("p7")("CondDatesDuringEnc")) == Set("2023-06"))
    // patients without a decidable condition-in-encounter collect nothing.
    for (p <- Seq("p1", "p2", "p3", "p4", "p5"))
      assert(strSet(r(p)("CondDatesDuringEnc")).isEmpty, s"$p should have no condition during an encounter")

    assert(r("p6")("HasCondDuringEnc") == ujson.True)
    assert(r("p7")("HasCondDuringEnc") == ujson.True)
    for (p <- Seq("p1", "p2", "p3", "p4", "p5"))
      assert(r(p)("HasCondDuringEnc") == ujson.False, s"$p should have no condition during an encounter")
  }
}
