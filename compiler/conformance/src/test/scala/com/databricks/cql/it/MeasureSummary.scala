package com.databricks.cql.it

import java.io.File

/** A measure-AGNOSTIC "pseudo measure report": a plain summary of a measure's execution results,
  * keyed by the population define names the engine actually computes — NOT a FHIR `MeasureReport`.
  *
  * A FHIR `MeasureReport` is intentionally avoided here: population roots are wired as plain CQL define
  * names (see `Corpus.MeasureDef.roots`), and not
  * every measure is FHIR-based. Keying the report by define name makes it work for any measure,
  * FHIR or not; a FHIR-shaped report would be an optional second tier layered on top.
  *
  * The input is the exact per-(subject, define) map every measure gate already collects
  * ([[SqlDifferential.engineResults]]). Each define value is reduced to a 0/1 population `count` with
  * the same rule [[MeasureScoreSpec]] scores by (a true boolean, or a non-empty list, is membership).
  * Off by default; emitted from [[MeasureScoreSpec]] under `-Dcqldatabricks.measureReport=true`. */
object MeasureSummary {

  /** Reduce an engine define value to a population `count`: a true boolean (or a non-empty list) is
    * membership (1); false/null/empty is non-membership (0). Mirrors `MeasureScoreSpec.score`. */
  def score(v: ujson.Value): Int = v match {
    case ujson.True   => 1
    case a: ujson.Arr => if (a.arr.nonEmpty) 1 else 0
    case _            => 0
  }

  /** Render the summary text.
    *
    * @param measure  display name (the measure library name).
    * @param defines  population define names, in display order (the measure's wired roots).
    * @param engine   `subjectId -> (defineName -> evaluated value)` from [[SqlDifferential.engineResults]].
    * @param expected optional scorekey for a side-by-side: `subjectId -> (defineName -> count)`.
    *                 When non-empty an `Expected` column (and a `match`/`MISMATCH` marker) is shown.
    * @param detail   when true, append a per-patient 0/1 grid (one row per subject).
    */
  def render(
      measure: String,
      defines: Seq[String],
      engine: Map[String, Map[String, ujson.Value]],
      expected: Map[String, Map[String, Int]] = Map.empty,
      detail: Boolean = false): String = {

    val subjects = (engine.keySet ++ expected.keySet).toSeq.sorted
    val hasExpected = expected.nonEmpty

    // Per-define engine count (sum of 0/1 over subjects) and, when available, the expected total.
    def engCount(subj: String, d: String): Int = engine.get(subj).flatMap(_.get(d)).map(score).getOrElse(0)
    def expCount(subj: String, d: String): Int = expected.get(subj).flatMap(_.get(d)).getOrElse(0)

    val nameW = (defines.map(_.length) :+ "Population".length).max
    def padR(s: String, w: Int): String = if (s.length >= w) s else s + " " * (w - s.length)
    def padL(s: String, w: Int): String = if (s.length >= w) s else " " * (w - s.length) + s
    val sb = new StringBuilder

    sb.append(s"=== Measure execution summary: $measure (${subjects.size} subjects) ===\n")
    if (hasExpected)
      sb.append(s"${padR("Population", nameW)}  ${padL("Engine", 8)}  ${padL("Expected", 8)}\n")
    else
      sb.append(s"${padR("Population", nameW)}  ${padL("Engine", 8)}\n")

    defines.foreach { d =>
      val eng = subjects.map(engCount(_, d)).sum
      if (hasExpected) {
        val exp = subjects.map(expCount(_, d)).sum
        val mark = if (eng == exp) "" else "  <- MISMATCH"
        sb.append(s"${padR(d, nameW)}  ${padL(eng.toString, 8)}  ${padL(exp.toString, 8)}$mark\n")
      } else {
        sb.append(s"${padR(d, nameW)}  ${padL(eng.toString, 8)}\n")
      }
    }

    if (detail) {
      sb.append("\n--- per patient (1 = in population) ---\n")
      sb.append(s"${padR("subject", 40)}  ${defines.mkString("  ")}\n")
      subjects.foreach { subj =>
        val cells = defines.map { d =>
          val e = engCount(subj, d)
          if (hasExpected && e != expCount(subj, d)) s"$e!" else e.toString
        }
        sb.append(s"${padR(subj, 40)}  ${cells.mkString("  ")}\n")
      }
    }
    sb.toString
  }

  /** Render the ACTUAL typed result data as JSON: `{ subjectId: { defineName: <value>, ... }, ... }`.
    * The values are the engine's evaluated defines verbatim ([[SqlDifferential.engineResults]]) — booleans,
    * dates, and full resource lists (e.g. "Visits During Period") — NOT the 0/1 scoring in [[render]].
    * Every define the engine computed is included (population roots and intermediates), with the named
    * population defines ordered first per subject and the rest alphabetical, for readability. */
  def renderJson(
      defines: Seq[String],
      engine: Map[String, Map[String, ujson.Value]]): String = {
    val root = ujson.Obj()
    engine.keys.toSeq.sorted.foreach { subj =>
      val byDefine = engine(subj)
      val ordered = defines.filter(byDefine.contains) ++ byDefine.keys.filterNot(defines.contains).toSeq.sorted
      val o = ujson.Obj()
      ordered.foreach(d => o(d) = byDefine(d))
      root(subj) = o
    }
    ujson.write(root, indent = 2)
  }

  /** The directory the report artifacts are written to (a build dir only — never `src/`). Mirrors the
    * debug-dump layout: `<dumpDir>/<measure>/` when `-Dcqldatabricks.dumpDir` is set (so the report lands
    * beside the other per-fixture dump artifacts), else `compiler/conformance/build/measure-reports/<measure>/`. */
  private def outDir(measure: String): File =
    sys.props.get("cqldatabricks.dumpDir").map(_.trim).filter(_.nonEmpty) match {
      case Some(dir) => new File(dir, measure)
      case None      => new File("compiler/conformance/build/measure-reports", measure)
    }

  /** Render, print the summary table to stdout, and write two sibling artifacts to [[outDir]]:
    * `measure-report.txt` (the human summary, [[render]]) and `measure-report.json` (the ACTUAL typed
    * result data, [[renderJson]]). Returns the rendered table text. */
  def emit(
      measure: String,
      defines: Seq[String],
      engine: Map[String, Map[String, ujson.Value]],
      expected: Map[String, Map[String, Int]] = Map.empty,
      detail: Boolean = false): String = {
    val text = render(measure, defines, engine, expected, detail)
    print(text)
    val dir = outDir(measure)
    Paths.write(new File(dir, "measure-report.txt"), text)
    Paths.write(new File(dir, "measure-report.json"), renderJson(defines, engine))
    text
  }
}
