package com.databricks.cql.transpiler.lower

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import com.databricks.cql.transpiler.emit.SqlRender
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Phase 3b tests for `SqlFrameLowering` (EMIT-SQL-EXECUTE): a whole library lowered to an ordered SQL
  * script — one named view per define (the DAG the proto could not express), grain-first Relation views
  * for list defines, and statement KIND (view vs a `CACHE TABLE` barrier) chosen by the policy. Server-free,
  * no Spark; correctness vs the Java reference is the Phase-5 gate. */
@RunWith(classOf[JUnitRunner])
class SqlFrameLoweringSpec extends AnyFunSuite {

  private val Encounter = "{http://hl7.org/fhir}Encounter"
  private def retrieve(dataType: String): Retrieve = Retrieve(dataType, None, None, None, None, None)
  private def lit(t: String, v: String): Literal = Literal(s"{urn:hl7-org:elm-types:r1}$t", Some(v))
  private def define(name: String, e: Expr): ExpressionDef = ExpressionDef(name, Some("Patient"), e)

  private def library(defs: ExpressionDef*): Library =
    Library("Test", None,
      usings = Seq(UsingDef(Some("FHIR"), "http://hl7.org/fhir", Some("4.0.1"))),
      includes = Nil, parameters = Nil, codeSystems = Nil, valueSets = Nil, codes = Nil, concepts = Nil,
      contexts = Seq(ContextDef("Patient")), statements = defs)

  private def env(config: Config = Config.defaults): SqlEvalEnv = SqlEvalEnv(Context.Patient, config)

