package com.databricks.cql.cqf

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** RUN-POINT-CQF — the point evaluator over a small inline library: values, requirements, the period, refusals.
  * The same-answer gate against the conformance oracle on a real measure is `PointOracleSpec` in conformance. */
@RunWith(classOf[JUnitRunner])
class CqfEvaluatorSpec extends AnyFunSuite {

  private val Vs = "http://example.org/fhir/ValueSet/example-condition"
  private val Library =
    s"""library PointTest version '1.0.0'
       |using FHIR version '4.0.1'
       |include FHIRHelpers version '4.0.1' called FHIRHelpers
       |valueset "Example Condition": '$Vs'
       |parameter "Measurement Period" Interval<DateTime>
       |context Patient
       |define "Is Female": Patient.gender = 'female'
       |define "Example Condition Count": Count([Condition: "Example Condition"])
       |define "Encounter In Period":
       |  exists ([Encounter] E where E.period.start during "Measurement Period")
       |define function "Twice"(x Integer): x * 2
       |""".stripMargin

  private def patient(id: String, gender: String) =
    s"""{"resourceType":"Patient","id":"$id","gender":"$gender"}"""
  private def condition(id: String, patientId: String, code: String) =
    s"""{"resourceType":"Condition","id":"$id","subject":{"reference":"Patient/$patientId"},
       |"code":{"coding":[{"system":"http://snomed.info/sct","code":"$code"}]}}""".stripMargin
  private def encounter(id: String, patientId: String, start: String) =
    s"""{"resourceType":"Encounter","id":"$id","status":"finished","subject":{"reference":"Patient/$patientId"},
       |"period":{"start":"$start","end":"$start"}}""".stripMargin

  private val expansions = Map(Vs -> Set(("http://snomed.info/sct", "38341003")))
  private val p2026 = Some(("2026-01-01T00:00:00.000", "2026-12-31T23:59:59.999"))
  private val patients = Seq(
    CqfPatient("a", Seq(patient("a", "female"), condition("c1", "a", "38341003"), condition("c2", "a", "38341003"),
      encounter("e1", "a", "2026-03-01T10:00:00Z"))),
    CqfPatient("b", Seq(patient("b", "male"), condition("c3", "b", "11111111"), encounter("e2", "b", "2025-03-01T10:00:00Z"))))

  test("RUN-POINT-CQF: every Patient-context define, per patient, in the oracle's JSON") {
    val out = new CqfEvaluator().evaluate("PointTest", Seq(Library), patients, expansions, p2026).toMap
    assert(out("a")("Is Female") == ujson.Bool(true))
    assert(out("a")("Example Condition Count") == ujson.Num(2))
    assert(out("a")("Encounter In Period") == ujson.Bool(true))
    assert(out("b")("Is Female") == ujson.Bool(false))
    assert(out("b")("Example Condition Count") == ujson.Num(0))     // a code outside the value set
    assert(out("b")("Encounter In Period") == ujson.Bool(false))
    assert(!out("a").contains("Twice"))                        // functions are not results
    assert(!out("a").contains("Patient"))
  }

  test("RUN-POINT-CQF: the period given is the one evaluated") {
    val p2025 = Some(("2025-01-01T00:00:00.000", "2025-12-31T23:59:59.999"))
    val out = new CqfEvaluator().evaluate("PointTest", Seq(Library), patients, expansions, p2025).toMap
    assert(out("a")("Encounter In Period") == ujson.Bool(false))
    assert(out("b")("Encounter In Period") == ujson.Bool(true))
  }

  test("RUN-POINT-CQF: requirements are the retrieved types and declared value sets of the closure") {
    val r = new CqfEvaluator().requirements("PointTest", Seq(Library))
    assert(r.resourceTypes.toSet == Set("Patient", "Condition", "Encounter"))
    assert(r.valueSets == Seq(Vs))
  }

  test("RUN-POINT-CQF: a closure is translated once and reused") {
    val e = new CqfEvaluator()
    e.evaluate("PointTest", Seq(Library), patients.take(1), expansions, p2026)
    val t0 = System.nanoTime()
    e.evaluate("PointTest", Seq(Library), patients.take(1), expansions, p2026)
    assert((System.nanoTime() - t0) / 1e6 < 1000, "a second call must not translate again")
  }

  test("RUN-POINT-CQF: refusals are loud") {
    val e = new CqfEvaluator()
    assert(intercept[IllegalArgumentException](
      e.evaluate("Nope", Seq(Library), patients, expansions, p2026)).getMessage.contains("no CQL"))
    assert(intercept[IllegalArgumentException](
      e.evaluate("PointTest", Seq(Library), patients, expansions, None)).getMessage.contains("give its bounds"))
    assert(intercept[IllegalArgumentException](
      e.evaluate("PointTest", Seq(Library), Seq(CqfPatient("z", Seq(patient("a", "female")))), expansions, p2026))
      .getMessage.contains("no Patient resource"))
    assert(intercept[IllegalArgumentException](
      e.evaluate("PointTest", Seq(Library.replace("context Patient", "context Patient\ndefine \"Bad\": 1 +")),
        patients, expansions, p2026)).getMessage.contains("did not translate"))
  }
}
