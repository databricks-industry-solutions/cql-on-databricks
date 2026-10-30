package com.databricks.cql.it

import com.databricks.cql.harness.GroundTruthRunner

import java.io.{File, FileWriter}
import org.apache.spark.sql.SparkSession
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** OPT-IN per-FIXTURE throughput harness: each data-backed FHIR fixture (the self-contained `fhir(...)`
  * corpus entries — NOT the multi-library measures, which [[ReferenceCrossoverScaleSpec]] covers) run through OUR
  * Spark engine and the Java CQF reference at a MUCH larger data size, to see — fixture by fixture — where
  * the set-at-a-time Spark engine wins and where the row-at-a-time reference wins.
  *
  * Same opt-in / hermeticity contract as [[ReferenceCrossoverScaleSpec]] (see that scaladoc + doc/databricks-connect-
  * setup.md section 4): skips green unless `-Dcqldatabricks.scale=true`; a directional laptop-OSS-Connect read
  * on a REPLICATED deck ([[ScaleData]] blows the 8-patient `data/fhir` deck up `factor`×), not a cluster
  * benchmark. Correctness is re-verified against the reference at every scale.
  *
  * WHY PER FIXTURE. The measures are dominated by one huge plan; the fixtures isolate ONE language feature
  * each (a bare retrieve, a value-set filter, a temporal `where`, a reference-follow join, a Choice union,
  * …), so their win/loss split shows WHICH shapes Spark's fixed overhead pays off on and which the
  * reference's near-zero-overhead row loop still wins at this scale. Config: `-Dcqldatabricks.scaleFactors=1,100`
  * (× the 8-patient base — factor 1 anchors the low end, the large factor is the "much larger" point);
  * `-Dcqldatabricks.only=<substr>` narrows to matching fixtures. Uses [[Differential.evalConfig]] so each lowers
  * byte-identically to the differential gate. */
@RunWith(classOf[JUnitRunner])
final class FixtureScaleSpec extends AnyFunSuite {

  private val enabled = sys.props.get("cqldatabricks.scale").contains("true")
  private val factors: Seq[Int] =
    sys.props.get("cqldatabricks.scaleFactors").map(_.split(",").toSeq.flatMap(s => scala.util.Try(s.trim.toInt).toOption))
      .filter(_.nonEmpty).getOrElse(Seq(1, 100)).distinct.sorted
  private val only: Option[String] =
    sys.props.get("cqldatabricks.only").map(_.trim.stripSuffix(".cql").stripSuffix(".CQL")).filter(_.nonEmpty)
  // Throughput-only (count(*), NO CQF, NO per-subject collect) so a fixture can scale to 1M without the
  // driver collect / CQF OOM. ref_ms is reported as 0. Opt-in via -Dcqldatabricks.throughput=true.
  private val throughput: Boolean = sys.props.get("cqldatabricks.throughput").contains("true")

  private case class Row(fixture: String, factor: Int, patients: Int, sparkMs: Double, refMs: Double)

  test("fixtures at scale: Spark engine vs Java CQF reference (opt-in, -Dcqldatabricks.scale=true)") {
    assume(enabled, "opt-in scale harness — set -Dcqldatabricks.scale=true (skips by default; see docs/deploy.md)")
    val spark = SparkConnect.session()
    val saved = Seq("cqldatabricks.maxRuntimeMs", "cqldatabricks.maxClientMs").map(k => k -> sys.props.get(k))
    Seq("cqldatabricks.maxRuntimeMs", "cqldatabricks.maxClientMs").foreach(System.setProperty(_, "3600000"))
    try measure(spark) finally saved.foreach { case (k, v) => v match {
      case Some(x) => System.setProperty(k, x); case None => System.clearProperty(k) } }
  }

  /** The self-contained, data-backed FHIR fixtures (has data, is not a linked measure), honoring
    * `-Dcqldatabricks.only`. Building the case is what injects the sample-data providers; we swap in scaled data. */
  private def fixtures(): Seq[Differential.Case] =
    Corpus.all.iterator.map(_.mk())
      .filter(c => c.data.isDefined && c.link.isEmpty)
      .filter(c => only.forall(k => c.library.toLowerCase.contains(k.toLowerCase)))
      .toSeq

