package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.passes.{Optimize, Pipeline}
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import scala.collection.mutable.ArrayBuffer

/** PHASE-7 Stage 4, Gate-1 execution layer (`CONF-GATE-OPERATOR`): the executable cases of the
  * vendored `cqframework` operator suite, lowered to a Spark Connect frame and EXECUTED, must agree
  * — per operator — with BOTH the maintained Java CQF reference evaluating the same generated CQL
  * (the `CONF-GATE-MEASURE` reference, reused per PROC-DRY) AND the spec's declared `<output>`. Two
  * independent anchors per case.
  *
  * Robust isolation, so one engine gap never hides the rest and never reds the whole file silently:
  *   1. `OperatorTests.excluded` (tracked engine bugs — wrong value, invalid plan, runtime error) are
  *      skipped up front; each is enumerated with a reason so a NEW occurrence is NOT pre-excluded.
  *   2. A normalize+lower probe drops the documented NODE frontiers (normalize-unsupported,
  *      lower-`UnsupportedNode`) — these never execute — counting them like
  *      [[OperatorTranslationSpec]].
  *   3. Survivors are diffed in CHUNKS (a single frame of hundreds of struct columns overflows the
  *      Spark analyzer's fixed-point); a chunk that throws at analysis/execution is RETRIED per case
  *      so the culprit is isolated — and reported as a hard failure (a new defect) unless it is in
  *      `excluded`.
  * An undocumented value divergence or execution failure is a hard failure — the gate doing its job.
  * It has already caught the Coalesce list-overload, the quantity unary/mixed-arithmetic invalid
  * plans, and the partial-DateTime cast errors (see the `Known*` maps).
  *
  * Skips cleanly when no Connect server is reachable, or FAILS under `-Dcqldatabricks.requireConnect=true`
  * (CI), like the other plan-executing gates. */
object OperatorConformanceSpec {
  /** Cases per executed frame. One frame of all survivors (hundreds of struct columns) overflows the
    * Spark analyzer's fixed-point iteration cap, so the diff runs in chunks of this width. */
  val ChunkSize = 40
}

@RunWith(classOf[JUnitRunner])
final class OperatorConformanceSpec extends AnyFunSuite {

  private val UnknownExprRe = """UnknownExpr\(([A-Za-z0-9]+)\)""".r

  OperatorTests.Files.foreach { file =>
    test(s"operator differential vs CQF reference + declared output — $file (CONF-GATE-OPERATOR)") {
      val spark = SparkConnect.session()
      val env = SqlEvalEnv(Context.Unfiltered, Config.defaults)

      val included = OperatorTests.included(OperatorTests.load(file))
      val toDiff = ArrayBuffer.empty[OperatorTests.OpCase]
      val probeFailures = ArrayBuffer.empty[String]
      var skippedFrontier = 0
      var skippedTracked = 0

      // (1)+(2): drop tracked bugs and the node frontiers; the rest are execution candidates.
      included.foreach { c =>
        val key = (c.file, c.group, c.name)
        if (OperatorTests.excluded.contains(key)) skippedTracked += 1
        else {
          try {
            val lib = Pipeline().normalizeCql(OperatorTests.caseLibrary(c))
            SqlFrameLowering.lower(Optimize.run(lib, Config.defaults), env) // probe lowering (a reject raises UnsupportedNode)
            toDiff += c
          } catch {
            case _: UnsupportedNode => skippedFrontier += 1 // lowering frontier
            case e: Throwable =>
              val msg = Option(e.getMessage).getOrElse(e.getClass.getSimpleName)
              UnknownExprRe.findFirstMatchIn(msg).map(_.group(1)) match {
                case Some(n) if OperatorTests.UnsupportedNodes.contains(n) => skippedFrontier += 1
                case _ => probeFailures += s"  ${c.group}.${c.name}: [${c.expression}] -> $msg"
              }
          }
        }
      }

      // (3): execute survivors in chunks; isolate any analysis/runtime failure per case.
      val diffs = ArrayBuffer.empty[String]
      def diffOne(c: OperatorTests.OpCase, ours: Map[String, ujson.Value], ref: Map[String, ujson.Value]): Unit = {
        val engineExpr = ours(c.exprDefine); val engineOut = ours(c.expectedDefine); val refExpr = ref(c.exprDefine)
        val key = (c.file, c.group, c.name)
        // The spec's declared <output> is the authoritative anchor (always hard). The CQF reference
        // is a cross-check, relaxed only where the reference itself is known to diverge from the spec
        // (OperatorTests.KnownReferenceDivergence) — there the engine matching the spec is the win.
        if (engineExpr != engineOut)
          diffs += s"  ${c.group}.${c.name}: [${c.expression}] engine=$engineExpr != declared output [${c.output.get}]=$engineOut"
        else if (engineExpr != refExpr && !OperatorTests.KnownReferenceDivergence.contains(key))
          diffs += s"  ${c.group}.${c.name}: [${c.expression}] engine=$engineExpr reference=$refExpr"
      }
      def runChunk(cases: Seq[OperatorTests.OpCase]): Unit = {
        val cqlText = OperatorTests.libraryText(file, cases)
        val lib = Pipeline().normalizeCql(cqlText)
        val ours  = SqlDifferential.runGenerated(spark, lib, Context.Unfiltered)
        val names = ours.headOption.map(_._2.keys.toSeq).getOrElse(Nil)
        val reference = GroundTruthRunner.evaluate(
          cqlText, lib.id, names, GroundTruthRunner.RefEnv(contextName = Context.Unfiltered.name))
        ours.keys.foreach(key => cases.foreach(c => diffOne(c, ours(key), reference(key))))
      }
      def process(cases: Seq[OperatorTests.OpCase]): Unit =
        try runChunk(cases)
        catch {
          case e: Throwable if cases.size > 1 => cases.foreach(c => process(Seq(c)))
          case e: Throwable => // single case failed at analysis/execution → a new, undocumented defect
            val c = cases.head
            probeFailures += s"  ${c.group}.${c.name}: [${c.expression}] -> exec ${e.getClass.getSimpleName}: ${Option(e.getMessage).getOrElse("").take(120)}"
        }
      toDiff.toSeq.grouped(OperatorConformanceSpec.ChunkSize).foreach(ch => process(ch.toSeq))

      info(s"$file: ${toDiff.size} diffed, $skippedFrontier frontier-skip, $skippedTracked tracked-bug-skip")
      assert(probeFailures.isEmpty,
        s"${probeFailures.size} case(s) in $file failed to compile/execute for an UNDOCUMENTED reason " +
          s"(the gate found a new defect — fix the engine, or record it in the OperatorTests.Known* maps " +
          s"with a reason if confirmed and deferred):\n${probeFailures.mkString("\n")}")
      assert(toDiff.nonEmpty, s"no diffable operator cases for $file")
      assert(diffs.isEmpty,
        s"${diffs.size} operator divergence(s) in $file (real bug — fix the engine, or record in " +
          s"OperatorTests.KnownDivergences with a reason if confirmed and deferred):\n${diffs.mkString("\n")}")
    }
  }
}
