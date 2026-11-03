package com.databricks.cql.env

import com.databricks.cql.error.{ConfigError, RetiredConfigKey}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Immutable config resolution and recording (TECH-CONFIG, TECH-DETERMINISM). */
@RunWith(classOf[JUnitRunner])
class ConfigSpec extends AnyFunSuite {

  test("defaults match the documented TECH-CONFIG table") {
    val c = Config.defaults
    assert(c.optimizeRetrievePushdown)
    assert(c.materializeMode == Config.MaterializeMode.Costly) // DATA-MATERIALIZE-POLICY: costly is the default
    assert(c.materializeCostThreshold == Config.DefaultMaterializeCostThreshold)
    assert(c.materializeSkipUnfilteredRetrieves)
    // DATA-RETRIEVE-BARRIER-OPT: OFF by default — an existing caller's script is byte-unchanged.
    assert(c.materializeRetrieves == Config.RetrieveMode.None)
    assert(c.materializeRetrieveMinReuse == 2)
  }

  test("materialize.retrieves keys: none/costly/all (no `selective` alias — a distinct policy)") {
    assert(Config.resolve(Map("materialize.retrieves" -> "none")).materializeRetrieves == Config.RetrieveMode.None)
    assert(Config.resolve(Map("materialize.retrieves" -> "costly")).materializeRetrieves == Config.RetrieveMode.Costly)
    assert(Config.resolve(Map("materialize.retrieves" -> "all")).materializeRetrieves == Config.RetrieveMode.All)
    // Unlike `materialize.mode`, `selective` is NOT an alias here — this policy never had that spelling, so it is refused.
    val err = intercept[ConfigError](Config.resolve(Map("materialize.retrieves" -> "selective")))
    assert(err.key == "materialize.retrieves" && err.value == "selective", err.getMessage)
    // The two policies are independent: setting one leaves the other at its own default.
    val c = Config.resolve(Map("materialize.retrieves" -> "all"))
    assert(c.materializeMode == Config.MaterializeMode.Costly)
  }

  test("materialize.retrieve_min_reuse parses, and refuses a value that is not an integer of at least 1 (CORE-NO-SILENT-MISLOWER)") {
    assert(Config.resolve(Map("materialize.retrieve_min_reuse" -> "6")).materializeRetrieveMinReuse == 6)
    for (bad <- Seq("nope", "0")) {
      val err = intercept[ConfigError](Config.resolve(Map("materialize.retrieve_min_reuse" -> bad)))
      assert(err.getMessage.contains("materialize.retrieve_min_reuse") && err.getMessage.contains(s"'$bad'"), err.getMessage)
    }
  }

  test("caller overrides take precedence over defaults") {
    val c = Config.resolve(Map(
      "optimize.retrieve_pushdown" -> "false",
      "materialize.mode" -> "all",
      "materialize.cost_threshold" -> "12345",
      "terminology.adapter" -> "uc-table"))
    assert(!c.optimizeRetrievePushdown)
    assert(c.materializeMode == Config.MaterializeMode.All)
    assert(c.materializeCostThreshold == 12345L)
    assert(c.terminologyAdapter.contains("uc-table"))
    // untouched key keeps its default
    assert(c.materializeSkipUnfilteredRetrieves)
  }

  test("materialize.mode keys: none/costly/all, and `selective` is a deprecated alias of costly") {
    assert(Config.resolve(Map("materialize.mode" -> "none")).materializeMode == Config.MaterializeMode.None)
    assert(Config.resolve(Map("materialize.mode" -> "costly")).materializeMode == Config.MaterializeMode.Costly)
    assert(Config.resolve(Map("materialize.mode" -> "all")).materializeMode == Config.MaterializeMode.All)
    assert(Config.resolve(Map("materialize.mode" -> "selective")).materializeMode == Config.MaterializeMode.Costly)
  }

  test("an unparseable known value fails, naming the key and the value, and never takes the default (CORE-NO-SILENT-MISLOWER)") {
    val mode = intercept[ConfigError](Config.resolve(Map("materialize.mode" -> "nonsense")))
    assert(mode.getMessage.contains("'materialize.mode'") && mode.getMessage.contains("'nonsense'"), mode.getMessage)
    val cost = intercept[ConfigError](Config.resolve(Map("materialize.cost_threshold" -> "notanumber")))
    assert(cost.getMessage.contains("'materialize.cost_threshold'") && cost.getMessage.contains("'notanumber'"), cost.getMessage)
    val flag = intercept[ConfigError](Config.resolve(Map("optimize.retrieve_pushdown" -> "yes")))
    assert(flag.getMessage.contains("'optimize.retrieve_pushdown'") && flag.getMessage.contains("'yes'"), flag.getMessage)
    val depth = intercept[ConfigError](Config.resolve(Map("optimize.conditional_union_max_depth" -> "deep")))
    assert(depth.getMessage.contains("'deep'"), depth.getMessage)
  }

  test("an unknown key is ignored (SVC-PROTOCOL)") {
    assert(Config.resolve(Map("not.a.setting" -> "anything")) == Config.defaults)
  }

  test("a retired key is refused, naming it (SVC-PROTOCOL)") {
    for (key <- Seq("lower.project_fuse", "lower.retrieve_views")) {
      val err = intercept[RetiredConfigKey](Config.resolve(Map(key -> "true")))
      assert(err.key == key && err.getMessage.contains(s"'$key'"), err.getMessage)
    }
  }

  test("recordedView is deterministic and complete") {
    val view = Config.defaults.recordedView
    assert(view.map(_._1) == Seq(
      "optimize.retrieve_pushdown", "materialize.mode", "materialize.cost_threshold",
      "materialize.skip_unfiltered_retrieves", "materialize.retrieves", "materialize.retrieve_min_reuse",
      "materialize.boundaries", "terminology.adapter", "emit.object_prefix", "verifiers.enabled",
      "debug.dump_dir", "lower.grain_first",
      "optimize.conditional_union", "optimize.conditional_union_max_depth",
      "lower.cte_defs"))
  }

  test("Env freezes the config and threads context functionally") {
    val env = Env.root(Context.Patient, Config.defaults)
    assert(env.context == Context.Patient)
    val pop = env.inContext(Context.Population)
    assert(pop.context == Context.Population)
    assert(env.context == Context.Patient) // original unchanged (immutable)
    assert(env.config eq pop.config)        // config frozen, shared down the tree
  }

  test("context is extensible, not hardcoded to Patient/Practitioner") {
    val custom = Context.named("Encounter")
    assert(custom.name == "Encounter")
    assert(custom.hasSubjectGrouping)
  }
}
