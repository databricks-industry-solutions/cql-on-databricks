package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context, DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.harness.GroundTruthRunner
import com.databricks.cql.ir.{Library, Retrieve}
import com.databricks.cql.lower.ThreadCpu
import com.databricks.cql.passes.{Optimize, ParameterScope, RetrieveDemand, TypeEncoding}
import com.databricks.cql.retrieve.RetrieveHints
import com.databricks.cql.runtime.ExplainCapture
import com.databricks.cql.service.{CompileService, PipelinePlan}
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.emit.{SqlNames, SqlRender}
import com.databricks.cql.transpiler.lower.{CallSite, SqlEvalEnv, SqlFrameLowering}
import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.scalatest.Assertions._

import scala.collection.mutable

/** The SQL-PATH differential (EMIT-SQL-EXECUTE, Phase 5): lower a fixture to a Spark SQL SCRIPT
  * ([[SqlFrameLowering]]), MOUNT its data as the `retrieve_<Type>` / `valueset_<id>` views the script
  * references (the runtime contract — here fed from the SAME sample-data `DataProvider`/`TerminologyProvider`
  * the proto path uses), run each statement via `spark.sql`, and diff the collected per-(subject, define)
  * results against the Java CQF reference — the SAME oracle as [[Differential]].
  *
  * It reuses [[Differential]]'s library build + config so the two paths lower the identical optimized IR;
  * only the BACK END differs (SQL text vs a Connect proto). A fixture whose SQL lowering is not yet ported
  * (a rejected node family) raises [[UnsupportedNode]] and is CANCELLED (skipped), not failed — so a run
  * yields a clean PASS / SKIP(unsupported) / FAIL(diff or exec) matrix of what the SQL path verifies today. */
object SqlDifferential {

  private def localName(dataType: String): String = dataType.substring(dataType.lastIndexOf('}') + 1)

  // ---------------------------------------------------------------- SQL-transpiler dump artifacts

  private def dumpDir(c: Differential.Case): Option[java.io.File] =
    sys.props.get("cqldatabricks.dumpDir").filter(_.nonEmpty).map(d => new java.io.File(d, c.library))

  private def writeArtifact(base: java.io.File, fixture: String, ext: String, body: String): Unit = {
    base.mkdirs()
    java.nio.file.Files.write(new java.io.File(base, s"$fixture.$ext").toPath,
      body.getBytes(java.nio.charset.StandardCharsets.UTF_8))
  }

  /** Render a script to SQL text SAFELY for a dump: any statement whose STRUCTURAL size
    * (`SqlRender.sizeOf`) exceeds `maxBytes` is ELIDED to a one-line placeholder rather than rendered, so
    * a within-define blowup (one measured at ~127 MB) can never OOM the dump. The small
    * statements render in full; `diagnostics.txt` carries the exact per-statement sizes. */
  private def renderScriptSafe(lowered: SqlFrameLowering.SqlLoweredLibrary, maxBytes: Long = 8L * 1024 * 1024): String =
    lowered.script.statements.map { s =>
      val n = SqlRender.sizeOf(s)
      if (n > maxBytes) f"-- [$n%,d B — ELIDED > ${maxBytes}%,d B render cap; see diagnostics.txt] ${s.name}"
      else SqlRender.render(s)
    }.mkString(";\n\n")

  /** Dump the diagnostics for a fixture that FAILED the emit-size gate (`e`): re-lower with the gate lifted
    * (a huge per-statement cap via the `cqldatabricks.sqlMaxStmtBytes` prop) so we obtain the `lowered` script,
    * then write the CQL, the structural per-statement size breakdown (`diagnostics.txt`), and a size-capped
    * `.sql`. Best-effort and server-free; restores the prop and swallows any secondary failure. */
  private def dumpSizeBlocked(c: Differential.Case, optimized: Library, env: SqlEvalEnv, e: UnsupportedNode): Unit =
    dumpDir(c).foreach { dir =>
      val key = "cqldatabricks.sqlMaxStmtBytes"; val prev = sys.props.get(key)
      try {
        sys.props(key) = Long.MaxValue.toString
        val lowered = SqlFrameLowering.lower(optimized, env)
        writeArtifact(dir, c.library, "cql", Paths.read(Paths.cql(c.library)))
        writeArtifact(dir, c.library, "sql", renderScriptSafe(lowered))
        writeArtifact(dir, c.library, "diagnostics.txt",
          s"# SQL SIZE GATE TRIPPED (not executed): ${e.detail}\n\n" + diagnosticsReport(c.library, lowered, None))
      } catch { case _: Throwable => () }
      finally prev match { case Some(p) => sys.props(key) = p; case None => sys.props.remove(key) }
    }


  /** Force-compute one object and return `(rowCount, elapsedMs)` — a per-materialization runtime for the
    * diagnostics report (a `count(*)` executes the view/table's whole DAG). Best-effort. */
  private def timeCount(spark: SparkSession, name: String): (Long, Double) = {
    val q = "`" + name.replace("`", "``") + "`"
    val t0 = System.nanoTime()
    val rows = try spark.sql(s"SELECT count(*) FROM $q").collect().head.getLong(0)
               catch { case _: Throwable => -1L }
    (rows, (System.nanoTime() - t0) / 1e6)
  }

