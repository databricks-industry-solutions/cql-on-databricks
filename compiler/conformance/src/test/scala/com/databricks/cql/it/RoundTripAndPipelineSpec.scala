package com.databricks.cql.it

import com.databricks.cql.env.Config
import com.databricks.cql.error.VerifierError
import com.databricks.cql.harness.Golden
import com.databricks.cql.ir.{Library, Retrieve, TreePrinter, UnknownExpr, ValueSetRef}
import com.databricks.cql.load.ElmCodec
import com.databricks.cql.passes.{Optimize, Pipeline}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** PHASE-0 exit criteria, end to end on the authored corpus:
  *   - the pipeline round-trips ELM->IR->ELM with verifiers passing;
  *   - a trivial measure normalizes clean (no unmodeled nodes, verifiers green);
  *   - an unmodeled construct fails LOUDLY (CORE-NO-SILENT-MISLOWER);
  *   - the golden-plan snapshot mechanism is live (CONF-GOLDEN-PLAN).
  *
  * Consumes ELM GENERATED from the authored CQL by cql-translate-cli (TECH-ELM-INPUT);
  * ELM is never committed.
  */
@RunWith(classOf[JUnitRunner])
class RoundTripAndPipelineSpec extends AnyFunSuite {

  private val elmFiles = Paths.listElm()

  test("the generated ELM corpus exists (translate step ran)") {
    assert(elmFiles.nonEmpty,
      s"no generated ELM in ${Paths.elmDir}; run :cql-translate-cli:translateCorpus")
  }

  test("every measure round-trips ELM->IR->ELM->IR as a fixed point") {
    elmFiles.foreach { f =>
      val ir1 = ElmCodec.loadString(Paths.read(f))
      val ir2 = ElmCodec.loadString(ElmCodec.emitString(ir1))
      assert(ir1 == ir2, s"round-trip not stable for ${f.getName}")
    }
  }

  test("the trivial measure is fully modeled (no UnknownExpr) and normalizes clean") {
    val elm = Paths.read(Paths.elm("TrivialBoolean"))
    val loaded = ElmCodec.loadString(elm)
    val unknowns = loaded.collect { case u: UnknownExpr => u.elmType }
    assert(unknowns.isEmpty, s"trivial measure has unmodeled nodes: ${unknowns.distinct.mkString(", ")}")

    // Full Phase 0 pipeline with verifiers enabled: must pass cleanly.
    val normalized = Pipeline().normalize(elm)
    assert(normalized.statements.size == 6)
  }

  test("an unmodeled construct fails loudly at the Normalize verifier (CORE-NO-SILENT-MISLOWER)") {
    // Unsupported.cql uses `collapse` (a list-of-intervals op) not modeled yet; it becomes
    // UnknownExpr and MUST trip the OnlyKernelNodes verifier — never silently lowered to a
    // plausible-but-wrong plan. (Intervals.cql is now fully modeled in Phase 3, LANG-INTERVAL.)
    val elm = Paths.read(Paths.elm("Unsupported"))
    val loaded = ElmCodec.loadString(elm)
    assert(loaded.collect { case u: UnknownExpr => u }.nonEmpty, "expected unmodeled nodes in Unsupported")
    val ex = intercept[VerifierError](Pipeline().normalize(elm))
    assert(ex.getMessage.contains("Normalize"))
  }

  test("IR snapshot of the trivial measure is stable (CONF-GOLDEN-PLAN)") {
    val normalized = Pipeline().normalize(Paths.read(Paths.elm("TrivialBoolean")))
    assume(Golden.check(Paths.goldenDir, "TrivialBoolean", "ir.txt", TreePrinter.treeString(normalized)),
      "no TrivialBoolean/ir.txt golden; regenerate with -Dcqldatabricks.blessPlans=true, then move it in")
  }

  test("the optimizer pushes value-set + date filters onto the retrieve scan (ARCH-PUSHDOWN, CONF-GOLDEN-PLAN)") {
    val lib = Pipeline().normalize(Paths.read(Paths.elm("FhirNumerator")))
    def condition(l: Library): Retrieve =
      l.collect { case r: Retrieve if r.dataType.endsWith("Condition") => r }.head

    // Pushdown ON: both filters fold onto the [Condition] scan, the where is gone — the kernel-level
    // scan pruning our optimizer is responsible for (Catalyst/Photon do the physical skipping below).
    val optimized = condition(Optimize.run(lib, Config.defaults))
    assert(optimized.codes.contains(ValueSetRef("Diabetes", None)), "value-set filter pushed to the scan")
    assert(optimized.codeProperty.contains("code"))
    assert(optimized.dateProperty.contains("recordedDate") && optimized.dateRange.isDefined,
      "date-range filter pushed to the scan")

    // Pushdown OFF: the retrieve stays bare; the filters remain in the where (the unoptimized twin).
    val unoptimized = condition(Optimize.run(lib, Config.defaults.copy(optimizeRetrievePushdown = false)))
    assert(unoptimized.codes.isEmpty && unoptimized.dateRange.isEmpty, "execute-as-written leaves the scan unfiltered")

    // Golden snapshot of the optimized kernel, so an optimizer change surfaces as a review diff.
    assume(Golden.check(Paths.goldenDir, "FhirNumerator", "optimized.ir.txt",
      TreePrinter.treeString(Optimize.run(lib, Config.defaults))),
      "no FhirNumerator/optimized.ir.txt golden; regenerate with -Dcqldatabricks.blessPlans=true, then move it in")
  }

  test("determinism: loading the same ELM twice yields identical IR (TECH-DETERMINISM)") {
    val elm = Paths.read(Paths.elm("TrivialBoolean"))
    assert(ElmCodec.loadString(elm) == ElmCodec.loadString(elm))
  }
}
