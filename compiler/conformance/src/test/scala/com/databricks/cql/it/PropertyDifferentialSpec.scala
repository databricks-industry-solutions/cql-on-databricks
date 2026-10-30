package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.passes.Pipeline
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Gate 2 — the property-based differential (CONF-GATE-PROPERTY, `06` section 1.2), turned on in
  * Phase 3. 3VL and the interval/quantity families have a combinatorial space hand cases never
  * cover; generating random kernel expressions and diffing every one against the maintained Java
  * CQF reference is the technique that actually earns trust for the hardest families.
  *
  * Generation is DETERMINISTIC (a fixed seed, never `Math.random` — TECH-DETERMINISM): the same
  * library is produced every run, so a failure is reproducible and the gate is stable in CI. The
  * SAME generated CQL feeds both sides — translated once to ELM for the engine, re-translated by
  * the reference with the version-aligned front end (TECH-ELM-INPUT) — so a divergence cannot be
  * smuggled in through the ELM. Skips cleanly when no Spark Connect server is reachable. */
@RunWith(classOf[JUnitRunner])
final class PropertyDifferentialSpec extends AnyFunSuite {

  test("property-based: random interval (Allen-relation) expressions diff clean (LANG-INTERVAL)") {
    runProperty("PropIntervals", PropertyGen.intervals(seed = 0xA11E4L, count = 80))
  }

  test("property-based: random UCUM quantity expressions diff clean (LANG-QUANTITY)") {
    runProperty("PropQuantities", PropertyGen.quantities(seed = 0x9CF17L, count = 80))
  }

  test("property-based: random precision-aware temporal expressions diff clean (LANG-INTERVAL)") {
    runProperty("PropTemporal", PropertyGen.temporal(seed = 0x7E13F0A1L, count = 80))
  }

  test("property-based: random list set-ops + query clauses diff clean (LANG-QUERY/LANG-LIST)") {
    runProperty("PropQueries", PropertyGen.queries(seed = 0x9EC5L, count = 80))
  }

  // Each generated define stacks one `withColumn` on the single-row base frame; a very deep stack
  // trips Catalyst's analyzer iteration cap (the ARCH-HAZARDS "plan blowup" hazard). The gate keeps
  // its breadth by running the generated defines in bounded-depth chunks — each its own frame.
  private val ChunkSize = 16

  /** Lower + execute each chunk of generated `(name -> CQL expression)` defines on Spark Connect,
    * evaluate the SAME CQL through the reference, and assert value-equality per define (single
    * Unfiltered subject). Fails on the first divergence with both values + the offending CQL. */
  private def runProperty(libName: String, defines: Seq[(String, String)]): Unit = {
    val spark = SparkConnect.session()
    defines.grouped(ChunkSize).zipWithIndex.foreach { case (chunk, idx) =>
      runChunk(spark, s"$libName$idx", chunk)
    }
  }

  private def runChunk(spark: org.apache.spark.sql.SparkSession, libName: String,
                       defines: Seq[(String, String)]): Unit = {
    val cqlText =
      s"library $libName version '1.0.0'\n\n" +
        defines.map { case (n, e) => s"""define "$n": $e""" }.mkString("\n") + "\n"

    val translated = CqlToElmCli.translateText(cqlText)
    val elm = translated.elmJson.getOrElse(
      fail(s"[$libName] generated CQL failed to translate: ${translated.errors.mkString("; ")}"))

    val lib = Pipeline().normalize(elm)
    // EXEC-SQL-BACKEND / two-layer eval S1: run the property differential on the PRIMARY back end — the
    // SQL emitter — so the combinatorial 3VL/interval/quantity/temporal families are gated on the same
    // substrate the corpus gate uses ([[SqlDifferentialSpec]]). The generated defines are System-typed
    // in Unfiltered context (no FHIR data to mount), so the single synthetic subject '*' carries them.
    val ours = SqlDifferential.runGenerated(spark, lib, Context.Unfiltered)
      .getOrElse(SystemColumns.UnfilteredSubject, Map.empty[String, ujson.Value])
    assert(ours.nonEmpty, s"[$libName] produced no defines")

    val names = defines.map(_._1)
    val reference = GroundTruthRunner.evaluate(cqlText, lib.id, names)(SystemColumns.UnfilteredSubject)
    val byExpr = defines.toMap

    names.foreach { n =>
      assert(ours(n) == reference(n),
        s"[$libName] define '$n' (${byExpr(n)}): engine=${ours(n)} reference=${reference(n)}")
    }
  }
}

/** Deterministic generators of random kernel expressions for the property gate (CONF-GATE-PROPERTY).
  * Each returns `(defineName -> CQL expression)` pairs over the operator families Phase 3 adds. A
  * fixed-seed `java.util.Random` keeps generation reproducible (TECH-DETERMINISM). */
object PropertyGen {

