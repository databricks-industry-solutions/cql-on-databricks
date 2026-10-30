package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.passes.{Optimize, Pipeline}
import com.databricks.cql.transpiler.emit.SqlRender
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SERVER-FREE, DATA-FREE lowering invariants over the WHOLE corpus (`CONF-GATE-EMIT-INVARIANT`).
  *
  * WHY THIS EXISTS. Two real mis-lowerings of a condition-interval helper's age arms (an `Age` or age
  * `Range` onset turned into an interval) shipped green through the entire suite,
  * because the DIFFERENTIAL can only catch a bug that some sample row actually reaches:
  *
  *  1. `(choice.low as Quantity)` resolved the `Interval<DateTime>` (Period) slot instead of the
  *     age-Range slot, emitting `CAST(substr(onsetPeriod.start,1,23) AS DECIMAL(28,8))` — a runtime
  *     `CAST_INVALID_INPUT` under ANSI on any Period-onset Condition (`LANG-CHOICE-MODEL`).
  *  2. `Patient.birthDate + <age Quantity>` passed the dimensionless `'1'` as the calendar unit, so
  *     `cql_date_add` returned NULL for EVERY age-based interval — a silent wrong answer.
  *
  * Neither was reachable from the corpus DATA of the time: no NDJSON fixture carried an `onsetAge`,
  * `onsetRange`, `abatementAge` or `abatementRange` (so the age arms were never SELECTED), and the
  * `onsetPeriod`/`abatementPeriod` rows that did exist carried codes that match no value set of any
  * define that projects the offending column, so its eagerly-hoisted cast is never EVALUATED. A
  * data-driven gate cannot see either defect; a gate on the emitted TEXT sees both immediately.
  *
  * WHAT IT ASSERTS, over every measure and every FHIR fixture in the corpus:
  *
  *  - No temporal string is cast to a Quantity's DECIMAL. `substr(<x>.start|.end, 1, n) AS DECIMAL` can
  *    only arise from reading a Period endpoint where a Quantity bound belongs (defect 1). This also
  *    guards the general shape, since `SqlHoist` lifts marked operands to UNCONDITIONAL projection
  *    columns: a cast that a `CASE` would never select is still evaluated for every row, so a
  *    type-inappropriate cast is a hard runtime failure and not a dormant branch.
  *  - No `cql_date_add` receives the dimensionless `'1'` as its unit. A calendar-duration Quantity read
  *    from data carries its UCUM code in `code`/`unit`, so the unit argument must be either a
  *    statically-known literal or a column read — never the `'1'` fallback (defect 2).
  *
  * These are INVARIANTS of the emitted text, so they need no `SparkSession` and no rows, and they cover
  * every define in every measure rather than only the ones sample data happens to exercise
  * (`CORE-NO-SILENT-MISLOWER`). Pairs with [[SqlDifferentialSpec]]: that gate proves the VALUES agree
  * with the Java reference where data reaches; this one proves the emitted SQL is well-typed everywhere. */
@RunWith(classOf[JUnitRunner])
final class TemporalLoweringInvariantSpec extends AnyFunSuite {

  /** Lower a case to SQL text with no session in scope (see [[OfflineCompileSpec]] for why this is
    * structurally possible: the SQL emitter is Spark-free). */
  private def lower(c: Differential.Case, hasTerminology: Boolean): Seq[(String, String)] = {
    val lib = Differential.buildLibrary(c)
    val config = Differential.withConditionalUnion(c.library, Differential.withGrainFirst(c.library, Config.defaults))
    val lowered = SqlFrameLowering.lower(Optimize.run(lib, config), SqlEvalEnv(c.context, config, terminology = hasTerminology))
    lowered.script.statements.map { st =>
      val sql = SqlRender.render(st)
      (sql.linesIterator.take(2).mkString(" ").trim.take(120), sql)
    }
  }

  private def measureCase(md: Corpus.MeasureDef): Differential.Case = Corpus.offlineCase(md)

