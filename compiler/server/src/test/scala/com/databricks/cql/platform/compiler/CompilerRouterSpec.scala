package com.databricks.cql.platform.compiler

import com.databricks.cql.service.CompileService
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** L1 (CONF-TEST-LAYERS): the router's dispatch + auth + the compile ops, exercised WITHOUT a socket and
  * WITHOUT Spark. The engine's SQL backend (`EXEC-SQL-BACKEND`) is session-free — translate, compileSql,
  * compilePipeline, and describe are all pure IR -> text/metadata — so the router drives a real
  * (cheap, Spark-free) [[CompileService]] and `/compile-sql` is unit-testable end to end here.
  *
  * @RunWith(JUnitRunner) is required for discovery under the JUnit Platform. */
@RunWith(classOf[JUnitRunner])
class CompilerRouterSpec extends AnyFunSuite {

  /** A router over a real, Spark-free CompileService (SVC-STATELESS, EXEC-SQL-BACKEND). */
  private def router(token: Option[String] = None): CompilerRouter =
    new CompilerRouter(new CompileService(), token)

  private def get(path: String, auth: Option[String] = None) =
    router().handle("GET", path, auth, "")
  private def post(path: String, body: String, auth: Option[String] = None, token: Option[String] = None) =
    router(token).handle("POST", path, auth, body)

  private val smokeCql =
    "library Smoke version '1.0.0'\nusing FHIR version '4.0.1'\ndefine \"One\": 1 + 1\n"

  test("RUN-CLIENT-RESULT-IDENTITY: only opt-in pipeline responses carry durable claims and input coordinates") {
    val request = ujson.Obj("cql" -> smokeCql, "overrides" -> ujson.Obj("materialize.mode" -> "all"))
    val ordinary = post("/compile-pipeline", ujson.write(request))
    assert(ordinary.status == 200)
    val ordinaryRecipes = ujson.read(ordinary.body)("datasets").arr
    assert(ordinaryRecipes.nonEmpty)
    assert(ordinaryRecipes.forall(!_.obj.contains("durable")))
    request("durable") = ujson.Bool(true)
    val response = post("/compile-pipeline", ujson.write(request))
    assert(response.status == 200)
    val recipes = ujson.read(response.body)("datasets").arr
    assert(recipes.map(_("query")) == ordinaryRecipes.map(_("query")))
    assert(recipes.forall(_("durable")("format").str == "cql-result-identity-v1"))
    assert(recipes.forall(_("durable")("fingerprint").str.matches("[0-9a-f]{64}")))
    assert(recipes.forall(!_("durable").obj.contains("logicHash")))
    assert(recipes.head("library").str == "Smoke")
    assert(recipes.head("define").str == "One")
  }

  test("/health is open and returns ok") {
    val r = get("/health")
    assert(r.status == 200)
    assert(ujson.read(r.body)("status").str == "ok")
  }

  test("/capabilities advertises the SQL-backend method set, all Spark-free") {
    val r = get("/capabilities")
    assert(r.status == 200)
    val o = ujson.read(r.body)
    // Method names advertise the served URL paths — lowercase kebab-case (BUILD-URL-KEBAB).
    val expected = Set("translate", "compile-sql", "compile-pipeline", "describe", "resource-schema", "merge-cql")
    assert(o("methods").arr.map(_.str).toSet == expected)
    assert(o("sparkFreeMethods").arr.map(_.str).toSet == expected)
  }

  test("/capabilities publishes a compiler identity distinct from the wire and engine versions " +
    "(SVC-COMPILER-IDENTITY)") {
    val o = ujson.read(get("/capabilities").body)

    // The cache key CQL on Databricks keys materializations on (PIPE-BARRIER-HASH). Must be present and
    // non-empty, or the second recompute determinant silently does nothing.
    val identity = o("compilerVersion").str
    assert(identity.nonEmpty)

    // It must NOT be the wire contract version: `protocol` moves only on a wire change, so keying on
    // it would miss every engine change — the failure this requirement exists to prevent.
    assert(identity != o("protocol").str)

    // The engine's own build constant is reported for provenance and is NOT the identity. Unstamped
    // JAR manifest => "0.1.0-dev" on every engine build, so it can never signal an upgrade.
    assert(o("engineVersion").str.nonEmpty)

    // Stable WITHIN a process: two reads in one JVM must agree, or a single run would see itself
    // change compilers mid-flight.
    assert(ujson.read(get("/capabilities").body)("compilerVersion").str == identity)
  }

