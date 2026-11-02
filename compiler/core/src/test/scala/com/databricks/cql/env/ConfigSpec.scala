package com.databricks.cql.env

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
    // Unlike `materialize.mode`, `selective` is NOT an alias here — this policy never had that spelling.
    assert(Config.resolve(Map("materialize.retrieves" -> "selective")).materializeRetrieves == Config.RetrieveMode.None)
    // The two policies are independent: setting one leaves the other at its own default.
    val c = Config.resolve(Map("materialize.retrieves" -> "all"))
    assert(c.materializeMode == Config.MaterializeMode.Costly)
  }

  test("materialize.retrieve_min_reuse parses, and rejects a nonsensical threshold") {
    assert(Config.resolve(Map("materialize.retrieve_min_reuse" -> "6")).materializeRetrieveMinReuse == 6)
    assert(Config.resolve(Map("materialize.retrieve_min_reuse" -> "nope")).materializeRetrieveMinReuse == 2)
    // A threshold below 1 cannot mean anything (every scan has ≥1 call site) — fall back to the default.
    assert(Config.resolve(Map("materialize.retrieve_min_reuse" -> "0")).materializeRetrieveMinReuse == 2)
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

  test("an unparseable known value falls back to the default for that key") {
    val c = Config.resolve(Map("materialize.mode" -> "nonsense", "materialize.cost_threshold" -> "notanumber"))
    assert(c.materializeMode == Config.MaterializeMode.Costly)
    assert(c.materializeCostThreshold == Config.DefaultMaterializeCostThreshold)
  }

  test("recordedView is deterministic and complete") {
    val view = Config.defaults.recordedView
    assert(view.map(_._1) == Seq(
      "optimize.retrieve_pushdown", "materialize.mode", "materialize.cost_threshold",
      "materialize.skip_unfiltered_retrieves", "materialize.retrieves", "materialize.retrieve_min_reuse",
      "materialize.boundaries", "terminology.adapter", "emit.object_prefix", "verifiers.enabled",
      "debug.dump_dir", "lower.grain_first",
      "optimize.conditional_union", "optimize.conditional_union_max_depth",
      "lower.cte_defs",
      "lower.project_fuse",
      "lower.retrieve_views",
      "bind.gap_lenient"))
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
