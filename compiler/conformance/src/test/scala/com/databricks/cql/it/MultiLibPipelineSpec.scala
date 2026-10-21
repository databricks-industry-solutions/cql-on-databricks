package com.databricks.cql.it

import com.databricks.cql.ir.{ExpressionDef, FunctionDef, Library}
import com.databricks.cql.passes.{LibraryLinker, Pipeline}
import com.databricks.cql.service.{CompileRequest, CompileService, LibraryInput}
import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Multi-library pipeline compilation gate (`PIPE-MULTI-LIB`, `PIPE-MULTI-DESCRIBE`). Two independent
  * libraries compiled into a single pipeline plan: recipe names are library-qualified
  * (`cql_{libId}__{define}`), shared dependency defines are deduplicated, and the single-library path
  * remains unchanged (backward-compatible). Server-free (no Connect server needed). */
@RunWith(classOf[JUnitRunner])
final class MultiLibPipelineSpec extends AnyFunSuite {

  // Two data-free, self-contained CQL libraries — no includes, no dependencies.
  private val cqlA =
    """library Alpha version '1.0.0'
      |define "Sum": 2 + 3
      |define "Product": 6 * 7""".stripMargin

  private val cqlB =
    """library Beta version '1.0.0'
      |define "Quotient": 22 / 7
      |define "Modulo": 10 mod 3""".stripMargin

  /** Translate CQL text to ELM JSON for use in a [[LibraryInput]]. */
  private def toElm(cql: String): String =
    CqlToElmCli.translateText(cql).elmJson.getOrElse(
      throw new IllegalStateException(s"CQL translation failed: ${CqlToElmCli.translateText(cql).errors.mkString("; ")}"))

  private lazy val elmA: String = toElm(cqlA)
  private lazy val elmB: String = toElm(cqlB)

  // ---- PIPE-MULTI-LIB: compilePipeline with libraries manifest ----------------------------

  test("multi-lib pipeline plan has library-qualified recipe names for each root") {
    // Force materialization of ALL defines (`materialize.mode=all`) so the cost model is not the
    // gate — simple scalar defines are not materialized by default (the semantic classifier sees
    // them as plain scalars).
    val req = CompileRequest(
      context = Some("Unfiltered"),
      libraries = Seq(LibraryInput(elmA), LibraryInput(elmB)),
      overrides = Map("materialize.mode" -> "all"))
    val plan = new CompileService().compilePipeline(req)
    val names = plan.datasets.map(_.name).toSet

    // Every recipe name carries the library-qualified define under the merged root prefix.
    // Alpha defines qualified as alpha__{define}, Beta as beta__{define}, under the merged root's prefix.
    assert(names.exists(_.contains("alpha__")), s"expected Alpha-qualified recipes: $names")
    assert(names.exists(_.contains("beta__")), s"expected Beta-qualified recipes: $names")
    // Specific: {merged_prefix}__alpha__sum, etc. (merged root = Alpha v1.0.0 → alpha_1_0_0).
    assert(names.contains("alpha_1_0_0__alpha__sum"), s"missing alpha_1_0_0__alpha__sum: $names")
    assert(names.contains("alpha_1_0_0__alpha__product"), s"missing alpha_1_0_0__alpha__product: $names")
    assert(names.contains("alpha_1_0_0__beta__quotient"), s"missing alpha_1_0_0__beta__quotient: $names")
    assert(names.contains("alpha_1_0_0__beta__modulo"), s"missing alpha_1_0_0__beta__modulo: $names")
  }

  test("multi-lib describe returns a merged graph with library-qualified define names") {
    val req = CompileRequest(
      context = Some("Unfiltered"),
      libraries = Seq(LibraryInput(elmA), LibraryInput(elmB)))
    val graph = new CompileService().describe(req)

    // All four defines appear, library-qualified.
    assert(graph.defines.contains("Alpha__Sum"), s"missing Alpha__Sum in defines: ${graph.defines}")
    assert(graph.defines.contains("Beta__Quotient"), s"missing Beta__Quotient in defines: ${graph.defines}")
    assert(graph.defines.size >= 4, s"expected at least 4 defines: ${graph.defines}")
  }

  // ---- ELM origin: library, libraryVersion, define on each recipe -----------

  test("multi-lib pipeline recipes carry ELM origin coordinates per library") {
    val req = CompileRequest(
      context = Some("Unfiltered"),
      libraries = Seq(LibraryInput(elmA), LibraryInput(elmB)),
      overrides = Map("materialize.mode" -> "all"))
    val plan = new CompileService().compilePipeline(req)

    // Every recipe must carry a non-empty library and define.
    plan.datasets.foreach { r =>
      assert(r.library.nonEmpty, s"recipe ${r.name}: library must be non-empty")
      assert(r.define.nonEmpty, s"recipe ${r.name}: define must be non-empty")
    }

    // Alpha recipes should carry library="Alpha", version="1.0.0", raw define names.
    val alphaSum = plan.datasets.find(_.define == "Sum")
    assert(alphaSum.isDefined, s"missing Sum recipe: ${plan.datasets.map(_.name)}")
    assert(alphaSum.get.library == "Alpha", s"expected library 'Alpha', got '${alphaSum.get.library}'")
    assert(alphaSum.get.libraryVersion == "1.0.0", s"expected version '1.0.0', got '${alphaSum.get.libraryVersion}'")
    assert(alphaSum.get.define == "Sum", s"expected define 'Sum', got '${alphaSum.get.define}'")

    val betaMod = plan.datasets.find(_.define == "Modulo")
    assert(betaMod.isDefined, s"missing Modulo recipe: ${plan.datasets.map(_.name)}")
    assert(betaMod.get.library == "Beta", s"expected library 'Beta', got '${betaMod.get.library}'")
    assert(betaMod.get.libraryVersion == "1.0.0", s"expected version '1.0.0', got '${betaMod.get.libraryVersion}'")
    assert(betaMod.get.define == "Modulo", s"expected define 'Modulo', got '${betaMod.get.define}'")
  }

