package com.databricks.cql.transpiler.lower

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.ir._
import com.databricks.cql.transpiler.emit.SqlRender
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** `DATA-RETRIEVE-BARRIER-OPT` at the EMIT level: the optional retrieve barrier gives a filtered read its
  * own object and rewrites every consumer to read it, and is strictly inert by default. */
@RunWith(classOf[JUnitRunner])
class SqlRetrieveBarrierSpec extends AnyFunSuite {

  private val Condition = "{http://hl7.org/fhir}Condition"
  private val diabetes  = ValueSetDef("Diabetes", "urn:oid:2.999.1.101", None)

  private def vsRetrieve(name: String = "Diabetes") =
    Retrieve(Condition, None, Some("code"), Some(ValueSetRef(name, None)), None, None)

  private def define(name: String, e: Expr): ExpressionDef = ExpressionDef(name, Some("Patient"), e)

  private def library(valueSets: Seq[ValueSetDef], defs: ExpressionDef*): Library =
    Library("Test", Some("1.0.0"),
      usings = Seq(UsingDef(Some("FHIR"), "http://hl7.org/fhir", Some("4.0.1"))),
      includes = Nil, parameters = Nil, codeSystems = Nil, valueSets = valueSets, codes = Nil, concepts = Nil,
      contexts = Seq(ContextDef("Patient")), statements = defs)

  private def env(mode: Config.RetrieveMode, minReuse: Int = 2): SqlEvalEnv =
    SqlEvalEnv(Context.Patient,
      Config.defaults.copy(materializeRetrieves = mode, materializeRetrieveMinReuse = minReuse),
      terminology = true)

  private val twoConsumers = library(Seq(diabetes),
    define("DiabetesA", vsRetrieve()),
    define("DiabetesB", vsRetrieve()))

  test("default (none) is INERT: the script is byte-identical to the pre-feature lowering") {
    val off = SqlFrameLowering.lower(twoConsumers, env(Config.RetrieveMode.None))
    assert(off.materializedReads.isEmpty)
    val out = SqlRender.render(off.script)
    assert(!out.contains("read_condition_"))
    // The mount call is still inlined at BOTH use sites, with the membership re-applied above it.
    assert(out.split("retrieve_Condition", -1).length - 1 == 2)
  }

  test("all: a filtered read becomes its own CACHE TABLE and every consumer READS it") {
    val on = SqlFrameLowering.lower(twoConsumers, env(Config.RetrieveMode.All))
    assert(on.materializedReads.sizeIs == 1)
    val obj = on.materializedReads.head.obj
    val out = SqlRender.render(on.script)

    // The read is a barrier object, emitted BEFORE any define that reads it.
    assert(out.contains(s"CACHE TABLE $obj AS"))
    assert(out.indexOf(s"CACHE TABLE $obj AS") < out.indexOf("DiabetesA"))
    // The mount is called exactly ONCE now — inside the read object, not at each use site.
    assert(out.split("retrieve_Condition", -1).length - 1 == 1)
    // ...and each consumer reads the object instead.
    assert(out.split(s"FROM $obj", -1).length - 1 == 2)
  }

  test("the substitution preserves the mount's (__subject_id, __resource) shape, so consumers are unchanged") {
    val on  = SqlFrameLowering.lower(twoConsumers, env(Config.RetrieveMode.All))
    val off = SqlFrameLowering.lower(twoConsumers, env(Config.RetrieveMode.None))
    // A relation define still projects the resource id as `__ordinal` and the struct as `__val`, whether
    // its rows came from an inlined mount call or from the read object.
    Seq(on, off).foreach { l =>
      val out = SqlRender.render(l.script)
      assert(out.contains("__resource.__resource_id AS __ordinal"))
      assert(out.contains("__resource AS __val"))
    }
    assert(on.relationDefines == off.relationDefines)
    assert(on.names == off.names)
  }

  test("costly gates on reuse: a single-consumer read stays inlined, a shared one is promoted") {
    val oneConsumer = library(Seq(diabetes), define("DiabetesA", vsRetrieve()))
    assert(SqlFrameLowering.lower(oneConsumer, env(Config.RetrieveMode.Costly)).materializedReads.isEmpty)
    assert(SqlFrameLowering.lower(twoConsumers, env(Config.RetrieveMode.Costly)).materializedReads.sizeIs == 1)
    // ...and `all` promotes the single-consumer one the reuse gate declined.
    assert(SqlFrameLowering.lower(oneConsumer, env(Config.RetrieveMode.All)).materializedReads.sizeIs == 1)
  }

  test("two local aliases of ONE canonical value set share a single object (the cross-measure dedup)") {
    val aliased = library(
      Seq(diabetes, ValueSetDef("Helpers.Diabetes", diabetes.id, None)),
      define("DiabetesA", vsRetrieve("Diabetes")),
      define("DiabetesB", vsRetrieve("Helpers.Diabetes")))
    val on = SqlFrameLowering.lower(aliased, env(Config.RetrieveMode.All))
    assert(on.materializedReads.sizeIs == 1)
    val out = SqlRender.render(on.script)
    assert(out.split(s"FROM ${on.materializedReads.head.obj}", -1).length - 1 == 2)
  }

  test("a bare retrieve is never promoted, and its define keeps reading the mount directly") {
    val bare = library(Nil, define("AllConditions", Retrieve(Condition, None, None, None, None, None)))
    val on = SqlFrameLowering.lower(bare, env(Config.RetrieveMode.All))
    assert(on.materializedReads.isEmpty)
    assert(SqlRender.render(on.script).contains("retrieve_Condition"))
  }

  test("the decision report names each read barrier with a read- reason term") {
    val on = SqlFrameLowering.lower(twoConsumers, env(Config.RetrieveMode.All))
    val d = on.decisions.find(_.name == on.materializedReads.head.obj).get
    assert(d.kind == "cache_table")
    assert(d.reason == "read-valueset")
  }
}
