package com.databricks.cql.service

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** RUN-CLIENT-RESULT-IDENTITY, PIPE-DURABLE-IDENTITY — synthetic CQL proves definition-grained, default-inert
  * fingerprints. */
@RunWith(classOf[JUnitRunner])
final class DurablePlanSpec extends AnyFunSuite {
  private val source = """library DurableExample version '1.0.0'
                         |define Base: 7
                         |define Output: Base + 3
                         |""".stripMargin

  private def compile(cql: String = source, durable: Boolean = true): PipelinePlan =
    CompileService.compilePipeline(CompileRequest(cql = Some(cql), durable = durable,
      overrides = Map("materialize.mode" -> "all")))

  private def recipe(plan: PipelinePlan, define: String): DatasetRecipe =
    plan.datasets.find(_.define == define).getOrElse(fail(s"missing $define"))

  test("PIPE-DURABLE-IDENTITY: opt-in preserves every existing recipe field and SQL") {
    val ordinary = compile(durable = false)
    val durable = compile()
    assert(ordinary.datasets.nonEmpty)
    assert(ordinary.datasets.forall(_.durable.isEmpty))
    assert(durable.datasets.map(_.copy(durable = None)) == ordinary.datasets)
    assert(durable.datasets.forall(_.durable.exists(_.reusable)))
    assert(durable.datasets.forall(_.durable.get.fingerprint.matches("[0-9a-f]{64}")))
    assert(durable.datasets.forall(_.durable.get.format == "cql-result-identity-v1"))
  }

  test("RUN-CLIENT-RESULT-IDENTITY: the same calculation compiles to the same fingerprints") {
    assert(compile().datasets.map(_.durable) == compile().datasets.map(_.durable))
  }

  test("PIPE-DURABLE-IDENTITY: adding a downstream definition preserves upstream identities") {
    val initial = compile()
    val extended = compile(source + "define Added: Output + 5\n")
    Seq("Base", "Output").foreach { name =>
      assert(recipe(initial, name).durable == recipe(extended, name).durable)
    }
    assert(recipe(extended, "Added").reads.contains(recipe(extended, "Output").name))
  }

  test("RUN-CLIENT-RESULT-IDENTITY: an upstream change invalidates its dependent even across a cut") {
    val initial = compile()
    val changed = compile(source.replace("Base: 7", "Base: 8"))
    assert(recipe(initial, "Output").query == recipe(changed, "Output").query)
    Seq("Base", "Output").foreach { name =>
      assert(recipe(initial, name).durable.get.fingerprint != recipe(changed, name).durable.get.fingerprint)
    }
  }

  test("PIPE-DURABLE-IDENTITY: inlined function changes and parameter overrides affect claims") {
    val functions = """library Functions version '1.0.0'
                      |parameter Amount Integer default 7
                      |define function Add(value Integer): value + 1
                      |define Base: Add(Amount)
                      |define Output: Base + 3
                      |""".stripMargin
    val original = compile(functions)
    val changed = compile(functions.replace("value + 1", "value + 2"))
    assert(recipe(original, "Base").durable != recipe(changed, "Base").durable)
    val parameterized = CompileService.compilePipeline(CompileRequest(cql = Some(functions), durable = true,
      parameters = Map("Amount" -> "9"), overrides = Map("materialize.mode" -> "all")))
    assert(recipe(original, "Output").durable != recipe(parameterized, "Output").durable)
  }

  test("RUN-CLIENT-RESULT-IDENTITY: reuse eligibility propagates to readers") {
    val plan = compile(durable = false)
    val annotated = DurablePlan.annotate(plan, Set(recipe(plan, "Base").name))
    assert(annotated.datasets.forall(_.durable.exists(!_.reusable)))
  }

  test("PIPE-DURABLE-IDENTITY: default durable seams and typed parameter claims survive extensions") {
    val cql = """library Parameters version '1'
                |parameter Amount Integer default 7
                |define Base: Amount
                |define Output: Base + 3
                |""".stripMargin
    val initial = CompileService.compilePipeline(CompileRequest(cql = Some(cql), durable = true))
    val extended = CompileService.compilePipeline(CompileRequest(
      cql = Some(cql.replace("define Base:", "parameter Unrelated Integer default 2\ndefine Base:") +
        "define Added: Output + Unrelated\n"),
      durable = true, parameters = Map("Unrelated" -> "4")))
    Seq("Base", "Output").foreach { name =>
      assert(recipe(initial, name).name == recipe(extended, name).name)
      assert(recipe(initial, name).durable == recipe(extended, name).durable)
      assert(recipe(initial, name).durable.get.parameters.obj.keySet == Set("Amount"))
    }
    val claim = recipe(initial, "Base").durable.get.parameters("Amount")
    assert(claim("type").str.endsWith("Integer"))
    assert(claim("value")("value").str == "7")
  }

  test("PIPE-DURABLE-IDENTITY: unsupported clock expressions fail rather than receive reusable claims") {
    val error = intercept[com.databricks.cql.error.VerifierError] {
      compile("""library Clock version '1'
                          |define Clock: Today()
                          |define Output: Clock
                          |""".stripMargin)
    }
    assert(error.getMessage.contains("Today"))
  }