  /** The COMBINED diagnostics report (EMIT-SQL-EXECUTE): one row per emitted statement — its
    * materialization KIND (`view` vs `cache_table`/`table` barrier), the single-term REASON the classifier
    * assigned, its SQL byte size, its row count, and its per-materialization RUNTIME (ms to force-compute
    * it). Merges the old size + materialization reports and adds runtimes. */
  private def diagnosticsReport(fixture: String, lowered: SqlFrameLowering.SqlLoweredLibrary,
                                spark: Option[SparkSession]): String = {
    val decisionBy = lowered.decisions.map(d => d.name -> d).toMap
    // Byte size is the CHEAP structural estimate (`SqlRender.sizeOf`) — never materializes the (possibly
    // huge) string, so this report is safe to write even for a define that blew up. `spark` present =>
    // also force-compute per-statement rows/runtime (post-execution); absent => sizes only (written
    // BEFORE execution, so the breakdown survives even if a later statement stalls).
    val rows = lowered.script.statements.map { s =>
      val bytes = SqlRender.sizeOf(s)
      val (n, ms) = spark.map(sp => timeCount(sp, s.name)).getOrElse((-1L, 0.0))
      val d = decisionBy.get(s.name)
      (s.name, d.map(_.kind).getOrElse("view"), d.map(_.reason).getOrElse("-"), bytes, n, ms)
    }
    val totalBytes = rows.map(_._4).sum
    val barriers   = rows.count(_._2 != "view")
    val phase = if (spark.isDefined) "post-execution (with runtimes)" else "pre-execution (sizes only)"
    val sb = new StringBuilder
    sb ++= s"# $fixture — SQL transpiler diagnostics (EMIT-SQL-EXECUTE) — $phase\n"
    sb ++= f"# target=${lowered.script.target}   statements=${rows.size}   est_total_bytes=$totalBytes%,d\n"
    sb ++= "# bytes = STRUCTURAL estimate (SqlRender.sizeOf, monotone in the real rendered size), NOT the exact byte count.\n"
    sb ++= "# kind: view = logical (Catalyst re-derives/shares); cache_table/table = a materialization barrier.\n"
    sb ++= "# reason (priority order): population > valueset > date > pushdown > passthru > filter > scalar.\n"
    sb ++= "#   population=always materialize; valueset/date/filter=applies a filter, materialize;\n"
    sb ++= "#   passthru=bare/unfiltered retrieve, pushdown=filter flows through upstream, scalar=subject-grained → view.\n"
    sb ++= "# rows/ms = force-computed count(*) over the object (post-execution only; -1 = pending/not run).\n\n"
    sb ++= f"${"kind"}%-12s  ${"reason"}%-10s  ${"est_bytes"}%12s  ${"rows"}%6s  ${"ms"}%7s   name\n"
    sb ++= "------------  ----------  ------------  ------  -------   ----\n"
    rows.foreach { case (n, kind, reason, bytes, rc, ms) =>
      sb ++= f"$kind%-12s  $reason%-10s  $bytes%,12d  $rc%6d  $ms%7.1f   $n\n"
    }
    sb ++= "------------\n"
    sb ++= f"# totals: est $totalBytes%,d bytes, ${rows.map(_._5).filter(_ >= 0).sum}%,d rows, ${rows.map(_._6).sum}%.1f ms;  $barriers barrier(s), ${rows.size - barriers} view(s)\n"
    sb.toString
  }

  /** The schema of the data rule: Spark's global temporary views, `global_temp.<type>` (`BIND-CALL-SITE`). The
    * suite runs its cases one after another in one session, so each case replaces the views. */
  private val RuleSchema = "global_temp"

