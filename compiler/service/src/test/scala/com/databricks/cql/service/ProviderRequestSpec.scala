package com.databricks.cql.service

import com.databricks.cql.env.RetrieveRelation
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** BIND-PROVIDER-REQUEST, BIND-RETRIEVE-REQUEST, BIND-NAMED-ARGS, CLIENT-PROVIDER-IDENTITY: the one-field-per-role
  * compile request. Server-free and Spark-free; the SQL is checked as text here and run against Spark by the
  * conformance differential. */
@RunWith(classOf[JUnitRunner])
final class ProviderRequestSpec extends AnyFunSuite {

  private val cql =
    """library ProviderRequest version '1.0.0'
      |using FHIR version '4.0.1'
      |valueset "A1c": 'urn:example:vs:a1c'
      |parameter "Measurement Period" Interval<DateTime>
      |  default Interval[@2026-01-01T00:00:00.0, @2027-01-01T00:00:00.0)
      |context Patient
      |define "Tests": [Observation: "A1c"] O where start of "Measurement Period" is not null
      |define "Encounters": [Encounter]
      |define "Initial Population": exists "Tests" and exists "Encounters"
      |""".stripMargin

  private def elm: String = CqlToElmCli.translateText(cql).elmJson.getOrElse(fail("no ELM"))

  private def described = describe(CompileRequest(elmJson = elm))

  private def ids: Map[String, String] = described.retrieves.map(r => r.fhirType -> r.id).toMap

  private def bound(data: Map[String, RetrieveRelation],
                    terminology: Seq[RetrieveRelation] = Seq(RetrieveRelation.Table("acme.terminology.valueset",
                      Some("acme.terminology")))): CompileRequest =
    CompileRequest(elmJson = elm, data = Some(data), terminologyRelations = Some(terminology))

  private val service = new CompileService()
  private def describe(req: CompileRequest) = service.describe(req)
  private def compileSql(req: CompileRequest) = CompileService.compileSql(req, hasTerminology = false)
  private def compilePipeline(req: CompileRequest) = CompileService.compilePipeline(req, hasTerminology = false)

  test("BIND-PROVIDER-REQUEST: describe lists each retrieve once with an id and its request") {
    val retrieves = described.retrieves
    assert(retrieves.map(_.fhirType).sorted == Seq("Encounter", "Observation", "Patient"))
    retrieves.foreach(r => assert(r.id.matches(s"${r.fhirType}_[0-9a-f]{12}"), r.id))
    val tests = retrieves.find(_.fhirType == "Observation").get.requestJson
    assert(tests("valueSet").str == "urn:example:vs:a1c")
    assert(tests("codes").arr.isEmpty && tests("dateLow").isNull && tests("dateHigh").isNull)
    // BIND-RETRIEVE-REQUEST: the context's own retrieve belongs to no define and carries no parameters.
    assert(retrieves.find(_.fhirType == "Patient").get.requestJson("parameters").obj.isEmpty)
    // Retired, not a request field.
    assert(!tests.obj.contains("codeProperty"))
  }

  test("BIND-PROVIDER-REQUEST: the ids are stable, and a different request has a different id") {
    assert(ids == describe(CompileRequest(elmJson = elm)).retrieves.map(r => r.fhirType -> r.id).toMap)
    val otherElm = CqlToElmCli.translateText(cql.replace("urn:example:vs:a1c", "urn:example:vs:other")).elmJson.get
    val other = describe(CompileRequest(elmJson = otherElm)).retrieves.map(r => r.fhirType -> r.id).toMap
    assert(other("Observation") != ids("Observation"))
    assert(other("Encounter") == ids("Encounter") && other("Patient") == ids("Patient"))
  }

  test("BIND-RELATION: a table relation is read as the contract's two columns; a function by named arguments") {
    val sql = compileSql(bound(Map(
      ids("Patient") -> RetrieveRelation.Table("acme.fhir.patient", Some("acme.fhir")),
      ids("Encounter") -> RetrieveRelation.Table("acme.fhir.encounter", Some("acme.fhir")),
      ids("Observation") -> RetrieveRelation.Function("acme.fhir.observations",
        Seq("value_set", "date_low", "parameters"), Some("acme.fhir"))))).script
    assert(sql.contains("FROM acme.fhir.encounter"), sql)
    assert(!sql.contains("SELECT * FROM acme.fhir.encounter"), sql)
    // BIND-NAMED-ARGS: only the declared parameters, by name, in the declared order.
    assert(sql.contains("acme.fhir.observations(value_set => 'urn:example:vs:a1c', date_low => NULL, " +
      "parameters => map())"),
      sql)
    assert(!sql.contains("retrieve_"), sql)
    // BIND-PROVIDER-REQUEST: the one terminology relation is read directly.
    assert(sql.contains("FROM acme.terminology.valueset"), sql)
  }