  test("PIPE-DURABLE-IDENTITY: reject missing dependencies and cycles rather than mint claims") {
    val plan = compile(durable = false)
    val base = recipe(plan, "Base")
    intercept[IllegalArgumentException] {
      DurablePlan.annotate(PipelinePlan(Seq(base.copy(reads = Seq("absent")))), Set.empty)
    }
    intercept[IllegalArgumentException] {
      DurablePlan.annotate(PipelinePlan(Seq(base.copy(reads = Seq(base.name)))), Set.empty)
    }
  }

  private val fhirSource = """library DurableFhir version '1.0.0'
                             |using FHIR version '4.0.1'
                             |include FHIRHelpers version '4.0.1' called FHIRHelpers
                             |context Patient
                             |define Female: Patient.gender = 'female'
                             |define Output: Female
                             |""".stripMargin

  /** BIND-CALL-SITE: `fhirSource` bound by rule to the views of `schema`, with that schema as its identity. */
  private def bound(schema: String, identity: Option[String] = None): CompileRequest =
    CompileRequest(cql = Some(fhirSource), depCql = Seq(FhirHelpersCql.text), durable = true,
      data = Some(com.databricks.cql.env.DataBinding(
        Some(com.databricks.cql.env.DataBinding.Rule(schema, identity.orElse(Some(schema)))), Map.empty,
        TestBindings.StockShapes)))

  test("CLIENT-RESOLVER-IDENTITY: a different data resolver changes every fingerprint") {
    val first = CompileService.compilePipeline(bound("synthetic.source_a"))
    val again = CompileService.compilePipeline(bound("synthetic.source_a"))
    val other = CompileService.compilePipeline(bound("synthetic.source_a", Some("another resolver")))
    Seq("Female", "Output").foreach { name =>
      assert(recipe(first, name).durable == recipe(again, name).durable)
      assert(recipe(first, name).durable.get.fingerprint != recipe(other, name).durable.get.fingerprint)
    }
  }

  test("DATA-MODEL-SUBSET, CLIENT-RESOLVER-IDENTITY: a retrieved type's shape is in the fingerprint; another type's is not") {
    def withShapes(shapes: Map[String, com.databricks.cql.sema.ResourceShape.Checked]): CompileRequest = {
      val req = bound("synthetic.source_a")
      req.copy(data = req.data.map(_.copy(shapes = shapes)))
    }
    def parse(t: String, ddl: String) = t -> com.databricks.cql.sema.ResourceShape.parse(t, s"struct<resource:$ddl>")
    val stock = CompileService.compilePipeline(withShapes(TestBindings.StockShapes))
    val narrowPatient = CompileService.compilePipeline(withShapes(TestBindings.StockShapes +
      parse("Patient", "struct<id:string,gender:string>")))
    val reordered = CompileService.compilePipeline(withShapes(TestBindings.StockShapes +
      parse("Patient", "struct<gender:string,id:string>")))
    val narrowCondition = CompileService.compilePipeline(withShapes(TestBindings.StockShapes +
      parse("Condition", "struct<id:string>")))
    Seq("Female", "Output").foreach { name =>
      val fp = (p: PipelinePlan) => recipe(p, name).durable.get.fingerprint
      assert(fp(stock) != fp(narrowPatient), name)
      assert(fp(narrowPatient) == fp(reordered), s"$name: field order is not part of a shape")
      assert(fp(stock) == fp(narrowCondition), s"$name: the CQL retrieves no Condition")
    }
  }

  test("CLIENT-RESOLVER-IDENTITY: provider identities are hashed even where the SQL is identical") {
    val plan = compile(durable = false)
    def fingerprints(data: Seq[String], terminology: Seq[String]) =
      DurablePlan.annotate(plan, Set.empty, boundTo = DurablePlan.Providers(data, terminology))
        .datasets.map(_.durable.get.fingerprint)
    val base = fingerprints(Seq("synthetic.source_a"), Seq("synthetic.terminology"))
    assert(base == fingerprints(Seq("synthetic.source_a"), Seq("synthetic.terminology")))
    assert(base != fingerprints(Seq("synthetic.source_b"), Seq("synthetic.terminology")))
    assert(base != fingerprints(Seq("synthetic.source_a"), Seq("synthetic.other_terminology")))
  }

  test("BIND-CALL-SITE, CLIENT-RESOLVER-IDENTITY: a durable compile that retrieves needs data, each relation an identity") {
    val unbound = bound("synthetic.source_a").copy(data = None)
    val bare = intercept[com.databricks.cql.error.UnsupportedNode] { CompileService.compilePipeline(unbound) }
    assert(bare.getMessage.contains("needs data"))
    val anonymous = unbound.copy(data = Some(com.databricks.cql.env.DataBinding(
      Some(com.databricks.cql.env.DataBinding.Rule("synthetic.source_a")), Map.empty, TestBindings.StockShapes)))
    val err = intercept[IllegalArgumentException] { CompileService.compilePipeline(anonymous) }
    assert(err.getMessage.contains("has no identity"))
    val ordinary = anonymous.copy(durable = false, overrides = Map("materialize.mode" -> "all"))
    assert(CompileService.compilePipeline(ordinary).datasets.nonEmpty)
  }

  test("PIPE-DURABLE-IDENTITY: durable is an explicit request field with an inert default") {
    assert(!CompileRequest.fromJson("{}").durable)
    assert(CompileRequest.fromJson("{\"durable\":true}").durable)
  }
}
