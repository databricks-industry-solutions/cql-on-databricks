package com.databricks.cql.transpiler.lower

import com.databricks.cql.ir._
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.transpiler.emit.{SqlRel, SqlRender}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.util.concurrent.atomic.AtomicInteger

/** Increment-1 tests for SqlColumnLowering (EMIT-SQL-EXECUTE). Lowers real IR `Expr` nodes to the SQL
  * AST and locks the rendered SQL TEXT — server-free, no Spark. Correctness vs the Java reference is the
  * Phase-5 gate; this pins that the families emit well-formed, readable SQL, and that the temporal towers
  * (the measured temporal-tower blow-up) render as plain SQL that a define-once view/CTE will collapse. */
@RunWith(classOf[JUnitRunner])
class SqlColumnLoweringSpec extends AnyFunSuite {

  private def sql(e: Expr, typeOf: Expr => CqlType = _ => AnyType): String =
    SqlRender.expr(SqlColumnLowering.lower(e, typeOf = typeOf))

  private def lit(t: String, v: String): Literal = Literal(s"{urn:hl7-org:elm-types:r1}$t", Some(v))

  test("literals, 3VL logical, and equality/equivalence") {
    assert(sql(lit("Integer", "5")) == "5")
    assert(sql(lit("String", "hi")) == "'hi'")
    assert(sql(NullExpr()) == "NULL")
    assert(sql(And(lit("Boolean", "true"), lit("Boolean", "false"))) == "(true AND false)")
    assert(sql(Not(lit("Boolean", "true"))) == "NOT true")
    // `~` on strings is case-insensitive, whitespace-normalizing and null-safe (LANG-EQUIV).
    val strEquiv = Equivalent(lit("String", "A"), lit("String", "a"))
    // The SQL literal carries ESCAPED backslashes (`'[ \\t\\r\\n]'`), so the regex engine sees
    // `[ \t\r\n]` — the CQL whitespace lexical element.
    assert(sql(strEquiv, _ => StringType) ==
      """(regexp_replace(lower('A'), '[ \\t\\r\\n]', ' ') <=> regexp_replace(lower('a'), '[ \\t\\r\\n]', ' '))""")
  }

  test("equivalence normalizes whitespace PER CHARACTER — runs are not collapsed, ends not trimmed") {
    // The spec's rule is "all whitespace characters are treated as equivalent" over `[ \t\r\n]`, so the
    // emitted key maps each whitespace CHARACTER to one space. A `\s+` collapse or a `trim` here would
    // make 'a  b' ~ 'a b' true, which the spec (and HL7's fhirpath.js) say is false.
    val out = sql(Equivalent(lit("String", "a b"), lit("String", "a  b")), _ => StringType)
    assert(out.contains("regexp_replace"))
    assert(!out.contains("\\s+") && !out.contains("trim("))
  }

  // The `~` normalization above must NOT leak into `distinct`/`in`: the CQL Reference defines both on
  // EQUALITY semantics ("duplicates eliminated using equality semantics"; "in the given list using
  // equality semantics"). Equivalence-based membership exists in the CQF reference only behind an
  // opt-in compatibility option, so these stay case-SENSITIVE.
  test("list membership uses EQUALITY, not equivalence (LANG-EQUIV)") {
    val in = In(lit("String", "condition"), ListExpr(Seq(lit("String", "Condition"))))
    val out = sql(in, { case _: ListExpr => ListType(StringType); case _ => StringType })
    assert(out == "array_contains(array('Condition'), 'condition')")
    assert(!out.contains("regexp_replace") && !out.contains("lower("))
  }

  test("distinct uses EQUALITY, not equivalence — the standalone operator now translates (LANG-EQUIV)") {
    // `Distinct` previously did not decode at all and tripped the Normalize verifier as
    // `UnknownExpr(Distinct)`, so `distinct { … }` was unreachable outside a query clause.
    val d = Distinct(ListExpr(Seq(lit("String", "Condition"), lit("String", "condition"))))
    val out = sql(d, _ => ListType(StringType))
    assert(out == "array_distinct(array('Condition', 'condition'))")
    assert(!out.contains("regexp_replace"))
  }

