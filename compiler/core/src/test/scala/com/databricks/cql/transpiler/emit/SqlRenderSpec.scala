package com.databricks.cql.transpiler.emit

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Walking-skeleton test for the SQL AST + renderer (EMIT-SQL-EXECUTE, Phase 1). Builds scripts by hand
  * and locks the emitted TEXT — no Spark, no proto, no server. Proves the target artifact is readable,
  * define-once/reference-many SQL. The IR → AST lowering that produces these scripts lands in Phases
  * 2-3; this pins the contract the lowering renders to. */
@RunWith(classOf[JUnitRunner])
class SqlRenderSpec extends AnyFunSuite {
  import SqlExpr._
  import SqlRel._

  test("a two-statement define-once script renders as readable, correctly-quoted SQL") {
    val retrieve = SqlStmt.CreateView(
      "retrieve_Encounter",
      Select(
        projections = Seq(Proj(col("__subject_id")), Proj(Field(col("__resource"), "status"), "status")),
        from = TableRef("encounter_source")
      )
    )
    // A define name with a space MUST be backtick-quoted; a plain reference must not be.
    val define = SqlStmt.CreateView(
      "Visits During Period",
      Select(
        projections = Seq(Proj.star),
        from = TableRef("retrieve_Encounter"),
        where = Some(Infix("=", col("status"), str("finished")))
      )
    )
    val out = SqlRender.render(SqlScript(Seq(retrieve, define), "Visits During Period"))

    val expected =
      """CREATE OR REPLACE TEMP VIEW retrieve_Encounter AS
        |SELECT
        |  __subject_id,
        |  __resource.status AS status
        |FROM encounter_source;
        |
        |CREATE OR REPLACE TEMP VIEW `Visits During Period` AS
        |SELECT
        |  *
        |FROM retrieve_Encounter
        |WHERE (status = 'finished');
        |""".stripMargin
    assert(out == expected)
  }

  test("string literals are SQL-escaped by doubling the single quote") {
    assert(SqlRender.expr(str("O'Brien")) == "'O''Brien'")
  }

  test("CASE, HOF lambda, and named_struct render natively (no UDF)") {
    val caseExpr = Case(Seq((Postfix(col("x"), "IS NOT NULL"), int(1))), Some(Null))
    assert(SqlRender.expr(caseExpr) == "CASE WHEN x IS NOT NULL THEN 1 ELSE NULL END")

    val hof = call("transform", col("xs"), Lambda(Seq("e"), Field(col("e"), "value")))
    assert(SqlRender.expr(hof) == "transform(xs, e -> e.value)")

    val q = Struct(Seq("value" -> decimal(BigDecimal("5.0")), "unit" -> str("mg")))
    assert(SqlRender.expr(q) == "named_struct('value', CAST(5.0 AS DECIMAL(38,8)), 'unit', 'mg')")
  }

  test("a barrier define becomes CACHE TABLE; a lateral posexplode renders grain-first rows") {
    val rel = LateralView(
      input = TableRef("AllEncounters"),
      generator = call("posexplode", col("__val")),
      tableAlias = "p",
      columnAliases = Seq("__ordinal", "__item"),
      outer = true
    )
    val out = SqlRender.render(SqlStmt.CacheTable("Numerator", Select(Seq(Proj.star), rel)))
    assert(out.startsWith("CACHE TABLE Numerator AS"))
    assert(out.contains("FROM AllEncounters LATERAL VIEW OUTER posexplode(__val) p AS __ordinal, __item"))
  }

  test("window over renders with partition and order") {
    val e = Over(call("row_number"), Seq(col("__subject_id")), Seq(SortKey(col("__ordinal"), ascending = true)))
    assert(SqlRender.expr(e) == "row_number() OVER (PARTITION BY __subject_id ORDER BY __ordinal ASC)")
  }

  test("a table-function call renders as FROM fn(args), catalog-qualified per segment (BIND-RETRIEVE-UDF)") {
    // A convention-name UDTF with the explicit retrieve arguments (BIND-EXPLICIT-PARAMS): a collection
    // argument renders as `array(...)` / `map(...)`, an absent scalar as NULL.
    val bare = TableFunc("retrieve_Encounter",
      Seq(str("urn:oid:1.2.3"), str("type"), call("array"), Null, Null, Null,
          call("map", str("Threshold"), Cast(str("40"), "VARIANT"))))
    assert(SqlRender.renderRel(bare, 0) ==
      "SELECT * FROM retrieve_Encounter('urn:oid:1.2.3', " +
        "'type', array(), NULL, NULL, NULL, map('Threshold', CAST('40' AS VARIANT)))")

    // A catalog-qualified binding: each dotted segment independently quoted, aliasable as a derived table.
    val qualified = AliasRel(TableFunc("retrieve_observation", Seq(col("c"), col("h")), Some("acme.fhir")), "__ret")
    val out = SqlRender.renderRel(Select(Seq(Proj.star), from = qualified), 0)
    assert(out.contains("FROM acme.fhir.retrieve_observation(c, h) AS __ret"))
  }
}