  test("single-lib pipeline recipes carry root library origin, not library-qualified") {
    val req = CompileRequest(elmJson = elmA, context = Some("Unfiltered"),
      overrides = Map("materialize.mode" -> "all"))
    val plan = new CompileService().compilePipeline(req)

    val sum = plan.datasets.find(_.define == "Sum")
    assert(sum.isDefined, s"missing Sum recipe: ${plan.datasets.map(_.name)}")
    assert(sum.get.library == "Alpha", s"expected library 'Alpha', got '${sum.get.library}'")
    assert(sum.get.libraryVersion == "1.0.0", s"expected version '1.0.0', got '${sum.get.libraryVersion}'")
    assert(sum.get.define == "Sum", s"expected define 'Sum', got '${sum.get.define}'")
  }

  // ---- backward compatibility: single-library unchanged -----------------------------------

  test("single-library path is unchanged when libraries is empty") {
    val singleReq = CompileRequest(elmJson = elmA, context = Some("Unfiltered"),
      overrides = Map("materialize.mode" -> "all"))
    val plan = new CompileService().compilePipeline(singleReq)
    val names = plan.datasets.map(_.name).toSet

    // Single-library: {library}_{version}__{define}, no library-qualified double-prefix.
    assert(names.contains("alpha_1_0_0__sum"), s"single-lib must have alpha_1_0_0__sum: $names")
    assert(names.contains("alpha_1_0_0__product"), s"single-lib must have alpha_1_0_0__product: $names")
    // Must NOT have the double library-qualified form (Alpha__Sum is multi-lib only).
    assert(!names.exists(_.contains("alpha__")), s"single-lib must NOT library-qualify defines: $names")
  }

  // ---- linkMulti: deduplication of shared dependencies ------------------------------------

  test("linkMulti deduplicates shared dependency defines by qualified name") {
    // Construct two root libraries that share a common dependency (simulated via IR).
    val sharedDep = Pipeline().load(toElm(
      """library SharedHelper version '1.0.0'
        |define "HelperValue": 42""".stripMargin))
    val rootACql =
      """library RootA version '1.0.0'
        |define "A1": 1 + 1""".stripMargin
    val rootBCql =
      """library RootB version '1.0.0'
        |define "B1": 2 + 2""".stripMargin
    val rootA = Pipeline().load(toElm(rootACql))
    val rootB = Pipeline().load(toElm(rootBCql))

    // Both roots claim SharedHelper as a dependency.
    val depsA = Map(sharedDep.id -> sharedDep)
    val depsB = Map(sharedDep.id -> sharedDep)

    // Add include declarations to roots so link() finds the dep.
    val rootAWithInclude = rootA.copy(includes = rootA.includes :+
      com.databricks.cql.ir.IncludeDef(sharedDep.id, sharedDep.id, sharedDep.version))
    val rootBWithInclude = rootB.copy(includes = rootB.includes :+
      com.databricks.cql.ir.IncludeDef(sharedDep.id, sharedDep.id, sharedDep.version))

    val merged = LibraryLinker.linkMulti(Seq(
      (rootAWithInclude, depsA),
      (rootBWithInclude, depsB)))

    val defNames = merged.statements.collect { case e: ExpressionDef => e.name }

    // Root defines are qualified: RootA__A1, RootB__B1.
    assert(defNames.contains("RootA__A1"), s"missing RootA__A1: $defNames")
    assert(defNames.contains("RootB__B1"), s"missing RootB__B1: $defNames")
    // Shared dep defines: qualified by dep id, appearing ONCE (deduplicated).
    val helperDefs = defNames.filter(_.contains("HelperValue"))
    assert(helperDefs.size == 1, s"shared dep define must appear exactly once: $helperDefs (all: $defNames)")
  }

  // ---- wire format: CompileRequest.fromJson parsing ---------------------------------------

  test("CompileRequest.fromJson parses the libraries manifest") {
    val json = ujson.write(ujson.Obj(
      "libraries" -> ujson.Arr(
        ujson.Obj("elm" -> "elm_a", "deps" -> ujson.Arr("dep1", "dep2")),
        ujson.Obj("elm" -> "elm_b")),
      "context" -> "Patient"))
    val req = CompileRequest.fromJson(json)

    assert(req.libraries.size == 2)
    assert(req.libraries(0).elmJson == "elm_a")
    assert(req.libraries(0).deps == Seq("dep1", "dep2"))
    assert(req.libraries(1).elmJson == "elm_b")
    assert(req.libraries(1).deps.isEmpty)
    assert(req.context.contains("Patient"))
  }

  test("CompileRequest.fromJson without libraries keeps the field empty") {
    val json = ujson.write(ujson.Obj("elm" -> "some_elm"))
    val req = CompileRequest.fromJson(json)
    assert(req.libraries.isEmpty)
    assert(req.elmJson == "some_elm")
  }
}