  /** A temporal string (a `substr(<period endpoint>, 1, n)` normalization) cast to a Quantity's DECIMAL:
    * a dateTime read where a calendar-duration NUMBER belongs. */
  private val temporalCastToDecimal =
    """substr\([^()]*\.(?:start|`end`|end)[^()]*\)\s+AS DECIMAL""".r

  /** The unit argument of each `cql_date_add(point, count, unit)` call, located by a balanced-paren scan.
    * A regex cannot do this: the count argument itself contains a Quantity `named_struct(…, 'unit', '1')`
    * whose nested `'1'` a naive pattern reports as the call's own unit. */
  private def dateAddUnits(sql: String): Seq[String] = {
    val out = Seq.newBuilder[String]
    var from = sql.indexOf("cql_date_add(")
    while (from >= 0) {
      var depth = 0
      var i = from + "cql_date_add(".length
      val argStarts = scala.collection.mutable.ArrayBuffer(i)
      var closed = false
      while (!closed && i < sql.length) {
        sql.charAt(i) match {
          case '(' => depth += 1
          case ')' => if (depth == 0) closed = true else depth -= 1
          case ',' => if (depth == 0) argStarts += (i + 1)
          case _   => ()
        }
        i += 1
      }
      if (argStarts.size >= 3) out += sql.substring(argStarts(2), i - 1).trim
      from = sql.indexOf("cql_date_add(", from + 1)
    }
    out.result()
  }

  private def check(label: String, statements: Seq[(String, String)]): Unit = {
    val badCasts = statements.flatMap { case (name, sql) =>
      temporalCastToDecimal.findAllIn(sql).map(m => s"$label / $name: $m").toSeq
    }
    assert(badCasts.isEmpty,
      s"a temporal endpoint is cast to a Quantity DECIMAL — the age-arm slot dispatch regressed " +
        s"(LANG-CHOICE-MODEL); ${badCasts.size} occurrence(s):\n  ${badCasts.take(5).mkString("\n  ")}")

    val dimensionless = statements.flatMap { case (name, sql) =>
      dateAddUnits(sql).filter(_ == "'1'").map(_ => s"$label / $name")
    }
    assert(dimensionless.isEmpty,
      s"cql_date_add received the dimensionless unit '1' — a calendar-duration Quantity's unit was " +
        s"baked instead of read, so the result is unconditionally NULL (CORE-NO-SILENT-MISLOWER); " +
        s"${dimensionless.size} occurrence(s):\n  ${dimensionless.distinct.take(5).mkString("\n  ")}")
  }

  Corpus.linkMeasures.foreach { md =>
    test(s"${md.library}: emitted temporal SQL is well-typed (CONF-GATE-EMIT-INVARIANT)") {
      check(md.library, lower(measureCase(md), hasTerminology = true))
    }
  }

  test("every FHIR fixture: emitted temporal SQL is well-typed (CONF-GATE-EMIT-INVARIANT)") {
    val libraries = Option(new java.io.File(Paths.cqlDir, "fhir").listFiles())
      .getOrElse(Array.empty).filter(_.getName.endsWith(".cql"))
      .map(_.getName.stripSuffix(".cql")).sorted.toSeq
    assert(libraries.nonEmpty, "no FHIR fixtures found — the sweep would be vacuous")
    // `translateCorpus` SKIPS a fixture whose CQL fails to translate (it logs and reports
    // "translated N of M") rather than failing the build, so an authored fixture can silently never be
    // tested. Enumerating from the authored SOURCES and requiring the ELM makes that loud here
    // (`CORE-NO-SILENT-MISLOWER`) — a fixture that does not compile is a broken fixture, not an absent one.
    val untranslated = libraries.filterNot(l => Paths.elm(l).isFile)
    assert(untranslated.isEmpty,
      s"authored FHIR fixture(s) produced no ELM — translation failed and `translateCorpus` skipped them, " +
        s"so they are silently untested: ${untranslated.mkString(", ")}")
    libraries.foreach { lib =>
      check(lib, lower(Differential.Case(lib, Context.Patient), hasTerminology = true))
    }
  }
}
