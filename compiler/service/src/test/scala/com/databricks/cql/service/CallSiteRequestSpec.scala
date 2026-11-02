package com.databricks.cql.service

import com.databricks.cql.env.{DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.translate.CqlToElmCli
import com.databricks.cql.transpiler.lower.CallSite
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** BIND-CALL-SITE, BIND-RETRIEVE-REQUEST, BIND-RELATION, CLIENT-RESOLVER-IDENTITY: call sites in `describe`, and
  * the compile request that binds them by rule or per site. Server-free and Spark-free; the SQL is checked as
  * text here and run against Spark by the conformance differential. */
@RunWith(classOf[JUnitRunner])
final class CallSiteRequestSpec extends AnyFunSuite {

  private val cql =
    """library CallSites version '1.0.0'
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

  private val service = new CompileService()
  private def sites(source: String = elm): Seq[CallSite] = service.describe(CompileRequest(elmJson = source)).sites
  private def retrieveSite(fhirType: String, all: Seq[CallSite] = sites()): CallSite =
    all.find(s => s.kind == CallSite.Retrieve && s.request("fhirType").str == fhirType).getOrElse(fail(fhirType))
  private def valueSetSite(all: Seq[CallSite] = sites()): CallSite = all.find(_.kind == CallSite.ValueSet).get

  private val rule = Some(DataBinding.Rule("acme.data", Some("acme.data")))
  private val terminology = TerminologyBinding(Some(Seq(RetrieveRelation("acme.terminology.valueset",
    Some("acme.terminology")))), Map.empty)

  private def bound(data: DataBinding, term: TerminologyBinding = terminology): CompileRequest =
    CompileRequest(elmJson = elm, data = Some(data), terminology = Some(term))
  private def compileSql(req: CompileRequest) = CompileService.compileSql(req).script
  private def compilePipeline(req: CompileRequest) = CompileService.compilePipeline(req)

  test("BIND-CALL-SITE: describe lists every retrieve and value-set site with its library, define and request") {
    val all = sites()
    val tests = retrieveSite("Observation", all)
    assert(tests.library.contains("CallSites" -> "1.0.0") && tests.define.contains("Tests"), tests.json)
    assert(tests.id.matches("retrieve_[0-9a-f]{12}"), tests.id)
    assert(tests.request("valueSet").str == "urn:example:vs:a1c")
    assert(!tests.request.obj.contains("codeProperty"))
    assert(retrieveSite("Encounter", all).define.contains("Encounters"))
    // The context's own retrieve belongs to no define and no library (EMIT-ROOT-INDEPENDENT).
    val context = retrieveSite("Patient", all)
    assert(context.library.isEmpty && context.define.isEmpty, context.json)
    val vs = valueSetSite(all)
    assert(vs.id.matches("valueset_[0-9a-f]{12}") && vs.request("valueSet").str == "urn:example:vs:a1c", vs.json)
    assert(vs.define.contains("Tests"))
  }

  test("BIND-RETRIEVE-REQUEST: a retrieve's parameters carry the literal parameters its define uses") {
    val tests = retrieveSite("Observation").request("parameters").obj
    assert(tests.keySet == Set("Measurement Period"), tests)
    val period = tests("Measurement Period").obj
    assert(period("lowClosed").bool && !period("highClosed").bool, period)
    assert(period("low").str.startsWith("2026-01-01") && period("high").str.startsWith("2027-01-01"), period)
    // A define that uses no parameter, and the context's own retrieve, carry none.
    assert(retrieveSite("Encounter").request("parameters").obj.isEmpty)
    assert(retrieveSite("Patient").request("parameters").obj.isEmpty)
  }

  test("BIND-CALL-SITE: ids are stable, and differ when the request or the define differs") {
    assert(sites().map(_.id) == sites().map(_.id))
    val renamed = CqlToElmCli.translateText(cql.replace("define \"Encounters\"", "define \"Visits\"")
      .replace("exists \"Encounters\"", "exists \"Visits\"")).elmJson.get
    assert(retrieveSite("Encounter", sites(renamed)).id != retrieveSite("Encounter").id)
    assert(retrieveSite("Observation", sites(renamed)).id == retrieveSite("Observation").id)
  }

  test("BIND-CALL-SITE: a rule binds every retrieve to <schema>.<type in lower case>, read as the contract") {
    val sql = compileSql(bound(DataBinding(rule, Map.empty)))
    Seq("observation", "encounter", "patient").foreach(t => assert(sql.contains(s"FROM acme.data.$t"), sql))
    assert(!sql.contains("SELECT * FROM acme.data"), sql)
    assert(!sql.contains("retrieve_"), sql)
    assert(sql.contains("FROM acme.terminology.valueset"), sql)
  }

  test("BIND-CALL-SITE: a site's map entry wins over the rule") {
    val tests = retrieveSite("Observation").id
    val sql = compileSql(bound(DataBinding(rule, Map(tests -> RetrieveRelation("hist.data.observation_archive")))))
    assert(sql.contains("FROM hist.data.observation_archive") && !sql.contains("FROM acme.data.observation"), sql)
    assert(sql.contains("FROM acme.data.encounter"), sql)
  }

  test("BIND-CALL-SITE: a site with no rule and no entry fails the compile, naming it") {
    val err = intercept[UnsupportedNode](compileSql(bound(DataBinding(None,
      Map(retrieveSite("Patient").id -> RetrieveRelation("acme.data.patient"))))))
    assert(err.getMessage.contains("has no relation"), err.getMessage)
  }

  test("BIND-CALL-SITE: value sets read from the first layer that holds them, and a site entry wins") {
    val layered = TerminologyBinding(Some(Seq(RetrieveRelation("dev.vs.valueset"),
      RetrieveRelation("acme.terminology.valueset"))), Map.empty)
    val sql = compileSql(bound(DataBinding(rule, Map.empty), layered))
    assert(sql.contains("FROM dev.vs.valueset") && sql.contains("FROM acme.terminology.valueset"), sql)
    assert(sql.contains("min(__layer) OVER ()") && sql.contains("__layer = __first"), sql)
    val site = TerminologyBinding(terminology.rule, Map(valueSetSite().id -> Seq(RetrieveRelation("vol.vs.a1c"))))
    val one = compileSql(bound(DataBinding(rule, Map.empty), site))
    assert(one.contains("FROM vol.vs.a1c") && !one.contains("FROM acme.terminology.valueset"), one)
    val err = intercept[UnsupportedNode](compileSql(bound(DataBinding(rule, Map.empty), TerminologyBinding(None, Map.empty))))
    assert(err.getMessage.contains("needs terminology"), err.getMessage)
  }

  test("CLIENT-RESOLVER-IDENTITY: a durable compile hashes identities, refuses a relation without one, lists value sets") {
    def plan(identity: Option[String]) =
      compilePipeline(bound(DataBinding(Some(DataBinding.Rule("acme.data", identity)), Map.empty)).copy(durable = true))
    val a = plan(Some("acme.data")).datasets
    val b = plan(Some("other.data")).datasets
    assert(a.nonEmpty && a.map(_.durable.get.fingerprint) != b.map(_.durable.get.fingerprint))
    val err = intercept[IllegalArgumentException](plan(None))
    assert(err.getMessage.contains("no identity"), err.getMessage)
    val ip = a.find(_.name.endsWith("initial_population")).getOrElse(fail(a.map(_.name).mkString(", ")))
    assert(ip.durable.get.valueSets == Seq("urn:example:vs:a1c" -> None), ip.durable.get.toJson)
    assert(ip.durable.get.toJson("valueSets").arr.head("id").str == "urn:example:vs:a1c")
  }

  test("BIND-CALL-SITE: the wire carries rules and sites, and refuses a function, an unknown key and a replaced field") {
    val site = retrieveSite("Observation").id
    val json = ujson.write(ujson.Obj(
      "elm" -> elm,
      "data" -> ujson.Obj("rule" -> ujson.Obj("schema" -> "acme.data", "identity" -> "acme.data"),
        "sites" -> ujson.Obj(site -> ujson.Obj("table" -> "hist.data.obs", "identity" -> "hist.data"))),
      "terminology" -> ujson.Obj("rule" -> ujson.Arr(ujson.Obj("table" -> "acme.terminology.valueset"))),
      "knowledge" -> ujson.Arr(),
      "runtime" -> ujson.Obj("schema" -> "acme.runtime")))
    val req = CompileRequest.fromJson(json)
    assert(req.data.get.rule.contains(DataBinding.Rule("acme.data", Some("acme.data"))))
    assert(req.data.get.sites(site) == RetrieveRelation("hist.data.obs", Some("hist.data")))
    assert(req.terminology.get.rule.contains(Seq(RetrieveRelation("acme.terminology.valueset"))))
    assert(req.udfSchema.contains("acme.runtime"))
    Seq("sources" -> ujson.Obj(), "dataSource" -> ujson.Obj(), "deps" -> ujson.Arr(), "libraries" -> ujson.Arr())
      .foreach { case (key, value) =>
        val mixed = ujson.read(json); mixed(key) = value
        val err = intercept[IllegalArgumentException](CompileRequest.fromJson(ujson.write(mixed)))
        assert(err.getMessage.contains(key), err.getMessage)
      }
    val function = intercept[IllegalArgumentException](CompileRequest.fromJson(ujson.write(ujson.Obj("elm" -> elm,
      "data" -> ujson.Obj("sites" -> ujson.Obj(site -> ujson.Obj("function" -> "acme.fhir.obs")))))))
    assert(function.getMessage.contains("no function relation"), function.getMessage)
    val unknown = intercept[IllegalArgumentException](CompileRequest.fromJson(ujson.write(ujson.Obj("elm" -> elm,
      "data" -> ujson.Obj("retrieves" -> ujson.Obj())))))
    assert(unknown.getMessage.contains("retrieves"), unknown.getMessage)
  }
}
