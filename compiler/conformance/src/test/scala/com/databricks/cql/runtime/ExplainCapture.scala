package com.databricks.cql.runtime

import org.apache.spark.sql.DataFrame

/** Capture `df.explain(mode)` as a String, best-effort, without leaking the (often multi-MB) plan to
  * the console (DEBUG-PLAN-DUMP). The Connect client PRINTS the plan to `System.out`, which
  * `Console.withOut` alone does NOT intercept, so we redirect BOTH the JVM streams and Scala's
  * `Console` to a buffer (restored in `finally`). Any failure (e.g. the server cannot analyze a huge
  * plan) yields `None`, never throws — a debug capture must not perturb the run it observes.
  *
  * Used by the SQL differential ([[SqlDifferential]]) to capture the analyzed/optimized/physical plan of
  * the compiled script's target (`resolved-plan.txt`) via `spark.sql("SELECT * FROM <target>").explain`. */
object ExplainCapture {

  /** `df.explain(mode)` as a String, or `None` on any failure. `mode` is Spark's explain mode
    * (`"cost"` for the analyzed/optimized/physical plan with size estimates). A SERVER round-trip. */
  def capture(df: DataFrame, mode: String = "cost"): Option[String] = {
    val buf = new java.io.ByteArrayOutputStream()
    val ps = new java.io.PrintStream(buf, true, "UTF-8")
    val (savedOut, savedErr) = (System.out, System.err)
    try {
      System.setOut(ps); System.setErr(ps)
      Console.withOut(ps)(Console.withErr(ps)(df.explain(mode)))
    } catch { case _: Throwable => () }
    finally { System.setOut(savedOut); System.setErr(savedErr) }
    val s = buf.toString("UTF-8").trim
    if (s.nonEmpty) Some(s) else None
  }
}
