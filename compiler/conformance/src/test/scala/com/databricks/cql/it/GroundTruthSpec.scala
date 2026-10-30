package com.databricks.cql.it

import com.databricks.cql.harness.GroundTruthRunner
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** PHASE-0 exit: "the harness can run a trivial measure through the reference and store
  * expected results" (CONF-GROUND-TRUTH). The reference is the maintained Java CQL engine
  * (engine-jvm), run in-process — no Node. The same authored CQL feeds both the engine's
  * Load (via generated ELM) and this reference (via the same cqframework translator), so
  * neither side can diverge (TECH-ELM-INPUT).
  */
@RunWith(classOf[JUnitRunner])
class GroundTruthSpec extends AnyFunSuite {

  private def evaluate(name: String): Map[String, ujson.Value] =
    GroundTruthRunner.evaluateFromElm(
      cqlText = Paths.read(Paths.cql(name)),
      elmJson = Paths.read(Paths.elm(name)))

  test("the trivial measure runs through the Java reference and produces expected results") {
    val expected = evaluate("TrivialBoolean")

    assert(expected("AgeThreshold").num == 18.0)
    assert(expected("PatientAgeForTest").num == 42.0)
    assert(expected("MeetsThreshold").bool)
    assert(expected("InInitialPopulation").bool)
    assert(expected("InNumerator").bool)

    // Store the captured expected results as a fixture (CONF-GROUND-TRUTH). Under build/.
    val out = new File("build/ground-truth/TrivialBoolean.expected.json")
    val obj = ujson.Obj()
    expected.toSeq.sortBy(_._1).foreach { case (k, v) => obj(k) = v }
    Paths.write(out, ujson.write(obj, indent = 2))
    assert(out.isFile)
  }

  test("the 3VL operator fixture reproduces CQL null-propagation through the reference") {
    val expected = evaluate("Logic3VL")

    // null propagation (LANG-3VL) — the cases that silently break naive engines.
    assert(expected("TrueAndNull") == ujson.Null)
    assert(expected("FalseAndNull").bool == false) // false dominates
    assert(expected("TrueOrNull").bool == true)    // true dominates
    assert(expected("FalseOrNull") == ujson.Null)
    assert(expected("NotNull") == ujson.Null)
    assert(expected("TrueXorFalse").bool == true)
    assert(expected("NullXorTrue") == ujson.Null)
  }

  test("the Java reference implements Implies correctly (gap G4 resolved vs the stale JS stack)") {
    val expected = evaluate("Logic3VL")
    // cql-execution 2.1.0 returned null for all of these (no Implies impl); the maintained
    // Java engine computes CQL-correct 3VL implies — matching our Bool3 library.
    assert(expected("TrueImpliesFalse").bool == false)
    assert(expected("NullImpliesTrue").bool == true)  // null implies true = true
    assert(expected("FalseImpliesNull").bool == true) // false implies anything = true
  }
}
