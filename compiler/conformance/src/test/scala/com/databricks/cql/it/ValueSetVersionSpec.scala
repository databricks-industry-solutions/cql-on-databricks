package com.databricks.cql.it

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** LANG-VALUESET-VERSION: a membership that cannot read exactly one expansion of its value set fails the run,
  * naming the value set, where it would otherwise answer from the wrong codes. `FhirValueSetVersionRefused` has one
  * define per refusal; the pinned case that reads, `FhirValueSetVersion`, is in the differential ([[Corpus]]).
  * Needs a Spark Connect server, like [[SqlDifferentialSpec]]. */
@RunWith(classOf[JUnitRunner])
final class ValueSetVersionSpec extends AnyFunSuite {

  private lazy val failures = SqlDifferential.failures(SparkConnect.session(),
    Corpus.fhirCase("FhirValueSetVersionRefused", withTerminology = true), Seq("Unpinned", "Missing", "Pinned Nine"))

  private def failure(define: String): String =
    failures(define).getOrElse(fail(s"\"$define\" read rows; it should have failed (LANG-VALUESET-VERSION)"))

  test("an unpinned value set holding two versions fails, naming them (LANG-VALUESET-VERSION)") {
    val message = failure("Unpinned")
    assert(message.contains("value set 'http://example.org/vs/versioned' holds several versions, 1, 2"), message)
    assert(message.contains("pin one"), message)
  }

  test("a value set no relation holds fails, naming the relations (LANG-VALUESET-VERSION)") {
    val message = failure("Missing")
    assert(message.contains("value set 'http://example.org/vs/absent' is in no terminology relation the run binds " +
      "(valuesets, valuesets__base)"), message)
  }

  test("a pinned version the relation does not hold fails, naming what it holds (LANG-VALUESET-VERSION)") {
    val message = failure("Pinned Nine")
    assert(message.contains("value set 'http://example.org/vs/diabetes' has no version '9': it holds (no version)"),
      message)
  }
}