  test("the compile and describe endpoints report the SAME identity as discovery " +
    "(SVC-COMPILER-IDENTITY)") {
    val identity = ujson.read(get("/capabilities").body)("compilerVersion").str
    val cql = "library Probe version '1.0.0'\nusing FHIR version '4.0.1'\ncontext Patient\n" +
      "define \"Answer\": true\n"
    // BIND-CALL-SITE: the Patient context retrieve binds by rule.
    val body = ujson.write(ujson.Obj("cql" -> cql,
      "data" -> ujson.Obj("rule" -> ujson.Obj("schema" -> "acme.data", "identity" -> "acme.data"))))

    // A consumer that reads the identity off the compile response and one that reads it off
    // discovery must not be able to disagree about which compiler produced a materialization.
    assert(ujson.read(post("/compile-pipeline", body).body)("compilerVersion").str == identity)
    assert(ujson.read(post("/describe", body).body)("compilerVersion").str == identity)
  }

  test("/describe lists the retrieved types, empty for data-free CQL (EMIT-DESCRIBE-RETRIEVE-TYPES)") {
    def types(cql: String) =
      ujson.read(post("/describe", ujson.write(ujson.Obj("cql" -> cql,
        "deps" -> ujson.Arr(ujson.Obj("cql" -> FhirHelpersCql.text))))).body)("retrieveTypes").arr.map(_.str).toSeq
    val head = "library Probe version '1.0.0'\nusing FHIR version '4.0.1'\ninclude FHIRHelpers version '4.0.1'\n"
    assert(types(head + "context Patient\ndefine \"Any\": exists [Encounter]\n") == Seq("Encounter", "Patient"))
    assert(types(head + "context Unfiltered\ndefine \"Two\": 1 + 1\n").isEmpty)
  }

  test("bearer auth rejects a missing/incorrect token with 401") {
    assert(post("/translate", "{}", auth = None, token = Some("secret")).status == 401)
    assert(post("/translate", "{}", auth = Some("Bearer nope"), token = Some("secret")).status == 401)
  }

  test("bearer auth accepts the correct token (case-insensitive scheme)") {
    val r = post("/translate", ujson.write(ujson.Obj("cql" -> smokeCql)),
      auth = Some("bearer secret"), token = Some("secret"))
    assert(r.status == 200)
    assert(ujson.read(r.body)("ok").bool)
  }

  test("/translate of a valid library returns ELM and ok=true") {
    val r = post("/translate", ujson.write(ujson.Obj("cql" -> smokeCql)))
    val o = ujson.read(r.body)
    assert(o("ok").bool, s"expected ok, diagnostics=${o("diagnostics")}")
    assert(o("elm").strOpt.isDefined)
    assert(o("diagnostics").arr.isEmpty)
  }

  test("/translate of invalid CQL fails loudly with diagnostics, no ELM (CORE-NO-SILENT-MISLOWER)") {
    val cql = "library Bad version '1.0.0'\nusing FHIR version '4.0.1'\ndefine \"X\": Undefined + 1\n"
    val r = post("/translate", ujson.write(ujson.Obj("cql" -> cql)))
    val o = ujson.read(r.body)
    assert(!o("ok").bool)
    assert(o("elm") == ujson.Null)
    assert(o("diagnostics").arr.nonEmpty)
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: /merge-cql merges fragments and names the last one's defines") {
    val body = ujson.write(ujson.Obj("fragments" -> ujson.Arr("define \"foo\": 1", "define \"bar\": \"foo\" + 1")))
    val r = post("/merge-cql", body)
    assert(r.status == 200, r.body)
    val o = ujson.read(r.body)
    assert(o("defines").arr.map(_.str) == Seq("bar"))
    assert(o("cql").str.contains("define \"foo\": 1"))
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: /merge-cql refuses a fragment with a library header (400)") {
    val r = post("/merge-cql", ujson.write(ujson.Obj("fragments" -> ujson.Arr(smokeCql))))
    assert(r.status == 400)
    assert(r.body.contains("library Smoke"))
  }

  test("/compile-sql of a data-free measure returns a SQL script (Spark-free, EXEC-SQL-BACKEND)") {
    // cql -> ELM -> SQL script in one call; proves the compile path needs no Spark server.
    val r = post("/compile-sql", ujson.write(ujson.Obj("cql" -> smokeCql, "target" -> "One")))
    assert(r.status == 200, s"body=${r.body}")
    val o = ujson.read(r.body)
    assert(o("statements").arr.nonEmpty, s"expected SQL statements, got ${r.body}")
    assert(o("target").str.nonEmpty)
    o("statements").arr.foreach(s => assert(s("sql").str.nonEmpty))
  }