  test("arithmetic guards division by zero to null (LANG-RUNTIME-COND)") {
    assert(sql(Divide(lit("Integer", "22"), lit("Integer", "7"))) ==
      "CASE WHEN (7 = 0) THEN NULL ELSE round((22 / 7), 8) END")
  }

  test("precision-aware temporal comparison compares to the lesser precision") {
    // @2020 < @2021-06 : both literal, folds to static strings + lengths.
    val a = DateExpr(Seq(lit("Integer", "2020")))
    val b = DateExpr(Seq(lit("Integer", "2021"), lit("Integer", "6")))
    val out = sql(Less(a, b), { case _: DateExpr => DateType(None); case _ => AnyType })
    // lesser precision = 4 chars (year); decided true/false by the truncated prefixes, tie -> null-guarded.
    assert(out.contains("substr('2020', 1, 4)"))
    assert(out.contains("substr('2021-06', 1, 4)"))
    assert(out.contains("CASE WHEN"))
  }

  test("CalculateAgeAt(Year) renders the completed-age SQL over canonical strings — no UDF") {
    val birth = Property(None, Some("P"), "birthDate")
    val asOf  = DateExpr(Seq(lit("Integer", "2027"), lit("Integer", "1"), lit("Integer", "1")))
    val out = sql(CalculateAgeAt(birth, asOf, Some("Year")),
      { case _: Property => DateType(None); case _: DateExpr => DateTimeType(None); case _ => AnyType })
    assert(out.contains("year(") && out.contains("month(") && out.contains("dayofmonth("))
    assert(out.contains("make_interval") == false) // age is field arithmetic, not interval math
    assert(out.startsWith("CASE WHEN (") && out.contains("IS NULL"))
  }

  test("Date + 1 year lowers to a make_interval step preserving precision (the temporal tower)") {
    val d = DateExpr(Seq(lit("Integer", "2020"), lit("Integer", "1"), lit("Integer", "1")))
    val q = QuantityExpr(BigDecimal(1), Some("year"))
    val out = sql(Add(d, q), { case _: DateExpr => DateType(None); case _: QuantityExpr => QuantityType(Some("year")); case _ => AnyType })
    assert(out.contains("make_interval(") && out.contains("timestamp_ntz".toUpperCase) )
    assert(out.contains("date_format("))
  }

  // Repro for the temporal-tower blow-up: a precision-preserving date step (`closeTemporal`,
  // `dateArith`, `boundary`) used to expand a 7-way `CASE WHEN rankCol = <r> THEN CAST(ts + make_interval(…))`
  // that re-inlined the `ts` concat/cast tower AND the `rankOfLen` precision ladder in EVERY arm — 7 copies
  // of each per step. The refactor collapses it to ONE `make_interval` with per-slot picks (ARCH-TEMPORAL-CSE)
  // and hoists the precision ladder to a `__h<N>` column (ARCH-SQL-CSE).
  private def openHighDateInterval: IntervalExpr = IntervalExpr(
    low = Some(DateExpr(Seq(lit("Integer", "2026"), lit("Integer", "1"), lit("Integer", "1")))),
    high = Some(DateExpr(Seq(lit("Integer", "2027"), lit("Integer", "1"), lit("Integer", "1")))),
    lowClosed = true, highClosed = false) // open high => closeTemporal on the high bound
  private val dateIntervalTypes: Expr => CqlType = {
    case _: IntervalExpr => IntervalType(DateTimeType(None))
    case _: DateExpr     => DateTimeType(None)
    case _               => AnyType
  }
  private def countOf(needle: String, hay: String): Int =
    if (needle.isEmpty) 0 else hay.sliding(needle.length).count(_ == needle)

