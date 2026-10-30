package com.databricks.cql.it

import java.io.{File, FileWriter}

/** Per-fixture CPU-vs-wait timing for the differential gate (phase-8 perf inspection). The engine builds
  * the Spark Connect plan CLIENT-side and lazily, so the work splits onto two clean axes — client-vs-server
  * for CPU, and wall measured independently — reported as four numbers:
  *
  *   - **client_cpu** = our actual compile CPU — `ThreadMXBean` CPU over the lowering span MINUS the
  *     materialize seam's CPU. This is the part that ports unchanged to production (the client always
  *     builds the same plan). While the lowering thread is blocked on a Connect socket read it burns no
  *     CPU, so this number naturally excludes server waits.
  *   - **client_wait** = lowering BLOCKED on incidental server analyze — the `.schema`/`.columns`
  *     round-trips (e.g. the choice/union `tagChoiceArms` path). A wait state: it does not count against
  *     our CPU, but it is visible because it is eliminable. Derived as
  *     `loweringWall − client_cpu − (seamWall − seamCpu)`.
  *   - **runtime** = ALL real server execution — the WHOLE materialize seam (the `Cost` decision + the
  *     client proto serialize + the `localCheckpoint` staging round-trip, accumulated DURING lowering) PLUS
  *     the final `.collect()`. Materialize folds into runtime: in production it is a staged Delta / cached
  *     table (amortizable across runs), so it is server work, not client compile.
  *   - **total_wall** = the whole evaluate (lowering + dump + collect), measured DIRECTLY by one span — not
  *     a sum. The accounting identity `total_wall − (client_cpu + client_wait + runtime) = untimed glue`
  *     (mostly the `StageDump`, which lives in no work bucket) is the diagnostic: if lowering ever overlaps
  *     server work, `total_wall < sum` and that surfaces here with zero rework.
  *   - **reference_ms** = the Java CQF reference engine's own wall time evaluating the SAME authored CQL
  *     (the `GroundTruthRunner.evaluate` in-process run the differential diffs against). It is NOT part of
  *     our evaluate (not in `total_wall`, not in the identity) — it is a side-by-side comparison: our
  *     Spark pipeline vs the row-at-a-time reference on the tiny conformance datasets. Reported, never
  *     gated (it is a different engine on a different execution model). Merged into the row by
  *     [[recordReference]] AFTER our own timing lands, so a run that does not evaluate the reference (e.g.
  *     a self-diff variant) simply leaves it at 0.
  *
  * Splitting these tells you whether a slow fixture is OUR lowering (client_cpu), eliminable server waits
  * during lowering (client_wait), or real server execution (runtime) — for the tiny conformance datasets
  * the server time is dominated by plan ANALYSIS, not data, so a fat plan shows up as runtime even though
  * no real work happens. Rows are written to `<build>/test-runtimes.txt` (the build dir that parents the
  * dump dir) and the whole file is REWRITTEN sorted by `total_wall` DESC on each record (heaviest fixture
  * first, for at-a-glance triage). CUMULATIVE: the file is seeded once per JVM from its own prior
  * contents and rows are keyed by fixture (latest wins), so a FOCUSED run (`-Dcqldatabricks.only=X`) updates
  * X's row and leaves every other fixture's row intact — the file represents everything compiled so far
  * across runs, not just this run's fixtures.
  *
  * KNOWN LIMITATION: `ThreadMXBean` is per-thread CPU. Lowering is single-threaded today, so client_cpu is
  * exact; if it ever parallelizes we would need to sum per-thread CPU (and would flag it then).
  *
  * Part of the NORMAL dump output: the caller records whenever `StageDump.enabled` (the same gate as the
  * lowered tree/`resolved-plan.txt`), so `-Dcqldatabricks.dumpDir=<dir>` produces the runtimes too — no separate flag.
  * [[record]] itself is unconditional (the caller gates). */
