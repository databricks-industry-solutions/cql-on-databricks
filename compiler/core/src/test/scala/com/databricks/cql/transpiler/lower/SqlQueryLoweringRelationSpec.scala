package com.databricks.cql.transpiler.lower

import com.databricks.cql.env.{Config, Context, SourceBinding}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.transpiler.emit.SqlRender
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Phase 3b tests for the RELATIONAL core of `SqlQueryLowering` (EMIT-SQL-EXECUTE): a FHIR query over
  * retrieve mounts lowered to a `SqlRel` of joins/filters, rendered to SQL TEXT — server-free, no Spark.
  * These lock that the relation encoding emits well-formed, readable SQL that references the mounted
  * `retrieve_<Type>` views by NAME (the DAG the proto could not express). Real semantic correctness vs
  * the Java CQF reference is the Phase-5 gate; here we only pin the shape. */
@RunWith(classOf[JUnitRunner])
class SqlQueryLoweringRelationSpec extends AnyFunSuite {

  private val Encounter = "{http://hl7.org/fhir}Encounter"
  private def retrieve(dataType: String, codeProperty: Option[String] = None, codes: Option[Expr] = None,
                       dateProperty: Option[String] = None, dateRange: Option[Expr] = None): Retrieve =
    Retrieve(dataType, None, codeProperty, codes, dateProperty, dateRange)

  private def lit(t: String, v: String): Literal = Literal(s"{urn:hl7-org:elm-types:r1}$t", Some(v))

  private def env(terminology: Boolean = false,
                  sources: Map[String, SourceBinding] = Map.empty,
                  terminologyTable: Option[String] = None): SqlEvalEnv =
    SqlEvalEnv(context = Context.Patient, config = Config.defaults,
      terminology = terminology, sources = sources, terminologyTable = terminologyTable)

  private val lib: Library = Library("Test", None, Nil, Nil, Nil, Nil,
    valueSets = Seq(ValueSetDef("RoutineEncounter", "urn:oid:2.999.1.102", None)),
    codes = Nil, concepts = Nil, contexts = Nil, statements = Nil)

  private def render(rel: com.databricks.cql.transpiler.emit.SqlRel): String = SqlRender.renderRel(rel, 0)

  /** A retrieve lowered inside define `D`, which uses every parameter `l` declares (EMIT-ROOT-INDEPENDENT). */
  private def scoped(l: Library): SqlEvalEnv =
    env().copy(currentDefine = Some("D"), defineParameters = Map("D" -> l.parameters.map(p => p.name -> p)))

  test("a bare retrieve lowers to grain-first rows over the mounted view (retrieveRelationFrame)") {
    val out = render(SqlQueryLowering.retrieveRelationFrame(retrieve(Encounter), env(), lib, _ => AnyType))
    assert(out.contains("FROM retrieve_Encounter"))
    assert(out.contains("__resource.__resource_id AS __ordinal"))
    assert(out.contains("__resource AS __val"))
    assert(out.contains("__subject_id"))
  }

