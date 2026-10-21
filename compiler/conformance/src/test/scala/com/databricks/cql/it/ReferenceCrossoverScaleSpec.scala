package com.databricks.cql.it

import com.databricks.cql.harness.GroundTruthRunner

import java.io.{File, FileWriter}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** OPT-IN throughput scale harness: a corpus measure through OUR Spark engine vs the Java CQF reference, at
  * increasing patient counts, to answer "does the set-at-a-time Spark engine overtake the row-at-a-time
  * reference as data grows?" — and to separate our engine's FIXED overhead from its data-proportional
  * cost (the "runtime line minus overhead" the user asked for).
  *
  * WHY OPT-IN, SKIPS BY DEFAULT (`docs/deploy.md`): scale/throughput is
  * DELIBERATELY not part of the hermetic conformance core — the authoritative numbers belong to the
  * separate Databricks scale project on real clusters + large decks. This is the LOCAL, OSS-Connect
  * investigation tool: it replicates the measure's hand-authored test patients (`ScaleData`) rather than
  * using representative data, so treat the absolute ms as directional, not a benchmark. Enable with
  * `-Dcqldatabricks.scale=true`; it `cancel`s (green) otherwise, so it never runs in the normal suite or CI.
  *
  * THE MODEL. On the tiny conformance data our engine is OVERHEAD-BOUND — a fixed floor of plan
  * analysis, `localCheckpoint` staging round-trips, and the 200-partition shuffle that does not shrink
  * with the data (`fhir-retrieve-runtime-baseline`). The reference is row-at-a-time: ~no fixed floor, but
  * it re-runs the whole CqlEngine PER subject, so its cost is ~linear in patients. Fitting each engine's
  * measured wall to `wall = intercept + slope·patients` recovers exactly the split asked for:
  *   - `intercept` = the fixed OVERHEAD (our Spark floor; the reference's is ~0),
  *   - `slope`     = the marginal ms/patient — our engine's "runtime line MINUS overhead",
  * and the crossover `patients* = (interceptSpark − interceptRef) / (slopeRef − slopeSpark)` is the deck
  * size beyond which Spark's flat overhead has amortized and it wins. Correctness is re-verified against
  * the reference at every scale (this is real, completed work, not just a timer).
  *
  * Config: `-Dcqldatabricks.scaleMeasure=<library>` (a [[Corpus.scaleMeasures]] key, default
  * [[Corpus.defaultScaleMeasure]]) and `-Dcqldatabricks.scaleFactors=1,2,4,8` (× the measure's test patients). The reference re-evaluates every
  * subject, so large factors get slow — that is the point; keep factors modest on a laptop. Uses the
  * SAME evaluation config as the differential gate ([[Differential.evalConfig]]) so numbers reflect the
  * shipped defaults (all perf enhancements on; every define a barrier for auditability). */
@RunWith(classOf[JUnitRunner])
final class ReferenceCrossoverScaleSpec extends AnyFunSuite {

  private val enabled = sys.props.get("cqldatabricks.scale").contains("true")
  private val factors: Seq[Int] =
    sys.props.get("cqldatabricks.scaleFactors").map(_.split(",").toSeq.flatMap(s => scala.util.Try(s.trim.toInt).toOption))
      .filter(_.nonEmpty).getOrElse(Seq(1, 2, 4, 8)).distinct.sorted
  private val measureName = sys.props.getOrElse("cqldatabricks.scaleMeasure", Corpus.defaultScaleMeasure).trim
  private lazy val md: Corpus.MeasureDef = Corpus.scaleMeasures.getOrElse(measureName,
    fail(s"unknown measure '$measureName' — known: ${Corpus.scaleMeasures.keys.toSeq.sorted.mkString(", ")}"))

  test("measure scale: Spark engine vs Java CQF reference (opt-in, -Dcqldatabricks.scale=true)") {
    assume(enabled, "opt-in scale harness — set -Dcqldatabricks.scale=true (skips by default; see docs/deploy.md)")
    val spark = SparkConnect.session()

    // The CONF-PERF-TIME budgets in engineResults would (rightly) HARD-FAIL at scale — a scale run is
    // deliberately over the corpus-volume budget. Lift them for this JVM, restore after.
    val saved = Seq("cqldatabricks.maxRuntimeMs", "cqldatabricks.maxClientMs").map(k => k -> sys.props.get(k))
    Seq("cqldatabricks.maxRuntimeMs", "cqldatabricks.maxClientMs").foreach(System.setProperty(_, "3600000"))
    try measure(spark) finally saved.foreach { case (k, v) => v match {
      case Some(x) => System.setProperty(k, x); case None => System.clearProperty(k) } }
  }

  /** One (spark_ms, ref_ms) measurement at `factor`, correctness re-verified against the reference. */
  private def runScale(spark: org.apache.spark.sql.SparkSession, factor: Int): (Int, Double, Double) = {
    val scaled  = ScaleData.replicate(md.dataDir, md.resourceTypes, factor)
    val base    = md.entry.mk()
    val c = base.copy(
      data = Some(new SparkFhirData(spark, scaled.dir.getAbsolutePath)),
      refEnv = base.refEnv.copy(
        contextName = base.context.name,
        resourcesByType = scaled.resourcesByType,
        subjectIds = scaled.subjectIds))

    val cqlText = Paths.read(Paths.cql(c.library))
    val lib     = Differential.buildLibrary(c)

    // SPARK: the whole evaluate (lower + mount + execute + collect) via the SQL back end. Fixed lowering
    // folds into the fitted intercept, which is precisely the "overhead" we subtract — so timing the
    // whole call is correct.
    val sparkT0 = System.nanoTime()
    val ours     = SqlDifferential.engineResults(spark, c)
    val allNames = ours.headOption.map(_._2.keys.toSeq).getOrElse(Nil)
    val sparkMs = (System.nanoTime() - sparkT0) / 1e6

    val names = c.link.map(_.roots).filter(_.nonEmpty).map(roots => allNames.filter(roots)).getOrElse(allNames)
    val refEnv = c.refEnv.copy(subjectIds = ours.keys.toSeq.sorted)

    // REFERENCE: the Java CQF engine re-evaluating every subject (its cost is ~linear in patients).
    val refT0 = System.nanoTime()
    val reference = GroundTruthRunner.evaluate(cqlText, lib.id, names, refEnv)
    val refMs = (System.nanoTime() - refT0) / 1e6

    // Correctness at scale: both engines produced the SAME per-(subject, population) result.
    ours.keys.foreach(k => names.foreach(n =>
      assert(ours(k)(n) == reference(k)(n),
        s"[${md.library} x$factor] subject '$k' define '$n': engine=${ours(k)(n)} reference=${reference(k)(n)}")))
    (scaled.patients, sparkMs, refMs)
  }

  private def measure(spark: org.apache.spark.sql.SparkSession): Unit = {
    // Warm-up (discarded): JIT / Connect plan-cache / Spark shuffle warm-up on both sides. Its patient
    // count at the smallest factor also gives the base deck size (patients-per-factor) for the report.
    val (warmPatients, _, _) = runScale(spark, factors.min)
    val baseCount = warmPatients / factors.min
    info(s"${md.library} scale sweep at factors ${factors.mkString(",")} (× $baseCount patients) — replicated deck, OSS Connect")

    val rows = factors.map { f =>
      val (patients, sparkMs, refMs) = runScale(spark, f)
      info(f"  x$f%-3d $patients%5d patients   spark ${sparkMs / 1000}%7.2f s   reference ${refMs / 1000}%7.2f s")
      (patients.toDouble, sparkMs, refMs)
    }
    report(rows, baseCount)
  }

  /** Ordinary-least-squares fit of `y = intercept + slope·x`, plus R². */
  private def fit(pts: Seq[(Double, Double)]): (Double, Double, Double) = {
    val n = pts.size.toDouble
    val mx = pts.map(_._1).sum / n
    val my = pts.map(_._2).sum / n
    val sxx = pts.map { case (x, _) => (x - mx) * (x - mx) }.sum
    val sxy = pts.map { case (x, y) => (x - mx) * (y - my) }.sum
    val slope = if (sxx == 0) 0.0 else sxy / sxx
    val intercept = my - slope * mx
    val ssTot = pts.map { case (_, y) => (y - my) * (y - my) }.sum
    val ssRes = pts.map { case (x, y) => val p = intercept + slope * x; (y - p) * (y - p) }.sum
    val r2 = if (ssTot == 0) 1.0 else 1.0 - ssRes / ssTot
    (intercept, slope, r2)
  }

  private def report(rows: Seq[(Double, Double, Double)], baseCount: Int): Unit = {
    val sb = new StringBuilder
    sb ++= s"# ${md.library} throughput: OUR Spark engine vs the Java CQF reference, at scale (OPT-IN, -Dcqldatabricks.scale=true).\n"
    sb ++= "# Replicated test patients (ScaleData) on OSS Spark Connect — directional, NOT a cluster benchmark\n"
    sb ++= "# (authoritative scale numbers live in the separate Databricks scale project; see docs/deploy.md).\n"
    sb ++= f"#\n${"patients"}%9s ${"spark_ms"}%12s ${"reference_ms"}%14s ${"spark_per_pt"}%13s ${"ref_per_pt"}%12s\n"
    rows.foreach { case (p, s, r) => sb ++= f"$p%9.0f $s%12.1f $r%14.1f ${s / p}%13.2f ${r / p}%12.2f\n" }

    val (sInt, sSlope, sR2) = fit(rows.map { case (p, s, _) => (p, s) })
    val (rInt, rSlope, rR2) = fit(rows.map { case (p, _, r) => (p, r) })
    sb ++= "#\n# Linear fit  wall_ms = intercept(overhead) + slope·patients :\n"
    sb ++= f"#   spark:     overhead ${sInt / 1000}%8.2f s   marginal ${sSlope}%8.2f ms/patient   R2 ${sR2}%.3f\n"
    sb ++= f"#   reference: overhead ${rInt / 1000}%8.2f s   marginal ${rSlope}%8.2f ms/patient   R2 ${rR2}%.3f\n"
    sb ++= f"#\n# 'runtime line minus overhead' (data-proportional): spark ${sSlope}%.2f ms/patient vs reference ${rSlope}%.2f ms/patient"
    sb ++= ((sSlope, rSlope) match {
      case (s, r) if s > 0 && r > 0 && s < r => f" -> Spark is ${r / s}%.1fx cheaper per patient at the margin.\n"
      case (s, r) if s > 0 && r > 0          => f" -> the reference is ${s / r}%.1fx cheaper per patient at the margin (Spark is still overhead+throughput bound on this tiny replicated deck).\n"
      case _                                  => ".\n"
    })

    val crossover = if (rSlope - sSlope != 0) Some((sInt - rInt) / (rSlope - sSlope)) else None
    (crossover, sSlope < rSlope) match {
      case (Some(x), true) if x > 0 =>
        sb ++= f"# Crossover: Spark's total wall drops below the reference beyond ~${x}%.0f patients (~${x / baseCount}%.1f× the base deck);\n"
        sb ++= f"#   below that, the reference's near-zero overhead wins on tiny data. Overhead amortized => Spark wins at scale.\n"
      case (_, true) =>
        sb ++= "# Spark already has both lower overhead AND a shallower marginal slope: it wins at every measured scale.\n"
      case _ =>
        sb ++= "# Spark's marginal slope is NOT below the reference's at these scales — no crossover; widen the range or inspect the plan.\n"
    }

    val out = new File("build", s"${md.library}-scale.txt")
    Option(out.getParentFile).foreach(_.mkdirs())
    val w = new FileWriter(out, false); try w.write(sb.toString) finally w.close()
    info(s"scale report written to ${out.getAbsolutePath}")
    sb.toString.linesIterator.foreach(info(_))
  }
}
