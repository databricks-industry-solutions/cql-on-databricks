package com.databricks.cql.it

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.passes.{Optimize, Pipeline}
import com.databricks.cql.sema.FhirModel
import com.databricks.cql.transpiler.emit.{SqlExpr, SqlRel, SqlRender, SqlStmt}
import com.databricks.cql.transpiler.lower.{SqlEvalEnv, SqlFrameLowering}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** ISOLATE + CHARACTERIZE the het-Choice ANALYSIS-time blowup.
  * A measure's het-Choice union once pegged the Spark Connect
  * server at 100% of one core for MINUTES, uninterruptibly, in Catalyst `NestedColumnAliasing`
  * (O(distinct-nested-extractions^2) pairwise `semanticEquals` over the discriminated struct). So we do
  * NOT iterate on the full measure — this builds a MINIMAL, parameterized reproducer, dials the one knob
  * (N = distinct fields navigated on the het alias), and measures the driver **server-free**: the count
  * of distinct nested `GetStructField` extractions in the emitted SQL, which IS the input set
  * `NestedColumnAliasing` squares. If that count grows ~linearly in N, analysis is O(N^2) — the blowup —
  * and the split between CONSTRUCTION (per-arm handle build) and NAVIGATION (per-arm dispatch reads)
  * decides which lever Phase-3 step-1 must pull (the doc insists this is decided BEFORE the fix).
  *
  * Server-free by design (zero risk of pegging the server — the jstack + `nestedSchemaPruning=false`
  * experiments already CONFIRMED the O(n^2) is real, memory `opaque-udf-exec`); this quantifies WHERE the
  * extractions come from. Opt-in: `-Dcqldatabricks.hetScale=true` (sweep `-Dcqldatabricks.hetScaleNavs=2,4,8,16`). */
@RunWith(classOf[JUnitRunner])
final class HetChoiceScaleSpec extends AnyFunSuite {

  private val enabled = sys.props.get("cqldatabricks.hetScale").contains("true")
  private val navCounts: Seq[Int] =
    sys.props.get("cqldatabricks.hetScaleNavs").map(_.split(",").toSeq.flatMap(s => scala.util.Try(s.trim.toInt).toOption))
      .filter(_.nonEmpty).getOrElse(Seq(2, 4, 8, 16, 24))

  // Two GENUINELY heterogeneous FHIR resource arms (distinct tags -> a real discriminated struct, not the
  // degenerate Choice<Condition,Condition> collapse). A pool of NON-SCALAR + scalar single-valued fields
  // valid on at least one arm — validated live against FhirModel so a generated nav never fails to type.
  private val ArmA = "Condition"
  private val ArmB = "Encounter"
  private val fieldPool: Seq[String] = Seq(
    "recordedDate", "clinicalStatus", "verificationStatus", "category", "severity", "code", "bodySite",
    "subject", "encounter", "recorder", "asserter", "note", "stage",
    "status", "class", "type", "serviceType", "priority", "period", "reasonCode", "hospitalization",
    "location", "serviceProvider", "partOf", "account", "length")
  private def validFields: Seq[String] =
    fieldPool.filter(f => FhirModel.navType(ArmA, f).isDefined || FhirModel.navType(ArmB, f).isDefined).distinct

  /** A single-define het-union query navigating the first `n` valid fields on the alias (a return tuple
    * forces one nested extraction per field). Patient context; the het union is `[Condition] union
    * [Encounter]`. */
  private def genCql(n: Int): String = {
    val fields = validFields.take(n)
    require(fields.sizeIs >= n, s"only ${validFields.size} valid fields available, need $n")
    val tupleElems = fields.zipWithIndex.map { case (f, i) => s"    f$i: X.$f" }.mkString(",\n")
    s"""library HetChoiceScale version '1.0.0'
       |using FHIR version '4.0.1'
       |include FHIRHelpers version '4.0.1'
       |context Patient
       |define "Nav":
       |  ([Condition] union [Encounter]) X
       |  return Tuple {
       |$tupleElems
       |  }
       |""".stripMargin
  }

  // ---- server-free structural metrics over the emitted SQL AST ----

