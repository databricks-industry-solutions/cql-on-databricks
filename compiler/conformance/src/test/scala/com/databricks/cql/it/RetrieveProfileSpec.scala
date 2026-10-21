package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.ir.Retrieve
import com.databricks.cql.passes.{Optimize, Pipeline, RetrieveDemand, TypeEncoding}
import com.databricks.cql.transpiler.emit.SqlRender
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SPARK-FREE gate for `LANG-RETRIEVE-PROFILE`: a retrieve constrained to a SPECIALISED profile must
  * emit a `meta.profile` scan filter, and a plain base-type retrieve must NOT.
  *
  * The distinction is the whole feature. The translator stamps a `templateId` on EVERY retrieve, so
  * filtering on its mere presence would drop every row of a `using FHIR` library (real data does not list
  * the base `StructureDefinition/<Type>` in `meta.profile`). Filtering on NONE of them is what once let a
  * profile-only retrieve return every `Observation` for the subject. Both directions are asserted here
  * because either mistake is silent.
  *
  * Lowering is text-only, so this needs no Connect server — it is the offline half of the evidence; the
  * executable half is the `FhirProfileOnlyRetrieve` differential (`SqlDifferentialSpec`). */
@RunWith(classOf[JUnitRunner])
final class RetrieveProfileSpec extends AnyFunSuite {

  private val BloodPressure = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-blood-pressure"

  /** The profile-only fixture with NO data/terminology provider — lowering emits text, so there is no
    * session in scope at all (the [[OfflineCompileSpec]] construction). */
  private def profileOffline: Differential.Case = Differential.Case("FhirProfileOnlyRetrieve", Context.Patient)

  private def renderOffline(c: Differential.Case): String = {
    val lib    = Differential.buildLibrary(c)
    val config = Differential.withConditionalUnion(c.library, Differential.withGrainFirst(c.library, Config.defaults))
    val env    = SqlEvalEnv(c.context, config, terminology = true)
    SqlFrameLowering.lower(Optimize.run(lib, config), env).script.statements.map(SqlRender.render).mkString(";\n\n")
  }

  test("a SPECIALISED-profile retrieve emits a meta.profile scan filter (LANG-RETRIEVE-PROFILE)") {
    val sql = renderOffline(profileOffline)
    assert(sql.contains("meta.profile"),
      "[USCoreBloodPressureProfile] lowers with no meta.profile filter — the retrieve degrades to every " +
        "Observation for the subject")
    assert(sql.contains(BloodPressure),
      s"expected the blood-pressure profile canonical in the emitted scan filter: $BloodPressure")
    assert(sql.contains("array_contains"), "expected an array_contains membership test on meta.profile")
  }

  test("an untagged resource is KEPT — meta.profile is a claim, not a guarantee (LANG-RETRIEVE-PROFILE)") {
    val sql = renderOffline(profileOffline)
    // "declares profiles => must declare THIS one"; a resource claiming none is not excluded, so QI-Core
    // measures still work against untagged customer data. Same rule as the reference provider.
    assert(sql.contains("IS NULL") && sql.contains("size("),
      "the profile filter must admit resources with a NULL/empty meta.profile")
  }

  test("a BASE-type retrieve emits NO profile filter (using FHIR, templateId = base StructureDefinition)") {
    val elm = Pipeline().load(Paths.read(Paths.elm("FhirBareRetrieveList")))
    val retrieves = elm.collect { case r: Retrieve => r }
    assert(retrieves.nonEmpty, "fixture has no retrieves")
    assert(retrieves.forall(_.templateId.isDefined),
      "precondition: the translator stamps templateId on every retrieve, base types included")
    assert(retrieves.forall(_.profileConstraint.isEmpty),
      "a base StructureDefinition must NOT be treated as a profile constraint — it would drop every row: " +
        retrieves.flatMap(_.profileConstraint).mkString(", "))
  }

  test("a profile-constrained retrieve keeps `meta` in the projection demand (ARCH-RETRIEVE-PROJECT)") {
    val lib    = Optimize.run(Differential.buildLibrary(profileOffline), Config.defaults)
    val tctx   = TypeEncoding.ctxOf(lib)
    val typeOf = (e: com.databricks.cql.ir.Expr) => TypeEncoding.typeOf(e, tctx)
    RetrieveDemand.analyze(lib, typeOf).get("Observation") match {
      case Some(RetrieveDemand.Narrow(fields)) =>
        assert(fields.contains("meta"),
          s"Observation demand prunes `meta`, so the profile filter would reference a dropped column: $fields")
      case other => info(s"Observation demand is $other (un-pruned) — nothing to assert")
    }
  }
}
