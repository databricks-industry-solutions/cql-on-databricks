package com.databricks.cql.transpiler.lower

import com.databricks.cql.env.{Config, Context, DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.transpiler.emit.SqlRender
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Phase 3b tests for the RELATIONAL core of `SqlQueryLowering` (EMIT-SQL-EXECUTE): a FHIR query over
  * retrieve mounts lowered to a `SqlRel` of joins/filters, rendered to SQL TEXT — server-free, no Spark.
  * These lock that the relation encoding emits well-formed, readable SQL that reads each retrieve's bound relation
  * by NAME (BIND-CALL-SITE; the DAG the proto could not express). Real semantic correctness vs
  * the Java CQF reference is the Phase-5 gate; here we only pin the shape. */
@RunWith(classOf[JUnitRunner])
class SqlQueryLoweringRelationSpec extends AnyFunSuite {

  private val Encounter = "{http://hl7.org/fhir}Encounter"
  private def retrieve(dataType: String, codeProperty: Option[String] = None, codes: Option[Expr] = None,
                       dateProperty: Option[String] = None, dateRange: Option[Expr] = None): Retrieve =
    Retrieve(dataType, None, codeProperty, codes, dateProperty, dateRange)

  private def lit(t: String, v: String): Literal = Literal(s"{urn:hl7-org:elm-types:r1}$t", Some(v))

  /** BIND-CALL-SITE: every retrieve bound by rule to `acme.data.<type>`, every value set to one relation. */
  private def env(): SqlEvalEnv =
    SqlEvalEnv(context = Context.Patient, config = Config.defaults,
      data = Some(DataBinding(Some(DataBinding.Rule("acme.data")), Map.empty, com.databricks.cql.TestFixtures.stockShapes)),
      terminologyBinding = Some(TerminologyBinding(Some(Seq(RetrieveRelation("acme.terminology.valueset"))), Map.empty)))

  private val lib: Library = Library("Test", None, Nil, Nil, Nil, Nil,
    valueSets = Seq(ValueSetDef("RoutineEncounter", "urn:oid:2.999.1.102", None)),
    codes = Nil, concepts = Nil, contexts = Nil, statements = Nil)

  private def render(rel: com.databricks.cql.transpiler.emit.SqlRel): String = SqlRender.renderRel(rel, 0)

  /** A retrieve lowered inside define `D`, which uses every parameter `l` declares (EMIT-ROOT-INDEPENDENT). */
  private def scoped(l: Library): SqlEvalEnv =
    env().copy(currentDefine = Some("D"), defineParameters = Map("D" -> l.parameters.map(p => p.name -> p)))

  test("a bare retrieve lowers to grain-first rows over the mounted view (retrieveRelationFrame)") {
    val out = render(SqlQueryLowering.retrieveRelationFrame(retrieve(Encounter), env(), lib, _ => AnyType))
    assert(out.contains("FROM acme.data.encounter"))
    assert(out.contains("CAST(__resource.id AS STRING) AS __ordinal")) // DATA-EXPORT: the resource's own id
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
    assert(out.contains("FROM acme.data.encounter"))
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
    assert(out.contains("FROM acme.data.encounter"))
  }

  test("a value-set membership where renders as a LEFT SEMI JOIN against the mounted expansion") {
    // [Encounter] E where E.type in "RoutineEncounter"
    val q = Query(
      sources = Seq(QuerySource("E", retrieve(Encounter))),
      where = Some(InValueSet(Property(None, Some("E"), "type"), ValueSetRef("RoutineEncounter", None))),
      returnExpr = None)
    val out = render(SqlQueryLowering.relationFrameDirect(q, env(), lib,
      { case r: Retrieve if r.dataType == Encounter => ListType(NamedType("{http://hl7.org/fhir}Encounter")); case _ => AnyType }))
    assert(out.contains("LEFT SEMI JOIN"))
    // BIND-CALL-SITE: one keyed value-set relation, the set selected by canonical id.
    assert(out.contains("FROM acme.terminology.valueset"))
    assert(out.contains("valueset_id = 'urn:oid:2.999.1.102'"))
    assert(out.contains("exists(")) // coding membership HOF, no UDF
  }

  test("a code-filtered retrieve scan renders a membership semi-join at the mount (filteredRetrieve)") {
    val r = retrieve(Encounter, codeProperty = Some("type"), codes = Some(ValueSetRef("RoutineEncounter", None)))
    val out = render(SqlQueryLowering.filteredRetrieve(r, "Encounter", env(), lib, _ => AnyType))
    assert(out.contains("FROM acme.data.encounter"))
    assert(out.contains("LEFT SEMI JOIN"))
    assert(out.contains("FROM acme.terminology.valueset"))
    assert(out.contains("valueset_id = 'urn:oid:2.999.1.102'"))
  }

  test("a retrieve reads only the contract columns of its relation, with no arguments (BIND-CALL-SITE)") {
    val r = retrieve(Encounter, codeProperty = Some("type"), codes = Some(ValueSetRef("RoutineEncounter", None)))
    val out = render(SqlQueryLowering.filteredRetrieve(r, "Encounter", env(), lib, _ => AnyType))
    // BIND-CONTRACT: the engine builds its own two columns from the view's FHIR columns.
    assert(out.contains("AS __subject_id,") && out.contains("AS __resource\n  FROM acme.data.encounter\n"), out)
    assert(!out.contains("acme.data.encounter("))
    // BIND-CAPABILITY: the value-set membership is applied ABOVE the relation, which takes no request.
    assert(out.contains("LEFT SEMI JOIN"))
  }

  test("a retrieve with no data binding fails, naming its site (BIND-CALL-SITE)") {
    val unbound = env().copy(data = None)
    val err = intercept[UnsupportedNode](
      SqlQueryLowering.retrieveRelationFrame(retrieve(Encounter), unbound, lib, _ => AnyType))
    assert(err.getMessage.contains("[Encounter] needs data"))
  }

  /** LANG-VALUESET-VERSION: `filteredRetrieve`'s SQL for "RoutineEncounter" declared with `versions`. */
  private def membershipWith(versions: Option[String]*): String = {
    val declared = lib.copy(valueSets = versions.map(v => ValueSetDef("RoutineEncounter", "urn:oid:2.999.1.102", v)))
    val r = retrieve(Encounter, codeProperty = Some("type"), codes = Some(ValueSetRef("RoutineEncounter", None)))
    render(SqlQueryLowering.filteredRetrieve(r, "Encounter", env(), declared, _ => AnyType))
  }

  test("a pinned value set reads only that version's codes, and fails when the relation lacks it (LANG-VALUESET-VERSION)") {
    val out = membershipWith(Some("2025"))
    assert(out.contains("filter(collect_list(named_struct('system', system, 'code', code, 'version', version)), " +
      "x -> (x.version = '2025'))"), out)
    assert(out.contains("count_if((version = '2025'))"), out)
    assert(out.contains("has no version ''2025''"), out)
    assert(out.contains("raise_error("), out)
  }

  test("an unpinned value set reads every row but fails when they hold more than one version (LANG-VALUESET-VERSION)") {
    val out = membershipWith(None)
    assert(!out.contains("version = "), out)
    assert(out.contains("ELSE collect_list(named_struct('system', system, 'code', code)) END AS __codes"), out)
    assert(out.contains("LATERAL VIEW explode(__codes) __vs_codes AS __code"), out)
    assert(out.contains("size(collect_set(coalesce(version, '')))"), out)
    assert(out.contains("holds several versions"), out)
  }

  test("a value set no relation holds fails, naming the relations (LANG-VALUESET-VERSION)") {
    val out = membershipWith(None)
    assert(out.contains("(count(*) = 0)"), out)
    assert(out.contains("is in no terminology relation the run binds (acme.terminology.valueset)"), out)
  }

  test("a value set declared with two versions fails at compile, naming both (LANG-VALUESET-VERSION)") {
    val err = intercept[UnsupportedNode](membershipWith(Some("2025"), Some("2026")))
    assert(err.getMessage.contains("declared with different versions (2025, 2026)"), err.getMessage)
    val mixed = intercept[UnsupportedNode](membershipWith(Some("2025"), None))
    assert(mixed.getMessage.contains("(2025, none)"), mixed.getMessage)
  }

  test("a membership with no terminology binding fails (BIND-CALL-SITE)") {
    val r = retrieve(Encounter, codeProperty = Some("type"), codes = Some(ValueSetRef("RoutineEncounter", None)))
    val err = intercept[UnsupportedNode](
      SqlQueryLowering.filteredRetrieve(r, "Encounter", env().copy(terminologyBinding = None), lib, _ => AnyType))
    assert(err.getMessage.contains("needs terminology"))
  }

  test("a bare retrieve's request has every predicate absent (BIND-RETRIEVE-REQUEST)") {
    val req = SqlQueryLowering.retrieveCall(None, "Encounter", lib, env()).requestJson
    assert(req("valueSet").isNull && req("codes").arr.isEmpty && req("dateProperty").isNull &&
      req("dateLow").isNull && req("dateHigh").isNull && req("parameters").obj.isEmpty)
  }

  test("EMIT-ROOT-INDEPENDENT: retrieveCall names no library, and passes only the current define's parameters") {
    val libV = lib.copy(version = Some("1.2.000"))
    val req = SqlQueryLowering.retrieveCall(None, "Encounter", libV, env()).requestJson
    assert(!ujson.write(req).contains("Test"))
    // The context base belongs to no define, so its parameters map is empty.
    assert(req("parameters").obj.isEmpty)
  }

  test("retrieveCall carries literal parameter defaults in its request (BIND-RETRIEVE-REQUEST)") {
    val dt = "{urn:hl7-org:elm-types:r1}DateTime"
    val mp = IntervalExpr(Some(Literal(dt, Some("2026-01-01"))), Some(Literal(dt, Some("2026-12-31"))),
      lowClosed = true, highClosed = false)
    val libP = lib.copy(parameters = Seq(
      ParameterDef("Measurement Period", Some("Interval<DateTime>"), Some(mp)),
      ParameterDef("Threshold", Some("Integer"), Some(Literal("{urn:hl7-org:elm-types:r1}Integer", Some("40"))))))
    val out = SqlQueryLowering.retrieveCall(None, "Encounter", libP, scoped(libP)).requestJson("parameters")
    // An INTERVAL default lands as a `{low, high}` object with its closure, a scalar as its string — one open
    // container keyed by the CQL name, spaces and all.
    assert(out == ujson.Obj(
      "Measurement Period" -> ujson.Obj("low" -> "2026-01-01", "lowClosed" -> true, "high" -> "2026-12-31",
        "highClosed" -> false),
      "Threshold" -> "40"))
  }

  test("retrieveCall carries DIRECT codes, with no value set") {
    val libC = lib.copy(codes = Seq(CodeDef("Aspirin", "1191", Some("RxNorm"))),
      codeSystems = Seq(CodeSystemDef("RxNorm", "http://www.nlm.nih.gov/research/umls/rxnorm", None)))
    val r = retrieve("{http://hl7.org/fhir}MedicationRequest", codeProperty = Some("medication"),
      codes = Some(ToList(CodeRef("Aspirin", None))))
    val req = SqlQueryLowering.retrieveCall(Some(r), "MedicationRequest", libC, env()).requestJson
    assert(req("valueSet").isNull) // no value set on a direct-code retrieve
    assert(req("codes") == ujson.Arr(ujson.Obj("system" -> "http://www.nlm.nih.gov/research/umls/rxnorm",
      "code" -> "1191")))
  }

  test("retrieveCall renders a Date/DateTime CONSTRUCTOR bound at its authored precision") {
    // `@2023-06-01` reaches the IR as DateTimeExpr(Literal(2023), Literal(6), Literal(1)) — not a string
    // literal. A PARTIAL bound stays partial: padding would widen the window the binding prunes on.
    def dtParts(ns: Int*): Expr = DateTimeExpr(ns.map(n => lit("Integer", n.toString)))
    def window(lo: Expr, hi: Expr) = IntervalExpr(Some(lo), Some(hi), lowClosed = true, highClosed = true)
    def bounds(lo: Expr, hi: Expr): (ujson.Value, ujson.Value) = {
      val a = SqlQueryLowering.retrieveCall(
        Some(retrieve(Encounter, dateProperty = Some("period"), dateRange = Some(window(lo, hi)))),
        "Encounter", lib, env()).requestJson
      (a("dateLow"), a("dateHigh"))
    }
    def both(lo: String, hi: String): (ujson.Value, ujson.Value) = (ujson.Str(lo), ujson.Str(hi))
    assert(bounds(dtParts(2023, 6, 1), dtParts(2023, 6, 30)) == both("2023-06-01", "2023-06-30"))
    assert(bounds(dtParts(2023), dtParts(2024)) == both("2023", "2024"))            // year precision kept
    assert(bounds(dtParts(2023, 6, 1, 12, 30, 0), dtParts(2023, 6, 1, 13, 0, 0)) ==
      both("2023-06-01T12:30:00", "2023-06-01T13:00:00"))
    assert(bounds(DateExpr(Seq(lit("Integer", "2023"), lit("Integer", "1"), lit("Integer", "5"))),
                  DateExpr(Seq(lit("Integer", "2023"), lit("Integer", "12"), lit("Integer", "31")))) ==
      both("2023-01-05", "2023-12-31"))
    // A non-literal component leaves the bound ABSENT rather than guess.
    assert(bounds(DateTimeExpr(Seq(ExpressionRef("SomeYear", None))), dtParts(2024))._1.isNull)
  }

  test("retrieveCall carries a Measurement Period interval default into the parameters map") {
    // Every eCQM declares this; the bound is a DateTime CONSTRUCTOR, which is why the retired
    // context.measurementPeriod field was always absent in the real corpus.
    val mp = IntervalExpr(
      Some(DateTimeExpr(Seq(lit("Integer", "2026"), lit("Integer", "1"), lit("Integer", "1")))),
      Some(DateTimeExpr(Seq(lit("Integer", "2026"), lit("Integer", "12"), lit("Integer", "31")))),
      lowClosed = true, highClosed = true)
    val libP = lib.copy(parameters = Seq(ParameterDef("Measurement Period", Some("Interval<DateTime>"), Some(mp))))
    val out = SqlQueryLowering.retrieveCall(None, "Encounter", libP, scoped(libP)).requestJson("parameters")
    // The CLOSURE is carried, not assumed — the standard eCQM period is HALF-OPEN, and a binding that
    // read a bare `high` as inclusive would over-select the boundary instant.
    assert(out == ujson.Obj("Measurement Period" -> ujson.Obj("low" -> "2026-01-01", "lowClosed" -> true,
      "high" -> "2026-12-31", "highClosed" -> true)))
  }

  test("retrieveCall emits the folded date window as inclusive bounds; a half-open range stays absent") {
    val dt = "{urn:hl7-org:elm-types:r1}DateTime"
    def range(closedHigh: Boolean) = IntervalExpr(Some(Literal(dt, Some("2026-01-01"))),
      Some(Literal(dt, Some("2026-12-31"))), lowClosed = true, highClosed = closedHigh)
    val closed = retrieve(Encounter, dateProperty = Some("period"), dateRange = Some(range(true)))
    val cArgs = SqlQueryLowering.retrieveCall(Some(closed), "Encounter", lib, env()).requestJson
    assert(cArgs("dateProperty").str == "period" && cArgs("dateLow").str == "2026-01-01" &&
      cArgs("dateHigh").str == "2026-12-31")
    // A half-open interval leaves the WINDOW absent rather than shift a bound (CORE-NO-SILENT-MISLOWER);
    // the date PROPERTY is still named.
    val open = retrieve(Encounter, dateProperty = Some("period"), dateRange = Some(range(false)))
    val oArgs = SqlQueryLowering.retrieveCall(Some(open), "Encounter", lib, env()).requestJson
    assert(oArgs("dateProperty").str == "period" && oArgs("dateLow").isNull && oArgs("dateHigh").isNull)
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
    assert(!out.contains("C.onsetPeriod"), s"the age-range arm must not read the Period slot; got:\n$out")
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