  private def exprsOfRel(r: SqlRel): Seq[SqlExpr] = r match {
    case s: SqlRel.Select =>
      s.projections.map(_.expr) ++ exprsOfRel(s.from) ++ s.where.toSeq ++ s.groupBy ++ s.having.toSeq ++ s.orderBy.map(_.expr)
    case SqlRel.Join(l, rr, _, on, _) => exprsOfRel(l) ++ exprsOfRel(rr) ++ on.toSeq
    case SqlRel.UnionAll(parts)       => parts.flatMap(exprsOfRel)
    case SqlRel.LateralView(in, g, _, _, _) => exprsOfRel(in) :+ g
    case SqlRel.AliasRel(rel, _)      => exprsOfRel(rel)
    case SqlRel.With(ctes, body)      => ctes.flatMap { case (_, rr) => exprsOfRel(rr) } ++ exprsOfRel(body)
    case _                            => Nil
  }
  private def exprsOfStmt(s: SqlStmt): Seq[SqlExpr] = s match {
    case SqlStmt.CreateView(_, r)  => exprsOfRel(r)
    case SqlStmt.CacheTable(_, r)  => exprsOfRel(r)
    case _: SqlStmt.CreateFunction => Nil
  }

  /** Every `Field` (GetStructField) subexpression, as its rendered text — DISTINCT count == the set
    * `NestedColumnAliasing` runs O(n^2) `semanticEquals` over. Split into NAV reads (a chain that reads
    * THROUGH an `asFHIR_<M>` discriminated slot) vs CONSTRUCTION (all other struct-field reads — the
    * per-arm handle body builds). */
  private def fieldChains(e: SqlExpr): Seq[String] = {
    val acc = scala.collection.mutable.ListBuffer.empty[String]
    def walk(x: SqlExpr): Unit = {
      x match { case f: SqlExpr.Field => acc += SqlRender.expr(f); case _ => () }
      children(x).foreach(walk)
    }
    walk(e); acc.toList
  }
  private def children(e: SqlExpr): Seq[SqlExpr] = e match {
    case SqlExpr.Field(b, _)      => Seq(b)
    case SqlExpr.Call(_, as)      => as
    case SqlExpr.Infix(_, l, r)   => Seq(l, r)
    case SqlExpr.Prefix(_, x)     => Seq(x)
    case SqlExpr.Postfix(x, _)    => Seq(x)
    case SqlExpr.Case(ws, oe)     => ws.flatMap { case (c, v) => Seq(c, v) } ++ oe.toSeq
    case SqlExpr.Cast(x, _)       => Seq(x)
    case SqlExpr.Lambda(_, b)     => Seq(b)
    case SqlExpr.Struct(fs)       => fs.map(_._2)
    case SqlExpr.Over(fn, p, o)   => (fn +: p) ++ o.map(_.expr)
    case _                        => Nil
  }

  private def lowerToSql(cql: String, config: Config): Seq[SqlStmt] = {
    val lib = Pipeline().normalizeCql(cql)
    val optimized = Optimize.run(lib, config)
    SqlFrameLowering.lower(optimized, SqlEvalEnv(Context.Patient, config, terminology = false)).script.statements
  }

  if (enabled) test("het-Choice analysis-driver curve: distinct nested extractions vs N navs") {
    val base = Config.defaults
    val sb = new StringBuilder
    def line(s: String): Unit = { info(s); System.out.println(s); sb.append(s).append('\n') }
    line(f"${"N"}%3s | ${"stmts"}%5s | ${"sizeB"}%8s | ${"fieldNodes"}%10s | ${"distinct"}%8s | ${"navReads"}%8s | ${"construct"}%9s")
    val rows = validFields.size
    navCounts.filter(_ <= rows).foreach { n =>
      val stmts = lowerToSql(genCql(n), base)
      val allChains = stmts.flatMap(exprsOfStmt).flatMap(fieldChains)
      val distinct  = allChains.distinct
      val navReads  = distinct.count(_.contains(".asFHIR_"))
      val construct = distinct.size - navReads
      val sizeB     = stmts.map(SqlRender.sizeOf).sum
      line(f"$n%3d | ${stmts.size}%5d | $sizeB%8d | ${allChains.size}%10d | ${distinct.size}%8d | $navReads%8d | $construct%9d")
    }
    line(s"(only ${validFields.size} valid fields available for the pool; N capped there)")
    val out = new java.io.File(Option(Paths.elmDir.getParentFile).getOrElse(Paths.elmDir), "hetscale-curve.txt")
    java.nio.file.Files.write(out.toPath, sb.toString.getBytes("UTF-8"))
    succeed
  }
}
