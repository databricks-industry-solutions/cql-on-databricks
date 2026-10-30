package com.databricks.cql.it

import com.databricks.cql.ir.{ExpressionDef, Library, UnknownExpr}
import com.databricks.cql.load.ElmCodec
import com.databricks.cql.passes.{LibraryLinker, Pipeline}
import com.databricks.cql.service.CompileService
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Hermetic (no Spark) gate for the multi-library link + prune step every multi-library measure needs
  * (CONF-GATE-MEASURE): for EVERY corpus measure ([[Corpus.linkMeasures]]), load its translated ELM
  * closure, flatten it to one self-contained library, prune to its population-root slice, bind the
  * measurement period, and normalize. A node the engine does not model would trip the Normalize kernel-node verifier here
  * (loud, never a silent mislowering, CORE-NO-SILENT-MISLOWER) — so this is the per-node backstop
  * for the slice, and it runs offline in CI. */
@RunWith(classOf[JUnitRunner])
final class MeasureLinkSpec extends AnyFunSuite {

  private def load(name: String): Library = ElmCodec.loadString(Paths.read(Paths.elm(name)))

  Corpus.linkMeasures.foreach { md =>
    val measure = md.library
    val roots = md.roots

    test(s"$measure links + prunes to its population-root slice") {
      val root = load(measure)
      val deps = md.deps.map(d => load(d)).map(l => l.id -> l).toMap
      val pruned = LibraryLinker.prune(LibraryLinker.link(root, deps), roots)

      // The slice must be self-contained: every population root and its transitive defines present.
      roots.foreach { r =>
        assert(pruned.statements.exists { case e: ExpressionDef => e.name == r; case _ => false },
          s"[$measure] pruned library is missing population define '$r'")
      }
    }

    test(s"$measure population-root slice normalizes to fully-modeled kernel IR") {
      val root = load(measure)
      val deps = md.deps.map(d => load(d)).map(l => l.id -> l).toMap
      val pruned = LibraryLinker.prune(LibraryLinker.link(root, deps), roots)

      val normalized = Pipeline().normalize(CompileService.applyParameterOverrides(pruned,
        Map("Measurement Period" -> md.measurementPeriod)))
      val unknowns = normalized.collect { case u: UnknownExpr => u.elmType }.distinct
      assert(unknowns.isEmpty, s"unmodeled nodes in $measure population slice: ${unknowns.mkString(", ")}")
    }
  }
}