object Timing {
  /** `<build>/test-runtimes.txt` — the build dir parenting the dump dir (so it sits beside, not
    * inside, the per-fixture dump folders). */
  private def outFile: File = {
    val dumpDir = new File(sys.props.getOrElse("cqldatabricks.dumpDir", "build/dump"))
    new File(Option(dumpDir.getParentFile).getOrElse(dumpDir), "test-runtimes.txt")
  }
  private val lock = new Object
  /** All rows to render, keyed by fixture (latest wins). Tuple:
    * (fixture, clientCpuMs, clientWaitMs, runtimeMs, totalWallMs, referenceMs, defines). CUMULATIVE across
    * gradle runs: seeded ONCE per JVM from the existing `test-runtimes.txt` (below) so a FOCUSED run
    * (`-Dcqldatabricks.only=<fixture>`) UPDATES that fixture's row and LEAVES every other fixture's row intact —
    * the file always represents everything compiled so far, not just this run's fixtures. */
  private type Row = (String, Double, Double, Double, Double, Double, Int)
  private val rows = scala.collection.mutable.LinkedHashMap.empty[String, Row]
  private var seeded = false

  /** Seed `rows` ONCE per JVM from the existing file's DATA lines, so successive runs accumulate rather
    * than overwrite. Parses the fixed-width layout by whitespace (a fixture name has no spaces); ignores
    * the `#` preamble, the header, and any malformed line. Accepts BOTH the current 7-column layout and
    * the legacy 6-column one (no `reference_ms` — seeds it 0) so an old file upgrades in place. Best-effort:
    * a missing/garbled file just starts empty. */
  private def seed(f: File): Unit = {
    if (seeded) return
    seeded = true
    if (!f.exists) return
    try {
      val src = scala.io.Source.fromFile(f)
      try src.getLines().foreach { ln =>
        val p = ln.trim.split("\\s+")
        if (!ln.startsWith("#") && !p.headOption.contains("fixture")) {
          val row: Option[Row] =
            if (p.length == 7)
              scala.util.Try((p(0), p(1).toDouble, p(2).toDouble, p(3).toDouble, p(4).toDouble, p(5).toDouble, p(6).toInt): Row).toOption
            else if (p.length == 6) // legacy: no reference_ms column
              scala.util.Try((p(0), p(1).toDouble, p(2).toDouble, p(3).toDouble, p(4).toDouble, 0.0, p(5).toInt): Row).toOption
            else None
          row.foreach(r => rows(r._1) = r)
        }
      } finally src.close()
    } catch { case _: Throwable => () }
  }

  def record(fixture: String, clientCpuMs: Double, clientWaitMs: Double, runtimeMs: Double,
             totalWallMs: Double, defines: Int): Unit =
    lock.synchronized {
      seed(outFile)
      // Preserve any reference_ms already merged for this fixture (recordReference may land in either
      // order relative to our own timing); a fresh row starts it at 0 until the reference is evaluated.
      val referenceMs = rows.get(fixture).map(_._6).getOrElse(0.0)
      rows(fixture) = ((fixture, clientCpuMs, clientWaitMs, runtimeMs, totalWallMs, referenceMs, defines))
      rewrite()
    }

  /** Merge the Java CQF reference engine's evaluation wall time into `fixture`'s row (all other columns
    * preserved). Recorded from [[SqlDifferential.run]] AFTER our own [[record]], so the row already exists;
    * defensively creates a stub row if not. Keyed by the bare fixture name — the reference does not depend
    * on the retrieve-pushdown variant, and `run` always uses the default (pushdown-on) config, so the key
    * matches the `record` tag. */
  def recordReference(fixture: String, referenceMs: Double): Unit =
    lock.synchronized {
      seed(outFile)
      val (fx, cpu, wait, rt, tw, _, d) = rows.getOrElse(fixture, (fixture, 0.0, 0.0, 0.0, 0.0, 0.0, 0))
      rows(fixture) = ((fx, cpu, wait, rt, tw, referenceMs, d))
      rewrite()
    }

  // ---------------------------------------------------------------- CONF-PERF-BARRIER-DETAIL
  // Per-barrier detail, consolidated across ALL fixtures into ONE `test-runtimes-details.txt` beside
  // `test-runtimes.txt`. With `materializeMode = All` (every define a barrier), each define is a hard
  // `localCheckpoint` — force-computed independently at staging — so `wall_ms` is that barrier's isolated
  // runtime. One row per (fixture, barrier); cumulative across runs, like the main file.

