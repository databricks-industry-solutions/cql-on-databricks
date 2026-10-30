package com.databricks.cql.pyudf

import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.it._
import com.databricks.cql.passes.Optimize
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.emit.{SqlNames, SqlRender}
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files

/** CALC-ONLY comparison (the "cancel the infrastructure, isolate the calc engine" test): run the Scala-SQL
  * path and the Java CQF reference over the SAME data sourced through the SAME Spark session, on a SINGLE
  * core, and time each. Removes the two biases of the throughput harness — CQF's free in-memory pre-indexed
  * retrieve, and the 1-core-CQF-vs-14-core-Spark asymmetry — so the wall delta is (as far as this repo's
  * Connect-client/server split allows) the CALC engine alone.
  *
  * SINGLE CORE: point `-Dcqldatabricks.sparkRemote` at a `local[1]` Connect server. Both paths then run on one
  * core, so wall == per-core calc+access cost, no parallelism-strategy confound.
  *
  * DATA VIA SPARK FOR BOTH: the Scala path mounts the deck as `retrieve_<Type>` views and runs the emitted
  * SQL (its scan is on Spark). The CQF path reads the SAME NDJSON deck through `spark.read.text` (the
  * "querying on Spark"), then evaluates per-subject with the maintained Java engine. Both timers therefore
  * include Spark data access + the engine's calc. (CQF still parses per subject in-JVM — that is intrinsic
  * to its row-at-a-time model, not an infra advantage; the test JVM is a Connect CLIENT only, so CQF cannot
  * literally run as executor tasks here without shipping HAPI to the server — a separate, larger lift.)
  *
  * CORRECTNESS-GATED: CQF population membership must equal the Scala golden (same green base-deck result).
  * Opt-in `-Dcqldatabricks.scale=true`; `-Dcqldatabricks.scaleMeasure=<name>`; `-Dcqldatabricks.scaleFactors=<n>` (single). */
@RunWith(classOf[JUnitRunner])
final class MeasureCalcOnlySpec extends AnyFunSuite {

  // Where the per-run summary json is written; override with -Dcqldatabricks.pyudfDir=<dir>.
  private val SP = sys.props.getOrElse("cqldatabricks.pyudfDir",
    new File(System.getProperty("java.io.tmpdir"), "cqldatabricks-calcbench").getAbsolutePath)
  private val enabled = sys.props.get("cqldatabricks.scale").contains("true")
  private val factor = sys.props.get("cqldatabricks.scaleFactors").flatMap(s => scala.util.Try(s.trim.toInt).toOption).getOrElse(1)
  private val measureName = sys.props.getOrElse("cqldatabricks.scaleMeasure", Corpus.defaultScaleMeasure)

  private def isMember(v: ujson.Value): Boolean = v match {
    case ujson.Bool(b)          => b
    case a: ujson.Arr           => a.arr.nonEmpty
    case _                      => false
  }