  /** BIND-CALL-SITE, BIND-RELATION: bind `c`'s data and value sets the way a client's resolvers do, and return
    * the env to lower with. A first lowering records every call site the SQL makes (`SqlEvalEnv.callSites`).
    *
    * Data is bound by BOTH paths, so a case fails if either is wrong. The rule maps every type to
    * `global_temp.<type>`: for the context type that view holds its rows, for every other type it is an EMPTY
    * decoy. Every other retrieve site is bound through the map to a view of its own holding its type's rows. So a
    * site that reads the rule instead of its map entry reads nothing, and a site bound to another site's view
    * reads another type. The rows are the case's injected sample data (the SAME `DataProvider` the reference
    * reads), a correct superset the plan re-filters above the relation (BIND-CAPABILITY).
    *
    * Terminology is TWO layers by rule (`CLIENT-RESOLVER-OVERLAY`). The value sets are sorted by id. An
    * even-indexed set's expansion is in the first layer, and the second layer holds, under its id, every code of
    * every value set. An odd-indexed set is only in the second layer. So reading a set from anything but the
    * first layer that holds it changes the answer. */
  private[it] def providerEnv(spark: SparkSession, c: Differential.Case, optimized: Library,
                              base: SqlEvalEnv): SqlEvalEnv = {
    // BIND-RETRIEVE-REQUEST: the parameter scope is read off the library before parameters are inlined, as the
    // compile service does, and every case checks that a retrieve's request carries what its define uses.
    val loaded = Differential.loadedLibrary(c)
    val scoped = base.copy(defineParameters = ParameterScope.of(loaded))
    val probe = scoped.describing.copy(callSites = mutable.LinkedHashMap.empty)
    SqlFrameLowering.lower(optimized, probe)
    val sites = probe.callSites.values.filter(_.kind == CallSite.Retrieve).toSeq
    sites.foreach(site => checkParameters(loaded, site))
    // EMIT-SQL-RETRIEVE-DEMAND-BOUND (opt-in `-Dcqldatabricks.demandMount=true`): mount each retrieve
    // narrowed to the compiler's DEMAND for that type (read DIRECTLY downstream — no reconstruction), so the
    // scanned `__resource` is bounded by demand, not resource width. The CONTEXT type stays full (its base
    // frame navigates it outside RetrieveDemand's define-walk); a `Full` demand keeps the whole body.
    val demand: Map[String, RetrieveDemand.Demand] =
      if (sys.props.get("cqldatabricks.demandMount").contains("true")) {
        val ctx = TypeEncoding.ctxOf(optimized)
        RetrieveDemand.analyze(optimized, e => TypeEncoding.typeOf(e, ctx))
      } else Map.empty
    def hintsFor(rt: String): RetrieveHints =
      if (rt == c.context.name) RetrieveHints.none
      else demand.get(rt) match {
        case Some(RetrieveDemand.Narrow(fields)) => RetrieveHints(project = Some(fields))
        case _                                   => RetrieveHints.none
      }
    val data: DataBinding = c.data.map { dp =>
      val types = sites.map(_.request("fhirType").str).distinct
      val frames = types.map(rt => rt -> dp.retrieve(rt, hintsFor(rt))).toMap
      frames.foreach { case (rt, df) =>
        (if (rt == c.context.name) df else df.limit(0)).createOrReplaceGlobalTempView(rt.toLowerCase)
      }
      val bySite = sites.filterNot(_.request("fhirType").str == c.context.name).map { site =>
        val view = s"__site_${site.id}"
        frames(site.request("fhirType").str).createOrReplaceTempView(view)
        site.id -> RetrieveRelation(view, Some("conformance"))
      }.toMap
      DataBinding(Some(DataBinding.Rule(RuleSchema, Some("conformance"))), bySite)
    }.getOrElse(DataBinding(None, Map.empty))
    val layers: Seq[RetrieveRelation] = c.terminology.map { tp =>
      val key = base.valueSetKeyColumn
      val sets = optimized.valueSets.map(_.id).distinct.sorted
      val expansions = sets.map(id => id -> tp.expand(id).select("system", "code"))
      val everyCode = expansions.map(_._2).reduceOption(_ unionByName _)
      def keyed(id: String, df: DataFrame) = df.withColumn(key, F.lit(id)).select(key, "system", "code")
      val first = expansions.zipWithIndex.collect { case ((id, df), i) if i % 2 == 0 => keyed(id, df) }
      val second = expansions.zipWithIndex.map {
        case ((id, df), i) => if (i % 2 == 0) keyed(id, everyCode.get) else keyed(id, df)
      }
      val names = Seq("valuesets", "valuesets__base")
      Seq(first, second).zip(names).foreach { case (parts, name) =>
        parts.reduceOption(_ unionByName _).getOrElse(spark.sql(
          s"SELECT CAST(NULL AS STRING) AS $key, CAST(NULL AS STRING) AS system, CAST(NULL AS STRING) AS code " +
            "WHERE false")).createOrReplaceTempView(name)
      }
      names.map(n => RetrieveRelation(n, Some("conformance-terminology")))
    }.getOrElse(Nil)
    scoped.copy(data = Some(data), terminologyBinding = Some(TerminologyBinding(Some(layers), Map.empty)),
      callSites = mutable.LinkedHashMap.empty)
  }

  /** BIND-RETRIEVE-REQUEST: a retrieve site in a define whose closure uses a parameter with a constant interval
    * or scalar default carries that parameter in its request's `parameters`, under the name the site's own
    * library gives it. Checked independently of [[ParameterScope]]: by walking the loaded library's references. */
  private def checkParameters(loaded: Library, site: CallSite): Unit = site.define.foreach { define =>
    val owner = site.library.get
    val name = loaded.statements.collectFirst {
      case d: com.databricks.cql.ir.ExpressionDef
        if { val o = loaded.ownerOf(d.name); (o.library, o.version, o.name) == (owner._1, owner._2, define) } => d.name
    }.getOrElse(fail(s"site ${site.id}: no define $define in ${owner._1}"))
    val bodies: Map[String, com.databricks.cql.ir.Expr] = loaded.statements.collect {
      case d: com.databricks.cql.ir.ExpressionDef => d.name -> d.expression
      case f: com.databricks.cql.ir.FunctionDef => f.name -> f.body
    }.toMap
    def walk(n: String, seen: Set[String]): Set[String] =
      if (seen(n)) Set.empty
      else bodies.get(n).toSet.flatMap { (e: com.databricks.cql.ir.Expr) =>
        e.collect { case p: com.databricks.cql.ir.ParameterRef => p.name }.toSet ++
          e.collect { case r: com.databricks.cql.ir.ExpressionRef => r.name
                      case f: com.databricks.cql.ir.FunctionRef => f.name }.toSet.flatMap((m: String) => walk(m, seen + n))
      }
    // A constant default: an interval with both bounds, or a scalar, referencing nothing.
    val literal = loaded.parameters.filter(_.default.exists { d =>
      val constant = d.collect {
        case _: com.databricks.cql.ir.ParameterRef | _: com.databricks.cql.ir.ExpressionRef |
             _: com.databricks.cql.ir.FunctionRef | _: com.databricks.cql.ir.Retrieve => ()
      }.isEmpty
      constant && (d match {
        case com.databricks.cql.ir.IntervalExpr(Some(_), Some(_), _, _) => true
        case _: com.databricks.cql.ir.IntervalExpr => false
        case _ => true
      })
    }).map(_.name).toSet
    val used = walk(name, Set.empty).filter(literal)
    val carried = site.request("parameters").obj.keySet.toSet
    used.foreach { p =>
      assert(carried.exists(k => k == p || p.endsWith("." + k) || k.endsWith("." + p)),
        s"retrieve site ${site.id} in $define uses parameter '$p' but its request carries ${carried.mkString(", ")} " +
          "(BIND-RETRIEVE-REQUEST)")
    }
  }