  test("a precision-preserving date step emits ONE make_interval, not a 7-way ts-duplicating CASE (ARCH-TEMPORAL-CSE)") {
    val out = sql(openHighDateInterval, dateIntervalTypes)
    // Exactly one interval add and one `ts` (concat/cast) tower for the single open bound — not seven.
    assert(countOf("make_interval(", out) == 1, s"expected 1 make_interval, got:\n$out")
    assert(countOf("AS TIMESTAMP_NTZ", out.toUpperCase) == 2, // tsOf's cast + the interval-add cast
      s"expected the ts tower to appear once (2 TIMESTAMP_NTZ casts), got:\n$out")
  }

  test("hoisting lifts the precision ladder to a __h<N> column referenced by name (ARCH-SQL-CSE)") {
    val counter = new AtomicInteger(0)
    val (rel, _) = SqlHoist.hoisted(SqlRel.TableRef("t"), counter) { h =>
      SqlColumnLowering.lower(openHighDateInterval, typeOf = dateIntervalTypes, hoist = h, hoistT = h)
    }
    val relSql = SqlRender.renderRel(rel, 0)
    // The 6-way `rankOfLen` precision ladder (`length(...) <= 4` … `<= 19`) is emitted ONCE as a hoisted
    // column, not re-expanded per interval slot inside `make_interval`.
    assert(relSql.contains("__h"), s"expected a hoisted __h column, got:\n$relSql")
    assert(countOf("<= 4)", relSql) == 1, s"expected the precision ladder emitted once, got:\n$relSql")
  }

  test("same-unit quantity comparison compares the raw values (LANG-QUANTITY)") {
    val q1 = QuantityExpr(BigDecimal(1), Some("mg"))
    val q2 = QuantityExpr(BigDecimal(2), Some("mg"))
    val out = sql(Less(q1, q2), _ => QuantityType(Some("mg")))
    // value fields compared directly (units equal, factors cancel); struct carries value+unit.
    assert(out.startsWith("(named_struct('value'"))
    assert(out.contains("'unit', 'mg').value < named_struct('value'"))
    assert(out.endsWith("'unit', 'mg').value)"))
  }

  test("point during a literal interval lowers to a bounded comparison (LANG-INTERVAL)") {
    val point = Property(None, Some("E"), "authoredOn")
    val iv = IntervalExpr(
      low = Some(DateExpr(Seq(lit("Integer", "2020"), lit("Integer", "1"), lit("Integer", "1")))),
      high = Some(DateExpr(Seq(lit("Integer", "2020"), lit("Integer", "12"), lit("Integer", "31")))),
      lowClosed = true, highClosed = true)
    val out = sql(In(point, iv), {
      case _: Property   => DateType(None)
      case _: IntervalExpr => IntervalType(DateType(None))
      case _: DateExpr   => DateType(None)
      case _             => AnyType
    })
    // point-in-interval = low <= point AND point <= high, precision-aware, over the literal bounds.
    assert(out.contains("substr('2020-01-01'"))
    assert(out.contains("substr('2020-12-31'"))
    assert(out.contains(" AND "))
  }

  test("array-encoded query lowers to filter/transform HOFs with fresh lambda vars") {
    // ({1,2,3}) X where X > 1 return X * 10
    val listSrc = ListExpr(Seq(lit("Integer", "1"), lit("Integer", "2"), lit("Integer", "3")))
    val q = Query(
      sources = Seq(QuerySource("X", listSrc)),
      where = Some(Greater(AliasRef("X"), lit("Integer", "1"))),
      returnExpr = Some(Multiply(AliasRef("X"), lit("Integer", "10"))),
      returnDistinct = false, sort = Nil, aggregate = None, relationships = Nil)
    val out = sql(q, { case `listSrc` => ListType(IntegerType); case _ => IntegerType })
    assert(out == "transform(filter(array(1, 2, 3), X_1 -> (X_1 > 1)), X_2 -> (X_2 * 10))")
  }

