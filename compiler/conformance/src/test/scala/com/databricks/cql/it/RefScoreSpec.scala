package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.passes.Pipeline
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** SPARK-FREE answer-key gate (`CONF-REF-ANSWER-KEY`, companion to `CONF-GATE-SCOREKEY`): run the Java
  * CQF REFERENCE over the committed NDJSON and diff its per-patient population values against the measure's
  * hand-written answer key (`data/<subdir>/answer-key.json`).
  *
  * Why this exists separately from [[MeasureScoreSpec]]: that gate scores the ENGINE, so it needs a
  * Connect server and skips without one. This gate asks the reference rather than the engine, so it gives
  * an offline signal on whether a change to a measure's CQL or data still matches its answer key. The
  * three are complementary and none subsumes another:
  *
  *   - reference vs answer key (HERE)                — does the CQL compute what its author worked out?
  *   - engine vs reference ([[SqlDifferentialSpec]]) — does our lowering agree with the reference?
  *   - engine vs answer key ([[MeasureScoreSpec]])   — the end-to-end bar.
  *
  * A divergence here is NOT a lowering bug: the CQL, the data or the answer key is wrong, and the failure
  * names the patients. */
@RunWith(classOf[JUnitRunner])
final class RefScoreSpec extends AnyFunSuite {

  Corpus.linkMeasures.foreach(runMeasure)

  private def runMeasure(md: Corpus.MeasureDef): Unit = {
    val measure = md.library

    def lines(file: String): Seq[String] =
      Paths.read(new File(md.dataDir, file)).linesIterator.filter(_.trim.nonEmpty).toSeq

    /** `subjectId -> (populationCode -> 0|1)`, the same answer key [[MeasureScoreSpec]] reads. */
    lazy val expected: Map[String, Map[String, Int]] = Corpus.answerKey(md)

    /** The reference's per-(subject, define) values. Mirrors the wiring `Corpus.measure` gives the
      * differential's reference side — same NDJSON, same expansions (root + dependency value sets),
      * same dependency CQL sources — minus every Spark participant. */
    lazy val reference: Map[String, Map[String, ujson.Value]] = {
      val rootLib = Pipeline().load(Paths.read(Paths.elm(md.library)))
      val depLibs = md.deps.map(d => Pipeline().load(Paths.read(Paths.elm(d))))
      val expansions = (rootLib.valueSets ++ depLibs.flatMap(_.valueSets))
        .map(vs => vs.id -> Corpus.measureExpansion(vs.id)).toMap

      GroundTruthRunner.evaluate(
        Paths.read(Paths.cql(md.library)),
        md.library,
        md.populations.map(_._1),
        GroundTruthRunner.RefEnv(
          contextName         = Context.Patient.name,
          resourcesByType     = md.resourceTypes.map(t => t -> lines(s"$t.ndjson")).toMap,
          expansions          = expansions,
          subjectIds          = lines("Patient.ndjson").map(l => ujson.read(l)("id").str),
          extraLibrarySources = md.deps.map(d => Paths.read(Paths.cql(d))),
          parameters          = Map("Measurement Period" -> md.measurementPeriod)))
    }

    md.populations.foreach { case (define, code) =>
      test(s"$measure reference-vs-answer-key: '$define' ($code) — Spark-free (CONF-REF-ANSWER-KEY)") {
        assume(!Corpus.skipMeasures, "measures skipped (-Dcqldatabricks.skipMeasures=true)")
        val ref = reference

        val rows = expected.keys.toSeq.sorted.map { subj =>
          (subj, ref.get(subj).flatMap(_.get(define)).map(score).getOrElse(0), expected(subj).getOrElse(code, 0))
        }
        val mismatches = rows.filter { case (_, r, x) => r != x }
        val refTotal   = rows.map(_._2).sum
        val expTotal   = rows.map(_._3).sum

        // The reference could not evaluate ANY subject → no signal at all, which must not pass vacuously.
        assert(ref.nonEmpty, s"[$code] the reference evaluated 0 of ${rows.size} subjects for '$define'")

        assert(mismatches.isEmpty,
          s"[$code] '$define': reference total $refTotal != answer key $expTotal across ${rows.size} " +
            s"patients; ${mismatches.size} per-patient mismatch(es): " +
            mismatches.map { case (s, r, x) => s"$s reference=$r answerKey=$x" }.mkString("; "))

        succeed
      }
    }
  }

  /** Same reduction [[MeasureScoreSpec]] uses: true (or a non-empty list) is membership. */
  private def score(v: ujson.Value): Int = v match {
    case ujson.True   => 1
    case a: ujson.Arr => if (a.arr.nonEmpty) 1 else 0
    case _            => 0
  }
}