  /** Collect the SQL-path per-(subject, define) results off the executed views — the SQL twin of
    * `LoweredExecution.collect`. Every define publishes the SAME `__val` payload column
    * (EMIT-SQL-VALUE-COLUMN); only the ROW shape differs: a scalar define is its own view
    * `(__subject_id, __val)`, one row per subject, while a grain-first list define is
    * `(__subject_id, __ordinal, __val)` assembled driver-side into an ordered (dedup-if-distinct) list.
    * Values render through the SAME [[Differential.UjsonResults]] as the proto path. */
  private def collect(spark: SparkSession, lowered: SqlFrameLowering.SqlLoweredLibrary): Map[String, Map[String, ujson.Value]] = {
    val rb = Differential.UjsonResults
    def q(id: String): String = "`" + id.replace("`", "``") + "`"
    val relNames    = lowered.names.filter(lowered.relationDefines.contains)
    val scalarNames = lowered.names.filterNot(relNames.contains)

    val subjects = spark.sql(s"SELECT ${q(SystemColumns.SubjectId)} FROM __frame_base")
      .collect().map(_.getString(0)).toSet

    // EMIT-SQL-VALUE-COLUMN: a scalar define reads exactly like a relation define — the SAME `__val` payload
    // column out of its own object; only the ROW shape differs (one row per subject, vs one per element).
    val scalarMaps: Map[String, Map[String, ujson.Value]] = scalarNames.map { n =>
      val rows = spark.sql(
        s"SELECT ${q(SystemColumns.SubjectId)}, ${q(SystemColumns.Val)} FROM ${q(SqlNames.obj(n))}").collect()
      n -> rows.map(r => r.getString(0) -> rb.cell(r.get(1))).toMap
    }.toMap

    val relMaps: Map[String, Map[String, ujson.Value]] = relNames.map { n =>
      val distinct = lowered.listDefines.getOrElse(n, false)
      val rows = spark.sql(
        s"SELECT ${q(SystemColumns.SubjectId)}, ${q(SystemColumns.Val)} FROM ${q(SqlNames.rel(n))} " +
          s"ORDER BY ${q(SystemColumns.SubjectId)}, ${q(SystemColumns.Ordinal)}").collect()
      val bySubj = mutable.LinkedHashMap.empty[String, mutable.ArrayBuffer[ujson.Value]]
      rows.foreach(r => bySubj.getOrElseUpdate(r.getString(0), mutable.ArrayBuffer.empty) += rb.cell(r.get(1)))
      n -> bySubj.map { case (s, vals) => s -> rb.list((if (distinct) vals.distinct else vals).toSeq) }.toMap
    }.toMap

    // ARCH-LIST-NULL-SIDECAR: a nullable relation define's subjects marked `__is_null` render as
    // `rb.nullValue` (not `rb.emptyList`) when they produced no rows — the null/empty distinction the
    // `null_<n>` sidecar view carries (LANG-LIST-NULL-STATE), read here like the proto path's collector.
    val nullSubjects: Map[String, Set[String]] = lowered.nullDefines.map { n =>
      n -> spark.sql(s"SELECT ${q(SystemColumns.SubjectId)} FROM ${q(SqlNames.nullView(n))} WHERE `__is_null`")
        .collect().map(_.getString(0)).toSet
    }.toMap

    subjects.map { s =>
      s -> (scalarNames.map(n => n -> scalarMaps(n).getOrElse(s, rb.nullValue)).toMap ++
            relNames.map(n => n -> relMaps(n).getOrElse(s,
              if (nullSubjects.get(n).exists(_.contains(s))) rb.nullValue else rb.emptyList)).toMap)
    }.toMap
  }

