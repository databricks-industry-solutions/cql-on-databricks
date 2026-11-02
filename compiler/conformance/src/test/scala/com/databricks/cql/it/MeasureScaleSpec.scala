package com.databricks.cql.it

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** OPT-IN, PARAMETERIZED throughput scale harness (CONF-PERF-TIME's directional at-scale companion): run
  * ANY corpus measure through the SQL back end at increasing patient counts and time the wall. This is
  * ADD-ONCE — a measure is scale-testable the moment it lands in [[Corpus.scaleMeasures]], with no new code
  * (folds in the former single-measure scale specs).
  *
  * WHY THE SQL PATH: the SQL emitter is the primary EXECUTION substrate (`compiler/docs/execution-model.md`
  * and 11.1); it is the path that runs the full corpus and scales. So the scale harness lowers via
  * [[SqlDifferential.runThroughput]] (SQL script -> mount -> execute -> force-materialize via `count(*)`,
  * NO Java CQF reference and NO per-subject diff — correctness is vetted on the base deck by
  * [[SqlDifferentialSpec]]; this measures wall time). Data is replicated `factor`x with FRESH patient IDs
  * ([[ScaleData]]: same resources, new `-r<k>` ids) so per-subject work actually multiplies.
  *
  * OPT-IN, SKIPS GREEN unless `-Dcqldatabricks.scale=true` (same convention as [[ReferenceCrossoverScaleSpec]];
  * `docs/deploy.md`). Deliberately not part of the hermetic conformance core:
  * authoritative scale numbers belong to the separate Databricks cluster project — this is the LOCAL,
  * OSS-Connect investigation tool (a large factor on a laptop may be slow or OOM the local server — that
  * is the experiment). Config:
  *   - `-Dcqldatabricks.scaleMeasure=<library>` — a measure library name ([[Corpus.scaleMeasures]] key);
  *     `ALL` sweeps every registered measure. Default `ALL`.
  *   - `-Dcqldatabricks.scaleFactors=1000` — the replication multiplier(s), comma-swept (e.g. `1,10,100,1000`).
  *     Default `1000`.
  * Each `(measure, factor)` line is `info`-logged AND appended to `build/measure-scale.txt`. */
@RunWith(classOf[JUnitRunner])
final class MeasureScaleSpec extends AnyFunSuite {

  private val enabled = sys.props.get("cqldatabricks.scale").contains("true")

  /** The measure short-names to run: an explicit name, or `ALL` -> every registered measure. Default ALL. */
  private def selectedMeasures: Seq[String] = {
    val sel = sys.props.getOrElse("cqldatabricks.scaleMeasure", "ALL").trim
    if (sel.equalsIgnoreCase("ALL")) Corpus.scaleMeasures.keys.toSeq.sorted else Seq(sel)
  }

  private val factors: Seq[Int] =
    sys.props.get("cqldatabricks.scaleFactors")
      .map(_.split(",").toSeq.flatMap(s => scala.util.Try(s.trim.toInt).toOption))
      .filter(_.nonEmpty).getOrElse(Seq(1000)).distinct.sorted

  test("measure throughput scale via the SQL back end (opt-in, -Dcqldatabricks.scale=true)") {
    assume(enabled, "opt-in scale harness — set -Dcqldatabricks.scale=true (skips by default; see docs/deploy.md)")
    val spark = SparkConnect.session()
    val out   = new File("build", "measure-scale.txt")
    Option(out.getParentFile).foreach(_.mkdirs())

    selectedMeasures.foreach { name =>
      val d = Corpus.scaleMeasures.getOrElse(name,
        fail(s"unknown measure '$name' — known: ${Corpus.scaleMeasures.keys.toSeq.sorted.mkString(", ")}, or ALL"))
      factors.foreach { f =>
        // Replicate the base deck f× with fresh ids, then rebind the case's data/terminology to the
        // scaled temp dir (the measure's own build thunk supplies the linked, pruned library + config).
        val scaled = ScaleData.replicate(d.dataDir, d.resourceTypes, f)
        val c = d.entry.mk().copy(data = Some(new SparkFhirData(spark, scaled.dir.getAbsolutePath)))
        val (patients, lowerMs, runtimeMs) = SqlDifferential.runThroughput(spark, c)
        val total = lowerMs + runtimeMs
        val line = f"[$name x$f] patients=$patients  lower=$lowerMs%.0f ms  runtime=$runtimeMs%.0f ms  " +
          f"total=$total%.0f ms  (~${total / math.max(patients, 1)}%.2f ms/patient)"
        info(line)
        val w = new java.io.FileWriter(out, true)
        try w.write(line + "\n") finally w.close()
      }
    }
  }
}