  /** One (spark_ms, ref_ms) measurement of `base` at `factor`, correctness re-verified against the reference. */
  private def runScale(spark: SparkSession, base: Differential.Case, factor: Int): Row = {
    val rtypes = base.refEnv.resourcesByType.keys.toSeq
    val scaled = ScaleData.replicate(Paths.dataDir, rtypes, factor)
    val c = base.copy(
      data = Some(new SparkFhirData(spark, scaled.dir.getAbsolutePath)),
      terminology = base.terminology.map(_ => new SparkTerminology(spark, scaled.dir.getAbsolutePath)),
      refEnv = base.refEnv.copy(
        contextName = base.context.name,
        resourcesByType = scaled.resourcesByType,
        subjectIds = scaled.subjectIds))

    // Throughput-only (experiment): force-materialize every view via count(*), no CQF, no driver collect —
    // the only path that survives 1M patients. Returns spark runtime as sparkMs; ref_ms=0.
    if (throughput) {
      val (patients, lowerMs, runtimeMs) = SqlDifferential.runThroughput(spark, c)
      return Row(c.library, factor, patients.toInt, lowerMs + runtimeMs, 0.0)
    }

    val cqlText = Paths.read(Paths.cql(c.library))
    val lib     = Differential.buildLibrary(c)

    val sparkT0 = System.nanoTime()
    val ours     = SqlDifferential.engineResults(spark, c)
    val allNames = ours.headOption.map(_._2.keys.toSeq).getOrElse(Nil)
    val sparkMs = (System.nanoTime() - sparkT0) / 1e6

    // Self-contained fixture: diff (and the reference evaluates) EVERY define, not a pruned population slice.
    val names  = allNames
    val refEnv = c.refEnv.copy(subjectIds = ours.keys.toSeq.sorted)
    val refT0 = System.nanoTime()
    val reference = GroundTruthRunner.evaluate(cqlText, lib.id, names, refEnv)
    val refMs = (System.nanoTime() - refT0) / 1e6

    // When the dump gate is on (`-Dcqldatabricks.dumpDir=<dir>`), `engineResults` already recorded THIS
    // fixture's Spark buckets (client_cpu/client_wait/runtime/total_wall) into `test-runtimes.txt` at the
    // SCALED data size; merge the reference wall into the same row so that file carries the side-by-side
    // Spark-vs-reference comparison at scale (same seam as `Differential.run`).
    if (sys.props.get("cqldatabricks.dumpDir").exists(_.nonEmpty)) Timing.recordReference(c.library, refMs)

    ours.keys.foreach(k => names.foreach(n =>
      assert(ours(k)(n) == reference(k)(n),
        s"[${c.library} x$factor] subject '$k' define '$n': engine=${ours(k)(n)} reference=${reference(k)(n)}")))
    Row(c.library, factor, scaled.patients, sparkMs, refMs)
  }

  private def measure(spark: SparkSession): Unit = {
    val fx = fixtures()
    assume(fx.nonEmpty, s"no data-backed FHIR fixtures matched${only.fold("")(k => s" '$k'")}")
    info(s"${fx.size} fixture(s) at factors ${factors.mkString(",")} (× the 8-patient data/fhir deck) — OSS Connect")
    if (!throughput) runScale(spark, fx.head, factors.min) // discarded warm-up (skip at 1M throughput — too costly to run twice)

    // Rewrite the report after EACH fixture (rows keyed by fixture+factor), so a slow/heavy fixture that
    // hangs the sweep still leaves every completed fixture's numbers on disk.
    val rows = scala.collection.mutable.ArrayBuffer.empty[Row]
    for (c <- fx; f <- factors) {
      val r = runScale(spark, c, f)
      info(f"  ${r.fixture}%-34s x$f%-4d ${r.patients}%5d pts   spark ${r.sparkMs}%8.0f ms   ref ${r.refMs}%8.0f ms   ${winner(r)}%s")
      rows += r
      report(rows.toSeq)
    }
    info(s"fixture scale sweep complete: ${fx.size} fixture(s).")
  }

  private def winner(r: Row): String = if (r.sparkMs < r.refMs) "SPARK" else "REF"

  /** OLS fit of `y = intercept(floor) + slope·x`, plus R². */
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

