package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.env.Config
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The materialization DECISION layer (DATA-MATERIALIZE, DATA-SKIP-UNFILTERED). Pure — no Spark — so
  * the selective/all/skip-unfiltered decision and the reproducibility record are gated at the engine
  * level, independent of the differential harness. */
@RunWith(classOf[JUnitRunner])
class MaterializeSpec extends AnyFunSuite {

  private def retrieve(dt: String, codes: Option[Expr] = None, dateRange: Option[Expr] = None) =
    Retrieve(s"{http://hl7.org/fhir}$dt", None, Some("code"), codes, Some("recordedDate"), dateRange)

  private val bareRetrieve = retrieve("Encounter")
  private val vsRetrieve   = retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None)))
  private val dateRetrieve = retrieve("Observation", dateRange = Some(int("1")))

  test("isUnfilteredRetrieve: bare retrieve true; value-set/date-filtered or non-retrieve false") {
    assert(Materialize.isUnfilteredRetrieve(bareRetrieve))
    assert(!Materialize.isUnfilteredRetrieve(vsRetrieve))
    assert(!Materialize.isUnfilteredRetrieve(dateRetrieve))
    assert(!Materialize.isUnfilteredRetrieve(bool(true)))
  }

  test("mode=all skips a bare unfiltered-retrieve define (DATA-SKIP-UNFILTERED, honored in every mode)") {
    val library = lib(
      edef("AllEncounters", bareRetrieve),
      edef("Diabetes", vsRetrieve),
      edef("Flag", bool(true)))
    val p = Materialize.plan(library, Config.defaults.copy(materializeMode = Config.MaterializeMode.All))
    assert(p.mode == "all")
    assert(p.skippedUnfiltered == Seq("AllEncounters"))
    assert(p.materialize.toSet == Set("Diabetes", "Flag"))
  }

  test("mode=all with skip flag OFF materializes the bare retrieve too") {
    val library = lib(edef("AllEncounters", bareRetrieve), edef("Flag", bool(true)))
    val p = Materialize.plan(library, Config.defaults.copy(
      materializeMode = Config.MaterializeMode.All, materializeSkipUnfilteredRetrieves = false))
    assert(p.skippedUnfiltered.isEmpty)
    assert(p.materialize.toSet == Set("AllEncounters", "Flag"))
  }

  test("mode=none materializes nothing (DATA-MATERIALIZE-POLICY)") {
    val library = lib(
      edef("Shared", bool(true)),
      edef("A", ref("Shared")),
      edef("B", ref("Shared")))
    val p = Materialize.plan(library, Config.defaults.copy(materializeMode = Config.MaterializeMode.None))
    assert(p.mode == "none")
    assert(p.materialize.isEmpty)
  }

  test("mode=costly reports the reuse-shared set purely (cost refines per-candidate at the seam)") {
    // Shared <- A, B (twice); Lonely <- A only; A, B are roots.
    val library = lib(
      edef("Shared", bool(true)),
      edef("Lonely", bool(false)),
      edef("A", And(ref("Shared"), ref("Lonely"))),
      edef("B", ref("Shared")))
    val p = Materialize.plan(library, Config.defaults) // Costly is the default
    assert(p.mode == "costly")
    assert(p.materialize == Seq("Shared")) // Lonely referenced once, A/B are leaves of nothing
  }

  test("costly still skips a shared bare unfiltered-retrieve define") {
    val library = lib(
      edef("AllEncounters", bareRetrieve),
      edef("X", ref("AllEncounters")),
      edef("Y", ref("AllEncounters")))
    val p = Materialize.plan(library, Config.defaults)
    assert(p.materialize.isEmpty)                          // AllEncounters is shared but unfiltered -> skipped
    assert(p.skippedUnfiltered == Seq("AllEncounters"))
  }

  test("reuseCounts: per-name refcount including the context name (DATA-MATERIALIZE-POLICY input)") {
    val library = lib(
      edef("Shared", bool(true)),
      edef("Lonely", bool(false)),
      edef("A", And(ref("Shared"), ref("Lonely"))),
      edef("B", ref("Shared")))
    val reuse = Materialize.reuseCounts(library)
    assert(reuse.getOrElse("Shared", 0) == 2) // A, B
    assert(reuse.getOrElse("Lonely", 0) == 1) // A
    assert(reuse.getOrElse("A", 0) == 0)
  }

  test("retrieveCounts: per-TYPE Retrieve consumer count (DATA-RETRIEVE-SHARE-BARRIER input)") {
    // Encounter retrieved 3× (across defines/branches), Condition 1×; keyed by bare local type name so
    // the counts match the `retrieve_<Type>` mount keys the seam consults.
    val library = lib(
      edef("A", retrieve("Encounter")),
      edef("B", And(Exists(retrieve("Encounter")), Exists(retrieve("Condition")))),
      edef("C", retrieve("Encounter")))
    val counts = Materialize.retrieveCounts(library)
    assert(counts.getOrElse("Encounter", 0) == 3)
    assert(counts.getOrElse("Condition", 0) == 1)
    assert(counts.getOrElse("Observation", 0) == 0)
  }

  test("skipUnfilteredNames: the bare retrieves, empty when the flag is off") {
    val library = lib(edef("AllEncounters", bareRetrieve), edef("Diabetes", vsRetrieve))
    assert(Materialize.skipUnfilteredNames(library, Config.defaults) == Set("AllEncounters"))
    assert(Materialize.skipUnfilteredNames(library,
      Config.defaults.copy(materializeSkipUnfilteredRetrieves = false)).isEmpty)
  }

  test("record pins engine version, resolved config, and value-set versions (TECH-DETERMINISM)") {
    val library = Library("M", Some("1.0.0"), Nil, Nil, Nil, Nil,
      valueSets = Seq(ValueSetDef("Diabetes", "urn:b", Some("2")), ValueSetDef("CKD", "urn:a", None)),
      Nil, Nil, Nil, Seq(edef("Flag", bool(true))))
    val rec = Materialize.record(library, Config.defaults, engineVersion = "test-1.2.3")
    assert(rec.engineVersion == "test-1.2.3")
    assert(rec.config.contains("materialize.skip_unfiltered_retrieves" -> "true"))
    assert(rec.valueSets == Seq("urn:a" -> None, "urn:b" -> Some("2"))) // sorted by id, versions pinned
  }
}