  /** Lower `c` to SQL, mount + run it on `spark`, and diff per (subject, define) vs the Java reference.
    * A not-yet-ported lowering CANCELS the test (skip); a diff mismatch or exec error FAILS it. */
  def run(spark: SparkSession, c: Differential.Case): Unit = {
    Differential.onlyFilter(c.library)

    val cqlText = Paths.read(Paths.cql(c.library))
    val lib     = Differential.buildLibrary(c)
    val config  = Differential.evalConfig(c.library)
    val optimized = Optimize.run(lib, config)
    val env = SqlEvalEnv(c.context, config)
    // BIND-CALL-SITE: binding registers the relations, so it runs on the server; it is timed with the
    // lowering it feeds, and its retrieve probe is a second, discarded lowering.
    var boundEnv: SqlEvalEnv = env
    def bound(): SqlEvalEnv = { boundEnv = providerEnv(spark, c, optimized, env); boundEnv }

    // SQL-PATH TIMING (CONF-PERF-TIME, the SQL twin of [[Differential]]'s four-bucket `Timing.record`).
    // The SQL back end has NO incidental server round-trips during lowering (it emits text, Spark-free),
    // so the model collapses to three live numbers:
    //   - client_cpu  = the `SqlFrameLowering.lower` CPU (`ThreadCpu` — the part that ports to production);
    //   - client_wait = a structural 0 (no `.schema`/`.columns` analyze waits — nothing to eliminate);
    //   - runtime     = ALL server execution — mount + statement register/exec + the final `collect`.
    // Recorded under a `.sql` fixture SUFFIX so the SQL row sits BESIDE the proto row in the same
    // `test-runtimes.txt` — the suffix keeps `Timing.seed`'s whitespace split intact and the keys distinct
    // (the same trick the proto path's `.nopushdown` variant uses; a space would break the seed parser).
    // The dump's own diagnostic `count(*)`/explain queries are EXCLUDED — they run after the timed span.
    val totalWallT0 = System.nanoTime()

    // client_cpu — lower to SQL first; a rejected node family CANCELS cleanly (the SQL back end doesn't
    // cover it yet) rather than failing, so the matrix separates "not ported" from "ported but wrong".
    val cpuT0 = ThreadCpu.nanos()
    val lowered =
      try SqlFrameLowering.lower(optimized, bound())
      catch {
        // The SQL size gate (a within-define inlining blowup) is exactly when a dump is most useful — but
        // it throws INSIDE `lower`, so `run` would cancel before ever writing artifacts. Re-lower with the
        // gate lifted (a huge per-statement cap) purely to DUMP the structural per-statement size breakdown
        // (`diagnostics.txt`, `SqlRender.sizeOf` — never materializes the giant string) + a size-capped
        // `.sql` (oversized statements elided), THEN re-throw the original gate so the fixture still cancels.
        case e: UnsupportedNode if e.elmType == "SqlScript" =>
          dumpSizeBlocked(c, optimized, boundEnv, e); cancel(s"[${c.library}] SQL lowering not yet supported: ${e.getMessage}")
        case e: UnsupportedNode => cancel(s"[${c.library}] SQL lowering not yet supported: ${e.getMessage}")
      }
    val cpuT1 = ThreadCpu.nanos()
    val clientCpuMs = if (cpuT0 >= 0 && cpuT1 >= 0) (cpuT1 - cpuT0) / 1e6 else 0.0

    // The SQL transpiler owns the dump now (EMIT-SQL-EXECUTE): write the source artifacts BEFORE execution
    // so even a diff-failing fixture leaves them for review. Server-free, so outside the runtime span.
    dumpDir(c).foreach { dir =>
      // The SQL and proto back ends write DISJOINT artifacts into the shared per-fixture folder — the SQL
      // path owns `.sql`/`.mounts.sql`/`.diagnostics.txt`, the proto path owns `.unresolved-plan.txt`/
      // `.plan-size.txt`/`.ir.txt`. Do NOT delete the proto artifacts here (that once wiped the two-layer
      // dump); each path only writes its own.
      writeArtifact(dir, c.library, "cql", cqlText)
      // Size-capped render (a raised `sqlMaxStmtBytes` lets a blown-up measure lower; a full render of a
      // ~127 MB statement would then OOM the dump, so elide the oversized ones — sizes stay in diagnostics).
      writeArtifact(dir, c.library, "sql", renderScriptSafe(lowered))
      // Write the per-statement SIZE breakdown BEFORE execution (sizes only, no timing) so the diagnostic
      // survives even if a later statement stalls — the report is written before the run, not only at its end. The
      // post-execution write (below) overwrites it with runtimes once the run completes.
      writeArtifact(dir, c.library, "diagnostics.txt", diagnosticsReport(c.library, lowered, None))
    }

    // runtime — ALL real server execution: mount the views, register/execute each statement, and collect.
    val runtimeT0 = System.nanoTime()
    // The Connect session is memoized across fixtures, so a barrier (`CACHE TABLE`, not "OR REPLACE")
    // would collide with a same-named object from an earlier fixture. Drop each object first so the run
    // is idempotent (best-effort; a no-op for a first-seen name). Views already use CREATE OR REPLACE.
    lowered.script.statements.foreach { s =>
      val q = "`" + s.name.replace("`", "``") + "`"
      try spark.sql(s"DROP VIEW IF EXISTS $q") catch { case _: Throwable => () }
      spark.sql(SqlRender.render(s))
    }
    val ours = collect(spark, lowered)
    val runtimeMs = (System.nanoTime() - runtimeT0) / 1e6
    val totalWallMs = (System.nanoTime() - totalWallT0) / 1e6

    // Record the SQL-path row — same dump gate as the artifacts (one `-Dcqldatabricks.dumpDir` yields the SQL
    // dump AND its timing, so the file lands beside the dump). client_wait is a structural 0 here.
    val timingTag = s"${c.library}.sql"
    if (dumpDir(c).isDefined)
      Timing.record(timingTag, clientCpuMs, 0.0, runtimeMs, totalWallMs, lowered.names.size)

    // Post-execution artifacts: `resolved-plan.txt` (Spark's analyzed→optimized→physical plan of the
    // target — what Catalyst folds that we could fold upfront) and the combined `diagnostics.txt`
    // (kind/reason/bytes/rows/runtime per statement). Run AFTER the timed span (their extra
    // `count(*)`/explain queries are measurement, not the evaluate) and BEFORE the diff so a diff-failing
    // fixture still leaves them. Best-effort — never perturbs the run.
    dumpDir(c).foreach { dir =>
      val q = "`" + lowered.script.target.replace("`", "``") + "`"
      ExplainCapture.capture(spark.sql(s"SELECT * FROM $q"))
        .foreach(plan => writeArtifact(dir, c.library, "resolved-plan.txt", plan))
      writeArtifact(dir, c.library, "diagnostics.txt", diagnosticsReport(c.library, lowered, Some(spark)))
    }

    // EVERY emitted define is diffed, not just the population roots (`CONF-GATE-MEASURE`).
    //
    // Pruning to `c.link.roots` (the 4-5 population defines) left the bulk of a
    // measure's logic uncompared: 19 of 23 defines in one measure of the time, 16 of 21 in another. An intermediate define could
    // be arbitrarily wrong and the gate stayed green unless the error happened to flip a POPULATION outcome
    // for some patient in the sample. That is how a mis-lowered dependency-library define shipped and
    // surfaced as a production error instead of a red test.
    //
    // The reason for the pruning was name resolution, not intent: `LibraryLinker` leaves the root's defines
    // BARE and qualifies dependency defines as `{depId}.{name}`, and no such flattened library exists on the
    // reference side, so a qualified name could not be looked up. `GroundTruthRunner.evaluateAcross` now
    // requests each library's own defines from that library and re-qualifies the results, so the whole
    // closure is comparable. `c.link.roots` still drives PRUNING at compile time (production-faithful);
    // it does not narrow what gets diffed.
    val allNames = lowered.names
    val depIds = c.link.map(_.deps.map(_.id).toSet).getOrElse(Set.empty)
    // Split `{depId}.{name}` back to its owning library; anything unqualified belongs to the root.
    val byLibrary: Map[String, Seq[String]] = allNames.groupBy { n =>
      val dot = n.indexOf('.')
      if (dot > 0 && depIds.contains(n.take(dot))) n.take(dot) else lib.id
    }.map { case (libId, ns) =>
      libId -> ns.map(n => if (libId == lib.id) n else n.drop(libId.length + 1)).toSeq
    }
    val names = allNames
    val refEnv = c.refEnv.copy(
      contextName = c.context.name,
      subjectIds = if (c.context.hasSubjectGrouping) ours.keys.toSeq.sorted else Seq.empty)
    // Merge the Java CQF reference's own wall for the SAME authored CQL into the `.sql` row — the
    // side-by-side comparison column, exactly as the proto path does (reported, never gated).
    val refT0 = System.nanoTime()
    val reference = GroundTruthRunner.evaluateAcross(cqlText, lib.id, byLibrary, refEnv)
    if (dumpDir(c).isDefined)
      Timing.recordReference(timingTag, (System.nanoTime() - refT0) / 1e6)

    // Diff over the subjects the REFERENCE could evaluate (`reference.keys` ⊆ `ours.keys`, since the
    // reference is fed `ours.keys`): a subject the reference cannot evaluate has no ground truth and is
    // excluded upstream ([[GroundTruthRunner]]), never silently passed as a match.
    // Report EVERY per-(subject, define) mismatch (not fail-fast on the first): a measure regression is
    // far easier to localize from the full divergence set than one arbitrary row at a time.
    val raw = for {
      key <- reference.keys.toSeq.sorted
      n   <- names
      if ours(key)(n) != reference(key)(n)
    } yield (key, n, ours(key)(n), reference(key)(n))

    // Partition against the fixture's DECLARED deviations (`CONF-REF-DEVIATION`). A declaration accepts
    // one narrow shape of difference and nothing wider, so an undeclared divergence — or a wider one on a
    // define that has a declaration — still fails.
    def sameElements(a: ujson.Value, b: ujson.Value): Boolean = (a, b) match {
      case (x: ujson.Arr, y: ujson.Arr) =>
        x.arr.map(_.render()).sorted == y.arr.map(_.render()).sorted
      case _ => false
    }
    def declaredFor(key: String, n: String, sql: ujson.Value, ref: ujson.Value): Option[Differential.Deviation] =
      c.deviations.find {
        case Differential.OrderOnly(d, _)  => d == n && sameElements(sql, ref)
        case Differential.ValueFor(d, s, _) => d == n && s == key
      }
    val (declared, mismatches) = raw.partition { case (k, n, sql, ref) => declaredFor(k, n, sql, ref).isDefined }

    val report = mismatches.map { case (k, n, sql, ref) =>
      s"[${c.library}] subject '$k' define '$n': sql=$sql reference=$ref" }
    assert(report.isEmpty, s"${report.size} mismatch(es):\n" + report.mkString("\n"))

    // STALENESS: a declaration that never fired describes behaviour that no longer exists, so it must be
    // removed rather than left to silently widen what the gate tolerates.
    val fired = declared.flatMap { case (k, n, sql, ref) => declaredFor(k, n, sql, ref) }.toSet
    val stale = c.deviations.filterNot(fired)
    assert(stale.isEmpty,
      s"${stale.size} DECLARED deviation(s) did not occur — the behaviour they describe is gone, so delete " +
        s"them (`CONF-REF-DEVIATION` staleness check):\n" +
        stale.map(d => s"  - ${d.define}: ${d.reason}").mkString("\n"))
  }