  test("BIND-PROVIDER-REQUEST: a retrieve with no relation fails the compile, naming it") {
    val err = intercept[UnsupportedNode](compileSql(bound(Map(
      ids("Patient") -> RetrieveRelation.Table("acme.fhir.patient")))))
    assert(err.getMessage.contains("has no relation"), err.getMessage)
  }

  test("BIND-NAMED-ARGS: a declared parameter that names no request field fails, naming it") {
    val err = intercept[IllegalArgumentException](compileSql(bound(Map(
      ids("Patient") -> RetrieveRelation.Table("acme.fhir.patient"),
      ids("Encounter") -> RetrieveRelation.Function("acme.fhir.encounters", Seq("code_property")),
      ids("Observation") -> RetrieveRelation.Table("acme.fhir.observation")))))
    assert(err.getMessage.contains("code_property"), err.getMessage)
  }

  test("BIND-PROVIDER-REQUEST: several terminology relations read a value set from the first that holds it") {
    val all = Map(
      ids("Patient") -> RetrieveRelation.Table("acme.fhir.patient"),
      ids("Encounter") -> RetrieveRelation.Table("acme.fhir.encounter"),
      ids("Observation") -> RetrieveRelation.Table("acme.fhir.observation"))
    val sql = compileSql(bound(all, Seq(RetrieveRelation.Table("dev.vs.valueset"),
      RetrieveRelation.Table("acme.terminology.valueset")))).script
    assert(sql.contains("FROM dev.vs.valueset") && sql.contains("FROM acme.terminology.valueset"), sql)
    assert(sql.contains("min(__layer) OVER ()"), sql)
    assert(sql.contains("__layer = __first"), sql)
    // No terminology at all fails a membership test.
    val err = intercept[UnsupportedNode](compileSql(bound(all, Nil)))
    assert(err.getMessage.contains("needs terminology"), err.getMessage)
  }

  test("CLIENT-PROVIDER-IDENTITY: a durable compile hashes provider identities and refuses a relation without one") {
    def plan(identity: Option[String]) = compilePipeline(bound(Map(
      ids("Patient") -> RetrieveRelation.Table("acme.fhir.patient", identity),
      ids("Encounter") -> RetrieveRelation.Table("acme.fhir.encounter", identity),
      ids("Observation") -> RetrieveRelation.Table("acme.fhir.observation", identity))).copy(durable = true))
    val a = plan(Some("acme.fhir")).datasets.map(_.durable.get.fingerprint)
    val b = plan(Some("other.fhir")).datasets.map(_.durable.get.fingerprint)
    assert(a.nonEmpty && a != b)
    val err = intercept[IllegalArgumentException](plan(None))
    assert(err.getMessage.contains("no identity"), err.getMessage)
  }

  test("BIND-PROVIDER-REQUEST: the wire carries one field per role and never the fields they replace") {
    val json = ujson.write(ujson.Obj(
      "elm" -> elm,
      "data" -> ujson.Obj(ids("Patient") -> ujson.Obj("table" -> "acme.fhir.patient", "identity" -> "acme.fhir"),
        ids("Encounter") -> ujson.Obj("function" -> "acme.fhir.enc", "parameters" -> ujson.Arr("fhir_type"))),
      "terminology" -> ujson.Arr(ujson.Obj("table" -> "acme.terminology.valueset")),
      "knowledge" -> ujson.Arr(),
      "runtime" -> ujson.Obj("schema" -> "acme.runtime")))
    val req = CompileRequest.fromJson(json)
    assert(req.data.get(ids("Patient")) == RetrieveRelation.Table("acme.fhir.patient", Some("acme.fhir")))
    assert(req.data.get(ids("Encounter")) == RetrieveRelation.Function("acme.fhir.enc", Seq("fhir_type")))
    assert(req.terminologyRelations.contains(Seq(RetrieveRelation.Table("acme.terminology.valueset"))))
    assert(req.udfSchema.contains("acme.runtime"))
    Seq("sources" -> ujson.Obj(), "dataSource" -> ujson.Obj(), "deps" -> ujson.Arr(), "libraries" -> ujson.Arr())
      .foreach { case (key, value) =>
        val mixed = ujson.read(json); mixed(key) = value
        val err = intercept[IllegalArgumentException](CompileRequest.fromJson(ujson.write(mixed)))
        assert(err.getMessage.contains(key), err.getMessage)
      }
    intercept[IllegalArgumentException](CompileRequest.fromJson(ujson.write(ujson.Obj(
      "elm" -> elm, "data" -> ujson.Obj("x" -> ujson.Obj("view" -> "v"))))))
  }
}
