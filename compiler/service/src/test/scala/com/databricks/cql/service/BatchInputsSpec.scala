package com.databricks.cql.service

import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SVC-COMPILE-BATCH — no separate translation/schema calls, same typed parameters and bound SQL. */
@RunWith(classOf[JUnitRunner])
final class BatchInputsSpec extends AnyFunSuite {
  private def source(t: String): String =
    s"""library BatchExample version '1'
       |parameter "Measurement Period" Interval<$t>
       |define Output: start of "Measurement Period"
       |""".stripMargin

  test("SVC-COMPILE-BATCH: date and datetime periods match explicit typed overrides exactly") {
    for ((t, start, end, expected) <- Seq(
      ("DateTime", "2026-01-01", "2026-12-31", "Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]"),
      ("DateTime", "2026-01-01T12:34:56.000", "2026-12-31", "Interval[@2026-01-01T12:34:56.000, @2026-12-31T23:59:59.999]"),
      ("Date", "2026-01-01T12:34:56.000", "2026-12-31", "Interval[@2026-01-01, @2026-12-31]"))) {
      val req = CompileRequest(cql = Some(source(t)), durable = true)
      val batch = CompileService.compilePipeline(req.copy(measurementPeriod = Some(start -> end)), false)
      val explicit = CompileService.compilePipeline(req.copy(parameters = Map("Measurement Period" -> expected)), false)
      assert(batch == explicit, t)
    }
  }

  test("SVC-COMPILE-BATCH: undeclared periods are inert, unsupported declarations and conflicts fail") {
    val req = CompileRequest(cql = Some("library NoPeriod version '1'\ndefine Output: 7"), durable = true)
    val bounds = Some("2026-01-01" -> "2026-12-31")
    assert(CompileService.compilePipeline(req.copy(measurementPeriod = bounds), false) ==
      CompileService.compilePipeline(req, false))
    val unsupported = intercept[IllegalArgumentException] {
      CompileService.compilePipeline(req.copy(cql = Some(source("Integer")), measurementPeriod = bounds), false)
    }
    assert(unsupported.getMessage.contains("Interval<DateTime> or Interval<Date>"))
    intercept[IllegalArgumentException] {
      CompileService.compilePipeline(req.copy(measurementPeriod = bounds,
        parameters = Map("Measurement Period" -> "1")), false)
    }
  }

  test("SVC-COMPILE-BATCH: caller metadata maps canonical types and rejects malformed layouts") {
    val d = DataSourceRoutines("synthetic", "source", Seq("retrieve_patient", "retrieve_patient_ids", "helper"))
    val resolved = BatchInputs.prepare(CompileRequest(dataSource = Some(d)))
    assert(resolved.sources("Patient").function == "retrieve_Patient")
    assert(resolved.sources("Patient").catalog.contains("synthetic.source"))
    for (routines <- Seq(Seq("retrieve_unknown"), Seq("helper"), Seq("retrieve_patient_ids"),
      Seq("retrieve_patient", "retrieve_encounter_ids"))) {
      intercept[IllegalArgumentException] { BatchInputs.prepare(CompileRequest(dataSource = Some(d.copy(routines = routines)))) }
    }
  }

  test("SVC-COMPILE-BATCH: nested shared includes are retained from one translation with no lost ELM") {
    val common = "library Common version '1'\ndefine Base: 7"
    val helper = "library Helper version '1'\ninclude Common version '1' called C\ndefine Value: C.Base + 1"
    val root = "library Root version '1'\ninclude Common version '1' called C\ninclude Helper version '1' called H\ndefine Output: H.Value + C.Base"
    val result = CqlToElmCli.translateClosure(root, Seq(common, helper))
    assert(result.root.errors.isEmpty)
    assert(result.dependencies.map(s => ujson.read(s)("library")("identifier")("id").str).toSet == Set("Common", "Helper"))
    assert(result.dependencies.size == 2)
    val req = CompileRequest(cql = Some(root), depCql = Seq(common, helper), durable = true)
    val elmReq = req.copy(cql = None, elmJson = CqlToElmCli.translateText(root, Seq(common, helper)).elmJson.get,
      depCql = Seq.empty, deps = Seq(common, helper).map(s => CqlToElmCli.translateText(s, Seq(common, helper)).elmJson.get))
    assert(CompileService.compilePipeline(req, false) == CompileService.compilePipeline(elmReq, false))
  }

  test("SVC-COMPILE-BATCH: bundled FHIRHelpers does not introduce pruning for a root without a closure") {
    val root = """library Standalone version '1'
                 |using FHIR version '4.0.1'
                 |include FHIRHelpers version '4.0.1' called FH
                 |define Denominator: true
                 |define Other: 7
                 |""".stripMargin
    val elm = CqlToElmCli.translateText(root).elmJson.get
    val req = CompileRequest(cql = Some(root), durable = true)
    assert(CompileService.compilePipeline(req, false) ==
      CompileService.compilePipeline(req.copy(cql = None, elmJson = elm), false))
    assert(CompileService.compilePipeline(req, false).datasets.exists(_.define == "Other"))
  }
}
