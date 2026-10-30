package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.meta.{LibraryDescribe, LibraryGraph}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The compiler's neutral, pipeline-agnostic METADATA capability (PHASE-10): `describe` returns a
  * [[LibraryGraph]] — the define nodes, the dependency edges, the cost-chosen boundary set, and the
  * pinned versions — that an EXTERNAL pipeline reads to build its own graph. This repo describes; it does
  * not generate a pipeline. SPARK-FREE: `describe` runs the pure SQL lowering, so it needs no session and
  * no server — this spec is always-on. */
@RunWith(classOf[JUnitRunner])
final class LibraryDescribeSpec extends AnyFunSuite {

  private def describe(fixture: String, context: Context, overrides: Map[String, String],
                       hasTerminology: Boolean = false): LibraryGraph =
    LibraryDescribe.describe(Paths.read(Paths.elm(fixture)), context = context, overrides = overrides,
      hasTerminology = hasTerminology)

  test("Arithmetic: describe yields the define nodes + a non-empty boundary set (materialize=all)") {
    val g = describe("Arithmetic", Context.Unfiltered, Map("materialize.mode" -> "all"))
    assert(Set("Sum", "Product", "DivideByZero").subsetOf(g.defines.toSet), s"nodes: ${g.defines}")
    assert(g.boundaries.nonEmpty, "materialize=all recommends a non-empty boundary set")
    assert(g.materializedDefines.nonEmpty, "some defines are recommended for persistence")
    assert(g.materializedDefines.subsetOf(g.defines.toSet), "materialized defines are graph nodes")
    assert(g.compilerVersion.nonEmpty, "the compiler version is pinned (BUILD-VERSION-PIN)")
  }

  test("TrivialBoolean: describe captures the ExpressionRef dependency edges") {
    // A scalar define DAG (define -> ExpressionRef -> define), so at least one node depends on another.
    val g = describe("TrivialBoolean", Context.Unfiltered, Map.empty) // realistic (costly) mode
    assert(g.defines.nonEmpty, "the library has define nodes")
    assert(g.dependencies.values.exists(_.nonEmpty), s"at least one dependency edge: ${g.dependencies}")
    // Every edge points at a real node (no dangling references).
    assert(g.dependencies.values.flatten.forall(g.defines.toSet.contains), "edges reference known nodes")
  }

  /** EMIT-SQL-VALUE-COLUMN: `describe` must be SELF-SUFFICIENT for reading results — a consumer that builds
    * its own dataset graph learns the payload column and each define's ROW shape here, without a second
    * compile call and without probing a produced schema (`ARCH-NO-RUNTIME-SCHEMA`). */
  test("describe publishes the uniform value column + a row kind per define") {
    val g = describe("TrivialBoolean", Context.Unfiltered, Map.empty)
    assert(g.valueColumn == "__val", "every define carries its payload in the same column")
    assert(g.defineKinds.keySet == g.defines.toSet, "one kind per described define")
    assert(g.defineKinds.values.forall(Set("scalar", "relation").contains), s"kinds: ${g.defineKinds}")
    // A boolean-scalar library describes every define as scalar (one row per subject).
    assert(g.defineKinds.values.forall(_ == "scalar"), s"kinds: ${g.defineKinds}")
  }

  test("a list define describes as a grain-first relation, under the SAME value column as a scalar") {
    // The fixture uses a value set, so describe it as the runtime would — with terminology mounted.
    val rel = describe("FhirNumerator", Context.Patient, Map.empty, hasTerminology = true)
    assert(rel.defineKinds("DiabetesInMP") == "relation", s"kinds: ${rel.defineKinds}")

    // The contract that matters: a relation-shaped library and a scalar-shaped one publish their payload
    // under the SAME column, so a reader is kind-agnostic and only the ROW shape differs.
    val sca = describe("TrivialBoolean", Context.Unfiltered, Map.empty)
    assert(sca.defineKinds.values.forall(_ == "scalar"), s"kinds: ${sca.defineKinds}")
    assert(rel.valueColumn == sca.valueColumn && rel.valueColumn == "__val")
  }

  /** EMIT-DESCRIBE-RETRIEVE-TYPES: describe names every type a library retrieves, so a caller binds exactly
    * the data a run needs (CLIENT-EXEC-DISCOVERY). A bare `exists [Condition]` navigates no field, so the
    * demand map leaves it out; the type list must not. */
  test("describe lists every retrieved type, including a retrieve that navigates no field") {
    // Patient context is itself a `[Patient]` retrieve (HL7 CQL 1.5 Author's Guide, "Context": the context
    // defines `Patient` as `SingletonFrom([Patient])`), so a
    // Patient-context run needs `retrieve_Patient` even where no define names it.
    val bare = describe("FhirRetrieve", Context.Patient, Map.empty)
    assert(bare.retrieveTypes == Seq("Condition", "Patient"), s"types: ${bare.retrieveTypes}")
    assert(!bare.retrieveDemand.contains("Condition"), s"demand: ${bare.retrieveDemand}")

    val filtered = describe("FhirNumerator", Context.Patient, Map.empty, hasTerminology = true)
    assert(filtered.retrieveTypes.contains("Condition"), s"types: ${filtered.retrieveTypes}")
    assert(filtered.retrieveTypes == filtered.retrieveTypes.distinct.sorted, "sorted and distinct")

    assert(describe("TrivialBoolean", Context.Unfiltered, Map.empty).retrieveTypes.isEmpty,
      "a data-free library retrieves nothing")
  }
}
