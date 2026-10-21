package com.databricks.cql.harness

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files

/** The single golden checker (CONF-GOLDEN-PLAN, `testing-and-conformance.md` section 2). Folds in
  * the two former mechanisms (the IR snapshot and the lowered plan-shape gate) into ONE comparison
  * over ONE golden folder.
  *
  * Surfaces lowering/optimizer/normalization changes as a reviewable diff, complementing — never
  * replacing — the result-equivalence gates (which assert values, not structure, so a perf
  * regression or a revert that keeps results identical sails through them).
  *
  *   - **One golden folder**, mirroring the dump folder: `<goldenDir>/<fixture>/<fixture>.<artifact>`
  *     (filenames are fixture-prefixed, the same rule [[debug.StageDump]] applies). An
  *     artifact present → compare; absent → skip (the `requireConnect` pattern). This works
  *     uniformly for `ir.txt`/`optimized.ir.txt` (the normalized/optimized IR, [[debug.LoweredTree]]'s
  *     sibling [[ir.TreePrinter]]) and `*.unresolved-plan.txt` (the post-lowering relation tree) — both are
  *     indented trees.
  *   - **Structural tree compare**, NOT a raw string compare and NOT a stored fingerprint. Both the
  *     golden and the actual are parsed into a sequence of `(depth, label)` (`depth =
  *     leadingSpaces / 2`, blank lines dropped) and compared. This tolerates trailing spaces, blank
  *     lines, and CRLF-vs-LF line endings but catches any real structural or label change, and is
  *     CASE-SENSITIVE on labels (define/table/alias names are meaningful). A mismatch reports the
  *     first differing `(depth, label)` precisely. (Both renderers emit 2-space indentation, so the
  *     `/2` depth is exact.)
  *
  * Bless (`-Dcqldatabricks.blessPlans=true`, or the legacy `-Dcqldatabricks.updateGolden=true`) stages the
  * actual to the BUILD dump dir ([[blessOutDir]]`/<fixture>/<artifact>`) — colocated with the
  * fixture's other dump artifacts, NEVER into `src/`; the user reviews it and moves it into the
  * golden dir themselves. */
object Golden {

  private def blessMode: Boolean =
    sys.props.get("cqldatabricks.blessPlans").contains("true") ||
      sys.props.get("cqldatabricks.updateGolden").contains("true")

  /** Where a blessed artifact is staged — the BUILD dump dir (colocated with the fixture's
    * `cql`/`elm.json`/`ir.txt`/`*.unresolved-plan.txt` dump artifacts under `<dumpDir>/<fixture>/`), NEVER
    * the `src/` golden dir. Reads the same `cqldatabricks.dumpDir` dir [[debug.StageDump]] uses (default
    * `build/dump`, relative to the test working dir `conformance/`). */
  private def blessOutDir: File = new File(sys.props.getOrElse("cqldatabricks.dumpDir", "build/dump"))

  /** Parse an indented tree into its `(depth, label)` skeleton: `depth = leadingSpaces / 2`,
    * `label = line.trim`, blank lines dropped. The normal form both sides are compared in — so the
    * comparison ignores trailing whitespace, blank lines, and line endings while preserving the tree
    * structure and every (case-sensitive) label. */
  def skeleton(text: String): Seq[(Int, String)] =
    text.linesIterator.flatMap { line =>
      val trimmed = line.trim
      if (trimmed.isEmpty) None
      else Some(line.takeWhile(_ == ' ').length / 2 -> trimmed)
    }.toVector

  /** First structural difference between two trees as a human message, or `None` if they are
    * structurally identical. Reports the first differing `(depth, label)` (e.g.
    * "depth 3: expected `Join LeftSemi`, got `Join LeftOuter`"). */
  def firstDiff(golden: String, actual: String): Option[String] = {
    val g = skeleton(golden)
    val a = skeleton(actual)
    val n = math.max(g.length, a.length)
    (0 until n).iterator.flatMap { i =>
      (g.lift(i), a.lift(i)) match {
        case (Some(x), Some(y)) if x == y => None
        case (Some((gd, gl)), Some((ad, al))) =>
          Some(if (gd == ad) s"depth $gd: expected `$gl`, got `$al`"
               else s"expected `depth $gd: $gl`, got `depth $ad: $al`")
        case (Some((gd, gl)), None) => Some(s"actual ended early; expected `depth $gd: $gl`")
        case (None, Some((ad, al))) => Some(s"actual has extra node `depth $ad: $al`")
        case (None, None)           => None
      }
    }.toSeq.headOption
  }

  /** Check `actualText` against the golden `<goldenDir>/<fixture>/<artifact>`.
    *
    *   - bless mode → stage `actualText` to the build dump dir and return `true`;
    *   - golden present → structural tree-compare, throwing a precise `(depth, label)` diff on
    *     mismatch, returning `true` on match;
    *   - golden absent → return `false` (clean skip).
    *
    * Returns whether a comparison happened (so callers can `assume(...)` to skip when absent). */
  def check(goldenDir: File, fixture: String, artifact: String, actualText: String): Boolean = {
    // Filenames are FIXTURE-PREFIXED (`<fixture>.<artifact>`) — the SAME rule [[debug.StageDump]] uses,
    // so the golden dir and the build dump stay name-aligned. The fixture subfolder still groups them.
    val fileName = s"$fixture.$artifact"
    val file = new File(new File(goldenDir, fixture), fileName)
    if (blessMode) {
      val out = new File(new File(blessOutDir, fixture), fileName)
      Files.createDirectories(out.getParentFile.toPath)
      Files.write(out.toPath, actualText.getBytes(StandardCharsets.UTF_8))
      println(s"[golden] blessed '$fixture/$fileName' → ${out.getPath} " +
        s"(review, then move to ${file.getPath})")
      true
    } else if (!file.exists()) {
      false // no committed golden → clean skip
    } else {
      val golden = new String(Files.readAllBytes(file.toPath), StandardCharsets.UTF_8)
      firstDiff(golden, actualText).foreach { d =>
        throw new AssertionError(
          s"golden mismatch for '$fixture/$fileName' ($d). Regenerate with " +
            s"-Dcqldatabricks.blessPlans=true, review the staged artifact, and move it into the golden dir.")
      }
      true
    }
  }
}
