package com.databricks.cql.it

import com.databricks.cql.passes.Optimize
import com.databricks.cql.transpiler.emit.SqlRender
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** OPT-IN, SPARK-FREE emitted-SQL SIZE probe (the "fast
  * proxy for analysis cost"): lower each corpus measure to its SQL script and report the structural
  * levers that track the single-core fixed cost — total emitted bytes, statement count, and the
  * JOIN / LEFT SEMI / CASE / cql_* operator counts per the doc's evidence table.
  *
  * This is a MEASUREMENT tool for the plan-shape optimization on `perf-test`, not a gate: it does NOT
  * touch Spark (no Connect server, no execution — only `SqlFrameLowering.lower`, which emits text). It
  * SKIPS GREEN unless `-Dcqldatabricks.emitSize=true`. Config:
  *   - `-Dcqldatabricks.emitSizeMeasure=<library>` — one measure library name, or `ALL` (default `ALL`).
  *   - `-Dcqldatabricks.materialize.mode=all|costly|none` — the same knob the differential/scale path reads
  *     (default `all`, the gate's mode); the doc's baseline table used `costly`.
  *   - `-Dcqldatabricks.emitSizePerStmt=true` — also print the per-statement byte breakdown (fattest first). */
@RunWith(classOf[JUnitRunner])
final class EmitSizeSpec extends AnyFunSuite {

  private val enabled = sys.props.get("cqldatabricks.emitSize").contains("true")

  private def selected: Seq[String] = {
    val sel = sys.props.getOrElse("cqldatabricks.emitSizeMeasure", "ALL").trim
    if (sel.equalsIgnoreCase("ALL")) Corpus.scaleMeasures.keys.toSeq.sorted else Seq(sel)
  }

  /** Count non-overlapping occurrences of `needle` in `hay` (case-sensitive; SQL keywords are upper-cased
    * by the renderer and the UDF names are lower-case `cql_`, so plain substring counting matches the
    * doc's methodology). */
  private def count(hay: String, needle: String): Int = {
    var i = 0; var n = 0
    while ({ i = hay.indexOf(needle, i); i >= 0 }) { n += 1; i += needle.length }
    n
  }

  test("emitted-SQL size probe (opt-in, -Dcqldatabricks.emitSize=true)") {
    assume(enabled, "opt-in size probe — set -Dcqldatabricks.emitSize=true (Spark-free; skips by default)")
    // Never elide a statement while measuring: raise the per-statement render cap so the full script
    // renders (the default gate would clip a within-define blowup to a placeholder).
    val key = "cqldatabricks.sqlMaxStmtBytes"; val prev = sys.props.get(key)
    sys.props(key) = Long.MaxValue.toString
    val out = new java.io.File("build", "emit-size.txt")
    Option(out.getParentFile).foreach(_.mkdirs())
    val sb = new StringBuilder
    def line(s: String): Unit = { info(s); sb ++= s; sb += '\n' }
    try {
      val perStmt = sys.props.get("cqldatabricks.emitSizePerStmt").contains("true")
      val mode    = sys.props.getOrElse("cqldatabricks.materialize.mode", "all")
      line(f"# emitted-SQL size probe  (materialize.mode=$mode)")
      line(f"${"measure"}%-8s ${"stmts"}%6s ${"KB"}%8s ${"JOIN"}%6s ${"SEMI"}%6s ${"CASE"}%6s ${"cql_*"}%7s")
      selected.foreach { name =>
        val d = Corpus.scaleMeasures.getOrElse(name,
          fail(s"unknown measure '$name' — known: ${Corpus.scaleMeasures.keys.toSeq.sorted.mkString(", ")}, or ALL"))
        val c         = d.entry.mk()
        val lib       = Differential.buildLibrary(c)
        val config    = Differential.evalConfig(c.library)
        val optimized = Optimize.run(lib, config)
        val env       = SqlEvalEnv(c.context, config).describing
        val lowered   = SqlFrameLowering.lower(optimized, env)

        val rendered = lowered.script.statements.map(s => s.name -> SqlRender.render(s))

        sys.props.get("cqldatabricks.emitSizeDumpSql").filter(_.equalsIgnoreCase(name)).foreach { _ =>
          val f = new java.io.File("build", s"$name.sql")
          val w = new java.io.FileWriter(f, false)
          try w.write(rendered.map { case (n, sql) => s"-- ===== $n =====\n$sql" }.mkString(";\n\n"))
          finally w.close()
        }
        val whole    = rendered.map(_._2).mkString(";\n\n")
        val bytes    = whole.length
        val joins    = count(whole, "JOIN")
        val semi     = count(whole, "LEFT SEMI")
        val cases    = count(whole, "CASE")
        val udfs     = count(whole, "cql_")
        line(f"$name%-8s ${lowered.script.statements.size}%6d ${bytes / 1024.0}%8.1f $joins%6d $semi%6d $cases%6d $udfs%7d")

        if (perStmt) {
          line(s"  --- $name per-statement (fattest first) ---")
          rendered.map { case (n, sql) => (n, sql.length, count(sql, "cql_date_add") + count(sql, "cql_temporal_step")) }
            .sortBy(-_._2).take(15).foreach { case (n, len, temporal) =>
              line(f"    ${len / 1024.0}%8.1f KB  temporal=$temporal%4d  $n")
            }
        }
      }
    } finally {
      prev match { case Some(p) => sys.props(key) = p; case None => sys.props.remove(key) }
      val w = new java.io.FileWriter(out, false)
      try w.write(sb.toString) finally w.close()
    }
  }
}
