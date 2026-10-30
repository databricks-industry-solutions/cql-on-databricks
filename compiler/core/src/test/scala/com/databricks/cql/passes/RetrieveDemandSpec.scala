package com.databricks.cql.passes

import com.databricks.cql.TestFixtures._
import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** ARCH-RETRIEVE-PROJECT: demand-driven projection analysis over retrieves (PROC-TDD). Uses an
  * injected `typeOf` so the pass is exercised independently of full inference: a retrieve / alias of
  * resource type is typed `List<Named>` / `Named`, everything else scalar. Field names are real
  * modeled Observation fields so the `value[x]` expansion is exercised against the static model. */
@RunWith(classOf[JUnitRunner])
class RetrieveDemandSpec extends AnyFunSuite {

  private def obs: Retrieve = Retrieve("Observation", None, None, None, None, None)
  private def enc: Retrieve = Retrieve("Encounter", None, None, None, None, None)

  // Type a retrieve's element by its declared FHIR type; an alias bound to one inherits it through the
  // query walk, so we only need the retrieve/ref expressions typed here.
  private val typeOf: Expr => CqlType = {
    case Retrieve("Observation", _, _, _, _, _, _, _) => CqlType.ListType(CqlType.NamedType("Observation"))
    case Retrieve("Encounter", _, _, _, _, _, _, _)   => CqlType.ListType(CqlType.NamedType("Encounter"))
    case _                                            => CqlType.IntegerType
  }
  private def analyze(l: Library) = RetrieveDemand.analyze(l, typeOf)

  test("navigation collects the demanded top-level fields per type") {
    val m = analyze(lib(
      edef("Statuses", Query(Seq(QuerySource("E", enc)), None, Some(Property(None, Some("E"), "status")))),
      edef("Periods",  Query(Seq(QuerySource("E", enc)), None, Some(Property(Some(AliasRef("E")), None, "period"))))))
    assert(m("Encounter") == RetrieveDemand.Narrow(Set("status", "period")))
  }

  test("a value[x] navigation expands to all typed slots of that base") {
    // Observation.value → valueQuantity, valueCodeableConcept, valueString, … (the per-row slot).
    val m = analyze(lib(
      edef("Vals", Query(Seq(QuerySource("O", obs)), None, Some(Property(None, Some("O"), "value"))))))
    val ncounter = m("Observation").asInstanceOf[RetrieveDemand.Narrow].fields
    assert(ncounter.contains("valueQuantity") && ncounter.contains("valueString"),
      s"value[x] must expand to typed slots; got $ncounter")
    assert(!ncounter.contains("value"), "the bare base is not a physical field")
  }

  test("a nested path demands only the top-level field") {
    val m = analyze(lib(
      edef("Codes", Query(Seq(QuerySource("O", obs)), None, Some(Property(Some(Property(None, Some("O"), "code")), None, "text"))))))
    assert(m("Observation") == RetrieveDemand.Narrow(Set("code")))
  }

  test("a whole-element escape widens the type to Full") {
    val m = analyze(lib(
      edef("Whole", Query(Seq(QuerySource("E", enc)), None, Some(AliasRef("E"))))))
    assert(m("Encounter") == RetrieveDemand.Full)
  }

  test("scan-filter code/date properties are demanded even with no explicit navigation") {
    val filtered = Retrieve("Observation", None, Some("code"), Some(ref("VS")), Some("effective"), None)
    val m = analyze(lib(edef("Filtered", filtered)))
    val f = m("Observation").asInstanceOf[RetrieveDemand.Narrow].fields
    assert(f.contains("code"), s"codeProperty must be demanded; got $f")
    // `effective` is a value[x] base → its slots are demanded.
    assert(f.exists(_.startsWith("effective")), s"dateProperty (value[x]) slots must be demanded; got $f")
  }

  test("a heterogeneous union / Choice-construction cast marks its arm types Full (Choice path owns width)") {
    assert(analyze(lib(edef("U", Union(obs, enc)))).get("Observation").contains(RetrieveDemand.Full))
    assert(analyze(lib(edef("A", As(obs, "X", strict = false, choiceTypes = Seq("Observation", "MedicationRequest")))))
      .get("Observation").contains(RetrieveDemand.Full))
  }

  test("no retrieve → empty demand map") {
    assert(analyze(lib(edef("A", int("1")))).isEmpty)
  }
}