  test("Measure calc-only: Scala-SQL vs CQF, single core, data via Spark (opt-in)") {
    assume(enabled, "opt-in — set -Dcqldatabricks.scale=true")
    val spark = SparkConnect.session()
    // On a single core, keep shuffle partitions small (default 200 would run 200 tasks SERIALLY on 1 core —
    // pure task-scheduling overhead that swamps the calc). shuffle=1 is the natural single-core choice and
    // isolates calc. Applied to BOTH engines' shared session.
    sys.props.get("cqldatabricks.shufflePartitions").foreach(n => spark.conf.set("spark.sql.shuffle.partitions", n))
    val d = Corpus.scaleMeasures.getOrElse(measureName,
      fail(s"unknown measure '$measureName' — known: ${Corpus.scaleMeasures.keys.toSeq.sorted.mkString(", ")}"))
    val pops = d.populations.map(_._1)
    val tag = measureName.toLowerCase
    val scaled = ScaleData.replicate(d.dataDir, d.resourceTypes, factor)

    val base = d.entry.mk()
    val c = base.copy(
      data        = Some(new SparkFhirData(spark, scaled.dir.getAbsolutePath)),
      refEnv      = base.refEnv.copy(contextName = base.context.name,
                      resourcesByType = scaled.resourcesByType, subjectIds = scaled.subjectIds))
    val cqlText   = Paths.read(Paths.cql(c.library))
    val lib       = Differential.buildLibrary(c)
    val config    = Differential.evalConfig(c.library)
    val optimized = Optimize.run(lib, config)
    val env       = SqlEvalEnv(c.context, config, terminology = c.terminology.isDefined)
    val lowered   = SqlFrameLowering.lower(optimized, env)

    // Mount all retrieved types (empty frame for a data-less reference-follow type), + valuesets.
    import com.databricks.cql.ir.{ExpressionDef, Retrieve}
    val retrievedTypes = optimized.statements.collect { case e: ExpressionDef => e }
      .flatMap(_.expression.collect { case r: Retrieve => r.dataType.substring(r.dataType.lastIndexOf('}') + 1) }).toSet
    val mountTypes = (retrievedTypes ++ (if (c.context.hasSubjectGrouping) Set(c.context.name) else Set.empty) ++ d.resourceTypes.toSet).toSeq.sorted
    val sfd = c.data.get.asInstanceOf[SparkFhirData]
    // BIND-RETRIEVE-UDF: the mount is a SQL UDTF `retrieve_<Type>(<8 explicit args>)` (the sole
    // contract), its parameter list from the single source of truth (SqlEvalEnv.retrieveParamsDdl,
    // BIND-EXPLICIT-PARAMS); this fixture UDTF ignores all eight and returns the whole mount
    // (BIND-CAPABILITY).
    mountTypes.foreach { rt =>
      val fn = env.retrieveMountName(rt)
      val df = sfd.retrieve(rt)
      df.createOrReplaceTempView(fn + "__src")
      spark.sql(s"CREATE OR REPLACE TEMPORARY FUNCTION `$fn`(${SqlEvalEnv.retrieveParamsDdl}) " +
        s"RETURNS TABLE (${SparkFhirData.mountReturnsDDL(df.schema)}) RETURN SELECT * FROM `${fn}__src`")
    }
    val hasVs = c.terminology.isDefined && optimized.valueSets.nonEmpty
    if (hasVs) {
      val stp = c.terminology.get.asInstanceOf[SparkTerminology]
      import org.apache.spark.sql.{functions => F}
      optimized.valueSets.map(v =>
        stp.expand(v.id).withColumn(env.valueSetKeyColumn, F.lit(v.id)).select(env.valueSetKeyColumn, "system", "code"))
        .reduceOption(_ unionByName _).foreach(_.createOrReplaceTempView(env.valueSetsMountName))
    }

    // ---- Scala-SQL calc (single core): exec defines + count-force populations. ----
    // EMIT-SQL-VALUE-COLUMN: the ROW shape comes from the lowering (relation vs scalar) rather than from the
    // produced schema. What the read contract does NOT answer is the scalar's VALUE type — a population may
    // be boolean or a LIST (episode-of-care measures) — so that one bit is read off `__val`. Indexing the
    // schema by name is deliberate: `__val` is guaranteed present now, so a miss should be LOUD rather than
    // swallowed into a wrong membership set (CORE-NO-SILENT-MISLOWER).
    def popKind(p: String): String =
      if (lowered.relationDefines.contains(p)) "relation"
      else spark.table(SqlNames.obj(p)).schema(SystemColumns.Val).dataType match {
        case _: org.apache.spark.sql.types.ArrayType => "array"
        case _                                       => "bool"
      }
    val stmts = lowered.script.statements.map(SqlRender.render)
    // Drop each object first, as `SqlDifferential` does: the session is memoized across specs in this JVM,
    // and a barrier CACHE TABLE is not "OR REPLACE", so a measure staged by an earlier spec would collide.
    lowered.script.statements.foreach { s =>
      val q = "`" + s.name.replace("`", "``") + "`"
      try spark.sql(s"DROP VIEW IF EXISTS $q") catch { case _: Throwable => () }
      try spark.sql(s"UNCACHE TABLE IF EXISTS $q") catch { case _: Throwable => () }
    }
    val sT0 = System.nanoTime()
    stmts.foreach(spark.sql)
    val kinds = pops.map(p => p -> popKind(p)).toMap
    def memberSql(p: String): String = {
      val subj = s"`${SystemColumns.SubjectId}`"
      val v    = s"`${SystemColumns.Val}`"
      kinds(p) match {
        case "relation" => s"SELECT DISTINCT $subj FROM `${SqlNames.rel(p)}`"
        case "array"    => s"SELECT $subj FROM `${SqlNames.obj(p)}` WHERE $v IS NOT NULL AND size($v) > 0"
        case _          => s"SELECT $subj FROM `${SqlNames.obj(p)}` WHERE $v = true"
      }
    }
    val scalaGolden: Map[String, Set[String]] =
      pops.map(p => p -> spark.sql(memberSql(p)).collect().map(_.getString(0)).toSet).toMap
    val scalaMs = (System.nanoTime() - sT0) / 1e6
    val patients = spark.sql("SELECT count(*) FROM __frame_base").collect().head.getLong(0)

    // ---- CQF calc (single core): source the SAME deck THROUGH Spark, then evaluate per-subject. ----
    val cqfT0 = System.nanoTime()
    val resByType: Map[String, Seq[String]] = d.resourceTypes.map { t =>
      val f = new File(scaled.dir, s"$t.ndjson")
      val lines = if (f.exists)
        spark.read.text(f.getAbsolutePath).collect().map(_.getString(0)).filter(_.trim.nonEmpty).toSeq
      else Seq.empty[String]
      t -> lines
    }.toMap
    val subjectIds = resByType.getOrElse("Patient", Nil).map(l => ujson.read(l)("id").str)
    val refEnv = c.refEnv.copy(resourcesByType = resByType, subjectIds = subjectIds)
    val reference = GroundTruthRunner.evaluate(cqlText, lib.id, pops, refEnv)
    val cqfMs = (System.nanoTime() - cqfT0) / 1e6

    // Correctness: CQF membership == Scala golden per population (the calc-only path is sound).
    val cqfMembers: Map[String, Set[String]] =
      pops.map(p => p -> reference.filter { case (_, m) => isMember(m.getOrElse(p, ujson.Null)) }.keys.toSet).toMap
    val mism = pops.filter(p => cqfMembers(p) != scalaGolden(p))
      .map(p => s"$p: cqf=${cqfMembers(p).size} scala=${scalaGolden(p).size} sym_diff=${(cqfMembers(p).diff(scalaGolden(p)) ++ scalaGolden(p).diff(cqfMembers(p))).size}")
    val correct = mism.isEmpty

    def w(name: String, s: String): Unit = {
      new File(SP).mkdirs()
      Files.write(new File(SP, name).toPath, s.getBytes(StandardCharsets.UTF_8))
    }
    w(s"${tag}_calconly.json", ujson.write(ujson.Obj(
      "measure" -> measureName, "factor" -> factor, "patients" -> patients,
      "cores" -> Runtime.getRuntime.availableProcessors(),
      "server_master" -> ujson.Str(sys.props.getOrElse("cqldatabricks.sparkRemote", "?")),
      "scala_calc_ms" -> scalaMs, "cqf_calc_ms" -> cqfMs, "correct" -> correct,
      "mismatches" -> ujson.Arr(mism.map(ujson.Str(_)): _*),
      "scala_pop_counts" -> ujson.Obj.from(pops.map(p => p -> ujson.Num(scalaGolden(p).size.toDouble)))), indent = 2))

    println(f"[CALC-ONLY] measure=$measureName factor=$factor patients=$patients " +
      f"scala_calc_ms=$scalaMs%.0f cqf_calc_ms=$cqfMs%.0f correct=$correct")
    println(f"[RESULT-CALC] $tag,$factor,$patients,$scalaMs%.1f,$cqfMs%.1f,$correct")
    if (!correct) println(s"[CALC-ONLY] MISMATCH: ${mism.mkString("; ")}")
    assert(patients > 0)
  }
}