  test("`return distinct` dedupes on EQUALITY, matching the standalone operator (LANG-EQUIV)") {
    val listSrc = ListExpr(Seq(lit("String", "Condition"), lit("String", "condition")))
    val q = Query(
      sources = Seq(QuerySource("X", listSrc)),
      where = None, returnExpr = Some(AliasRef("X")),
      returnDistinct = true, sort = Nil, aggregate = None, relationships = Nil)
    val out = sql(q, { case `listSrc` => ListType(StringType); case _ => StringType })
    assert(out == "array_distinct(transform(array('Condition', 'condition'), X_1 -> X_1))")
  }

  test("with/without over an array source fails loudly (retrieve relationships only)") {
    val q = Query(
      sources = Seq(QuerySource("X", ListExpr(Seq(lit("Integer", "1"))))),
      where = None, returnExpr = None, returnDistinct = false, sort = Nil, aggregate = None,
      relationships = Seq(RelationshipClause("Y", ListExpr(Nil), lit("Boolean", "true"), negated = false)))
    val ex = intercept[com.databricks.cql.error.UnsupportedNode](SqlColumnLowering.lower(q))
    assert(ex.getMessage.toLowerCase.contains("relationship") || ex.getMessage.contains("with/without"))
  }

  test("Length of a List fails loudly: only Length of a String is built (LANG-STRING)") {
    val list = ListExpr(Seq(lit("Integer", "1")))
    val ex = intercept[com.databricks.cql.error.UnsupportedNode](
      SqlColumnLowering.lower(Length(list), typeOf = { case `list` => ListType(IntegerType); case _ => IntegerType }))
    assert(ex.getMessage.contains("Length"))
  }

  test("Length of a String counts UTF-16 code units, as a 32-bit Integer (LANG-STRING)") {
    assert(sql(Length(lit("String", "Hello World")), _ => StringType) ==
      "CAST((octet_length(encode('Hello World', 'UTF-16BE')) div 2) AS INT)")
  }

  test("ELM Instance of Quantity builds the canonical {value,unit} struct (LANG-INSTANCE)") {
    val inst = InstanceExpr("{urn:hl7-org:elm-types:r1}Quantity",
      Seq(TupleElement("value", lit("Decimal", "7")), TupleElement("unit", lit("String", "d"))))
    // value cast to the canonical DECIMAL(28,8) so downstream quantity ops read one schema.
    assert(sql(inst) == "named_struct('value', CAST(CAST(7 AS DECIMAL(38,8)) AS DECIMAL(28,8)), 'unit', 'd')")
  }

  test("ConvertQuantity rescales by the compile-time UCUM factor for a static source unit (LANG-QUANTITY)") {
    // convert (500 'g') to 'kg' : 500 * (g->kg = 0.001), result unit 'kg'.
    val q  = QuantityExpr(BigDecimal(500), Some("g"))
    val cv = ConvertQuantity(q, "kg")
    val out = sql(cv, { case _: QuantityExpr => QuantityType(Some("g")); case _ => AnyType })
    assert(out == "named_struct('value', CAST((named_struct('value', CAST(CAST(500 AS DECIMAL(38,8)) AS " +
      "DECIMAL(28,8)), 'unit', 'g').value * CAST(0.001 AS DECIMAL(38,8))) AS DECIMAL(28,8)), 'unit', 'kg')", out)
  }

  test("query let clause inlines each binding at its references (LANG-QUERY-LET)") {
    // ({1,2,3}) N let doubled: N*2, plusOne: doubled+1 return plusOne
    val listSrc = ListExpr(Seq(lit("Integer", "1"), lit("Integer", "2"), lit("Integer", "3")))
    val q = Query(
      sources = Seq(QuerySource("N", listSrc)), where = None,
      returnExpr = Some(QueryLetRef("plusOne")), returnDistinct = false,
      lets = Seq(LetClause("doubled", Multiply(AliasRef("N"), lit("Integer", "2"))),
                 LetClause("plusOne", Add(QueryLetRef("doubled"), lit("Integer", "1")))))
    val out = sql(q, { case `listSrc` => ListType(IntegerType); case _ => IntegerType })
    assert(out == "transform(array(1, 2, 3), N_1 -> ((N_1 * 2) + 1))", out)
  }
}
