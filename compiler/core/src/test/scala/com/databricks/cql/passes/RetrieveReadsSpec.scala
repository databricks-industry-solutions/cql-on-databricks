package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.env.Config
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The OPTIONAL retrieve-barrier decision (`DATA-RETRIEVE-BARRIER-OPT`). Pure — no Spark — so read
  * IDENTITY, the reuse count, and the mode selection are gated at the engine level. */
@RunWith(classOf[JUnitRunner])
class RetrieveReadsSpec extends AnyFunSuite {

  private def retrieve(dt: String,
                       codes: Option[Expr] = None,
                       dateRange: Option[Expr] = None,
                       templateId: Option[String] = None) =
    Retrieve(s"{http://hl7.org/fhir}$dt", templateId, Some("code"), codes, Some("recordedDate"), dateRange)

  /** A library carrying value-set DECLARATIONS, so a `ValueSetRef` resolves to a canonical id. */
  private def libWithValueSets(valueSets: Seq[ValueSetDef], statements: Statement*): Library =
    Library("Test", Some("1.0.0"), Nil, Nil, Nil, Nil, valueSets, Nil, Nil, Nil, statements.toList)

  private val diabetes = ValueSetDef("Diabetes", "urn:oid:2.999.1.101", None)
  private val bare     = retrieve("Encounter")
  private val vs       = retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None)))

  test("isFiltered: a value-set / date / profile read is a candidate; a bare retrieve is not") {
    assert(!RetrieveReads.isFiltered(bare))
    assert(RetrieveReads.isFiltered(vs))
    assert(RetrieveReads.isFiltered(retrieve("Observation", dateRange = Some(int("1")))))
    // LANG-RETRIEVE-PROFILE: a profile-only retrieve filters, even with no codes or dates at all.
    assert(RetrieveReads.isFiltered(
      retrieve("Observation", templateId = Some("http://hl7.org/fhir/StructureDefinition/us-core-bmi"))))
    // The BASE template stamped on every retrieve is not a constraint.
    assert(!RetrieveReads.isFiltered(
      retrieve("Observation", templateId = Some("http://hl7.org/fhir/StructureDefinition/Observation"))))
  }

  test("a bare retrieve is never a candidate, even under mode=all (DATA-SKIP-UNFILTERED's rationale)") {
    val library = libWithValueSets(Seq(diabetes), edef("AllEncounters", bare), edef("Diabetic", vs))
    val reads = RetrieveReads.selected(library, Config.defaults.copy(
      materializeRetrieves = Config.RetrieveMode.All))
    assert(reads.map(_.coords.resourceType) == Seq("Condition"))
  }

  test("identity is the RESOLVED descriptor: two local aliases of one canonical id are ONE read") {
    // The shape LibraryLinker produces after flattening: a root and a dependency each declare the same
    // URL under their own name, so two structurally DIFFERENT Retrieve nodes read identical rows.
    val library = libWithValueSets(
      Seq(diabetes, ValueSetDef("Helpers.Diabetes", diabetes.id, None)),
      edef("A", retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None)))),
      edef("B", retrieve("Condition", codes = Some(ValueSetRef("Helpers.Diabetes", None)))))

    val reads = RetrieveReads.candidates(library)
    assert(reads.sizeIs == 1)                        // ONE object, not two
    assert(reads.head.nodes.sizeIs == 2)             // both nodes substitute to it
    assert(reads.head.callSites == 2)
    assert(reads.head.defines == Seq("A", "B"))
  }

  test("different value sets stay different reads") {
    val other = ValueSetDef("Example Condition", "urn:oid:2.999.1.103", None)
    val library = libWithValueSets(
      Seq(diabetes, other),
      edef("A", retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None)))),
      edef("B", retrieve("Condition", codes = Some(ValueSetRef("Example Condition", None)))))
    assert(RetrieveReads.candidates(library).sizeIs == 2)
  }

  test("callSites counts OCCURRENCES: one define reading a read twice pays for it twice") {
    val library = libWithValueSets(Seq(diabetes),
      edef("A", Union(retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None))),
                      retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None))))))
    val read = RetrieveReads.candidates(library).head
    assert(read.callSites == 2)
    assert(read.defines == Seq("A")) // ...but it is owned by ONE define
  }

  test("mode selection: none -> nothing, all -> every filtered read, costly -> reuse-gated") {
    val library = libWithValueSets(Seq(diabetes),
      edef("Shared1", vs),
      edef("Shared2", vs),
      edef("Lonely", retrieve("Observation", dateRange = Some(int("1")))))

    def readsUnder(mode: Config.RetrieveMode, minReuse: Int = 2) =
      RetrieveReads.selected(library, Config.defaults.copy(
        materializeRetrieves = mode, materializeRetrieveMinReuse = minReuse))
        .map(_.coords.resourceType).toSet

    assert(readsUnder(Config.RetrieveMode.None).isEmpty)
    assert(readsUnder(Config.RetrieveMode.All) == Set("Condition", "Observation"))
    // costly gates on REUSE — the whole reason it is not a synonym for `all` (the candidate set is
    // already exactly the filtered reads, so "is filtered" cannot also be the discriminator).
    assert(readsUnder(Config.RetrieveMode.Costly) == Set("Condition"))
    assert(readsUnder(Config.RetrieveMode.Costly, minReuse = 3).isEmpty)
    assert(readsUnder(Config.RetrieveMode.Costly, minReuse = 1) == Set("Condition", "Observation"))
  }

  test("objName is stable, content-addressed, and disjoint from the retrieve_<Type> mount namespace") {
    val library = libWithValueSets(Seq(diabetes), edef("A", vs))
    val resolve = RetrieveCatalog.codeFilterOf(library)
    val name = RetrieveReads.objName(vs, resolve)
    assert(name == RetrieveReads.objName(vs, resolve))          // deterministic
    assert(name.startsWith("read_condition_"))                  // readable prefix
    assert(!name.startsWith("retrieve_"))                       // never collides with the mount
    // The VALUE SET the bracket binds is named in the object, ahead of the identifying hash.
    assert(name.matches("read_condition_diabetes_[0-9a-f]{8}"))
  }

  test("the slug names the value set, the profile when there is no code filter, and nothing otherwise") {
    val library = libWithValueSets(Seq(diabetes), edef("A", vs))
    val resolve = RetrieveCatalog.codeFilterOf(library)
    // A value set -> its local alias, lowercased and underscored (punctuation collapses to one `_`).
    val punctuated = ValueSetDef("Complex Condition, Stage Advanced", "urn:oid:1.2.3", None)
    val punctLib = libWithValueSets(Seq(punctuated),
      edef("A", retrieve("Condition", codes = Some(ValueSetRef(punctuated.name, None)))))
    assert(RetrieveReads.candidates(punctLib).head.obj
      .matches("read_condition_complex_condition_stage_advanced_[0-9a-f]{8}"))

    // LANG-RETRIEVE-PROFILE: no code filter -> the profile canonical's last segment.
    assert(RetrieveReads.filterSlug(
      retrieve("Observation", templateId = Some("http://hl7.org/fhir/StructureDefinition/us-core-bmi")),
      resolve) == "us_core_bmi")

    // A date-only read has nothing nameable: the name degrades to the bare `read_<type>_<hash8>`.
    assert(RetrieveReads.filterSlug(retrieve("Observation", dateRange = Some(int("1"))), resolve).isEmpty)
    assert(RetrieveReads.objName(retrieve("Observation", dateRange = Some(int("1"))), resolve)
      .matches("read_observation_[0-9a-f]{8}"))

    // A slug NEVER participates in identity: it is absent from the descriptor entirely.
    assert(!RetrieveReads.descriptor(vs, resolve).contains("diabetes"))
    assert(RetrieveReads.candidates(library).head.obj.contains("diabetes"))
  }

  test("a long value-set name is capped at a WORD boundary, never mid-word") {
    // A value-set name longer than the slug budget: the readable part is dropped back to the last complete word, and the hash still identifies.
    val long = ValueSetDef("Synthetic Long Value Set Name For Word Boundary Capping", "urn:oid:1.2.3", None)
    val library = libWithValueSets(Seq(long),
      edef("A", retrieve("Encounter", codes = Some(ValueSetRef(long.name, None)))))
    val obj = RetrieveReads.candidates(library).head.obj
    assert(obj.matches("read_encounter_synthetic_long_value_set_name_for_word_boundary_[0-9a-f]{8}"), obj)
  }

  test("one canonical id under two local aliases keeps ONE name, chosen deterministically") {
    // The linker's shape: the dependency's declaration is qualified, so the two aliases differ as WRITTEN
    // but name the same rows. The qualifier is dropped, so both slug alike and the object is one table.
    val library = libWithValueSets(
      Seq(diabetes, ValueSetDef("Helpers.Diabetes", diabetes.id, None)),
      edef("A", retrieve("Condition", codes = Some(ValueSetRef("Helpers.Diabetes", None)))),
      edef("B", retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None)))))
    val reads = RetrieveReads.candidates(library)
    assert(reads.sizeIs == 1)
    assert(reads.head.obj.matches("read_condition_diabetes_[0-9a-f]{8}"))
    // ...and reversing the declaration order does not move the published name.
    val reversed = libWithValueSets(
      Seq(diabetes, ValueSetDef("Helpers.Diabetes", diabetes.id, None)),
      edef("A", retrieve("Condition", codes = Some(ValueSetRef("Diabetes", None)))),
      edef("B", retrieve("Condition", codes = Some(ValueSetRef("Helpers.Diabetes", None)))))
    assert(RetrieveReads.candidates(reversed).head.obj == reads.head.obj)
  }

  test("coordinates carry the CANONICAL value-set id, not the local alias the author wrote") {
    val library = libWithValueSets(Seq(diabetes), edef("A", vs))
    val coords = RetrieveReads.candidates(library).head.coords
    assert(coords.resourceType == "Condition")
    assert(coords.codeProperty.contains("code"))
    assert(coords.codeFilter.contains(RetrieveCatalog.ValueSet(diabetes.id, "Diabetes")))
  }

  test("an unresolvable bracket is reported as Other, never as unfiltered (CORE-NO-SILENT-MISLOWER)") {
    val library = libWithValueSets(Seq.empty, // no declaration -> the ref cannot resolve
      edef("A", retrieve("Condition", codes = Some(ValueSetRef("Undeclared", None)))))
    val coords = RetrieveReads.candidates(library).head.coords
    assert(coords.codeFilter.exists(_.isInstanceOf[RetrieveCatalog.Other]))
  }
}
