package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.passes.{LibraryLinker, Optimize, Pipeline}
import com.databricks.cql.service.CompileService
import com.databricks.cql.transpiler.emit.SqlNames
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.apache.spark.sql.SparkSession
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.nio.file.{Files, Path}
import scala.collection.mutable

/** The SQL-back-end cross-dataset DAG-reuse gate (`PIPE-SQL-DAG-REUSE`, `MATERIALIZE-SINK` sink flavor 4).
  * Uses `SdpDagReuse` — two MATERIALIZED defines where `Denominator = exists("DiabetesList")` references
  * the value-set retrieve `DiabetesList`. The compile-once pipeline plan cuts the DAG at each materialized
  * boundary, so `Denominator`'s recipe reads the PUBLISHED `sdpdagreuse_1_0_0__diabeteslist` dataset instead of re-inlining
  * the `Condition` retrieve.
  *
  * Two gates:
  *   1. STRUCTURE (always-on, server-free): the plan has one recipe per materialized define; the dependent
  *      `Denominator` recipe DECLARES a read of `sdpdagreuse_1_0_0__diabeteslist` and does NOT re-inline the retrieve, while
  *      the `DiabetesList` recipe DOES compute the retrieve. Non-vacuous control: the un-cut whole-library
  *      script references NO published `cql_` dataset (the sibling is a local view, re-inlined by
  *      name) — so the cut is what introduces the cross-dataset read.
  *   2. EQUIVALENCE (Connect-gated, skips cleanly without a server): executing the cut plan yields per-(subject,
  *      define) results IDENTICAL to the fully re-inlined baseline (`CORE-NO-SILENT-MISLOWER` — a structure
  *      change, not a semantic one), and `Denominator` is true only for the Diabetes patient p1. */
@RunWith(classOf[JUnitRunner])
final class SqlDagReuseSpec extends AnyFunSuite {

  private val Fixture = "SdpDagReuse"

  /** The cut pipeline plan for `SdpDagReuse`, lowered SERVER-FREE (terminology mounted, so the value-set
    * membership lowers; nothing executes). */
  private val dsCtx = SqlNames.DatasetNameContext("SdpDagReuse", "1.0.0")

  private def cutPlan: com.databricks.cql.service.PipelinePlan = {
    val optimized = Optimize.run(Pipeline().normalize(Paths.read(Paths.elm(Fixture))), Config.defaults)
    val env = SqlEvalEnv(Context.Patient, Config.defaults, terminology = true,
      cutSiblings = Some(mutable.Set.empty[String]), datasetNameCtx = Some(dsCtx))
    CompileService.cutPlan(SqlFrameLowering.lower(optimized, env), dsCtx = dsCtx)
  }

  /** The un-cut WHOLE-LIBRARY script (the re-inlined baseline structure) as one text blob. */
  private def reinlinedScript: String = {
    val optimized = Optimize.run(Pipeline().normalize(Paths.read(Paths.elm(Fixture))), Config.defaults)
    val env = SqlEvalEnv(Context.Patient, Config.defaults, terminology = true)
    val lowered = SqlFrameLowering.lower(optimized, env)
    lowered.script.statements.map(com.databricks.cql.transpiler.emit.SqlRender.render).mkString(";\n")
  }

  test("cut: Denominator recipe reads the published DiabetesList dataset, not the re-inlined retrieve") {
    val plan = cutPlan
    val names = plan.datasets.map(_.name).toSet
    assert(names == Set("sdpdagreuse_1_0_0__diabeteslist", "sdpdagreuse_1_0_0__denominator"),
      s"expected one recipe per materialized define, got $names")

    val den = plan.datasets.find(_.name == "sdpdagreuse_1_0_0__denominator").get
    val sql = den.statements.map(_.sql).mkString("\n")
    assert(den.reads.contains("sdpdagreuse_1_0_0__diabeteslist"),
      s"Denominator recipe must DECLARE a read of sdpdagreuse_1_0_0__diabeteslist; reads=${den.reads}")
    assert(sql.contains("sdpdagreuse_1_0_0__diabeteslist"),
      s"Denominator recipe SQL must reference the sibling dataset by name:\n$sql")
    assert(!sql.contains("retrieve_Condition"),
      s"Denominator recipe must NOT re-inline the Condition retrieve (it reads the cut sibling):\n$sql")
    // The declarative SDP-body form: a single WITH query that reads the sibling dataset and runs NO DDL
    // command (a Spark Declarative Pipeline body rejects CREATE VIEW/CACHE TABLE).
    assert(den.query.contains("sdpdagreuse_1_0_0__diabeteslist"), s"Denominator query must read the sibling dataset:\n${den.query}")
    assert(!den.query.contains("CREATE ") && !den.query.contains("CACHE "),
      s"Denominator query must be declarative (no DDL command) for an SDP body:\n${den.query}")

    val list = plan.datasets.find(_.name == "sdpdagreuse_1_0_0__diabeteslist").get
    val listSql = list.statements.map(_.sql).mkString("\n")
    assert(list.reads.isEmpty, s"DiabetesList has no materialized deps; reads=${list.reads}")
    assert(listSql.contains("retrieve_Condition"),
      s"DiabetesList recipe DOES compute the Condition retrieve:\n$listSql")
  }

