package com.databricks.cql.it

import com.databricks.cql.env.Config
import com.databricks.cql.sema.FhirSchema
import org.apache.spark.sql.types.{ArrayType, DataType, StructType}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Schema-size regression guard (`DATA-MODEL-SCHEMA` sections 4.4/4.5, `compiler/docs/data-model.md`):
  * the bounded-nesting depth cap + the closed extension allow-list keep the model-derived raw schema
  * FINITE and small. A literal "project every element" expansion explodes into thousands of leaf
  * fields and OOM-crashes the Connect server; this asserts the leaf-field count of the resources the
  * corpus exercises stays well under a sane bound, so a future regression (an unbounded extension, a
  * lifted depth cap) fails here loudly instead of at runtime.
  *
  * Purely OFFLINE — no Spark session, just the static model. */
@RunWith(classOf[JUnitRunner])
final class SchemaSizeGuardSpec extends AnyFunSuite {

  /** Count the leaf (scalar) fields of a schema, descending through structs and arrays. */
  private def leafCount(dt: DataType): Int = dt match {
    case st: StructType   => st.fields.iterator.map(f => leafCount(f.dataType)).sum
    case ArrayType(el, _) => leafCount(el)
    case _                => 1
  }

  /** A sane upper bound. With the default depth cap (3) + bounded synthetic extension, the corpus
    * resources land in the low hundreds of leaves; the explosion this guards against is THOUSANDS. */
  private val Bound = 1500
  private val cap = Config.defaults.fhirMaxNestingLevel

  test("Config default nesting cap matches FhirSchema (read side == lowering side)") {
    // `Config.fhirMaxNestingLevel` is a bare literal (so `Config` stays Spark-free); guard against it
    // drifting from `FhirSchema.DefaultMaxNesting` — the read and lowering sides MUST use the same cap.
    assert(Config.defaults.fhirMaxNestingLevel == FhirSchema.DefaultMaxNesting)
  }

  for (rt <- Seq("Patient", "Observation", "Encounter")) {
    test(s"$rt raw schema stays bounded (depth cap $cap)") {
      val n = leafCount(FhirSchema.rawResourceSchema(rt, cap))
      info(s"$rt leaf fields: $n")
      assert(n > 5, s"$rt schema is implausibly small ($n leaves) — model regression?")
      assert(n < Bound, s"$rt schema exploded to $n leaf fields (bound $Bound) — schema-size regression")
    }
  }
}
