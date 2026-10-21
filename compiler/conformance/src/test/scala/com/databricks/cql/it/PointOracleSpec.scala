package com.databricks.cql.it

import com.databricks.cql.cqf.{CqfEvaluator, CqfPatient}
import com.databricks.cql.harness.GroundTruthRunner
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** RUN-POINT-CQF, `DEC-SAME-ANSWER` — the evaluator's point engine gives the conformance oracle's answers.
  *
  * Each corpus measure's test patients and value sets ([[Corpus.measureDefs]]), evaluated two ways: the oracle
  * (`GroundTruthRunner`, with the 2026 measurement period passed as a run-time parameter) and `CqfEvaluator` as
  * the point App calls it (the closure as CQL, each patient's own resources, the 2026 period as canonical
  * inclusive bounds). Every population of every patient must be equal. No Spark: both sides are the CQF
  * engine, so this runs offline. */
@RunWith(classOf[JUnitRunner])
class PointOracleSpec extends AnyFunSuite {

  Corpus.measureDefs.foreach { m =>
    test(s"RUN-POINT-CQF: ${m.library}'s populations equal the oracle's for every test patient (DEC-SAME-ANSWER)") {
      def lines(f: File): Seq[String] = Paths.read(f).linesIterator.filter(_.trim.nonEmpty).toSeq
      val resources = m.resourceTypes.map(t => t -> lines(new File(m.dataDir, s"$t.ndjson"))).toMap
      val rootCql = Paths.read(Paths.cql(m.library))
      val depSources = m.deps.map(d => Paths.read(Paths.cql(d)))
      val evaluator = new CqfEvaluator()
      val needs = evaluator.requirements(m.library, rootCql +: depSources)
      val expansions = needs.valueSets.map(vs => vs -> Corpus.measureExpansion(vs)).toMap
      val ids = resources("Patient").map(r => ujson.read(r)("id").str)
      val populations = m.populations.map(_._1)

      val oracle = GroundTruthRunner.evaluateAcross(rootCql, m.library, Map(m.library -> populations),
        GroundTruthRunner.RefEnv(contextName = "Patient", resourcesByType = resources, expansions = expansions,
          subjectIds = ids, extraLibrarySources = depSources,
          parameters = Map("Measurement Period" -> m.measurementPeriod)))

      // As a caller would send them: each patient's own resources, plus those that reference no patient.
      def own(id: String): Seq[String] = resources.toSeq.flatMap { case (t, rs) =>
        rs.filter(r => if (t == "Patient") ujson.read(r)("id").str == id
                       else r.contains(s"\"Patient/$id\"") || !r.contains("\"Patient/"))
      }
      val t0 = System.nanoTime()
      val point = evaluator.evaluate(m.library, rootCql +: depSources, ids.map(id => CqfPatient(id, own(id))),
        expansions, Some(("2026-01-01T00:00:00.000", "2026-12-31T23:59:59.999"))).toMap
      val ms = (System.nanoTime() - t0) / 1e6

      assert(oracle.size == ids.size, s"the oracle evaluated only ${oracle.size} of ${ids.size} patients")
      val diffs = for {
        (id, expected) <- oracle.toSeq
        pop <- populations
        if point(id)(pop) != expected(pop)
      } yield s"$id $pop: point=${point(id)(pop)} oracle=${expected(pop)}"
      assert(diffs.isEmpty, diffs.mkString("\n"))
      info(f"${oracle.size} patients x ${populations.size} populations equal; point evaluation ${ms / ids.size}%.1f ms per patient")
    }
  }
}