  test("a library lowers to one view per define over a named context base, in dependency order") {
    val lib = library(
      define("InitialPopulation", lit("Boolean", "true")),
      define("AllEncounters", retrieve(Encounter)),
      define("HasEncounter", Exists(ExpressionRef("AllEncounters", None))))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, env()).script)

    // The context base view is named and reads the mounted context retrieve.
    assert(out.contains("CREATE OR REPLACE TEMP VIEW __frame_base AS"))
    assert(out.contains("__resource AS Patient"))
    assert(out.contains("FROM retrieve_Patient"))
    // A scalar define is its own view over the base, publishing its value as `__val`
    // (EMIT-SQL-VALUE-COLUMN — the define name names the OBJECT, never a column).
    assert(out.contains("CREATE OR REPLACE TEMP VIEW InitialPopulation AS"))
    assert(out.contains("true AS __val"))
    assert(!out.contains("AS InitialPopulation")) // the name is NOT the value column
    // A bare-retrieve list define is a grain-first Relation view — the SAME value column.
    assert(out.contains("CREATE OR REPLACE TEMP VIEW AllEncounters AS"))
    assert(out.contains("__resource AS __val"))
    // AllEncounters is scheduled before HasEncounter (dependency order).
    assert(out.indexOf("AllEncounters AS") < out.indexOf("HasEncounter AS"))
  }

  test("exists over a relation define becomes a LEFT OUTER JOIN presence column, absent -> false") {
    val lib = library(
      define("AllEncounters", retrieve(Encounter)),
      define("HasEncounter", Exists(ExpressionRef("AllEncounters", None))))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, env()).script)
    assert(out.contains("CREATE OR REPLACE TEMP VIEW HasEncounter AS"))
    assert(out.contains("LEFT OUTER JOIN"))
    assert(out.contains("SELECT DISTINCT"))
    assert(out.contains("FROM AllEncounters"))
    assert(out.contains("coalesce(") && out.contains("false)"))
  }

  test("Count over a relation define becomes a grouped count join, absent -> 0") {
    val lib = library(
      define("AllEncounters", retrieve(Encounter)),
      define("EncounterCount", Count(ExpressionRef("AllEncounters", None))))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, env()).script)
    assert(out.contains("CREATE OR REPLACE TEMP VIEW EncounterCount AS"))
    assert(out.contains("CAST(count(__val) AS INT)"))
    assert(out.contains("GROUP BY __subject_id"))
    assert(out.contains("coalesce(") && out.contains("0)"))
  }

  test("the semantic classifier stages a FILTERED query as a CACHE TABLE barrier; a bare retrieve stays a view") {
    // A query with its OWN filter (`where E.status = …`) applies a filter → materialize (reason `filter`).
    // A bare `[Encounter]` retrieve applies none → passthru → a logical view (DATA-SKIP-UNFILTERED). The
    // barrier is a `CACHE TABLE` (EMIT-SQL-EXECUTE localCheckpoint replacement), identical local + deploy.
    val filtered = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = Some(Equal(Property(None, Some("E"), "status"), lit("String", "finished"))),
      returnExpr = None)
    val lib = library(define("AllEncounters", retrieve(Encounter)), define("Finished", filtered))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, env()).script)
    assert(out.contains("CACHE TABLE Finished AS"))                        // own filter → barrier
    assert(out.contains("CREATE OR REPLACE TEMP VIEW AllEncounters AS"))   // bare retrieve → view
    assert(!out.contains("CACHE TABLE AllEncounters"))
  }

  test("BIND-CAPABILITY-OVERLAP: a demanded field the binding does not provide fails loud (CORE-NO-SILENT-MISLOWER)") {
    // `Finished` navigates `E.status`, so the Encounter demand is {status}. A binding whose AVAILABLE
    // capability provides only {period} is a coverage GAP on `status` → a loud bind-time error naming it.
    val filtered = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = Some(Equal(Property(None, Some("E"), "status"), lit("String", "finished"))),
      returnExpr = None)
    val lib = library(define("Finished", filtered))
    val available = Map("Encounter" -> Set("period"))
    val ex = intercept[UnsupportedNode](SqlFrameLowering.lower(lib, env().copy(available = available)))
    assert(ex.getMessage.contains("status"), s"gap error must name the missing field: ${ex.getMessage}")
    // The opt-in lenient override downgrades the gap (no error) — re-enabling silent gaps.
    val lenient = env(Config.defaults.copy(bindingGapLenient = true)).copy(available = available)
    SqlFrameLowering.lower(lib, lenient) // does not throw
    // A binding that DOES provide `status` binds cleanly.
    SqlFrameLowering.lower(lib, env().copy(available = Map("Encounter" -> Set("status", "period"))))
  }

  test("a measure population always materializes, even a scalar boolean define") {
    val lib = library(
      define("AllEncounters", retrieve(Encounter)),
      define("Numerator", Exists(ExpressionRef("AllEncounters", None))))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, env()).script)
    assert(out.contains("CACHE TABLE Numerator AS"))   // population → materialize (a scalar exists define)
  }

  test("the target defaults to the last scheduled define") {
    val lib = library(
      define("AllEncounters", retrieve(Encounter)),
      define("HasEncounter", Exists(ExpressionRef("AllEncounters", None))))
    assert(SqlFrameLowering.lower(lib, env()).script.target == "HasEncounter")
  }

  test("the emit-size gate rejects a script whose statement exceeds the per-statement byte limit") {
    val lib = library(define("X", lit("Integer", "1")))
    val prev = System.getProperty("cqldatabricks.sqlMaxStmtBytes")
    System.setProperty("cqldatabricks.sqlMaxStmtBytes", "1") // 1 byte — every real statement trips the gate
    try {
      val ex = intercept[com.databricks.cql.error.UnsupportedNode](SqlFrameLowering.lower(lib, env()))
      assert(ex.getMessage.contains("emit-size gate"))
    } finally {
      if (prev == null) System.clearProperty("cqldatabricks.sqlMaxStmtBytes")
      else System.setProperty("cqldatabricks.sqlMaxStmtBytes", prev)
    }
    // Under the (generous) default limit the same tiny library lowers fine.
    assert(SqlRender.render(SqlFrameLowering.lower(lib, env()).script).contains("1 AS __val"))
  }

  /** EMIT-SQL-VALUE-COLUMN — the case the uniform value column would break if the rename lived anywhere but
    * inside the join: a scalar define over TWO scalar dependencies. Both dependency views publish `__val`, so
    * joining them bare would put two identically-named columns on one frame and make the body's reference
    * ambiguous. Each dependency is therefore read through `SELECT __subject_id, __val AS <obj>` so the frame
    * carries two DISTINCT names. One dependency is not enough to catch this — `__val` resolves fine then. */
  test("a scalar define over TWO scalar dependencies renames each dependency's __val in its join") {
    val lib = library(
      define("HasReadingA", lit("Boolean", "true")),
      define("HasReadingB", lit("Boolean", "true")),
      define("Numerator", And(ExpressionRef("HasReadingA", None), ExpressionRef("HasReadingB", None))))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, env()).script)

    // Each dependency is renamed to its OBJECT name inside its own join subquery...
    assert(out.contains("__val AS HasReadingA"))
    assert(out.contains("__val AS HasReadingB"))
    // ...so the body references two distinct names, and the result is still published as `__val`.
    assert(out.contains("(HasReadingA AND HasReadingB) AS __val"))
    // A dependency is NEVER joined bare — that is what would put two `__val` columns on one frame.
    assert(!out.contains("LEFT OUTER JOIN HasReadingA"), "dependency joined bare, not renamed")
    assert(!out.contains("LEFT OUTER JOIN HasReadingB"), "dependency joined bare, not renamed")
    // Each renaming join is aliased as its own derived table.
    assert(out.contains("AS __scdef_HasReadingA") && out.contains("AS __scdef_HasReadingB"))
  }

  test("an ungrouped (Unfiltered) context seeds a single synthetic-subject base, no data read") {
    val lib = Library("Test", None, Nil, Nil, Nil, Nil, Nil, Nil, Nil,
      contexts = Seq(ContextDef("Unfiltered")),
      statements = Seq(ExpressionDef("X", Some("Unfiltered"), lit("Integer", "1"))))
    val out = SqlRender.render(SqlFrameLowering.lower(lib, SqlEvalEnv(Context.Unfiltered)).script)
    assert(out.contains("CREATE OR REPLACE TEMP VIEW __frame_base AS"))
    assert(out.contains("'*' AS __subject_id"))
    assert(out.contains("SELECT 1"))          // the one synthetic row, no data read
    assert(out.contains("1 AS __val"))        // EMIT-SQL-VALUE-COLUMN, in the view named X
  }
}