  test("PIPE-EMIT-SQL: every recipe carries a deploy-ready sqlFile with the correct DDL and embedded query") {
    val plan = cutPlan
    // Write each recipe's sqlFile to build/sdp-sql/{name}.sql so you can inspect the actual SDP output.
    val outDir = Paths.rootDir.toPath.resolve("compiler/conformance/build/sdp-sql")
    Files.createDirectories(outDir)
    plan.datasets.foreach { r =>
      Files.writeString(outDir.resolve(s"${r.name}.sql"), r.sqlFile)
    }
    plan.datasets.foreach { r =>
      assert(r.sqlFile.nonEmpty, s"recipe ${r.name}: sqlFile must not be empty")
      // All recipes are materialized (cutPlan filters to materializedDefines).
      assert(r.sqlFile.contains("CREATE OR REFRESH MATERIALIZED VIEW"),
        s"recipe ${r.name}: sqlFile must contain CREATE OR REFRESH MATERIALIZED VIEW")
      // The DDL names the published dataset (the same name SDP registers under).
      assert(r.sqlFile.contains(s"MATERIALIZED VIEW ${r.name}"),
        s"recipe ${r.name}: sqlFile DDL must name the dataset '${r.name}'")
      // The embedded query is EXACTLY the declarative query field.
      assert(r.sqlFile.contains(r.query),
        s"recipe ${r.name}: sqlFile must embed the query verbatim")
      // No DDL inside the query body (SDP rejects CREATE VIEW/CACHE TABLE in a body).
      assert(!r.query.contains("CREATE ") && !r.query.contains("CACHE "),
        s"recipe ${r.name}: the query body must be declarative (no DDL) for an SDP body")
    }
    // Spot-check: the dependent recipe's sqlFile reads the sibling dataset by published name.
    val den = plan.datasets.find(_.name == "sdpdagreuse_1_0_0__denominator").get
    assert(den.sqlFile.contains("sdpdagreuse_1_0_0__diabeteslist"),
      s"Denominator sqlFile must reference the sibling dataset:\n${den.sqlFile}")
  }

  test("control (non-vacuous): the un-cut whole-library script re-inlines the sibling — no cql_ dataset read") {
    assert(!reinlinedScript.contains("sdpdagreuse_1_0_0__diabeteslist"),
      "the un-cut script references DiabetesList as a local view (re-inlined by name), never a cql_ dataset")
  }

  test("RUN-RESULTS-BY-EVAL: a cut plan compiled for one evaluation reads only its rows of each published sibling") {
    val spark: SparkSession = SparkConnect.session()
    val data = Some(new SparkFhirData(spark, Paths.dataDir.getAbsolutePath))
    val terminology = Some(new SparkTerminology(spark, Paths.dataDir.getAbsolutePath))
    val c = Differential.Case(Fixture, Context.Patient, data, terminology)

    val truth = SqlDifferential.engineResults(spark, c)
    // Every published sibling also holds a decoy evaluation's copy of its rows (`pipelineResults`).
    val scoped = SqlDifferential.pipelineResults(spark, c, evalId = Some("abcd1234"))
    assert(scoped == truth, s"scoped cut differs from the re-inlined baseline:\nscoped=$scoped\ntruth=$truth")
    // Control (non-vacuous): the same decoy rows read BARE diverge or fail, so the scope is what keeps
    // the answer right.
    val bare = scala.util.Try(SqlDifferential.pipelineResults(spark, c, evalId = Some("abcd1234"), scopeReads = false))
    assert(bare.isFailure || bare.get != truth, "unscoped reads over two evaluations' rows still matched")
  }