  private def report(rows: Seq[Row]): Unit = {
    val maxFactor = factors.max
    val sb = new StringBuilder
    sb ++= "# Per-fixture throughput: OUR Spark engine vs the Java CQF reference, at scale (OPT-IN, -Dcqldatabricks.scale=true).\n"
    sb ++= "# Replicated data/fhir deck (ScaleData) on OSS Spark Connect — directional, NOT a cluster benchmark\n"
    sb ++= "# (see docs/deploy.md). winner = faster engine; ref/spark > 1 => Spark faster.\n"
    sb ++= f"#\n${"fixture"}%-34s ${"factor"}%7s ${"patients"}%9s ${"spark_ms"}%10s ${"ref_ms"}%10s ${"winner"}%7s ${"ref/spark"}%10s\n"
    rows.sortBy(r => (r.fixture, r.factor)).foreach { r =>
      val ratio = if (r.sparkMs > 0) r.refMs / r.sparkMs else 0.0
      sb ++= f"${r.fixture}%-34s ${r.factor}%7d ${r.patients}%9d ${r.sparkMs}%10.0f ${r.refMs}%10.0f ${winner(r)}%7s ${ratio}%10.2f\n"
    }

    val atMax = rows.filter(_.factor == maxFactor)
    val (sparkWins, refWins) = atMax.partition(r => r.sparkMs < r.refMs)
    sb ++= f"#\n# At factor $maxFactor (${atMax.headOption.map(_.patients).getOrElse(0)} patients): " +
      f"SPARK wins ${sparkWins.size}, REFERENCE wins ${refWins.size} of ${atMax.size} fixtures.\n"
    if (sparkWins.nonEmpty)
      sb ++= s"#   Spark wins:     ${sparkWins.sortBy(r => -(r.refMs / math.max(r.sparkMs, 1e-9))).map(_.fixture).mkString(", ")}\n"
    if (refWins.nonEmpty)
      sb ++= s"#   Reference wins: ${refWins.sortBy(r => -(r.sparkMs / math.max(r.refMs, 1e-9))).map(_.fixture).mkString(", ")}\n"

    // FLOOR-SUBTRACTED SCALING — the apples comparison the total_wall/winner columns can't give: fit each
    // engine's wall to `floor + slope·patients`, so `slope` (ms/patient) is the data-proportional cost
    // with the fixed floor removed. Spark's floor (plan analysis + shuffle setup + collect fixed cost)
    // amortizes / is one-time in production; the reference's floor is parse + index build. Compare the
    // SLOPES: `ref/spark_pp` > 1 means Spark's marginal per-patient cost is lower (it scales better). Watch
    // sR2 — if Spark is floor-dominated at these sizes its slope is noise (low/!~1 R2), and the ratio is
    // not yet meaningful (push to a larger top factor). Needs >= 2 distinct scales.
    if (factors.size >= 2) {
      sb ++= "#\n# FLOOR-SUBTRACTED SCALING  (OLS: wall_ms = floor + slope·patients; ms/pt = marginal cost, floor removed):\n"
      sb ++= f"${"fixture"}%-34s ${"spk_floor"}%10s ${"spk_ms/pt"}%10s ${"sR2"}%6s ${"ref_floor"}%10s ${"ref_ms/pt"}%10s ${"rR2"}%6s ${"ref/spk_pp"}%11s\n"
      rows.groupBy(_.fixture).toSeq.sortBy(_._1).foreach { case (fx, rs) =>
        val pts = rs.map(r => (r.patients.toDouble, r.sparkMs, r.refMs)).sortBy(_._1)
        if (pts.map(_._1).distinct.size >= 2) {
          val (sFloor, sSlope, sR2) = fit(pts.map(p => (p._1, p._2)))
          val (rFloor, rSlope, rR2) = fit(pts.map(p => (p._1, p._3)))
          val pp = if (sSlope > 1e-9) rSlope / sSlope else Double.NaN
          sb ++= f"$fx%-34s $sFloor%10.1f $sSlope%10.4f $sR2%6.2f $rFloor%10.1f $rSlope%10.4f $rR2%6.2f $pp%11.1f\n"
        }
      }
    }

    val out = new File("build", "fixture-scale.txt")
    Option(out.getParentFile).foreach(_.mkdirs())
    val w = new FileWriter(out, false); try w.write(sb.toString) finally w.close()
    info(s"fixture scale report written to ${out.getAbsolutePath}")
    sb.toString.linesIterator.foreach(info(_))
  }
}