  /** `<build>/test-runtimes-details.txt` (beside [[outFile]]). */
  private def detailsFile: File = {
    val dumpDir = new File(sys.props.getOrElse("cqldatabricks.dumpDir", "build/dump"))
    new File(Option(dumpDir.getParentFile).getOrElse(dumpDir), "test-runtimes-details.txt")
  }
  /** (fixture, barrier, wallMs, fns, bytes), keyed by (fixture, barrier); latest wins. */
  private type DetailRow = (String, String, Double, Int, Int)
  private val details = scala.collection.mutable.LinkedHashMap.empty[(String, String), DetailRow]
  private var detailsSeeded = false

  /** Seed `details` ONCE per JVM from the existing file, so successive/focused runs accumulate. */
  private def seedDetails(f: File): Unit = {
    if (detailsSeeded) return
    detailsSeeded = true
    if (!f.exists) return
    try {
      val src = scala.io.Source.fromFile(f)
      try src.getLines().foreach { ln =>
        val p = ln.trim.split("\\s+")
        if (!ln.startsWith("#") && !p.headOption.contains("fixture") && p.length == 5)
          scala.util.Try((p(0), p(1), p(2).toDouble, p(3).toInt, p(4).toInt): DetailRow)
            .toOption.foreach(r => details((r._1, r._2)) = r)
      } finally src.close()
    } catch { case _: Throwable => () }
  }

  /** Record ONE barrier's isolated instrumentation (CONF-PERF-BARRIER-DETAIL). */
  def recordDetail(fixture: String, barrier: String, wallMs: Double, fns: Int, bytes: Int): Unit =
    lock.synchronized {
      seedDetails(detailsFile)
      details((fixture, barrier)) = ((fixture, barrier, wallMs, fns, bytes))
      rewriteDetails()
    }

  /** Rewrite the whole details file, grouped by fixture then wall DESC. Caller holds `lock`. */
  private def rewriteDetails(): Unit = {
    val f = detailsFile
    Option(f.getParentFile).foreach(_.mkdirs())
    val w = new FileWriter(f, /*append=*/ false)
    try {
      w.write("# Per-BARRIER instrumentation (CONF-PERF-BARRIER-DETAIL), one row per (fixture, barrier). Under materializeMode=All every define is a hard localCheckpoint, so wall_ms is that barrier's INDEPENDENT staging runtime. fns = unresolved_function nodes in the staged subplan; bytes = its unresolved Connect proto size.\n" +
        f"${"fixture"}%-44s ${"barrier"}%-40s ${"wall_ms"}%10s ${"fns"}%8s ${"bytes"}%12s\n")
      details.values.toSeq.sortBy { case (fx, _, wall, _, _) => (fx, -wall) }.foreach { case (fx, b, wall, fns, bytes) =>
        w.write(f"$fx%-44s $b%-40s $wall%10.1f $fns%8d $bytes%12d\n")
      }
    } finally w.close()
  }

  /** Rewrite the whole (small) file sorted by total_wall DESC, so it is always sorted regardless of
    * arrival order — no separate post-run sort, and a heavy fixture is not buried. Caller holds `lock`. */
  private def rewrite(): Unit = {
    val f = outFile
    Option(f.getParentFile).foreach(_.mkdirs())
    val w = new FileWriter(f, /*append=*/ false)
    try {
      w.write("# Per-fixture CPU-vs-wait timing, ms: client_cpu (our lowering CPU) / client_wait (server analyze waits) / runtime (materialize seam + collect) / total_wall (measured whole-evaluate) / reference_ms (Java CQF reference engine, side-by-side, NOT part of total_wall). Identity: total_wall − (client_cpu+client_wait+runtime) = untimed glue (StageDump).\n" +
        f"${"fixture"}%-44s ${"client_cpu_ms"}%13s ${"client_wait_ms"}%14s ${"runtime_ms"}%10s ${"total_wall_ms"}%13s ${"reference_ms"}%12s ${"defines"}%8s\n")
      rows.values.toSeq.sortBy { case (_, _, _, _, tw, _, _) => -tw }.foreach { case (fx, cpu, wait, rt, tw, ref, d) =>
        w.write(f"$fx%-44s $cpu%13.1f $wait%14.1f $rt%10.1f $tw%13.1f $ref%12.1f $d%8d\n")
      }
    } finally w.close()
  }
}