  test("equivalence: executing the cut plan matches the fully re-inlined baseline (only p1 has Diabetes)") {
    val spark: SparkSession = SparkConnect.session() // a live server, or a clean skip
    val data = Some(new SparkFhirData(spark, Paths.dataDir.getAbsolutePath))
    val terminology = Some(new SparkTerminology(spark, Paths.dataDir.getAbsolutePath))
    val c = Differential.Case(Fixture, Context.Patient, data, terminology)

    val truth = SqlDifferential.engineResults(spark, c)  // fully re-inlined (single script)
    val cut   = SqlDifferential.pipelineResults(spark, c) // the cross-dataset DAG cut

    assert(cut.keySet == truth.keySet, s"subject sets differ: cut=${cut.keySet} truth=${truth.keySet}")
    val mismatches = for {
      s <- truth.keys.toSeq.sorted
      n <- truth(s).keys.toSeq
      if cut(s)(n) != truth(s)(n)
    } yield s"subject '$s' define '$n': cut=${cut(s)(n)} reinlined=${truth(s)(n)}"
    assert(mismatches.isEmpty, s"${mismatches.size} cut-vs-reinlined mismatch(es):\n" + mismatches.mkString("\n"))

    // Correctness anchor: only p1 has a Diabetes-coded Condition, so Denominator is true only for p1.
    assert(cut.contains("p1"), s"expected subject p1: ${cut.keySet}")
    cut.foreach { case (subject, defs) =>
      assert(defs("Denominator") == ujson.Bool(subject == "p1"),
        s"subject '$subject' Denominator: got ${defs("Denominator")}, expected ${subject == "p1"}")
    }
  }

  // --- PIPE-EMIT-SQL: full-measure SDP dump, every corpus measure ------------------------------------------------

  Corpus.measureDefs.foreach { md =>
    test(s"PIPE-EMIT-SQL: ${md.library} full-measure SDP SQL files (CONF-GATE-MEASURE)") {
      val root = Pipeline().load(Paths.read(Paths.elm(md.library)))
      val linked = Differential.buildLibrary(Corpus.offlineCase(md))
      val config = Differential.evalConfig(md.library)
      val optimized = Optimize.run(linked, config)
      val measureCtx = SqlNames.DatasetNameContext(root.id, root.version.getOrElse(""))
      val env = SqlEvalEnv(Context.Patient, config, terminology = true,
        cutSiblings = Some(mutable.Set.empty[String]), datasetNameCtx = Some(measureCtx))
      val plan = CompileService.cutPlan(SqlFrameLowering.lower(optimized, env),
        root.id, root.version.getOrElse(""), dsCtx = measureCtx)

      assert(plan.datasets.nonEmpty, s"${md.library} must produce at least one recipe")

      // Dump every recipe's sqlFile to build/sdp-sql/<library>/{name}.sql.
      val outDir = Paths.rootDir.toPath.resolve(s"compiler/conformance/build/sdp-sql/${md.library}")
      Files.createDirectories(outDir)
      plan.datasets.foreach { r =>
        Files.writeString(outDir.resolve(s"${r.name}.sql"), r.sqlFile)
      }

      // All four populations must appear as recipes.
      val names = plan.datasets.map(_.name).toSet
      md.populations.foreach { case (pop, _) =>
        val expected = SqlNames.datasetName(pop, measureCtx)
        assert(names.contains(expected), s"missing population recipe '$expected'; got $names")
      }

      // Every recipe has valid sqlFile structure.
      plan.datasets.foreach { r =>
        assert(r.sqlFile.nonEmpty, s"recipe ${r.name}: sqlFile must not be empty")
        assert(r.sqlFile.contains("CREATE OR REFRESH MATERIALIZED VIEW"),
          s"recipe ${r.name}: missing DDL")
        assert(r.sqlFile.contains(s"MATERIALIZED VIEW ${r.name}"),
          s"recipe ${r.name}: DDL must name the dataset")
        val asIdx = r.sqlFile.indexOf("\nAS\n")
        assert(asIdx > 0, s"recipe ${r.name}: must contain '\\nAS\\n'")
        val queryFromSqlFile = r.sqlFile.substring(asIdx + "\nAS\n".length)
        assert(queryFromSqlFile == r.query,
          s"recipe ${r.name}: query extracted from sqlFile must equal recipe.query")
      }
    }
  }
}
