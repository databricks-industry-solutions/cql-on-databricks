package com.databricks.cql.it

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Gate-1 translation layer (`CONF-GATE-OPERATOR`): every included operator test
  * from the vendored `cqframework` suite must EITHER translate (CQL→ELM) and normalize to the engine
  * kernel, OR fail solely because it needs a node kind documented in
  * [[OperatorTests.UnsupportedNodes]]. No Spark server is needed, so this is the fast, hermetic
  * backstop that catches an unsupported operator at the compiler boundary (`OnlyKernelNodes`
  * verifier) — and, just as importantly, catches a REGRESSION (a node that used to lower and no
  * longer does) or a brand-new gap, because either shows up as a `Broken` case outside the
  * documented frontier.
  *
  * Both the expression and its declared output are probed, since the execution gate
  * ([[OperatorConformanceSpec]]) evaluates both. The set of skipped node kinds is the explicit,
  * machine-checked coverage frontier the "iterate on UnsupportedNode" loop narrows
  */
@RunWith(classOf[JUnitRunner])
final class OperatorTranslationSpec extends AnyFunSuite {

  OperatorTests.Files.foreach { file =>
    test(s"operator suite normalizes to kernel IR (or a documented-unsupported node) — $file (CONF-GATE-OPERATOR)") {
      val cases = OperatorTests.included(OperatorTests.load(file))
      assert(cases.nonEmpty, s"no included cases parsed from $file")

      val classified = cases.map(c => c -> OperatorTests.classify(c))
      val broken = classified.collect {
        case (c, OperatorTests.Broken(why)) => s"  ${c.group}.${c.name}: [${c.expression}] -> $why"
      }
      val supported = classified.count(_._2 == OperatorTests.Ok)
      val skippedByNode = classified.collect { case (_, OperatorTests.Unsupported(n)) => n }
        .groupBy(identity).view.mapValues(_.size).toSeq.sortBy(-_._2)
      info(s"$file: ${cases.size} included — $supported gated, ${skippedByNode.map(_._2).sum} skipped " +
        s"on unsupported nodes [${skippedByNode.map { case (n, k) => s"$n×$k" }.mkString(", ")}]")

      assert(broken.isEmpty,
        s"${broken.size} operator case(s) in $file failed to normalize for an UNDOCUMENTED reason " +
          s"(fix the engine, or — if a genuinely new unsupported node — add it to " +
          s"OperatorTests.UnsupportedNodes with a reason):\n${broken.mkString("\n")}")
    }
  }
}
