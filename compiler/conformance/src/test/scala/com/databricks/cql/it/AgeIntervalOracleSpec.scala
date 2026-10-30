package com.databricks.cql.it

import com.databricks.cql.harness.GroundTruthRunner
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SERVER-FREE oracle for the age arms (`EXEC-SQL-TEMPORAL-RUNTIME-UNIT`, `CONF-GROUND-TRUTH`): pins what
  * the Java CQF reference computes for `FhirAgeIntervalBoundaries`, and — the load-bearing part — asserts
  * the reference could evaluate EVERY subject.
  *
  * WHY THE SUBJECT-COVERAGE ASSERTION EXISTS. [[GroundTruthRunner]] drops a subject the reference cannot
  * evaluate, logs it to stderr, and returns the rest; the differential then diffs only what survived. Its only
  * guard is "if EVERY subject fails, re-throw", so a fixture where MOST subjects fail still passes over
  * whatever is left. A QICore library that omits `include FHIRHelpers` does exactly that: the implicit value[x]
  * conversions put `FHIRHelpers.ToValue` into the ELM, the reference cannot load the library, and every
  * patient with a Condition is dropped. Asserting full coverage is what makes the differential's green mean
  * something here.
  *
  * The expected dates are the reference's, and they sit on the 2026 measurement period's boundaries: an age is
  * a DURATION, so the interval starts on the date the patient turned that age, and a one-day error moves a
  * patient across the boundary. */
@RunWith(classOf[JUnitRunner])
final class AgeIntervalOracleSpec extends AnyFunSuite {

  private val library = "FhirAgeIntervalBoundaries"
  private val defines = Seq("Onset Spans", "Onset Starts In Period", "Onset Overlaps Period")

  private def lines(f: String): Seq[String] =
    Paths.read(new java.io.File(Corpus.qicoreDataDir, f)).linesIterator.filter(_.trim.nonEmpty).toSeq

  private lazy val subjects: Seq[String] =
    lines("Patient.ndjson").map(l => ujson.read(l)("id").str).filter(_.startsWith("a")).sorted

  private lazy val reference: Map[String, Map[String, ujson.Value]] =
    GroundTruthRunner.evaluate(
      Paths.read(Paths.cql(library)), library, defines,
      GroundTruthRunner.RefEnv(
        contextName = "Patient",
        resourcesByType = Seq("Patient", "Condition").map(t => t -> lines(s"$t.ndjson")).toMap,
        subjectIds = subjects, extraLibrarySources = Corpus.librarySources(library)))

  test("the reference evaluates EVERY subject — no silent exclusions (CONF-GROUND-TRUTH)") {
    val missing = subjects.filterNot(reference.contains)
    assert(missing.isEmpty,
      s"the Java CQF reference could not evaluate ${missing.size} of ${subjects.size} subject(s): " +
        s"${missing.mkString(", ")}. GroundTruthRunner drops them NON-FATALLY, so the differential would " +
        s"still pass over whatever remains — see this spec's scaladoc. Check stderr for the per-subject cause.")
  }

  test("an age-coded onset starts on the date the patient turned that age (EXEC-SQL-TEMPORAL-RUNTIME-UNIT)") {
    def low(subject: String): Seq[String] =
      reference(subject)("Onset Spans").arr.toSeq.filter(_ != ujson.Null).map(_("low").str.take(10))
    assert(low("a1") == Seq("2026-01-01"), s"a1 (1986-01-01, age 40): got ${reference("a1")("Onset Spans")}")
    assert(low("a2") == Seq("2025-12-31"), s"a2 (1985-12-31, age 40): got ${reference("a2")("Onset Spans")}")
    assert(low("a3") == Seq("2027-01-01"), s"a3 (1987-01-01, age 40): got ${reference("a3")("Onset Spans")}")
    assert(low("a4") == Seq("2020-06-15"), s"a4 (1990-06-15, ages 30-35): got ${reference("a4")("Onset Spans")}")
    assert(low("a5").isEmpty, s"a5 (no birthDate) should yield no interval; got ${reference("a5")("Onset Spans")}")
  }

  test("the boundary patients flip the period booleans (CONF-GATE-EMIT-INVARIANT companion)") {
    def flag(s: String, d: String) = reference(s)(d)
    assert(flag("a1", "Onset Starts In Period") == ujson.True, "a1 turned 40 on the period's first day")
    assert(flag("a2", "Onset Starts In Period") == ujson.False, "a2 turned 40 the day before the period")
    assert(flag("a2", "Onset Overlaps Period") == ujson.True, "a2's year at 40 runs into the period")
    assert(flag("a3", "Onset Overlaps Period") == ujson.False, "a3 turns 40 the day after the period")
    assert(flag("a4", "Onset Overlaps Period") == ujson.True, "a4's age range ends mid-period")
    assert(flag("a4", "Onset Starts In Period") == ujson.False, "a4's age range starts in 2020")
  }
}