  test("/compile-sql publishes each define's object under the request's prefix (EMIT-SQL-VALUE-COLUMN)") {
    // CLIENT-INLINE-WAREHOUSE reads every define's object from `shapes`; the deployed host omitted them, so a
    // prefixed compile had no name a client could read. Same wire contract as the engine's OssCompileHost.
    val r = post("/compile-sql", ujson.write(ujson.Obj("cql" -> smokeCql, "objectPrefix" -> "inline_run")))
    assert(r.status == 200, s"body=${r.body}")
    val o = ujson.read(r.body)
    val shapes = o("shapes").arr
    assert(shapes.map(_("define").str).toSet == o("defines").arr.map(_.str).toSet)
    shapes.foreach { s =>
      assert(s("object").str.startsWith("inline_run__"), s"unprefixed object in ${r.body}")
      assert(Set("scalar", "relation").contains(s("kind").str))
      assert(s("valueColumn").str.nonEmpty)
    }
  }

  test("/compile-sql of invalid CQL is a loud 400 (CORE-NO-SILENT-MISLOWER)") {
    val cql = "library Bad version '1.0.0'\nusing FHIR version '4.0.1'\ndefine \"X\": Undefined + 1\n"
    val r = post("/compile-sql", ujson.write(ujson.Obj("cql" -> cql, "target" -> "X")))
    assert(r.status == 400)
    assert(ujson.read(r.body).obj.contains("error"))
  }

  test("unknown path is a 404; wrong method is a 405") {
    assert(get("/nope").status == 404)
    assert(get("/translate").status == 405)
    assert(get("/compile-sql").status == 405)
    assert(get("/resource-schema").status == 405)
  }

  test("/resource-schema returns canonical mount DDL for a modeled type, Spark-free (SVC-RESOURCE-SCHEMA)") {
    val r = post("/resource-schema", ujson.write(ujson.Obj("types" -> ujson.Arr("Patient", "Condition"))))
    assert(r.status == 200, s"body=${r.body}")
    val schemas = ujson.read(r.body)("schemas")
    val patient = schemas("Patient")
    assert(patient("modeled").bool)
    assert(patient("patientContext").bool) // Patient is keyed by its own id (DATA-SUBJECT-KEY)
    val ddl = patient("ddl").str
    // The mount contract's identity lineage is always present (DATA-RETRIEVE-VIEW); `id` backs the key.
    assert(ddl.contains("__resource_type"), s"ddl=$ddl")
    assert(ddl.contains("__resource_id"), s"ddl=$ddl")
    assert(schemas("Condition")("patientContext").bool) // carries a `subject` reference
    // CLIENT-RESOURCE-SCHEMA: the same struct as Spark's JSON schema, field for field.
    val fields = patient("schema")("fields").arr.map(_("name").str)
    assert(patient("schema")("type").str == "struct")
    assert(fields.contains("__resource_type") && fields.contains("__resource_id") && fields.contains("id"),
      s"fields=$fields")
    assert(fields.forall(f => ddl.contains(s"$f ") || ddl.contains(s"`$f` ")), s"fields=$fields ddl=$ddl")
  }

  test("/resource-schema reports an unmodeled type loudly, never silently omitted (CORE-NO-SILENT-MISLOWER)") {
    val r = post("/resource-schema", ujson.write(ujson.Obj("types" -> ujson.Arr("NotAResource"))))
    assert(r.status == 200)
    assert(!ujson.read(r.body)("schemas")("NotAResource")("modeled").bool)
  }

  test("/resource-schema with empty/absent types enumerates the modeled resources, sorted (SVC-RESOURCE-SCHEMA)") {
    for (body <- Seq("{}", ujson.write(ujson.Obj("types" -> ujson.Arr())))) {
      val r = post("/resource-schema", body)
      assert(r.status == 200, s"body=${r.body}")
      val types = ujson.read(r.body)("resourceTypes").arr.map(_.str).toSeq
      // Resources are present; closure-pulled datatypes are NOT.
      assert(types.contains("Patient"), s"expected Patient in $types")
      assert(types.contains("Encounter"))
      assert(!types.contains("CodeableConcept"), s"datatype leaked into resourceTypes: $types")
      assert(!types.contains("Quantity"))
      // Deterministic: sorted ascending (TECH-DETERMINISM).
      assert(types == types.sorted, s"resourceTypes must be sorted: $types")
    }
  }
}
