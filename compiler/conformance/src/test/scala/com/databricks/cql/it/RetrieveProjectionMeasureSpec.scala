package com.databricks.cql.it

import com.databricks.cql.env.Config
import com.databricks.cql.passes.{Optimize, RetrieveDemand, TypeEncoding}
import com.databricks.cql.retrieve.ResourceStruct
import com.databricks.cql.sema.FhirSchema
import com.databricks.cql.spark.SystemColumns
import org.apache.spark.sql.types.{ArrayType, DataType, StructType}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** ARCH-RETRIEVE-PROJECT measurement (server-free): for each fixture matching `-Dcqldatabricks.only`, run
  * `RetrieveDemand` over the normalized/optimized library and REPORT, per resource type, how much of
  * the `__resource` body a demand-driven projection would keep — top-level fields kept/total and, more
  * tellingly, STRUCT LEAVES kept/total (the analysis-time proto-width proxy). Quantifies the ceiling on
  * the projection-pushdown win BEFORE the invasive struct-build seam change. Prints only; asserts
  * nothing. Run: `-Dcqldatabricks.only=<measure library>` (no server needed). */
@RunWith(classOf[JUnitRunner])
final class RetrieveProjectionMeasureSpec extends AnyFunSuite {

  private def leaves(dt: DataType): Int = dt match {
    case st: StructType   => st.fields.iterator.map(f => leaves(f.dataType)).sum
    case ArrayType(el, _) => leaves(el)
    case _                => 1
  }

  Corpus.all.foreach { e =>
    test(s"retrieve-projection demand — ${e.label}") {
      val c = e.mk()
      Differential.onlyFilter(c.library)
      val lib = Differential.buildLibrary(c)
      val optimized = Optimize.run(lib, Config.defaults)
      val typeOf: com.databricks.cql.ir.Expr => com.databricks.cql.ir.CqlType =
        ex => TypeEncoding.typeOf(ex, TypeEncoding.ctxOf(optimized))
      val demand = RetrieveDemand.analyze(optimized, typeOf)

      val sb = new StringBuilder
      sb ++= s"\n[retrieve-projection] ${c.library}\n"
      sb ++= f"  ${"type"}%-26s ${"topKept/total"}%14s ${"leavesKept/total"}%18s  note\n"
      var totFull = 0; var totKept = 0
      demand.toSeq.sortBy(_._1).foreach { case (rt, d) =>
        if (FhirSchema.isModeled(rt)) {
          val full = ResourceStruct.structType(rt)
          val dataFields = full.fields.filterNot(f => SystemColumns.isReserved(f.name))
          val fullLeaves = leaves(StructType(dataFields))
          val (keptFields, keptLeaves, note) = d match {
            case RetrieveDemand.Full => (dataFields.length, fullLeaves, "FULL (escaped)")
            case RetrieveDemand.Narrow(fs) =>
              val kept = dataFields.filter(f => fs.contains(f.name))
              (kept.length, leaves(StructType(kept)), fs.toSeq.sorted.mkString(","))
          }
          totFull += fullLeaves; totKept += keptLeaves
          sb ++= f"  $rt%-26s ${s"$keptFields/${dataFields.length}"}%14s ${s"$keptLeaves/$fullLeaves"}%18s  $note\n"
        }
      }
      val pct = if (totFull > 0) 100.0 * totKept / totFull else 0.0
      sb ++= f"  TOTAL leaves kept: $totKept/$totFull (${pct}%.1f%%)\n"
      info(sb.toString)
      println(sb.toString)
    }
  }
}