  /** ENGINE-RESULTS entry (CONF-GATE-SCOREKEY, EXEC-SQL-BACKEND): lower `c` via the SQL back end, mount its
    * data, execute every statement, and return the per-(subject, define) result map — the SQL twin of
    * [[SqlDifferential.engineResults]], factored out of [[run]]'s execute+collect so the scorekey gate
    * ([[MeasureScoreSpec]]) scores on the SQL path (two-layer eval S1). No Java-reference diff here (the
    * caller diffs the collected values against the measure's hand-written answer key). Reuses [[Differential]]'s
    * library build + `evalConfig` so it lowers the identical optimized IR the differential gate does.
    * Single-factor per call (the shared session's `CACHE TABLE` names would collide across concurrent
    * measures; the caller runs one measure at a time). */
  def engineResults(spark: SparkSession, c: Differential.Case): Map[String, Map[String, ujson.Value]] = {
    Differential.onlyFilter(c.library)
    val lib       = Differential.buildLibrary(c)
    val config    = Differential.evalConfig(c.library)
    val optimized = Optimize.run(lib, config)
    val env       = providerEnv(spark, c, optimized, SqlEvalEnv(c.context, config))
    val lowered   = SqlFrameLowering.lower(optimized, env)
    // Drop each object first (idempotent across fixtures in the memoized session; a barrier CACHE TABLE
    // is not "OR REPLACE"), then register/execute — mirroring [[run]].
    lowered.script.statements.foreach { s =>
      val q = "`" + s.name.replace("`", "``") + "`"
      try spark.sql(s"DROP VIEW IF EXISTS $q") catch { case _: Throwable => () }
      try spark.sql(s"UNCACHE TABLE IF EXISTS $q") catch { case _: Throwable => () }
      spark.sql(SqlRender.render(s))
    }
    collect(spark, lowered)
  }