  /** Random integer-interval Allen-relation expressions: the relations, the accessors, and
    * point-during, over closed and open boundaries (the open cases exercise the discrete
    * predecessor/successor normalization). */
  def intervals(seed: Long, count: Int): Seq[(String, String)] = {
    val rnd = new java.util.Random(seed)
    // Width ≥ 2 so EVERY open/closed combination still normalizes to a non-empty interval
    // (an open `Interval(a,a+1)` would normalize to the empty `[a+1,a]`, which the reference
    // rejects as invalid — not a diffable case). This still exercises the open→closed
    // predecessor/successor normalization across both boundaries.
    def iv(): String = {
      val a = rnd.nextInt(12); val b = a + 2 + rnd.nextInt(6)
      val (lc, hc) = (rnd.nextBoolean(), rnd.nextBoolean())
      s"${if (lc) "[" else "("}$a, $b${if (hc) "]" else ")"}"
    }
    val relations = Seq("overlaps", "before", "after", "includes", "during", "starts", "ends")
    (1 to count).map { i =>
      val expr = rnd.nextInt(11) match {
        case 0 => s"width of Interval${iv()}"
        case 1 => s"start of Interval${iv()}"
        case 2 => s"end of Interval${iv()}"
        case 3 => s"${rnd.nextInt(14)} during Interval${iv()}"   // point-in-interval (ELM In)
        case _ =>
          val rel = relations(rnd.nextInt(relations.size))
          s"Interval${iv()} $rel Interval${iv()}"
      }
      s"PI$i" -> expr
    }
  }

  /** Random UCUM quantity expressions: cross-unit comparisons and addition within a commensurable
    * family (the "unit edge cases" the exit criteria name). */
  def quantities(seed: Long, count: Int): Seq[(String, String)] = {
    val rnd = new java.util.Random(seed)
    val families = Seq(
      Seq("g", "mg", "kg"),
      Seq("m", "cm", "mm"),
      Seq("s", "min", "h"),
      Seq("L", "mL", "dL"))
    val ops = Seq("=", "!=", "<", ">", "<=", ">=", "+")
    (1 to count).map { i =>
      val fam = families(rnd.nextInt(families.size))
      val u1 = fam(rnd.nextInt(fam.size)); val u2 = fam(rnd.nextInt(fam.size))
      val v1 = 1 + rnd.nextInt(999); val v2 = 1 + rnd.nextInt(999)
      val op = ops(rnd.nextInt(ops.size))
      s"PQ$i" -> s"$v1 '$u1' $op $v2 '$u2'"
    }
  }

  /** Random precision-aware temporal expressions: comparisons between dates of MIXED precision
    * (the "precision edge cases" — many resolve to null), `before`/`after`, and point-during a
    * date interval. Months/days stay in valid ranges so the reference accepts every literal. */
  def temporal(seed: Long, count: Int): Seq[(String, String)] = {
    val rnd = new java.util.Random(seed)
    // A date literal at a random precision (year / year-month / year-month-day).
    def date(prec: Int): (String, (Int, Int, Int)) = {
      val y = 2010 + rnd.nextInt(7); val m = 1 + rnd.nextInt(12); val d = 1 + rnd.nextInt(28)
      val lit = prec match {
        case 1 => f"@$y%04d"
        case 2 => f"@$y%04d-$m%02d"
        case _ => f"@$y%04d-$m%02d-$d%02d"
      }
      (lit, (y, m, d))
    }
    def full(): (String, (Int, Int, Int)) = date(3)
    val cmpOps = Seq("=", "!=", "<", ">", "<=", ">=")
    (1 to count).map { i =>
      val expr = rnd.nextInt(8) match {
        case 0 | 1 =>                                   // mixed-precision comparison (precision edges)
          val (a, _) = date(1 + rnd.nextInt(3)); val (b, _) = date(1 + rnd.nextInt(3))
          s"$a ${cmpOps(rnd.nextInt(cmpOps.size))} $b"
        case 2 => val (a, _) = full(); val (b, _) = full(); s"$a before $b"
        case 3 => val (a, _) = full(); val (b, _) = full(); s"$a after $b"
        case _ =>                                       // point during [lo, hi] (lo ≤ hi by sort)
          val (p, _) = date(1 + rnd.nextInt(3))
          val (s1, t1) = full(); val (s2, t2) = full()
          val (lo, hi) = if (ordLe(t1, t2)) (s1, s2) else (s2, s1)
          s"$p during Interval[$lo, $hi]"
      }
      s"PT$i" -> expr
    }
  }

  private def ordLe(a: (Int, Int, Int), b: (Int, Int, Int)): Boolean =
    implicitly[Ordering[(Int, Int, Int)]].lteq(a, b)

  /** Random list set-ops and query clauses over System lists: `union`/`intersect`/`except`,
    * `sort asc|desc`, `return distinct`, `aggregate` (sum fold), and `where`+`return`. */
  def queries(seed: Long, count: Int): Seq[(String, String)] = {
    val rnd = new java.util.Random(seed)
    def list(): String = "{ " + (0 to rnd.nextInt(4)).map(_ => rnd.nextInt(8).toString).mkString(", ") + " }"
    val setops = Seq("union", "intersect", "except")
    (1 to count).map { i =>
      val expr = rnd.nextInt(6) match {
        case 0 | 1 => s"${list()} ${setops(rnd.nextInt(setops.size))} ${list()}"
        case 2     => s"(${list()}) N return N sort ${if (rnd.nextBoolean()) "asc" else "desc"}"
        case 3     => s"(${list()}) N return distinct N"
        case 4     => s"(${list()}) N aggregate R starting 0: R + N"
        case _     => s"(${list()}) N where N > ${rnd.nextInt(5)} return N * 2"
      }
      s"PQc$i" -> expr
    }
  }
}