  test("a single-source relation query with a where lowers grain-first (relationFrameDirect)") {
    // [Encounter] E where E.status = 'finished'
    val q = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = Some(Equal(Property(None, Some("E"), "status"), lit("String", "finished"))),
      returnExpr = None)
    val out = render(SqlQueryLowering.relationFrameDirect(q, env(), lib, _ => AnyType))
    assert(out.contains("FROM retrieve_Encounter"))
    assert(out.contains("__resource AS E"))        // aliased source struct column
    assert(out.contains("(E.status = 'finished')")) // residual 3VL predicate
    assert(out.contains("__ord_E AS __ordinal"))    // single-source ordinal
    assert(out.contains("E AS __val"))              // element = the source struct
  }

  test("exists(query) is DISTINCT __subject_id over the combined relation (relationExists)") {
    val q = Query(sources = Seq(QuerySource("E", retrieve(Encounter))), where = None, returnExpr = None)
    val out = render(SqlQueryLowering.relationExists(q, env(), lib, _ => AnyType))
    assert(out.startsWith("SELECT DISTINCT"))
    assert(out.contains("__subject_id"))
    assert(out.contains("FROM retrieve_Encounter"))
  }

  test("a value-set membership where renders as a LEFT SEMI JOIN against the mounted expansion") {
    // [Encounter] E where E.type in "RoutineEncounter"
    val q = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = Some(InValueSet(Property(None, Some("E"), "type"), ValueSetRef("RoutineEncounter", None))),
      returnExpr = None)
    val out = render(SqlQueryLowering.relationFrameDirect(q, env(terminology = true), lib,
      { case r: Retrieve if r.dataType == Encounter => ListType(NamedType("{http://hl7.org/fhir}Encounter")); case _ => AnyType }))
    assert(out.contains("LEFT SEMI JOIN"))
    // BIND-TERMINOLOGY-TABLE: one keyed `valuesets` table, the set selected by canonical id.
    assert(out.contains("FROM valuesets"))
    assert(out.contains("valueset_id = 'urn:oid:2.999.1.102'"))
    assert(out.contains("exists(")) // coding membership HOF, no UDF
  }

  test("a value-set membership with terminologyTable emits the FQN instead of bare 'valuesets' (BIND-TERMINOLOGY-TABLE)") {
    val q = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = Some(InValueSet(Property(None, Some("E"), "type"), ValueSetRef("RoutineEncounter", None))),
      returnExpr = None)
    val out = render(SqlQueryLowering.relationFrameDirect(q,
      env(terminology = true, terminologyTable = Some("acme.bindings.valuesets")), lib,
      { case r: Retrieve if r.dataType == Encounter => ListType(NamedType("{http://hl7.org/fhir}Encounter")); case _ => AnyType }))
    assert(out.contains("FROM acme.bindings.valuesets"))
    assert(!out.contains("FROM valuesets\n") && !out.contains("FROM valuesets "))
    assert(out.contains("valueset_id = 'urn:oid:2.999.1.102'"))
  }

  test("a code-filtered retrieve scan renders a membership semi-join at the mount (filteredRetrieve)") {
    val r = retrieve(Encounter, codeProperty = Some("type"), codes = Some(ValueSetRef("RoutineEncounter", None)))
    val out = render(SqlQueryLowering.filteredRetrieve(r, "Encounter", env(terminology = true), lib, _ => AnyType))
    assert(out.contains("retrieve_Encounter"))
    assert(out.contains("LEFT SEMI JOIN"))
    assert(out.contains("FROM valuesets"))
    assert(out.contains("valueset_id = 'urn:oid:2.999.1.102'"))
  }

  test("a filtered retrieve calls FROM fn(...) with the SEVEN explicit literal args (BIND-EXPLICIT-PARAMS)") {
    val r = retrieve(Encounter, codeProperty = Some("type"), codes = Some(ValueSetRef("RoutineEncounter", None)))
    val out = render(SqlQueryLowering.filteredRetrieve(r, "Encounter", env(terminology = true), lib, _ => AnyType))
    // The mount is a UDTF CALL, not a bare `FROM retrieve_Encounter` view, and names no library
    // (EMIT-ROOT-INDEPENDENT).
    assert(out.contains("retrieve_Encounter('urn:oid:"))
    assert(!out.contains("named_struct('id'"))
    assert(!out.contains("FROM retrieve_Encounter\n") && !out.contains("FROM retrieve_Encounter "))
    // BIND-PUSHDOWN: this retrieve's statically-known predicates are POSITIONAL literals — value_set,
    // then code_property, then the (empty) direct-codes array. Absent scalars are NULL.
    assert(out.contains("'urn:oid:2.999.1.102', 'type', array(), NULL, NULL, NULL, map())"))
    // BIND-CAPABILITY: the value-set membership is STILL re-applied ABOVE the call (the args are advisory).
    assert(out.contains("LEFT SEMI JOIN"))
    assert(out.contains("FROM valuesets"))
  }

  test("a bare retrieve calls fn with every PREDICATE arg absent") {
    val out = render(SqlQueryLowering.retrieveRelationFrame(retrieve(Encounter), env(), lib, _ => AnyType))
    assert(out.contains("retrieve_Encounter(NULL, NULL, array(), NULL, NULL, NULL, map())"))
  }

  test("EMIT-ROOT-INDEPENDENT: retrieveArgs names no library, and passes only the current define's parameters") {
    val libV = lib.copy(version = Some("1.2.000"))
    val args = SqlQueryLowering.retrieveArgs(None, "Encounter", libV, env()).map(SqlRender.expr)
    assert(args.size == 7 && !args.exists(_.contains("Test")))
    // The context base belongs to no define, so its parameters map is empty.
    assert(args.last == "map()")
  }

  test("retrieveArgs maps literal parameter defaults to MAP<STRING, VARIANT> (BIND-EXPLICIT-PARAMS)") {
    val dt = "{urn:hl7-org:elm-types:r1}DateTime"
    val mp = IntervalExpr(Some(Literal(dt, Some("2026-01-01"))), Some(Literal(dt, Some("2026-12-31"))),
      lowClosed = true, highClosed = false)
    val libP = lib.copy(parameters = Seq(
      ParameterDef("Measurement Period", Some("Interval<DateTime>"), Some(mp)),
      ParameterDef("Threshold", Some("Integer"), Some(Literal("{urn:hl7-org:elm-types:r1}Integer", Some("40"))))))
    val out = SqlRender.expr(SqlQueryLowering.retrieveArgs(None, "Encounter", libP, scoped(libP)).last)
    // An INTERVAL default lands as a `{low, high}` VARIANT OBJECT (where context.measurementPeriod went),
    // a scalar as a VARIANT SCALAR — one open container keyed by the CQL name, spaces and all.
    assert(out == "map('Measurement Period', to_variant_object(named_struct('low', '2026-01-01', 'lowClosed', true, " +
      "'high', '2026-12-31', 'highClosed', false)), 'Threshold', CAST('40' AS VARIANT))")
  }

  test("retrieveArgs emits DIRECT codes and the RESOLVED code slot, not the raw choice element") {
    // A `value[x]`-choice code property resolves to its physical slot so a binding navigates what the
    // plan filters on: MedicationRequest.medication -> medicationCodeableConcept.
    val libC = lib.copy(codes = Seq(CodeDef("Aspirin", "1191", Some("RxNorm"))),
      codeSystems = Seq(CodeSystemDef("RxNorm", "http://www.nlm.nih.gov/research/umls/rxnorm", None)))
    val r = retrieve("{http://hl7.org/fhir}MedicationRequest", codeProperty = Some("medication"),
      codes = Some(ToList(CodeRef("Aspirin", None))))
    val args = SqlQueryLowering.retrieveArgs(Some(r), "MedicationRequest", libC, env()).map(SqlRender.expr)
    assert(args(0) == "NULL") // no value set on a direct-code retrieve
    assert(args(1) == "'medicationCodeableConcept'")
    assert(args(2) == "array(named_struct('system', 'http://www.nlm.nih.gov/research/umls/rxnorm', 'code', '1191'))")
  }

  test("retrieveArgs renders a Date/DateTime CONSTRUCTOR bound at its authored precision") {
    // `@2023-06-01` reaches the IR as DateTimeExpr(Literal(2023), Literal(6), Literal(1)) — not a string
    // literal. A PARTIAL bound stays partial: padding would widen the window the binding prunes on.
    def dtParts(ns: Int*): Expr = DateTimeExpr(ns.map(n => lit("Integer", n.toString)))
    def window(lo: Expr, hi: Expr) = IntervalExpr(Some(lo), Some(hi), lowClosed = true, highClosed = true)
    def bounds(lo: Expr, hi: Expr): (String, String) = {
      val a = SqlQueryLowering.retrieveArgs(
        Some(retrieve(Encounter, dateProperty = Some("period"), dateRange = Some(window(lo, hi)))),
        "Encounter", lib, env()).map(SqlRender.expr)
      (a(4), a(5))
    }
    assert(bounds(dtParts(2023, 6, 1), dtParts(2023, 6, 30)) == ("'2023-06-01'", "'2023-06-30'"))
    assert(bounds(dtParts(2023), dtParts(2024)) == ("'2023'", "'2024'"))            // year precision kept
    assert(bounds(dtParts(2023, 6, 1, 12, 30, 0), dtParts(2023, 6, 1, 13, 0, 0)) ==
      ("'2023-06-01T12:30:00'", "'2023-06-01T13:00:00'"))
    assert(bounds(DateExpr(Seq(lit("Integer", "2023"), lit("Integer", "1"), lit("Integer", "5"))),
                  DateExpr(Seq(lit("Integer", "2023"), lit("Integer", "12"), lit("Integer", "31")))) ==
      ("'2023-01-05'", "'2023-12-31'"))
    // A non-literal component leaves the bound ABSENT rather than guess.
    assert(bounds(DateTimeExpr(Seq(ExpressionRef("SomeYear", None))), dtParts(2024))._1 == "NULL")
  }

  test("retrieveArgs carries a Measurement Period interval default into the parameters map") {
    // Every eCQM declares this; the bound is a DateTime CONSTRUCTOR, which is why the retired
    // context.measurementPeriod field was always absent in the real corpus.
    val mp = IntervalExpr(
      Some(DateTimeExpr(Seq(lit("Integer", "2026"), lit("Integer", "1"), lit("Integer", "1")))),
      Some(DateTimeExpr(Seq(lit("Integer", "2026"), lit("Integer", "12"), lit("Integer", "31")))),
      lowClosed = true, highClosed = true)
    val libP = lib.copy(parameters = Seq(ParameterDef("Measurement Period", Some("Interval<DateTime>"), Some(mp))))
    val out = SqlRender.expr(SqlQueryLowering.retrieveArgs(None, "Encounter", libP, scoped(libP)).last)
    // The CLOSURE is carried, not assumed — the standard eCQM period is HALF-OPEN, and a binding that
    // read a bare `high` as inclusive would over-select the boundary instant.
    assert(out == "map('Measurement Period', to_variant_object(named_struct('low', '2026-01-01', 'lowClosed', true, " +
      "'high', '2026-12-31', 'highClosed', true)))")
  }

  test("retrieveArgs emits the folded date window as inclusive bounds; a half-open range stays absent") {
    val dt = "{urn:hl7-org:elm-types:r1}DateTime"
    def range(closedHigh: Boolean) = IntervalExpr(Some(Literal(dt, Some("2026-01-01"))),
      Some(Literal(dt, Some("2026-12-31"))), lowClosed = true, highClosed = closedHigh)
    val closed = retrieve(Encounter, dateProperty = Some("period"), dateRange = Some(range(true)))
    val cArgs = SqlQueryLowering.retrieveArgs(Some(closed), "Encounter", lib, env()).map(SqlRender.expr)
    assert(cArgs(3) == "'period'" && cArgs(4) == "'2026-01-01'" && cArgs(5) == "'2026-12-31'")
    // A half-open interval leaves the WINDOW absent rather than shift a bound (CORE-NO-SILENT-MISLOWER);
    // the date PROPERTY is still named.
    val open = retrieve(Encounter, dateProperty = Some("period"), dateRange = Some(range(false)))
    val oArgs = SqlQueryLowering.retrieveArgs(Some(open), "Encounter", lib, env()).map(SqlRender.expr)
    assert(oArgs(3) == "'period'" && oArgs(4) == "NULL" && oArgs(5) == "NULL")
  }

  test("BIND-MANIFEST: a bound source resolves the retrieve to its catalog-qualified UDTF, else the convention") {
    // Encounter is bound; Condition (unmapped) falls back to the convention `retrieve_Condition`.
    val bound = env(sources = Map("Encounter" -> SourceBinding("fetch_encounter", Some("acme.fhir"))))
    val enc = render(SqlQueryLowering.retrieveRelationFrame(retrieve(Encounter), bound, lib, _ => AnyType))
    assert(enc.contains("FROM acme.fhir.fetch_encounter(NULL"))
    assert(!enc.contains("retrieve_Encounter("))
    val cond = render(SqlQueryLowering.retrieveRelationFrame(retrieve("{http://hl7.org/fhir}Condition"), bound, lib, _ => AnyType))
    assert(cond.contains("FROM retrieve_Condition(NULL"))
  }

  test("with / without lower to correlated LEFT SEMI / LEFT ANTI joins on subject + such-that") {
    // [Encounter] E with [Encounter] F such that F.status = E.status
    def rel(negated: Boolean) = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = None, returnExpr = None,
      relationships = Seq(RelationshipClause("F", retrieve(Encounter),
        Equal(Property(None, Some("F"), "status"), Property(None, Some("E"), "status")), negated = negated)))
    val withOut = render(SqlQueryLowering.relationFrameDirect(rel(negated = false), env(), lib, _ => AnyType))
    assert(withOut.contains("LEFT SEMI JOIN"))
    assert(withOut.contains("(__l.__subject_id = __rel_F.__subject_id)"))
    assert(withOut.contains("(F.status = E.status)"))
    val without = render(SqlQueryLowering.relationFrameDirect(rel(negated = true), env(), lib, _ => AnyType))
    assert(without.contains("LEFT ANTI JOIN"))
  }

  test("a single-key sort lowers to a window row_number re-ordinal (relationFrameSortedDirect)") {
    // [Encounter] E sort by status
    val q = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = None, returnExpr = None,
      sort = Seq(SortItem(direction = "asc", path = Some("status"))))
    val out = render(SqlQueryLowering.relationFrameSortedDirect(q, env(), lib, _ => AnyType))
    assert(out.contains("row_number() OVER (PARTITION BY __subject_id ORDER BY"))
    assert(out.contains("- 1) AS __ordinal") || out.contains("- 1 AS __ordinal") || out.contains("- 1) AS `__ordinal`"))
  }

  test("`choice.low as Quantity` dispatches to the AGE-RANGE slot even when asType is the raw ELM QName (LANG-CHOICE-MODEL)") {
    // A helper library's `onsetSpan`/`abatementSpan` age-range arm:
    //   when choice is Interval<Quantity> then Interval[Patient.birthDate + (choice.low as Quantity), …]
    // The translator emits that cast with an `asTypeSpecifier` (not `asType`), so it loads with the raw
    // QName `{urn:hl7-org:elm-types:r1}Quantity`. A literal "Quantity" match fell through to the
    // Interval<DateTime> bound rewrite, reading `onsetPeriod.start` (a dateTime STRING) as the quantity —
    // the residual `as Quantity` then scalar-promoted it, emitting `CAST(<string> AS DECIMAL(28,8))` with
    // the dimensionless unit '1' (runtime CAST_INVALID_INPUT on any Period-onset Condition).
    val Condition = "{http://hl7.org/fhir}Condition"
    def onsetBound(bound: String): Expr =
      As(Property(Some(FunctionRef("ToValue", Some("FHIRHelpers"),
        Seq(Property(Some(AliasRef("C")), None, "onset")))), None, bound),
        "{urn:hl7-org:elm-types:r1}Quantity", strict = false, choiceTypes = Nil)
    val q = Query(
      sources = Seq(QuerySource("C", retrieve(Condition))),
      where = None,
      returnExpr = Some(IntervalExpr(Some(onsetBound("low")), Some(onsetBound("high")),
        lowClosed = true, highClosed = false)))
    val out = render(SqlQueryLowering.relationFrameDirect(q, env(), lib,
      { case r: Retrieve if r.dataType == Condition => ListType(NamedType(Condition)); case _ => AnyType }))
    // the Range slot's Quantity bounds, normalized by ToQuantity to {value, unit}
    assert(out.contains("C.onsetRange.low.value"), s"expected the onsetRange low bound; got:\n$out")
    assert(out.contains("C.onsetRange.high.value"), s"expected the onsetRange high bound; got:\n$out")
    // NEVER the Period slot, and never a dateTime string cast to a decimal quantity value
    assert(!out.contains("onsetPeriod"), s"the age-range arm must not read the Period slot; got:\n$out")
    assert(!out.contains("AS DECIMAL(28,8)), 'unit', '1')"),
      s"a dateTime must not be scalar-promoted to a dimensionless Quantity; got:\n$out")
  }

  test("a heterogeneous-resource union source realizes into a discriminated Choice struct (LANG-CHOICE-RESOURCE)") {
    // ([Encounter] union [Condition]) X  — different resource types: one discriminated Choice struct
    // (each resource member a demand-pruned identity HANDLE), NOT a schema-incompatible raw-body union.
    val u = Union(
      As(retrieve(Encounter), "Encounter", strict = false, choiceTypes = Seq("Encounter", "Condition")),
      As(retrieve("{http://hl7.org/fhir}Condition"), "Condition", strict = false, choiceTypes = Seq("Encounter", "Condition")))
    val q = Query(sources = Seq(QuerySource("X", u)), where = None, returnExpr = None)
    val typeOf: Expr => CqlType = {
      case r: Retrieve if r.dataType.endsWith("Condition") => ListType(NamedType("Condition"))
      case _: Retrieve                                     => ListType(NamedType("Encounter"))
      case _                                               => AnyType
    }
    val out = render(SqlQueryLowering.relationFrameDirect(q, env(), lib, typeOf))
    assert(out.contains("__choice"))
    assert(out.contains("UNION ALL"))
    // the demand-pruned resource handle promotes identity to the top of each member slot
    assert(out.contains("__resource_type") && out.contains("__resource_id"))
  }
}
