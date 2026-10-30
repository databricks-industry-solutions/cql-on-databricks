package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.ir.ExpressionDef
import com.databricks.cql.passes.Pipeline
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** Spark-free, opt-in FAILURE-ISOLATION aid for the reference side (`CONF-REF-ANSWER-KEY` debugging).
  *
  * [[RefScoreSpec]] and the differential both evaluate a measure's population defines TOGETHER, and
  * the reference aborts a whole subject if ANY requested define raises — so one bad define reads as
  * "every population wrong for 36 patients" with no hint which one. This evaluates each define in the
  * root library INDIVIDUALLY for one subject and names the ones that throw.
  *
  * That is how a whole-measure collapse was once pinned to a single profile-only retrieve define, and from
  * there to the retrieve-profile gap now fixed in `InMemoryFhirRetrieveProvider.profileMatches`.
  *
  * Skips green unless requested:
  * {{{
  * ./gradlew :compiler:conformance:test --tests '*RefDebugSpec*' \
  *   -Dcqldatabricks.refDebug=<measure library> [-Dcqldatabricks.refDebugSubject=<patient-id>]
  * }}} */
@RunWith(classOf[JUnitRunner])
final class RefDebugSpec extends AnyFunSuite {

  test("per-define reference failure isolation (opt-in, -Dcqldatabricks.refDebug=<measure library>)") {
    val key = sys.props.getOrElse("cqldatabricks.refDebug", "")
    assume(key.nonEmpty, "opt-in: -Dcqldatabricks.refDebug=<measure library>")
    val md = Corpus.linkMeasures.find(_.dataSubdir == key)
      .getOrElse(fail(s"unknown measure key '$key'; known: ${Corpus.linkMeasures.map(_.dataSubdir).mkString(", ")}"))

    def lines(f: String): Seq[String] =
      Paths.read(new File(md.dataDir, f)).linesIterator.filter(_.trim.nonEmpty).toSeq

    val rootLib = Pipeline().load(Paths.read(Paths.elm(md.library)))
    val depLibs = md.deps.map(d => Pipeline().load(Paths.read(Paths.elm(d))))
    val expansions = (rootLib.valueSets ++ depLibs.flatMap(_.valueSets)).map(vs => vs.id -> Corpus.measureExpansion(vs.id)).toMap
    val subjects = lines("Patient.ndjson").map(l => ujson.read(l)("id").str)
    val subject  = sys.props.getOrElse("cqldatabricks.refDebugSubject", subjects.head)

    val allDefines = rootLib.statements.collect { case e: ExpressionDef => e.name }
    println(s"[$key] library ${md.library}: ${allDefines.size} defines, subject $subject")

    val env = GroundTruthRunner.RefEnv(
      contextName         = Context.Patient.name,
      resourcesByType     = md.resourceTypes.map(t => t -> lines(s"$t.ndjson")).toMap,
      expansions          = expansions,
      subjectIds          = Seq(subject),
      extraLibrarySources = md.deps.map(d => Paths.read(Paths.cql(d))),
      parameters          = Map("Measurement Period" -> md.measurementPeriod))

    val failures = allDefines.flatMap { d =>
      try { GroundTruthRunner.evaluate(Paths.read(Paths.cql(md.library)), md.library, Seq(d), env); None }
      catch { case e: Throwable => Some(d -> Option(e.getMessage).getOrElse(e.toString)) }
    }
    if (failures.isEmpty) println(s"[$key] no per-define failures for $subject")
    else {
      println(s"[$key] ${failures.size} FAILING define(s) for $subject:")
      failures.foreach { case (d, m) => println(s"   - '$d': ${m.take(400)}") }
    }
    succeed
  }
}
