package com.databricks.cql.it

import com.databricks.cql.env.Context
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Engine side (DATA-RETRIEVER, ARCH-SET-EVAL): a FHIR `exists [Condition]`
  * measure lowers to a `__subject_id`-keyed Patient-context frame and computes per-patient
  * existence. This asserts the known-correct CQL answer over the sample data (p1/p2 have Conditions,
  * p3 does not; p4/p5 carry the partial-date Conditions added for `FhirPartialPrecision`; p6/p7 carry
  * the two-runtime-operand Conditions added for `FhirTemporalDynamic`); the differential gate
  * ([[SqlDifferentialSpec]]) then proves it against the Java reference. Runs the SQL back end (the
  * production path); skips when no Spark Connect server is reachable. */
@RunWith(classOf[JUnitRunner])
final class FhirRetrieveSpec extends AnyFunSuite {

  test("exists [Condition] keyed by __subject_id gives per-patient existence") {
    val spark = SparkConnect.session()
    val dp = new SparkFhirData(spark, Paths.dataDir.getAbsolutePath)
    val c = Differential.Case("FhirRetrieve", Context.Patient, data = Some(dp))

    val ours = SqlDifferential.engineResults(spark, c)
    val result = ours.map { case (s, defs) => s -> (defs("HasAnyCondition") == ujson.Bool(true)) }
    // The subject universe is every Patient (p1–p8); p8 has no Condition, so `exists` is false for it
    // (DATA-RETRIEVER, ARCH-SET-EVAL).
    assert(result == Map("p1" -> true, "p2" -> true, "p3" -> false, "p4" -> true, "p5" -> true,
      "p6" -> true, "p7" -> true, "p8" -> false), s"got $result")
  }
}