  /** PIPELINE-PLAN entry (`PIPE-SQL-DAG-REUSE`), SERVER-FREE: lower `c` ONCE in pipeline-cut mode and cut
    * the DAG into a [[PipelinePlan]] — one dataset recipe per materialized define. Nothing executes; used
    * to assert the cut STRUCTURE (a dependent recipe reads a materialized sibling by its published name and
    * does NOT re-inline the sibling's retrieve). Lowers with the SAME per-fixture config the re-inlined
    * differential uses, so the two paths lower the identical optimized IR. */
  def pipelinePlan(c: Differential.Case): PipelinePlan = {
    val lib       = Differential.buildLibrary(c)
    val config    = Differential.evalConfig(c.library)
    val optimized = Optimize.run(lib, config)
    val dsCtx     = SqlNames.DatasetNameContext(lib.id, lib.version.getOrElse(""))
    val env       = SqlEvalEnv(c.context, config,
      cutSiblings = Some(mutable.Set.empty[String]), datasetNameCtx = Some(dsCtx)).describing
    CompileService.cutPlan(SqlFrameLowering.lower(optimized, env),
      lib.id, lib.version.getOrElse(""), dsCtx = dsCtx)
  }

  /** PIPELINE-PLAN EXECUTION entry (`PIPE-SQL-DAG-REUSE`, EXEC-SQL-BACKEND): lower `c` in pipeline-cut mode,
    * cut it into recipes, mount its data, then execute each recipe as its OWN dataset — running the recipe's
    * statements and PUBLISHING its target under its `cql_<define>` name (a temp view) so a dependent recipe
    * reads that sibling as a DECLARED dataset (`FROM cql_<sibling>`) instead of re-inlining it. The
    * cross-dataset DAG cut driven in-JVM (the SDP node-body contract). Returns the per-(subject, define) map
    * off the produced objects — which must EQUAL the fully re-inlined [[engineResults]] (`CORE-NO-SILENT-MISLOWER`:
    * the cut is a structure change, not a semantic one). Single-factor per call (shared-session name reuse). */
  def pipelineResults(spark: SparkSession, c: Differential.Case): Map[String, Map[String, ujson.Value]] = {
    Differential.onlyFilter(c.library)
    val lib       = Differential.buildLibrary(c)
    val config    = Differential.evalConfig(c.library)
    val optimized = Optimize.run(lib, config)
    val dsCtx     = SqlNames.DatasetNameContext(lib.id, lib.version.getOrElse(""))
    val env       = providerEnv(spark, c, optimized, SqlEvalEnv(c.context, config,
      cutSiblings = Some(mutable.Set.empty[String]),
      datasetNameCtx = Some(dsCtx)))
    val lowered   = SqlFrameLowering.lower(optimized, env)
    val plan      = CompileService.cutPlan(lowered, dsCtx = dsCtx)
    def drop(name: String): Unit = {
      val q = "`" + name.replace("`", "``") + "`"
      try spark.sql(s"DROP VIEW IF EXISTS $q") catch { case _: Throwable => () }
      try spark.sql(s"UNCACHE TABLE IF EXISTS $q") catch { case _: Throwable => () }
    }
    // Run each recipe, then publish its produced object under `cql_<define>` — the declared dataset a
    // dependent recipe's `FROM cql_<sibling>` resolves against (idempotent across the shared session).
    plan.datasets.foreach { r =>
      r.statements.foreach { s => drop(s.name); spark.sql(s.sql) }
      drop(r.name)
      val qn = "`" + r.name.replace("`", "``") + "`"
      val qt = "`" + r.target.replace("`", "``") + "`"
      spark.sql(s"CREATE OR REPLACE TEMP VIEW $qn AS SELECT * FROM $qt")
    }
    // Collect off the LOCAL objects the recipes produced — a dependent define's object was computed from
    // its sibling's PUBLISHED dataset (the cut path), so this is the cut result.
    collect(spark, lowered)
  }

