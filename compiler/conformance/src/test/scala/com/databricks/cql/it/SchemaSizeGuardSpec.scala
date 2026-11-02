package com.databricks.cql.it

import com.databricks.cql.sema.{FhirModel, FhirSchema}
import org.apache.spark.sql.types.{ArrayType, DataType, StructType}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Schema-size regression guard (`DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`, `compiler/docs/data-model.md`): the
  * loop cuts and the bounded extension keep every modeled resource's raw schema finite and small. A full
  * `Extension.value[x]`, or extensions on every element, would expand into thousands of leaf fields and
  * exhaust the Connect server; this fails here, loudly, before that reaches a run.
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

  /** A sane upper bound. The widest modeled resource has several hundred leaves; the expansion this
    * guards against is thousands. */
  private val Bound = 1500

  for (rt <- FhirModel.resourceTypes.toSeq.sorted) {
    test(s"$rt raw schema stays bounded") {
      val n = leafCount(FhirSchema.rawResourceSchema(rt))
      info(s"$rt leaf fields: $n")
      assert(n > 5, s"$rt schema is implausibly small ($n leaves) — model regression?")
      assert(n < Bound, s"$rt schema exploded to $n leaf fields (bound $Bound) — schema-size regression")
    }
  }
}
