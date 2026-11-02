package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.passes.{Optimize, Pipeline}
import com.databricks.cql.transpiler.emit.SqlRender
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Offline-compile gate (`TYPE-OFFLINE-COMPILE`, `compiler/docs/data-model.md`): typing +
  * `Optimize` + `SqlFrameLowering.lower` perform ZERO Spark round-trips. With the SQL back end this is
  * STRUCTURAL, not merely observed: the SQL emitter is Spark-free (it constructs no `Column`/`DataFrame`,
  * touches no `SparkSession`), so lowering CANNOT reach a server — it emits text from the static FHIR
  * model (`DATA-SCHEMA-CONTRACT`). This gate stays as the NON-VACUOUS regression guard: build and lower a
  * real measure with NO session in scope at all, and assert it renders a non-trivial SQL script. If any
  * schema/action dependency ever sneaks back in, the lowering would need a session and fail to compile
  * here (there is none).
  *
  * Needs NO running server (that is the point) — it does not use [[SparkConnect]] at all. */
@RunWith(classOf[JUnitRunner])
final class OfflineCompileSpec extends AnyFunSuite {

  /** Lower `c` to a SQL script entirely server-free (no `SparkSession` exists in this method) and return
    * the rendered script text. It binds placeholder relations (`SqlEvalEnv.describing`, BIND-CALL-SITE): names,
    * not live providers. */
  private def lowerOffline(c: Differential.Case): String = {
    val lib = Differential.buildLibrary(c)
    val config = Differential.withConditionalUnion(c.library, Differential.withGrainFirst(c.library, Config.defaults))
    val env = SqlEvalEnv(c.context, config).describing
    val lowered = SqlFrameLowering.lower(Optimize.run(lib, config), env)
    lowered.script.statements.map(SqlRender.render).mkString(";\n\n")
  }

  // The lowering needs no data adapter — retrieves lower to references of their bound relations, never to
  // actual rows — so the cases carry NO DataProvider/TerminologyProvider (there is no session to build one).
  private def fhirCase(library: String): Differential.Case =
    Differential.Case(library, Context.Patient)

  Corpus.measureDefs.foreach { md =>
  test(s"${md.library} typing + lowering perform ZERO Spark round-trips (TYPE-OFFLINE-COMPILE)") {
    val tree = lowerOffline(Corpus.offlineCase(md))
    assert(tree.nonEmpty, "lowered script should render server-free")
    assert(tree.linesIterator.size > 5, s"expected a non-trivial lowered script, got:\n$tree")
  }
  }

  test("a FHIR retrieve + value[x] + terminology fixture lowers with ZERO round-trips (FhirNumerator)") {
    val tree = lowerOffline(fhirCase("FhirNumerator"))
    assert(tree.nonEmpty, "lowered script should render server-free")
  }

  // Heterogeneous `Choice` (LANG-CHOICE-RESOURCE) once forced a compile-time `.schema` round-trip
  // (`tagChoiceArms` read each arm's `__val` DataType). The SQL het-union encoding reconstructs member
  // types from the static model, so these MUST lower with no session too.
  for (fixture <- Seq("FhirChoiceUnionResources", "FhirConditionalUnionHet", "FhirChoiceResourcePrimitive")) {
    test(s"heterogeneous Choice lowers with ZERO round-trips ($fixture, TYPE-OFFLINE-COMPILE)") {
      val tree = lowerOffline(fhirCase(fixture))
      assert(tree.nonEmpty, "lowered script should render server-free")
    }
  }
}