  /** GENERATED-LIBRARY entry (CONF-GATE-PROPERTY, EXEC-SQL-BACKEND): lower an already-normalized `lib` in a
    * System/Unfiltered `context` (NO FHIR retrieves / terminology — nothing to mount) to SQL, execute every
    * statement on `spark`, and collect per-(subject, define). The entry the property gate
    * ([[PropertyDifferentialSpec]]) uses for ad-hoc GENERATED libraries (no `Differential.Case`/file fixture),
    * so the property differential runs on the SAME primary back end as the corpus gate (two-layer eval S1).
    * `Config.defaults` (no per-fixture grain-first/materialize wrappers), matching the property gate's
    * historical proto env. */
  def runGenerated(spark: SparkSession, lib: Library, context: Context): Map[String, Map[String, ujson.Value]] = {
    val config    = Config.defaults
    val optimized = Optimize.run(lib, config)
    val env       = SqlEvalEnv(context, config)
    val lowered   = SqlFrameLowering.lower(optimized, env)
    lowered.script.statements.foreach { s =>
      val q = "`" + s.name.replace("`", "``") + "`"
      try spark.sql(s"DROP VIEW IF EXISTS $q") catch { case _: Throwable => () }
      try spark.sql(s"UNCACHE TABLE IF EXISTS $q") catch { case _: Throwable => () }
      spark.sql(SqlRender.render(s))
    }
    collect(spark, lowered)
  }

  /** THROUGHPUT-ONLY scale entry (no Java reference, no per-subject diff): lower via the SQL back end,
    * mount the (already-scaled) data, execute every statement, and FORCE full materialization of each view
    * with `count(*)` (minimal driver transfer, unlike `collect`). Correctness is vetted on the base deck
    * ([[SqlDifferentialSpec]]); this measures wall time at scale ([[MeasureScaleSpec]]). Returns
    * (patients, lowerMs, runtimeMs). Single-factor per call (CACHE TABLE names collide across factors in a
    * shared session; the caller uses a fresh factor per run or resets). */
  def runThroughput(spark: SparkSession, c: Differential.Case): (Long, Double, Double) = {
    val lib       = Differential.buildLibrary(c)
    val config    = Differential.evalConfig(c.library)
    val optimized = Optimize.run(lib, config)
    val env       = providerEnv(spark, c, optimized,
      SqlEvalEnv(c.context, config))

    val lowerT0 = System.nanoTime()
    val lowered = SqlFrameLowering.lower(optimized, env)
    val lowerMs = (System.nanoTime() - lowerT0) / 1e6

    val runT0 = System.nanoTime()
    lowered.script.statements.foreach { s =>
      val q = "`" + s.name.replace("`", "``") + "`"
      try spark.sql(s"DROP VIEW IF EXISTS $q") catch { case _: Throwable => () }
      try spark.sql(s"UNCACHE TABLE IF EXISTS $q") catch { case _: Throwable => () }
      spark.sql(SqlRender.render(s))
    }
    // Force full execution of every materialized view — count, not collect (no 62k×N driver map).
    lowered.script.statements.foreach { s =>
      spark.sql(s"SELECT count(*) FROM `" + s.name.replace("`", "``") + "`").collect()
    }
    val runtimeMs = (System.nanoTime() - runT0) / 1e6
    val patients  = spark.sql(s"SELECT count(*) FROM __frame_base").collect().head.getLong(0)
    (patients, lowerMs, runtimeMs)
  }
}
