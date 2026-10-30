package com.databricks.cql.transpiler.lower

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.ir.{Case => CqlCase, _}
import com.databricks.cql.sema.{FhirModel, FhirSchema, Ucum}
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.emit.SqlExpr
import com.databricks.cql.transpiler.emit.SqlExpr._
import com.databricks.cql.transpiler.emit.SqlNames

/** SQL-target scalar lowering (EMIT-SQL-EXECUTE): the SQL twin of the compiler's `ColumnLowering`. A
  * pure function from a scalar kernel `Expr` to a [[SqlExpr]] (SQL AST) — never a Spark `Column` and
  * never a Connect proto. The semantic ALGORITHMS are copied from `ColumnLowering` verbatim (CQL 3VL,
  * precision-aware temporal comparison, canonical-string dates, UCUM quantity algebra, interval/Allen
  * relations, discriminated Choice); only the target type changes, via a small operator DSL so the port
  * reads 1:1 with the original.
  *
  * WHY THIS FIXES THE OOM: in `ColumnLowering` these towers are inlined into a share-free proto and
  * re-serialized thousands of times (one measure's `Patient.birthDate` tower x5546). Here each is a
  * [[SqlExpr]] the frame lowering (Phase 3) binds to ONE CTE/column referenced by name.
  *
  * Total by rejection: an unmodeled node raises `UnsupportedNode` (CORE-NO-SILENT-MISLOWER). The
  * heterogeneous-RESOURCE Choice handle (ARCH-RESOURCE-HANDLE) and Query (SqlQueryLowering, Phase 3) are
  * the two remaining frontiers. */
object SqlColumnLowering {

  type RefResolver = String => SqlExpr
  private[lower] val rejectRefs: RefResolver = name =>
    throw UnsupportedNode("ExpressionRef", s"unresolved reference '$name' (lower a define DAG with a resolver)")
  private val rejectAlias: RefResolver = name =>
    throw UnsupportedNode("AliasRef", s"query alias '$name' referenced outside a query")

  /** A SQL sub-expression hoister — binds a duplication-prone sub-expression to a named CTE/column and
    * returns a reference. Default identity (the frame lowering wires the real binding in Phase 3). */
  type Hoister = SqlExpr => SqlExpr
  private[lower] val noHoist: Hoister = identity

  private type TConst = Option[String]

  private val FhirPrimitiveConversions: Set[String] =
    Set("ToString", "ToBoolean", "ToInteger", "ToLong", "ToDecimal", "ToTime")

  private sealed trait Cmp
  private case object Lt extends Cmp; private case object Gt extends Cmp
  private case object Le extends Cmp; private case object Ge extends Cmp
  private case object Eq extends Cmp; private case object Ne extends Cmp
  private case object Equiv extends Cmp

  // A tiny operator DSL so the port reads like the Column original. Every binary infix is parenthesized
  // by the renderer, so precedence is always explicit.
  private implicit class Ops(private val e: SqlExpr) extends AnyVal {
    def ===(o: SqlExpr): SqlExpr = Infix("=", e, o)
    def =!=(o: SqlExpr): SqlExpr = Infix("<>", e, o)
    def <(o: SqlExpr): SqlExpr = Infix("<", e, o)
    def >(o: SqlExpr): SqlExpr = Infix(">", e, o)
    def <=(o: SqlExpr): SqlExpr = Infix("<=", e, o)
    def >=(o: SqlExpr): SqlExpr = Infix(">=", e, o)
    def &&(o: SqlExpr): SqlExpr = Infix("AND", e, o)
    def ||(o: SqlExpr): SqlExpr = Infix("OR", e, o)
    def <=>(o: SqlExpr): SqlExpr = Infix("<=>", e, o)
    def +(o: SqlExpr): SqlExpr = Infix("+", e, o)
    def -(o: SqlExpr): SqlExpr = Infix("-", e, o)
    def *(o: SqlExpr): SqlExpr = Infix("*", e, o)
    def /(o: SqlExpr): SqlExpr = Infix("/", e, o)
    def %(o: SqlExpr): SqlExpr = Infix("%", e, o)
    def unary_! : SqlExpr = Prefix("NOT", e)
    def isNull: SqlExpr = Postfix(e, "IS NULL")
    def isNotNull: SqlExpr = Postfix(e, "IS NOT NULL")
    def getField(n: String): SqlExpr = Field(e, n)
    def cast(t: String): SqlExpr = Cast(e, sqlType(t))
    def substr(pos: SqlExpr, len: SqlExpr): SqlExpr = Call("substr", Seq(e, pos, len))
  }

  private def ifc(c: SqlExpr, t: SqlExpr, e: SqlExpr): SqlExpr = CqlSql.caseWhen(Seq(c -> t), e)
  private def whenChain(pairs: Seq[(SqlExpr, SqlExpr)], orElse: SqlExpr): SqlExpr = CqlSql.caseWhen(pairs, orElse)
  private val nullBool: SqlExpr = Cast(Null, "BOOLEAN")

  // A tiny indirection so a bare `Case` never collides with the IR's `Case` (renamed CqlCase on import).
  private object CqlSql {
    def caseWhen(pairs: Seq[(SqlExpr, SqlExpr)], orElse: SqlExpr): SqlExpr = SqlExpr.Case(pairs, Some(orElse))
  }

  private def sqlType(ddl: String): String = ddl.trim.toLowerCase match {
    case "int"           => "INT"
    case "long"          => "BIGINT"
    case "string"        => "STRING"
    case "boolean"       => "BOOLEAN"
    case "timestamp_ntz" => "TIMESTAMP_NTZ"
    case other           => other.toUpperCase
  }

  /** The `op` token each `Cmp` maps to for the `cql_temporal_cmp` opaque UDF (EMIT-SQL-TEMPORAL-UDF). */
  private def cmpOp(kind: Cmp): String = kind match {
    case Lt => "lt"; case Gt => "gt"; case Le => "le"; case Ge => "ge"
    case Eq => "eq"; case Ne => "ne"; case Equiv => "equiv"
  }

  // ---------------------------------------------------------------------------
  // Equivalence (`LANG-EQUIV`) — ONE primitive for `~`.
  //
  // NOTE the boundary, which is easy to get wrong: `distinct` and list `in`/`contains` are defined on
  // EQUALITY, not equivalence ("the distinct operator returns the given list with duplicates
  // eliminated using equality semantics"; "the in operator for lists returns true if the given element
  // is in the given list using equality semantics"). Equivalence-based membership exists in the
  // reference only behind an opt-in compatibility option. So those operators correctly stay
  // on Spark's native `array_distinct`/`array_contains` and must NOT be routed through the equivalence
  // primitive below — doing so makes `'diabetes' in { 'Diabetes' }` wrongly true.
  // ---------------------------------------------------------------------------

  /** STRING equivalence normalization: case- and locale-insensitive, with whitespace normalized.
    *
    * "Normalizing whitespace" is defined per CHARACTER — the CQL Reference (and FHIRPath, which it
    * inherits from) says "all whitespace characters are treated as equivalent", over the whitespace
    * lexical element `[ \t\r\n]`. It does NOT collapse runs and does NOT trim, so `'a b' ~ 'a  b'` and
    * `'Diabetes ' ~ 'Diabetes'` are both FALSE; only `'a\tb' ~ 'a b'` is true. HL7's own fhirpath.js
    * settled on this same reading ("treats different Unicode whitespace characters and ordinary spaces
    * as identical and does not collapse consecutive spaces"), so a run-collapsing/trimming variant
    * would be a deliberate divergence from the spec, not a stricter reading of it.
    *
    * CONF-REF-DEVIATION: the Java CQF reference (`EquivalentEvaluator`, 4.8.0) compares strings with a
    * bare `equalsIgnoreCase` and so does NOT normalize whitespace at all — it returns false for
    * `'a\tb' ~ 'a b'`. This engine follows the SPEC there; the divergence is declared on the
    * `Comparison` fixture rather than quietly matching an upstream bug. */
  private[lower] def normalizeString(e: SqlExpr): SqlExpr =
    Call("regexp_replace", Seq(Call("lower", Seq(e)), str("[ \\t\\r\\n]"), str(" ")))

  /** Equivalence at a CQL type, as a reusable two-argument comparison — the ONE place a type's
    * equivalence rule lives, so the `~` arms cannot drift apart. Total by construction (`<=>`):
    * equivalence NEVER returns null. Types whose equivalence needs the operand IR (precision-aware
    * temporals, dimensional quantities, Code/Concept) keep their dedicated `Equivalent` arms in
    * [[lower]] and reach here only as `<=>` on the already canonical struct/string encoding. */
  private[lower] def equivAt(t: CqlType): (SqlExpr, SqlExpr) => SqlExpr = t match {
    case StringType  => (a, b) => normalizeString(a) <=> normalizeString(b)
    case DecimalType => (a, b) => Call("round", Seq(a, int(2))) <=> Call("round", Seq(b, int(2)))
    case _           => (a, b) => a <=> b
  }

  def lower(expr: Expr,
            resolveRef: RefResolver = rejectRefs,
            resolveAlias: RefResolver = rejectAlias,
            typeOf: Expr => CqlType = _ => AnyType,
            hoist: Hoister = noHoist,
            hoistT: Hoister = noHoist,
            temporalUdf: Boolean = false): SqlExpr = {

    def isDate(e: Expr): Boolean = typeOf(e) match { case _: DateType | _: DateTimeType | _: TimeType => true; case _ => false }
    def isDateType(t: CqlType): Boolean = t match { case _: DateType | _: DateTimeType | _: TimeType => true; case _ => false }
    def isQuantity(e: Expr): Boolean = typeOf(e) match { case _: QuantityType => true; case _ => false }
    // A System.Quantity (or a FHIR quantity-family datatype) is a STRUCT with a numeric `value` FIELD, so
    // `.value` reads that field — NOT the `navigate` FHIR-primitive-`value` unwrap (which would return the
    // whole struct). E.g. `dose.value` / `quantity.value` in a medication-request period helper.
    def isQuantityLikeType(t: CqlType): Boolean = t match {
      case _: QuantityType => true
      case NamedType(n)    => Set("Quantity", "SimpleQuantity", "Age", "Count", "Distance", "Duration", "MoneyQuantity")(n)
      case _               => false
    }
    def isInterval(e: Expr): Boolean = typeOf(e) match { case _: IntervalType => true; case _ => false }
    def isRatio(e: Expr): Boolean = typeOf(e) == RatioType
    def isChoice(e: Expr): Boolean = typeOf(e) match { case _: ChoiceType => true; case _ => false }
    def isList(e: Expr): Boolean = typeOf(e) match { case _: ListType => true; case _ => false }
    def elementOfList(e: Expr): CqlType = typeOf(e) match { case ListType(t) => t; case _ => AnyType }
    def choiceMembers(e: Expr): Seq[CqlType] = typeOf(e) match { case ChoiceType(m) => m; case _ => Nil }
    // The Choice member `Interval<Point>` whose POINT type matches `asType` — for a helper library's fluent
    // `onsetSpan`'s `choice.low as <Point>` / `choice.high as <Point>` over a `Choice<…, Interval<DateTime>,
    // Interval<Quantity>, …>`: `as Quantity` selects the `Interval<Quantity>` arm, `as DateTime` the
    // `Interval<DateTime>` arm. `None` when the base is not such a Choice (so a plain interval falls through).
    def choiceIvArm(base: Expr, asType: String): Option[CqlType] = {
      val pt = choiceTag(CqlType.parse(asType))
      choiceMembers(base).collectFirst { case m @ IntervalType(p) if choiceTag(p) == pt => m }
    }

    // --- `~` between a FHIR CodeableConcept/Coding and a resolved concept: coding membership. ---
    def conceptEquiv(cc: Expr, codes: Seq[(String, String)]): SqlExpr = {
      val col = go(cc); val coding = col.getField("coding")
      val pred =
        if (codes.isEmpty) bool(false)
        else codes.map[SqlExpr] { case (sys, code) =>
          Call("exists", Seq(coding, Lambda(Seq("x"),
            (Field(Col("x"), "system") === str(sys)) && (Field(Col("x"), "code") === str(code)))))
        }.reduce(_ || _)
      ifc(col.isNull || coding.isNull, bool(false), Call("coalesce", Seq(pred, bool(false))))
    }

    def toInt32OrNull(c: SqlExpr): SqlExpr =
      ifc((c > int(Int.MaxValue)) || (c < int(Int.MinValue)), Null, c.cast("int"))
    def step(o: Expr, minus: Boolean): SqlExpr = {
      val d = if (minus) -1 else 1
      // EXEC-SQL-DECIMAL-STEP: the ±1e-8 CQL decimal step lives in the 8th decimal place. Decimal
      // literals lower to DECIMAL(38,8) (`decimal`), and Spark's addition of two (38,8) operands
      // overflows precision 38 and — under allowPrecisionLoss — rounds the result to scale 7,
      // silently erasing the step (1.0 ± 1e-8 → 1.0000000). Narrowing both operands to DECIMAL(28,8)
      // keeps the natural sum at (29,8) so the 8th digit survives (the retired proto back end avoided
      // this because its DataFrame literals kept natural, minimal precision).
      val decStep = decimal(BigDecimal("0.00000001") * d).cast("decimal(28,8)")
      typeOf(o) match {
        case IntegerType     => go(o) + int(d)
        case DecimalType     => go(o).cast("decimal(28,8)") + decStep
        case QuantityType(u) => qStruct(qValue(o).cast("decimal(28,8)") + decStep, u.getOrElse("1"))
        case t               => throw UnsupportedNode(if (minus) "Predecessor" else "Successor",
                                  s"stepping a ${t.show} is not yet lowered (only Integer/Decimal/Quantity)")
      }
    }
    def boundValue(valueType: String, min: Boolean): SqlExpr = {
      val ty = valueType.substring(valueType.lastIndexOf('}') + 1)
      ty match {
        case "Integer"  => int(if (min) Int.MinValue else Int.MaxValue)
        case "Decimal"  =>
          val mag = BigDecimal("99999999999999999999.99999999")
          Cast(decimal(if (min) -mag else mag), "DECIMAL(28,8)")
        case "DateTime" => str(if (min) "0001-01-01T00:00:00.000" else "9999-12-31T23:59:59.999")
        case "Date"     => str(if (min) "0001-01-01" else "9999-12-31")
        case "Time"     => str(if (min) "1970-01-01T00:00:00.000" else "1970-01-01T23:59:59.999")
        case t          => throw UnsupportedNode(if (min) "MinValue" else "MaxValue",
                             s"$t bounds are not yet lowered (only Integer/Decimal/Date/DateTime/Time)")
      }
    }

    // --- Quantity helpers (LANG-QUANTITY): struct(value, unit); cross-unit ops use compile-time UCUM. ---
    def qUnit(e: Expr): String = (typeOf(e) match { case QuantityType(u) => u; case _ => None }).getOrElse("1")
    def qValue(e: Expr): SqlExpr = go(e).getField("value")
    def qStaticUnit(e: Expr): Option[String] = typeOf(e) match { case QuantityType(u) => u; case _ => None }
    def qUnitCol(e: Expr): SqlExpr = qStaticUnit(e).map(str).getOrElse(go(e).getField("unit"))
    def qStruct(value: SqlExpr, unit: String): SqlExpr =
      Struct(Seq("value" -> value.cast("decimal(28,8)"), "unit" -> str(unit)))
    // The System.Quantity UNIT of a FHIR Quantity struct: `Coalesce(code, unit, '1')` — the UCUM `code`
    // FIRST (FHIRHelpers.ToQuantity), then the human-display `unit`, then dimensionless `'1'`. A FHIR
    // Quantity commonly carries `unit`='mmHg' (display) alongside `code`='mm[Hg]' (UCUM); reading `unit`
    // alone yields a non-UCUM string that no cross-unit conversion recognizes, so `<value> 'mm[Hg]'`
    // compares to NULL (a vital-sign measurement reading `mm[Hg]`). LANG-QUANTITY.
    def fhirQuantityUnit(q: SqlExpr): SqlExpr =
      Call("coalesce", Seq(q.getField("code"), q.getField("unit"), str("1")))
    def ucumFactor(u: String): BigDecimal =
      Ucum.factor(u).getOrElse(throw UnsupportedNode("Quantity", s"unmodeled UCUM unit '$u'"))
    def requireCommensurable(ua: String, ub: String): Unit =
      if (!Ucum.commensurable(ua, ub)) throw UnsupportedNode("Quantity", s"incommensurable units '$ua' and '$ub'")
    def quantityCompare(a: Expr, b: Expr)(op: (SqlExpr, SqlExpr) => SqlExpr): SqlExpr =
      (qStaticUnit(a), qStaticUnit(b)) match {
        case (Some(ua), Some(ub)) =>
          requireCommensurable(ua, ub)
          if (ua == ub) op(qValue(a), qValue(b))
          else op(qValue(a) * decimal(ucumFactor(ua)), qValue(b) * decimal(ucumFactor(ub)))
        case _ => ifc(qUnitCol(a) === qUnitCol(b), op(qValue(a), qValue(b)), Null)
      }
    def quantityArith(a: Expr, b: Expr)(op: (SqlExpr, SqlExpr) => SqlExpr): SqlExpr = {
      val (ua, ub) = (qUnit(a), qUnit(b)); requireCommensurable(ua, ub)
      val target =
        if (ua == ub) ua
        else if (Ucum.factor(ua).getOrElse(BigDecimal(0)) >= Ucum.factor(ub).getOrElse(BigDecimal(0))) ua else ub
      def toTarget(u: String, v: SqlExpr): SqlExpr = if (u == target) v else v * decimal(Ucum.conversion(u, target).get)
      qStruct(op(toTarget(ua, qValue(a)), toTarget(ub, qValue(b))), target)
    }
    def isDurationCmp(a: Expr, b: Expr): Boolean =
      isQuantity(a) && isQuantity(b) && Ucum.isDurationUnit(qUnit(a)) && Ucum.isDurationUnit(qUnit(b))
    def durationEq(a: Expr, b: Expr, eq: Boolean): SqlExpr = {
      val (ua, ub) = (qUnit(a), qUnit(b))
      val decided: Option[SqlExpr] =
        (Ucum.durationPreciseMs(ua), Ucum.durationPreciseMs(ub)) match {
          case (Some(ma), Some(mb)) => Some((qValue(a) * decimal(ma)) === (qValue(b) * decimal(mb)))
          case _ =>
            (Ucum.durationImprecise(ua), Ucum.durationImprecise(ub)) match {
              case (Some(ia), Some(ib)) if ia == ib => Some(qValue(a) === qValue(b))
              case _                                => None
            }
        }
      decided.map(c => if (eq) c else !c).getOrElse(nullBool)
    }
    def durationEquiv(a: Expr, b: Expr): SqlExpr = {
      sealed trait K
      final case class P(ms: BigDecimal) extends K
      case object Y extends K
      case object Mo extends K
      def kind(u: String): K =
        Ucum.durationPreciseMs(u).map(P).orElse(Ucum.durationImprecise(u).map(i => if (i._1 == "year") Y else Mo)).get
      val (va, vb) = (qValue(a), qValue(b))
      val dayMs = BigDecimal(86400000)
      (kind(qUnit(a)), kind(qUnit(b))) match {
        case (P(ma), P(mb))    => (va * decimal(ma)) <=> (vb * decimal(mb))
        case (Y, Y) | (Mo, Mo) => va <=> vb
        case (Y, Mo)           => (va * decimal(BigDecimal(12))) <=> vb
        case (Mo, Y)           => va <=> (vb * decimal(BigDecimal(12)))
        case (Y, P(mb))        => (va * decimal(BigDecimal(365) * dayMs)) <=> (vb * decimal(mb))
        case (P(ma), Y)        => (va * decimal(ma)) <=> (vb * decimal(BigDecimal(365) * dayMs))
        case (Mo, P(mb))       => (va * decimal(BigDecimal(30) * dayMs)) <=> (vb * decimal(mb))
        case (P(ma), Mo)       => (va * decimal(ma)) <=> (vb * decimal(BigDecimal(30) * dayMs))
      }
    }

    // --- Interval helpers (LANG-INTERVAL). ---
    def lo(e: Expr): SqlExpr = hoist(go(e)).getField("low")
    def hi(e: Expr): SqlExpr = hoist(go(e)).getField("high")
    def ivPointType(e: Expr): CqlType = typeOf(e) match { case IntervalType(p) => p; case _ => AnyType }
    def ivIsDate(e: Expr): Boolean = isDateType(ivPointType(e))

    def constTemporal(e: Expr): TConst = e match {
      case DateExpr(parts)     => literalIso(parts, isDateTime = false)
      case DateTimeExpr(parts) => literalIso(parts, isDateTime = true)
      case ToDateTimeExpr(o)   => constTemporal(o)
      case Start(IntervalExpr(Some(loB), _, true, _)) => constTemporal(loB)
      case End(IntervalExpr(_, Some(hiB), _, true))   => constTemporal(hiB)
      case _ => None
    }
    final case class Bnd(col: SqlExpr, const: TConst, inf: Option[Boolean])
    def infDir(e: Expr, min: Boolean): Option[Boolean] = ivPointType(e) match {
      case _: DateTimeType | _: DateType | _: TimeType | IntegerType | DecimalType => Some(min)
      case _                                                                       => None
    }
    def ivOperand(e: Expr): (Bnd, Bnd) = e match {
      case iv: IntervalExpr =>
        val loC = if (iv.lowClosed) iv.low.flatMap(constTemporal) else None
        val hiC = if (iv.highClosed) iv.high.flatMap(constTemporal) else None
        if (loC.isDefined && hiC.isDefined) (Bnd(Null, loC, None), Bnd(Null, hiC, None))
        else { val c = hoist(go(e)); (Bnd(c.getField("low"), loC, infDir(e, true)), Bnd(c.getField("high"), hiC, infDir(e, false))) }
      case _ =>
        val c = hoist(go(e)); (Bnd(c.getField("low"), None, infDir(e, true)), Bnd(c.getField("high"), None, infDir(e, false)))
    }
    // Truncate an interval bound to DAY precision — used when the containment POINT was normalized to
    // day (`during day of`, [[In]]): both operands must reach the comparison at the SAME precision, or
    // `temporalCompare`'s "same precision?" tie-breaker treats a day point vs a full-precision bound as
    // UNCERTAIN (null) on a boundary date (the point on the interval's first/last day — a day-precision
    // `during` in a denominator exclusion). A finite col substrs to day; a const string truncates; an open (inf) bound is
    // untouched. Truncating never flips a DECIDED (prefix-differing) compare — only resolves the tie.
    def truncBnd(b: Bnd, rank: Int): Bnd =
      Bnd(if (b.col == Null) b.col else fhirTemporal(b.col, rank),
          b.const.map(_.take(lenOfRank(rank))), b.inf)
    def truncBndDay(b: Bnd): Bnd = truncBnd(b, DateTimePrecision.Day.rank)
    // LANG-INTERVAL-PRECISION: an Allen relation may NAME the precision it compares at (`X during day of
    // Y`, ELM `precision: "Day"`). The name is a property of the COMPARISON, not the operands, so BOTH
    // sides truncate to it — otherwise `day of` compares whatever precisions the operands happened to
    // arrive at, and a coarse bound against a fine value ties into an UNCERTAIN null (the row then fails
    // its WHERE). That is the opposite of what naming `day of` asks for. Concretely: a Measurement Period
    // bound as `Interval[@2026-01-01, @2026-12-31]` (day) against encounter periods starting
    // `2026-01-01T00:00:00.000` (ms) nulled ALL N clinical encounters in a measure and emptied the
    // denominator, silently. Unnamed (`None`) keeps the prior per-operand behaviour exactly.
    def atPrecision(p: Option[String])(bs: (Bnd, Bnd)): (Bnd, Bnd) =
      p.flatMap(DateTimePrecision.byName) match {
        case Some(prec) => (truncBnd(bs._1, prec.rank), truncBnd(bs._2, prec.rank))
        case None       => bs
      }
    // A point operand that is `start of`/`end of` an interval carries THAT BOUND's open-end infinity.
    // CQL `end of Interval[lo, null]` with a CLOSED null high (an ongoing condition's
    // `onsetSpan`) is +infinity (the max value), NOT a plain null — so `X ends before Y`
    // must be definitively FALSE, not an UNCERTAIN null. Routing the boundary point through the
    // interval's `Bnd` (whose `inf` = `infDir`) lets `cmpPoints` substitute ±infinity for a null open
    // bound; a non-boundary point stays a finite `Bnd` (no infinity), identical to a bare compare.
    def pointBnd(e: Expr): Bnd = e match {
      case Start(iv) if isInterval(iv) => ivOperand(iv)._1
      case End(iv)   if isInterval(iv) => ivOperand(iv)._2
      case _                           => Bnd(go(e), constTemporal(e), None)
    }
    // CQL: an Allen interval relation yields NULL when either INTERVAL operand is itself the NULL interval
    // — as opposed to an interval with a null/unbounded BOUND (which stays ±inf via `infDir`). A whole-null
    // interval lowers to a SQL-NULL struct (a medication-request period helper with no dosage, or any interval
    // PROMOTION of a null source — ElmCodec preserves that null through the Date→DateTime promotion), so
    // short-circuit the bound-comparison to NULL when the operand STRUCT is null. A non-null struct with a
    // null bound is untouched (the CSE cache makes the extra `hoist(go(e))` a no-op reuse). LANG-INTERVAL.
    def ivGuard(es: Expr*)(body: SqlExpr): SqlExpr = {
      val gs = es.collect { case e if isInterval(e) => hoist(go(e)).isNull }
      gs.reduceOption(_ || _).map(g => ifc(g, nullBool, body)).getOrElse(body)
    }

    def temporalCompare(a: SqlExpr, b: SqlExpr, aC: TConst, bC: TConst, inLambda: Boolean = false)(kind: Cmp): SqlExpr = {
      // EMIT-SQL-TEMPORAL-UDF (opaque): collapse the precision-aware compare tower to one black-box call at
      // its leaves. Only when a RUNTIME operand is involved — two literals stay native so they const-fold
      // (federated-retrieve arm elimination keeps its foldable guards). A scalar compare has no infinity.
      // `inLambda` keeps it native: Databricks forbids catalog UDFs inside a higher-order function's lambda.
      if (temporalUdf && !inLambda && (aC.isEmpty || bC.isEmpty))
        Call(SqlNames.TemporalCmp,
          Seq(aC.map(str).getOrElse(a), int(0), bC.map(str).getOrElse(b), int(0), str(cmpOp(kind))))
      else {
      val sa = aC.map(str).getOrElse(a)
      val sb = bC.map(str).getOrElse(b)
      val la = aC.map(s => int(s.length)).getOrElse(Call("length", Seq(sa)))
      val lb = bC.map(s => int(s.length)).getOrElse(Call("length", Seq(sb)))
      val L  = (aC, bC) match {
        case (Some(x), Some(y)) => int(math.min(x.length, y.length))
        case _                  => Call("least", Seq(la, lb))
      }
      val same = la === lb
      val ta = sa.substr(int(1), L); val tb = sb.substr(int(1), L)
      def tie(decided: SqlExpr): SqlExpr = ifc(same, decided, nullBool)
      val core = kind match {
        case Eq    => ifc(ta === tb, tie(bool(true)), bool(false))
        case Ne    => ifc(ta === tb, tie(bool(false)), bool(true))
        case Lt    => whenChain(Seq((ta < tb) -> bool(true), (ta > tb) -> bool(false)), tie(bool(false)))
        case Gt    => whenChain(Seq((ta > tb) -> bool(true), (ta < tb) -> bool(false)), tie(bool(false)))
        case Le    => whenChain(Seq((ta < tb) -> bool(true), (ta > tb) -> bool(false)), tie(bool(true)))
        case Ge    => whenChain(Seq((ta > tb) -> bool(true), (ta < tb) -> bool(false)), tie(bool(true)))
        case Equiv => ta <=> tb
      }
      val guards =
        if (kind != Eq && kind != Ne) Nil
        else Seq(aC.isEmpty -> sa, bC.isEmpty -> sb).collect { case (true, s) => s.isNull }
      guards.reduceOption(_ || _).map(g => ifc(g, nullBool, core)).getOrElse(core)
      }
    }

    def cmpPoints(a: SqlExpr, aC: TConst, b: SqlExpr, bC: TConst, dateMode: Boolean)
                 (kind: Cmp, aInf: Option[Boolean] = None, bInf: Option[Boolean] = None): SqlExpr = {
      // EMIT-SQL-TEMPORAL-UDF (opaque): a DATE-mode bound/point compare collapses to one call, carrying each
      // operand's open-end infinity (−1 low / +1 high / 0 finite) so the whole cmpPoints infinity ladder
      // lives in the UDF. Integer/non-date compares and all-const operands stay native (fold/pushdown).
      if (temporalUdf && dateMode && (aC.isEmpty || bC.isEmpty)) {
        def infArg(c: TConst, inf: Option[Boolean]): Int =
          if (c.isDefined) 0 else inf.map(m => if (m) -1 else 1).getOrElse(0)
        Call(SqlNames.TemporalCmp, Seq(
          aC.map(str).getOrElse(a), int(infArg(aC, aInf)),
          bC.map(str).getOrElse(b), int(infArg(bC, bInf)), str(cmpOp(kind))))
      } else {
      val core =
        if (dateMode) temporalCompare(a, b, aC, bC)(kind)
        else kind match {
          case Lt => a < b; case Gt => a > b; case Le => a <= b
          case Ge => a >= b; case Eq => a === b; case Ne => a =!= b; case Equiv => a <=> b
        }
      def pos(inf: Boolean): Int = if (inf) -1 else 1
      def decide(pa: Int, pb: Int): SqlExpr = bool(kind match {
        case Lt => pa < pb; case Le => pa <= pb; case Gt => pa > pb
        case Ge => pa >= pb; case Eq | Equiv => pa == pb; case Ne => pa != pb
      })
      val an = if (aC.isEmpty) aInf.map(d => (a.isNull, pos(d))) else None
      val bn = if (bC.isEmpty) bInf.map(d => (b.isNull, pos(d))) else None
      (an, bn) match {
        case (Some((ac, ap)), Some((bc, bp))) =>
          whenChain(Seq((ac && bc) -> decide(ap, bp), ac -> decide(ap, 0), bc -> decide(0, bp)), core)
        case (Some((ac, ap)), None) => ifc(ac, decide(ap, 0), core)
        case (None, Some((bc, bp))) => ifc(bc, decide(0, bp), core)
        case (None, None)           => core
      }
      }
    }
    def cmpB(a: Bnd, b: Bnd, dateMode: Boolean)(kind: Cmp): SqlExpr =
      cmpPoints(a.col, a.const, b.col, b.const, dateMode)(kind, a.inf, b.inf)
    def dateScalarCmp(a: Expr, b: Expr)(kind: Cmp): SqlExpr =
      cmpPoints(go(a), constTemporal(a), go(b), constTemporal(b), dateMode = true)(kind)

    def sameAs(a: Expr, b: Expr, precision: Option[String]): SqlExpr = {
      val (ca, cb) = (go(a), go(b))
      precision match {
        case None => temporalCompare(ca, cb, constTemporal(a), constTemporal(b))(Eq)
        case Some(name) if temporalUdf && (constTemporal(a).isEmpty || constTemporal(b).isEmpty) =>
          Call(SqlNames.TemporalSamePrec, Seq(ca, cb, int(lenOfRank(precRank(name)))))
        case Some(name) =>
          val pl = int(lenOfRank(precRank(name)))
          val ta = ca.substr(int(1), pl); val tb = cb.substr(int(1), pl)
          whenChain(Seq(
            (ca.isNull || cb.isNull) -> nullBool,
            (Call("length", Seq(ca)) < pl || Call("length", Seq(cb)) < pl) -> nullBool),
            ta === tb)
      }
    }

    def dateArith(a: Expr, b: Expr, minus: Boolean): SqlExpr = {
      val s = a match { case _: Property => fhirTemporal(go(a), DateTimePrecision.Day.rank); case _ => go(a) }
      // The calendar unit is a RUNTIME value whenever the Quantity comes from DATA rather than a literal:
      // a FHIR `Age`/`Range` bound carries its UCUM code in `code`/`unit` (a helper's age arms —
      // `Patient.birthDate + (choice as Quantity)`), so the TYPE has no static unit. Baking the
      // dimensionless `'1'` fallback there (what `qUnit`'s `getOrElse` yields) made `cql_date_add` return
      // NULL for EVERY age-based interval across the measure corpus — a silent wrong answer, not a loud one
      // (`CORE-NO-SILENT-MISLOWER`). Read the unit off the quantity instead; a statically-known unit
      // (`+ 1 year`, the common case) still emits its literal, byte-unchanged.
      val staticUnit = qStaticUnit(b)
      // With a runtime unit the count AND the unit are read off the SAME quantity struct — bind it once
      // (`ARCH-SQL-CSE`) rather than re-inlining the struct per read.
      val qBound = if (staticUnit.isEmpty) Some(hoist(go(b))) else None
      val n = {
        val v = qBound.map(_.getField("value")).getOrElse(qValue(b)).cast("int")
        if (minus) Prefix("-", v) else v
      }
      val unitArg = staticUnit.map(str).getOrElse(qBound.get.getField("unit"))
      // EMIT-SQL-TEMPORAL-UDF (opaque): one call folds the unit dispatch + make_interval step + re-truncate.
      if (temporalUdf) return hoistT(Call(SqlNames.DateAdd, Seq(s, n, unitArg)))
      val z = int(0)
      def mk(args: SqlExpr*): SqlExpr = Call("make_interval", args)
      // The unit families `cql_date_add` accepts — (CQL name | UCUM code)* per `make_interval` slot, in
      // slot order (years, months, weeks, days, hours, minutes, seconds); milliseconds ride the seconds
      // slot as a fraction.
      val families: Seq[Seq[String]] = Seq(
        Seq("year", "years", "a"), Seq("month", "months", "mo"), Seq("week", "weeks", "wk"),
        Seq("day", "days", "d"), Seq("hour", "hours", "h"), Seq("minute", "minutes", "min"),
        Seq("second", "seconds", "s"))
      val millis = Seq("millisecond", "milliseconds", "ms")
      def msCount(c: SqlExpr): SqlExpr = c.cast("decimal(28,3)") * decimal(BigDecimal("0.001"))
      // A dimensionless unit yields a NULL interval (mirrors `CqlOps.dateAdd`'s `"1"` arm).
      val nullIv = mk(Cast(Null, "INT"), z, z, z, z, z, z)
      val iv = staticUnit match {
        // Statically known: pick the slot at COMPILE time, emission unchanged.
        case Some(u) =>
          val slot = families.indexWhere(_.contains(u))
          if (slot >= 0) mk(families.indices.map(i => if (i == slot) n else z): _*)
          else if (millis.contains(u)) mk(z, z, z, z, z, z, msCount(n))
          else if (u == "1") nullIv
          else throw UnsupportedNode("Date arithmetic", s"unmodeled duration unit '$u'")
        // Runtime unit on the NATIVE path: DELIBERATELY still a NULL interval — the one place the
        // dimensionless behaviour survives, tracked as `EXEC-SQL-TEMPORAL-RUNTIME-UNIT`.
        //
        // Native lowering is FORCED inside a JOIN ON — Databricks rejects a catalog UDF in the ON clause of
        // a LEFT SEMI/ANTI join (44bd5f1), so `EMIT-SQL-TEMPORAL-UDF-OPAQUE` is switched off there — which
        // is exactly where a helper's age arms sit inside a two-sided `such that`. A runtime
        // unit has no compile-time `make_interval` slot, so honouring it means EMITTING the dispatch — and
        // this expression is re-inlined per use by the let-carrier, with no hoister available inside a
        // JOIN ON, so every count/unit reference re-emits its whole subtree. Three shapes were measured
        // against one measure's 272 KB baseline:
        //   - CASE over eight whole `make_interval` shapes -> 6 MB/statement, OVER the 4 MB emit-size gate
        //   - one `make_interval`, a CASE per slot         -> 4.30 MB total (15.8x), gate passes
        //   - count factored out, unit -> constant via map -> 4.58 MB total (16.9x), gate passes
        // None is shippable. `make_interval` sites go 30 -> 4024 because these arms previously folded to a
        // shared constant NULL and vanished entirely, so making them LIVE multiplies the plan wherever
        // a helper's prevalence-interval function is inlined (another measure likewise 15 KB -> 209 KB, 13.9x). Per-instance the
        // expression is already only ~1 KB — the cost is the SITE COUNT, which no rewrite of this
        // expression can reduce. The fix is to bind the age interval ONCE upstream rather than re-inline it
        // into a join predicate (an `EMIT-SQL-LET-CARRIER` extension), so this stays NULL until that lands
        // instead of shipping a 17x plan.
        //
        // The residual is NARROW: the UDF path above — every retrieve-level projection, i.e. all of the
        // emitted `cql_date_add` sites — is correct, so an age-coded onset/abatement computes properly
        // everywhere except inside a join predicate.
        case None => nullIv
      }
      hoistT(fmtTrunc((tsOf(s) + iv).cast("timestamp_ntz"), Call("length", Seq(s))))
    }
    def tStep(o: Expr, minus: Boolean): SqlExpr = {
      val s = go(o)
      if (temporalUdf) hoistT(Call(SqlNames.TemporalStep, Seq(s, bool(minus))))
      else hoistT(strStep(s, rankOfLen(Call("length", Seq(s))), minus, hoist))
    }

    def precVal(p: Expr): Option[Int] = p match { case Literal(_, Some(n)) => n.toIntOption; case _ => None }
    def isNullish(e: Expr): Boolean = e match {
      case NullExpr() | Literal(_, None) => true
      case As(inner, _, _, _)            => isNullish(inner)
      case Convert(inner, _)             => isNullish(inner)
      case _                             => false
    }
    def decimalScale(e: Expr): Option[Int] = e match {
      case Literal(vt, Some(raw)) if vt.endsWith("Decimal") => Some(raw.dropWhile(_ != '.').drop(1).count(_.isDigit))
      case _                                                => None
    }
    def dateRankFromCount(c: Int): Int = if (c >= 17) 6 else (c - 4) / 2
    def timeRankFromCount(c: Int): Int = if (c >= 9) 6 else c / 2 + 2
    def boundary(v: Expr, p: Expr, high: Boolean): SqlExpr = typeOf(v) match {
      case DecimalType =>
        if (!high) go(v)
        else decimalScale(v) match {
          case Some(sc) =>
            val pp = precVal(p).getOrElse(8)
            val incr = java.math.BigDecimal.ONE.movePointLeft(sc).subtract(java.math.BigDecimal.ONE.movePointLeft(pp))
            // EXEC-SQL-DECIMAL-STEP: same DECIMAL(38,8) addition precision loss as `step` — the pad
            // increment reaches into the 8th decimal (e.g. HighBoundary(1.587, 8) = 1.587 + 0.00099999
            // = 1.58799999). Narrow both operands to DECIMAL(28,8) so the sum stays (29,8), not (38,7).
            go(v).cast("decimal(28,8)") + decimal(BigDecimal(incr)).cast("decimal(28,8)")
          case None if isNullish(v) => Cast(Null, "DECIMAL(28,8)")
          case None => throw UnsupportedNode("HighBoundary", "HighBoundary of a non-literal Decimal is not yet lowered")
        }
      case t if t.isInstanceOf[DateType] || t.isInstanceOf[DateTimeType] || t.isInstanceOf[TimeType] =>
        val cnt = precVal(p).getOrElse(throw UnsupportedNode("Boundary", "temporal boundary needs an explicit precision"))
        val target = if (t.isInstanceOf[TimeType]) timeRankFromCount(cnt) else dateRankFromCount(cnt)
        // EMIT-SQL-TEMPORAL-UDF (opaque): one call folds the step-up/step-down + re-truncate tower.
        if (temporalUdf) return hoistT(Call(SqlNames.TemporalBoundary, Seq(go(v), int(target), bool(high))))
        val s = go(v); val ownRank = hoist(rankOfLen(Call("length", Seq(s)))); val baseTs = tsOf(s)
        val ts = if (!high) baseTs else addUnit(addUnit(baseTs, ownRank, minus = false), int(target), minus = true)
        hoistT(fmtTrunc(ts, int(lenOfRank(target))))
      case t => throw UnsupportedNode(if (high) "HighBoundary" else "LowBoundary", s"boundary of ${t.show} is not yet lowered")
    }

    // --- Choice (LANG-CHOICE). Discriminated struct(__choice, as<Type>…); scalar members only here. ---
    // A DEGENERATE Choice whose members all share one physical tag (`Choice<Condition, Condition>` — two
    // QI-Core profiles of one resource; a helper library's fluent functions typed over both) does NOT build a
    // discriminated struct: its slots would COLLIDE (two `asFHIR_Condition`) and navigation reads the field
    // DIRECTLY (`collapseChoice`/`dedupByTag` treat it as the plain resource, `hetAliasMembers` -> None). So
    // collapse to the plain value; a genuine multi-tag Choice builds the struct over its DEDUPED members.
    def buildChoice(operand: Expr, members: Seq[CqlType]): SqlExpr = {
      val deduped = dedupByTag(members)
      if (deduped.sizeIs <= 1) go(operand)
      else choiceStructOf(go(operand), choiceTag(typeOf(operand)), deduped)
    }
    def buildChoiceList(operand: Expr, members: Seq[CqlType]): SqlExpr = {
      val deduped = dedupByTag(members)
      if (deduped.sizeIs <= 1) go(operand)
      else {
        val elemTag = choiceTag(typeOf(operand) match { case ListType(el) => el; case t => t })
        Call("transform", Seq(go(operand), Lambda(Seq("x"), choiceStructOf(Col("x"), elemTag, deduped))))
      }
    }
    def isFhirType(t: String): Boolean = t.contains("hl7.org/fhir") || t.startsWith("FHIR.")
    def fhirChoiceSlot(src: Option[Expr], scope: Option[String], path: String, fhirType: String): SqlExpr = {
      val tyName = { val a = fhirType.substring(fhirType.lastIndexOf('}') + 1); a.substring(a.lastIndexOf('.') + 1) }
      val slot = path + tyName.capitalize
      (src, scope) match {
        case (Some(s), _)    => go(s).getField(slot)
        case (None, Some(_)) => Col(slot)
        case _ => throw UnsupportedNode("As", "FHIR value[x] navigation needs a source expression or scope alias")
      }
    }

    def navigate(base: SqlExpr, segs: List[String]): SqlExpr = segs match {
      case Nil            => base
      case "value" :: Nil => base
      case seg :: rest    => navigate(base.getField(seg), rest)
    }

    // --- Aggregate functions (LANG-AGGREGATE-FUNCTIONS). The list arrives as an array (the frame lowering boxes a
    // relation-encoded list first). CQL Reference, Aggregate Functions: every one ignores null elements, and all
    // but Count, AllTrue and AnyTrue return null for a null list or one with no non-null element. ---
    def nullDec: SqlExpr = Cast(Null, "DECIMAL(28,8)")
    def d28(e: SqlExpr): SqlExpr = Cast(e, "DECIMAL(28,8)")
    def lam1(name: String)(body: SqlExpr => SqlExpr): SqlExpr = Lambda(Seq(name), body(Col(name)))
    def lam2(l: String, r: String)(body: (SqlExpr, SqlExpr) => SqlExpr): SqlExpr =
      Lambda(Seq(l, r), body(Col(l), Col(r)))
    def sizeOf(arr: SqlExpr): SqlExpr = ifc(arr.isNull, int(0), Call("size", Seq(arr)))
    // A quantity with no value is a null element (`null as Quantity` is a struct with a null value).
    def nonNull(arr: SqlExpr, quantity: Boolean): SqlExpr = Call("filter", Seq(arr, lam1("__ag_x") { x =>
      if (quantity) x.isNotNull && x.getField("value").isNotNull else x.isNotNull }))

    // The non-null elements as DECIMAL(28,8), and how many there are.
    final case class Nums(vs: SqlExpr, n: SqlExpr)
    def nums(vs: SqlExpr): Nums = Nums(vs, hoist(sizeOf(vs)))
    def decSum(v: Nums): SqlExpr =
      ifc(v.n === int(0), nullDec,
        Call("aggregate", Seq(v.vs, d28(int(0)), lam2("__ag_acc", "__ag_x")((acc, x) => d28(acc + x)))))
    def decAvg(v: Nums): SqlExpr =
      ifc(v.n === int(0), nullDec, d28(Call("round", Seq(hoist(decSum(v)) / v.n, int(8)))))
    // An even count averages the middle two, as the reference's MedianEvaluator does.
    def decMedian(v: Nums): SqlExpr = {
      val s = hoist(Call("sort_array", Seq(v.vs)))
      val half = Cast(Infix("div", v.n, int(2)), "INT")
      whenChain(Seq(
        (v.n === int(0)) -> nullDec,
        ((v.n % int(2)) === int(1)) -> Call("get", Seq(s, half))),
        d28(Call("round", Seq((Call("get", Seq(s, half - int(1))) + Call("get", Seq(s, half))) / int(2), int(8)))))
    }
    // The sum of squared differences from the mean, then over n - 1 (sample) or n (population). A difference is
    // narrowed to DECIMAL(19,8) so its square keeps 8 places (two DECIMAL(28,8) operands would round to 6).
    def decVariance(v: Nums, population: Boolean): SqlExpr = {
      val mean = hoist(decAvg(v))
      val ssd = hoist(Call("aggregate", Seq(v.vs, d28(int(0)), lam2("__ag_acc", "__ag_x") { (acc, x) =>
        val dx = Cast(x - mean, "DECIMAL(19,8)"); d28(acc + dx * dx) })))
      if (population) ifc(v.n === int(0), nullDec, d28(Call("round", Seq(ssd / v.n, int(8)))))
      else ifc(v.n < int(2), nullDec, d28(Call("round", Seq(ssd / (v.n - int(1)), int(8)))))
    }
    def decStdDev(v: Nums, population: Boolean): SqlExpr = d28(Call("sqrt", Seq(decVariance(v, population))))

    // The most frequent element; a tie goes to the least, as the reference's ModeEvaluator (sort, then a strict
    // `>` on the count) decides it. Elements compare by equality, so precisions that differ never match.
    def modeOf(nn: SqlExpr): SqlExpr = {
      val counted = Call("transform", Seq(Call("array_distinct", Seq(nn)), lam1("__ag_d") { d =>
        Struct(Seq("c" -> Call("size", Seq(Call("filter", Seq(nn, lam1("__ag_y")(_ === d))))), "v" -> d)) }))
      val ranked = Call("array_sort", Seq(counted, lam2("__ag_l", "__ag_r") { (l, r) =>
        val (lc, rc, lv, rv) = (l.getField("c"), r.getField("c"), l.getField("v"), r.getField("v"))
        whenChain(Seq((lc > rc) -> int(-1), (lc < rc) -> int(1), (lv < rv) -> int(-1), (lv > rv) -> int(1)), int(0))
      }))
      Call("get", Seq(ranked, int(0))).getField("v")
    }

    // Min/Max of dates, date-times and times: a left fold that replaces the running value only when the comparison
    // is DECIDED (CQL ordering is precision-aware, so `@2012 < @2012-05` is null and keeps the running value), which
    // is the reference's MinEvaluator/MaxEvaluator fold.
    def temporalExtreme(arr: SqlExpr, max: Boolean): SqlExpr =
      Call("aggregate", Seq(arr, Cast(Null, "STRING"), lam2("__ag_m", "__ag_x") { (m, x) =>
        whenChain(Seq(x.isNull -> m, m.isNull -> x,
          temporalCompare(x, m, None, None, inLambda = true)(if (max) Gt else Lt) -> x), m) }))

    // A product over the non-null elements (CQL Reference, Product). Decimal factors narrow to DECIMAL(19,8) so the
    // product keeps 8 places, which holds for factors below 10^11.
    def productOf(nn: SqlExpr, t: CqlType): SqlExpr = {
      val ty = sqlType(sparkType(t))
      def mul(acc: SqlExpr, x: SqlExpr): SqlExpr =
        if (t == DecimalType) Cast(acc, "DECIMAL(19,8)") * Cast(x, "DECIMAL(19,8)") else acc * x
      Call("aggregate", Seq(nn, Cast(Null, ty), lam2("__ag_acc", "__ag_x") { (acc, x) =>
        ifc(acc.isNull, Cast(x, ty), Cast(mul(acc, x), ty)) }))
    }

    // A list of quantities brought to one unit (LANG-QUANTITY): `vs` the non-null values in `unit`, `nn` the
    // non-null quantities themselves (Min, Max and Mode return one of them), `ok` false when some unit cannot be
    // converted, which makes the result null. A statically known unit needs no conversion. Otherwise the unit is
    // known per row: a list in one unit keeps it (even one the UCUM table lacks), and a mixed list goes to its
    // coarsest unit, as the reference's addition does (`1 'g' + 500 'mg'` is `1.5 'g'`), through the
    // compile-time UCUM factors.
    final case class Qs(nn: SqlExpr, v: Nums, unit: SqlExpr, ok: SqlExpr)
    def quantities(arr: SqlExpr, staticUnit: Option[String]): Qs = {
      val nn = hoist(nonNull(arr, quantity = true))
      def value(x: SqlExpr): SqlExpr = d28(x.getField("value"))
      staticUnit match {
        case Some(u) =>
          Qs(nn, nums(hoist(Call("transform", Seq(nn, lam1("__ag_x")(value))))), str(u), bool(true))
        case None =>
          def unitOf(x: SqlExpr): SqlExpr = Call("coalesce", Seq(x.getField("unit"), str("1")))
          val units = hoist(Call("transform", Seq(nn, lam1("__ag_x")(unitOf))))
          val factorMap = Call("map", Ucum.factors.flatMap { case (u, f) =>
            Seq(str(u), Cast(Lit(f.bigDecimal.toPlainString), "DECIMAL(15,9)")) })
          val coarsest = Call("array_max", Seq(Call("transform", Seq(nn, lam1("__ag_x") { x =>
            Struct(Seq("f" -> Call("element_at", Seq(factorMap, unitOf(x))), "u" -> unitOf(x))) })))).getField("u")
          val unit = hoist(ifc(Call("size", Seq(Call("array_distinct", Seq(units)))) <= int(1),
            Call("get", Seq(units, int(0))), coarsest))
          val toCoarser = Call("map", Ucum.toCoarserFactors(12).flatMap { case (from, to, f) =>
            Seq(str(s"$from|$to"), Cast(Lit(f.bigDecimal.toPlainString), "DECIMAL(13,12)")) })
          val vs = hoist(Call("transform", Seq(nn, lam1("__ag_x") { x =>
            val factor = ifc(unitOf(x) === unit, int(1),
              Call("element_at", Seq(toCoarser, Call("concat", Seq(unitOf(x), str("|"), unit)))))
            d28(value(x) * Cast(factor, "DECIMAL(13,12)")) })))
          val ok = hoist(Call("forall", Seq(vs, lam1("__ag_v")(_.isNotNull))))
          Qs(nn, nums(vs), unit, ok)
      }
    }
    def quantityOf(q: Qs, value: SqlExpr, unit: SqlExpr): SqlExpr =
      ifc(q.ok && value.isNotNull, Struct(Seq("value" -> d28(value), "unit" -> unit)), Null)
    // The element whose converted value is `picked` (the first such, as the reference's strict-comparison folds keep).
    def elementWith(q: Qs, picked: SqlExpr): SqlExpr =
      ifc(q.ok, Call("get", Seq(q.nn, Cast(Call("array_position", Seq(q.v.vs, picked)) - int(1), "INT"))), Null)

    // The list an aggregate reads. A bare `null` list (`AllTrue(null)`) is typed so the higher-order functions
    // accept it.
    def listOperand(src: Expr): SqlExpr = go(src) match {
      case Null => Cast(Null, s"array<${sparkType(elementOfList(src))}>")
      case a    => hoist(a)
    }

    def aggregate(op: AggregateOp, src: Expr): SqlExpr = {
      import AggregateOp._
      val elem = elementOfList(src)
      val arr = listOperand(src)
      def unsupported(why: String): Nothing = throw UnsupportedNode(op.elmType, why)
      op match {
        // AllTrue of a null or empty list is true and AnyTrue is false (the reference agrees).
        case AllTrue => Call("coalesce", Seq(!Call("exists", Seq(arr, lam1("__ag_x")(_ <=> bool(false)))), bool(true)))
        case AnyTrue => Call("coalesce", Seq(Call("exists", Seq(arr, lam1("__ag_x")(_ <=> bool(true)))), bool(false)))
        case _ => elem match {
          case QuantityType(u) =>
            val q = quantities(arr, u)
            op match {
              case Sum      => quantityOf(q, decSum(q.v), q.unit)
              case Avg      => quantityOf(q, decAvg(q.v), q.unit)
              case Median   => quantityOf(q, decMedian(q.v), q.unit)
              case Min      => elementWith(q, Call("array_min", Seq(q.v.vs)))
              case Max      => elementWith(q, Call("array_max", Seq(q.v.vs)))
              case Mode     => elementWith(q, modeOf(q.v.vs))
              case StdDev   => quantityOf(q, decStdDev(q.v, population = false), q.unit)
              case PopulationStdDev => quantityOf(q, decStdDev(q.v, population = true), q.unit)
              // The square of the unit, spelled as `Ucum.squared` spells it.
              case Variance | PopulationVariance =>
                val sq = u.map(s => str(Ucum.squared(s))).getOrElse(ifc(q.unit === str("1"), str("1"), Call("concat", Seq(q.unit, str("2")))))
                quantityOf(q, decVariance(q.v, population = op == PopulationVariance), sq)
              case Product | GeometricMean =>
                unsupported("over quantities needs compound-unit algebra, which is not built")
              case AllTrue | AnyTrue => unsupported("over quantities")
            }
          case t @ (IntegerType | LongType | DecimalType | StringType | _: DateType | _: DateTimeType | _: TimeType | BooleanType) =>
            val numeric = t == IntegerType || t == LongType || t == DecimalType
            def decimals: Nums = nums(hoist(Call("transform", Seq(nonNull(arr, quantity = false), lam1("__ag_x")(d28)))))
            op match {
              case Min | Max if isDateType(t)          => temporalExtreme(arr, max = op == Max)
              case Min | Max if t != BooleanType       => Call(if (op == Max) "array_max" else "array_min", Seq(arr))
              case Mode                                => modeOf(hoist(nonNull(arr, quantity = false)))
              case Sum if t == DecimalType             => decSum(decimals)
              case Sum | Product if t == IntegerType || t == LongType =>
                val ty = sqlType(sparkType(t))
                if (op == Product) productOf(hoist(nonNull(arr, quantity = false)), t)
                else Call("aggregate", Seq(arr, Cast(Null, ty), lam2("__ag_acc", "__ag_x") { (acc, x) =>
                  whenChain(Seq(x.isNull -> acc, acc.isNull -> x), Cast(acc + x, ty)) }))
              case Product if t == DecimalType         => productOf(hoist(nonNull(arr, quantity = false)), t)
              case Avg if numeric                      => decAvg(decimals)
              case Median if numeric                   => decMedian(decimals)
              case Variance if numeric                 => decVariance(decimals, population = false)
              case PopulationVariance if numeric       => decVariance(decimals, population = true)
              case StdDev if numeric                   => decStdDev(decimals, population = false)
              case PopulationStdDev if numeric         => decStdDev(decimals, population = true)
              // GeometricMean(X) = Power(Product(X), 1 / Count(X)) (CQL Reference, GeometricMean), the exponent a
              // CQL Decimal division.
              case GeometricMean if numeric =>
                val nn = hoist(nonNull(arr, quantity = false))
                val n = hoist(sizeOf(nn))
                ifc(n === int(0), nullDec,
                  d28(Call("power", Seq(productOf(nn, DecimalType), Call("round", Seq(decimal(BigDecimal(1)) / n, int(8)))))))
              case _ => unsupported(s"over ${t.show}")
            }
          // A list with no element type: only a list of nulls (or no elements) types this way, and it has no
          // non-null element to aggregate.
          case AnyType => src match {
            case ListExpr(es) if es.forall(isNullish) => Null
            case _ => unsupported("over a list whose element type is unknown")
          }
          case t => unsupported(s"over ${t.show}")
        }
      }
    }

    def go(e: Expr): SqlExpr = e match {
      case NullExpr()             => Null
      case l: Literal             => literal(l)
      case ExpressionRef(name, _) => resolveRef(name)
      case AliasRef(name)         => resolveAlias(name)
      case QueryLetRef(name)      => resolveAlias(name)

      // --- 3VL logical connectives (LANG-3VL). ---
      case And(a, b)  => go(a) && go(b)
      case Or(a, b)   => go(a) || go(b)
      case Not(o)     => !go(o)
      case IsTrue(o)  => go(o) <=> bool(true)
      case IsFalse(o) => go(o) <=> bool(false)
      case Xor(a, b)  => val (ca, cb) = (go(a), go(b)); ifc(ca.isNull || cb.isNull, Null, ca =!= cb)
      case Implies(a, b) => !go(a) || go(b)

      // --- Concept equivalence (LANG-TERMINOLOGY): coding membership. ---
      case Equivalent(a, ConceptValue(codes)) => conceptEquiv(a, codes)
      case Equivalent(ConceptValue(codes), b) => conceptEquiv(b, codes)

      // --- Ratio comparison (component-wise struct equality). ---
      case Equal(a, b)      if isRatio(a) || isRatio(b) => go(a) === go(b)
      case NotEqual(a, b)   if isRatio(a) || isRatio(b) => go(a) =!= go(b)
      case Equivalent(a, b) if isRatio(a) || isRatio(b) => go(a) <=> go(b)

      // --- CQL duration-unit comparison (LANG-QUANTITY). ---
      case Equal(a, b)      if isDurationCmp(a, b) => durationEq(a, b, eq = true)
      case NotEqual(a, b)   if isDurationCmp(a, b) => durationEq(a, b, eq = false)
      case Equivalent(a, b) if isDurationCmp(a, b) => durationEquiv(a, b)

      // --- Dimensional quantity comparison (normalize units, then compare values). ---
      case Equal(a, b)          if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ === _)
      case NotEqual(a, b)       if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ =!= _)
      case Equivalent(a, b)     if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ <=> _)
      case Less(a, b)           if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ < _)
      case Greater(a, b)        if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ > _)
      case LessOrEqual(a, b)    if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ <= _)
      case GreaterOrEqual(a, b) if isQuantity(a) || isQuantity(b) => quantityCompare(a, b)(_ >= _)

      // --- Precision-aware temporal comparison (LANG-INTERVAL). ---
      case Equal(a, b)          if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Eq)
      case NotEqual(a, b)       if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Ne)
      case Equivalent(a, b)     if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Equiv)
      case Less(a, b)           if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Lt)
      case Greater(a, b)        if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Gt)
      case LessOrEqual(a, b)    if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Le)
      case GreaterOrEqual(a, b) if isDate(a) || isDate(b) => dateScalarCmp(a, b)(Ge)
      case SameAs(a, b, prec)   => sameAs(a, b, prec)

      // --- Type-specific equivalence (LANG-EQUIV): String case+whitespace, Decimal to 2 places. Both
      // route through the shared `equivAt` primitive, so the `~` arms cannot drift apart. ---
      case Equivalent(a, b) if typeOf(a) == StringType && typeOf(b) == StringType =>
        equivAt(StringType)(go(a), go(b))
      case Equivalent(a, b) if typeOf(a) == DecimalType || typeOf(b) == DecimalType =>
        equivAt(DecimalType)(go(a), go(b))

      case Equal(a, b)          => go(a) === go(b)
      case NotEqual(a, b)       => go(a) =!= go(b)
      case Equivalent(a, b)     => go(a) <=> go(b)
      case Less(a, b)           => go(a) < go(b)
      case Greater(a, b)        => go(a) > go(b)
      case LessOrEqual(a, b)    => go(a) <= go(b)
      case GreaterOrEqual(a, b) => go(a) >= go(b)
      case IsNull(o)            => go(o).isNull

      case q: QuantityExpr =>
        Struct(Seq("value" -> Cast(decimal(q.value), "DECIMAL(28,8)"),
                   "unit"  -> q.unit.map(str).getOrElse(Cast(Null, "STRING"))))

      // ELM Instance (LANG-INSTANCE): a typed constructor → the canonical struct of its named element
      // slots. A Quantity/Ratio's value fields cast to the canonical DECIMAL(28,8) so downstream
      // quantity ops read the same schema a `Quantity` literal produces.
      case InstanceExpr(classType, elements) =>
        val isQty = CqlType.parse(classType).isInstanceOf[QuantityType]
        val decimalFields = if (isQty) Set("value") else Set("numerator", "denominator")
        Struct(elements.map { e =>
          val v = go(e.value)
          e.name -> (if (decimalFields.contains(e.name)) v.cast("DECIMAL(28,8)") else v)
        })

      // ELM ConvertQuantity (LANG-QUANTITY): rescale a quantity into `toUnit` via the UCUM table. A
      // static source unit folds to a compile-time factor; a dynamic (runtime-column) unit becomes a
      // CASE over the units commensurable with the target (an unmodeled/incommensurable unit -> null).
      case ConvertQuantity(op, toUnit) =>
        qStaticUnit(op) match {
          case Some(src) =>
            requireCommensurable(src, toUnit)
            qStruct(qValue(op) * decimal(Ucum.conversion(src, toUnit)
              .getOrElse(throw UnsupportedNode("ConvertQuantity", s"no UCUM conversion '$src'->'$toUnit'"))), toUnit)
          case None =>
            val units = Ucum.commensurableUnits(toUnit)
            if (units.isEmpty) throw UnsupportedNode("ConvertQuantity", s"target unit '$toUnit' is unmodeled in UCUM")
            if (temporalUdf) {
              // EMIT-SQL-QUANTITY-UDF (opaque): collapse the UCUM CASE ladder — whose every arm re-embedded
              // the operand quantity struct — to ONE `cql_convert_quantity` call over ONE struct operand.
              // The compiler owns UCUM (ARCH-PURITY: the udf module cannot import `sema.Ucum`), so it passes
              // the unit->factor table as a compile-time literal `map(...)`; the UDF is a lookup × multiply.
              val factorMap = Call("map", units.flatMap(u => Seq(str(u), decimal(Ucum.conversion(u, toUnit).get))))
              Call(SqlNames.ConvertQuantity, Seq(go(op), factorMap, str(toUnit)))
            } else {
              val v = go(op)
              val cases = units.map { u =>
                (v.getField("unit") === str(u)) -> (v.getField("value") * decimal(Ucum.conversion(u, toUnit).get))
              }
              qStruct(SqlExpr.Case(cases, Some(Null)), toUnit)
            }
        }

      case DateExpr(parts)     => dateStruct(parts, isDateTime = false, go)
      case DateTimeExpr(parts) => dateStruct(parts, isDateTime = true, go)
      case TimeExpr(parts)     => timeStruct(parts, go)
      case RatioExpr(n, d)     => Struct(Seq("numerator" -> go(n), "denominator" -> go(d)))

      // --- Date/DateTime ± calendar Quantity (temporal), before quantity-quantity arithmetic. ---
      case Add(a, b)      if isDate(a) && isQuantity(b) => dateArith(a, b, minus = false)
      case Subtract(a, b) if isDate(a) && isQuantity(b) => dateArith(a, b, minus = true)
      case Add(a, b)      if isQuantity(a) || isQuantity(b) => quantityArith(a, b)(_ + _)
      case Subtract(a, b) if isQuantity(a) || isQuantity(b) => quantityArith(a, b)(_ - _)
      case Divide(a, b) if isQuantity(a) && isQuantity(b) && qUnit(b) == "1" =>
        val d = qValue(b); ifc(d === int(0), Null, qStruct(Call("round", Seq(qValue(a) / d, int(8))), qUnit(a)))
      case Divide(a, b) if isQuantity(a) && isQuantity(b) =>
        requireCommensurable(qUnit(a), qUnit(b))
        val (va, vb) = if (qUnit(a) == qUnit(b)) (qValue(a), qValue(b))
          else (qValue(a) * decimal(ucumFactor(qUnit(a))), qValue(b) * decimal(ucumFactor(qUnit(b))))
        ifc(vb === int(0), Null, qStruct(Call("round", Seq(va / vb, int(8))), "1"))
      case Divide(a, b) if isQuantity(a) =>
        val d = go(b); ifc(d === int(0), Null, qStruct(Call("round", Seq(qValue(a) / d, int(8))), qUnit(a)))
      case Modulo(a, b) if isQuantity(a) && isQuantity(b) =>
        requireCommensurable(qUnit(a), qUnit(b))
        val vb = if (qUnit(a) == qUnit(b)) qValue(b) else qValue(b) * decimal(Ucum.conversion(qUnit(b), qUnit(a)).get)
        ifc(vb === int(0), Null, qStruct(qValue(a) % vb, qUnit(a)))
      case TruncatedDivide(a, b) if isQuantity(a) && isQuantity(b) =>
        requireCommensurable(qUnit(a), qUnit(b))
        val vb = if (qUnit(a) == qUnit(b)) qValue(b) else qValue(b) * decimal(Ucum.conversion(qUnit(b), qUnit(a)).get)
        val qq = qValue(a) / vb
        ifc(vb === int(0), Null, qStruct(ifc(qq < int(0), Call("ceil", Seq(qq)), Call("floor", Seq(qq))), qUnit(a)))
      case Multiply(a, b) if isQuantity(a) && isQuantity(b) && qUnit(a) == qUnit(b) =>
        qStruct(qValue(a) * qValue(b), qUnit(a) + "2")
      case (_: Multiply | _: Power | _: TruncatedDivide) if isQuantity(e) =>
        throw UnsupportedNode(e.elmType, "quantity multiply/power (compound-unit algebra) arrives in a later phase")

      // --- Arithmetic. Runtime conditions (÷0, mod 0) become null (LANG-RUNTIME-COND). ---
      case Add(a, b)      => go(a) + go(b)
      case Subtract(a, b) => go(a) - go(b)
      case Multiply(a, b) => go(a) * go(b)
      case Divide(a, b)   => val d = go(b); ifc(d === int(0), Null, Call("round", Seq(go(a) / d, int(8))))
      case Modulo(a, b)   => val d = go(b); ifc(d === int(0), Null, go(a) % d)
      case TruncatedDivide(a, b) =>
        val (n, d) = (go(a), go(b)); val qq = n / d
        ifc(d === int(0), Null, ifc(qq < int(0), Call("ceil", Seq(qq)), Call("floor", Seq(qq))))
      case Power(a, b)    => Call("power", Seq(go(a), go(b)))
      case Negate(o) if isQuantity(o) => qStruct(Prefix("-", qValue(o)), qUnit(o))
      case Negate(o)      => Prefix("-", go(o))
      case Abs(o) if isQuantity(o) => qStruct(Call("abs", Seq(qValue(o))), qUnit(o))
      case Abs(o)         => Call("abs", Seq(go(o)))
      case Floor(o)       => toInt32OrNull(Call("floor", Seq(go(o))))
      case Ceiling(o)     => toInt32OrNull(Call("ceil", Seq(go(o))))
      case Truncate(o)    => val x = go(o); toInt32OrNull(ifc(x < int(0), Call("ceil", Seq(x)), Call("floor", Seq(x))))
      case Round(o, prec) =>
        val scale = prec match {
          case None                      => 0
          case Some(Literal(_, Some(p))) => p.toInt
          case Some(_)                   => throw UnsupportedNode("Round", "non-literal Round precision is not yet lowered")
        }
        Call("round", Seq(go(o), int(scale)))
      case Predecessor(o) if isDate(o) => tStep(o, minus = true)
      case Successor(o)   if isDate(o) => tStep(o, minus = false)
      case Predecessor(o) => step(o, minus = true)
      case Successor(o)   => step(o, minus = false)
      case Precision(o)   =>
        def dateCount(r: Int): Int = if (r <= 5) 4 + 2 * r else 17
        def timeCount(r: Int): Int = if (r <= 5) 2 * (r - 2) else 9
        typeOf(o) match {
          case DateType(Some(p))     => int(dateCount(p.rank))
          case DateTimeType(Some(p)) => int(dateCount(p.rank))
          case TimeType(Some(p))     => int(timeCount(p.rank))
          case DecimalType => o match {
            case Literal(_, Some(raw)) => int(raw.dropWhile(_ != '.').drop(1).count(_.isDigit))
            case _ => throw UnsupportedNode("Precision", "Precision of a non-literal Decimal is not yet lowered")
          }
          case t => throw UnsupportedNode("Precision", s"Precision of ${t.show} is not yet lowered")
        }
      case LowBoundary(v, p)  => boundary(v, p, high = false)
      case HighBoundary(v, p) => boundary(v, p, high = true)
      case Exp(o)             => Call("exp", Seq(go(o)))
      case Ln(o)              => val x = go(o); ifc(x < int(0), Null, Call("ln", Seq(x)))
      case Log(x, base)       =>
        val (xx, bb) = (go(x), go(base)); val lb = Call("ln", Seq(bb))
        ifc(xx <= int(0) || bb <= int(0) || lb === int(0), Null, Call("ln", Seq(xx)) / lb)
      case MinValue(vt)       => boundValue(vt, min = true)
      case MaxValue(vt)       => boundValue(vt, min = false)

      case Coalesce(Seq(list)) if isList(list) =>
        Call("get", Seq(Call("filter", Seq(go(list), Lambda(Seq("x"), Col("x").isNotNull))), int(0)))
      case Coalesce(os)       => Call("coalesce", os.map(go))
      case Convert(o, toType) => castScalar(go(o), toType)

      // --- Choice / FHIR value[x] / As-Is (order mirrors ColumnLowering). ---
      case As(o, _, _, cts) if cts.nonEmpty =>
        val members = cts.map(CqlType.parse)
        if (isList(o)) buildChoiceList(o, members) else buildChoice(o, members)
      // A helper's fluent `onsetSpan`: `choice.low as <Point>` / `choice.high as <Point>` on a Choice-of-intervals
      // (`Choice<DateTime, Interval<DateTime>, Interval<Quantity>, …>`). Read the bound of the interval ARM
      // whose point type is <Point>, guarded by the discriminator — NOT `.low` off the whole choice struct
      // (which fails analysis: `[FIELD_NOT_FOUND] low in __choice, asDateTime, asInterval_DateTime_, …`).
      // Must precede the generic `As … if isChoice(o)` (which would `go(Property(choice,"low"))` → that read).
      case As(Property(Some(base), _, bound), asType, _, Nil)
          if (bound == "low" || bound == "high") && choiceIvArm(base, asType).isDefined =>
        val m = choiceIvArm(base, asType).get
        val c = hoist(go(base))
        ifc(c.getField("__choice") === str(choiceTag(m)), c.getField(choiceSlot(m)).getField(bound), Null)
      case As(Property(src, scope, path), asType, _, Nil) if isFhirType(asType) && FhirModel.isChoiceElement(path) =>
        fhirChoiceSlot(src, scope, path, asType)
      case Is(Property(src, scope, path), isType) if isFhirType(isType) && FhirModel.isChoiceElement(path) =>
        fhirChoiceSlot(src, scope, path, isType).isNotNull
      case As(Property(_, _, _), asType, _, Nil) if isFhirType(asType) => castScalar(Null, asType)
      case Is(Property(_, _, _), isType)         if isFhirType(isType) => bool(false)
      case As(o, asType, _, _) if isChoice(o) =>
        val target = CqlType.parse(asType)
        if (choiceMembers(o).map(choiceTag).contains(choiceTag(target))) {
          val c = go(o)
          ifc(c.getField("__choice") === str(choiceTag(target)), c.getField(choiceSlot(target)), Null)
        } else Null
      // `as Quantity` on an operand that is ALREADY a quantity-family struct (a resolved value[x] slot, a
      // FHIR Quantity element) is NOT a scalar promotion — normalize it to the System.Quantity shape
      // `{value, unit}` (matching FHIRHelpers.ToQuantity), so `.value`/arithmetic and a Coalesce across
      // sibling quantity branches see one struct type (a `Coalesce` over a dose range's end and a dose quantity).
      case As(o, asType, _, Nil) if CqlType.parse(asType).isInstanceOf[QuantityType] && isQuantityLikeType(typeOf(o)) =>
        val q = go(o); Struct(Seq("value" -> q.getField("value").cast("decimal(28,8)"), "unit" -> fhirQuantityUnit(q)))
      case As(o, asType, _, _) => castScalar(go(o), asType)
      case Is(o, isType) if isChoice(o) =>
        Call("coalesce", Seq(go(o).getField("__choice") === str(choiceTag(CqlType.parse(isType))), bool(false)))
      case Is(o, isType) => bool(choiceTag(typeOf(o)) == choiceTag(CqlType.parse(isType)))

      // --- EXEC-OPAQUE-UDF: an opaque het-union-nav catalog call the rewrite tagged with the reserved
      // marker library. Lower to a raw `Call(<udf name>, <alias struct>, '<field>')` — Catalyst sees one
      // black-box node, never the per-arm cast/coalesce tree. The struct operand stays a native column
      // (EXEC-NATIVE-DISCRIMINATOR). ---
      case FunctionRef(fn, Some(SqlNames.UdfMarkerLib), Seq(structOperand, Literal(_, Some(field)))) =>
        Call(fn, Seq(go(structOperand), str(field)))

      // --- FHIR property navigation + FHIRHelpers converters (DATA-RETRIEVER). ---
      case Property(None, Some(_), path) => Col(path)
      // A System.Code field read off `ToCode(<FHIR Coding>)` (e.g. `Encounter.class.code`): FHIRHelpers
      // ToCode yields a System.Code whose `code`/`system`/`version`/`display` ARE the FHIR Coding's fields
      // (the `{coding:[…]}` ToCode lowering is the Concept shape for `conceptEquiv`'s `.coding`, which has no
      // top-level `code`), so read the field straight off the Coding.
      case Property(Some(FunctionRef("ToCode", Some("FHIRHelpers"), Seq(arg))), _, field)
          if Set("code", "system", "version", "display")(field) => go(arg).getField(field)
      // `.value` on a System.Quantity reads the numeric FIELD (a Quantity is a struct with `value`), not
      // the FHIR-primitive `.value` unwrap that `navigate` applies (which returns the whole struct).
      case Property(Some(src), _, "value") if isQuantityLikeType(typeOf(src)) => go(src).getField("value")
      case Property(Some(src), _, path)  => navigate(go(src), path.split("\\.").toList)
      case FunctionRef("ToDateTime", Some("FHIRHelpers"), Seq(arg)) => fhirTemporal(go(arg), DateTimePrecision.Millisecond.rank)
      case FunctionRef("ToDate", Some("FHIRHelpers"), Seq(arg))     => fhirTemporal(go(arg), DateTimePrecision.Day.rank)
      case FunctionRef("ToInterval", Some("FHIRHelpers"), Seq(arg)) =>
        val period = go(arg)
        // EMIT-SQL-TEMPORAL-UDF (opaque): return the {low,high} struct from one call, so Catalyst never
        // recurses into the per-endpoint normalization (and the named_struct leaves the plan).
        if (temporalUdf) Call(SqlNames.ToInterval, Seq(period.getField("start"), period.getField("end")))
        else Struct(Seq(
          "low"  -> fhirTemporal(period.getField("start"), DateTimePrecision.Millisecond.rank),
          "high" -> fhirTemporal(period.getField("end"), DateTimePrecision.Millisecond.rank)))
      // FHIRHelpers.ToQuantity: a null FHIR Quantity is a null System.Quantity, not a quantity whose value is
      // null — so `(O.value as Quantity) is not null` is false when the slot is absent (LANG-QUANTITY).
      case FunctionRef("ToQuantity", Some("FHIRHelpers"), Seq(arg)) =>
        val q = go(arg)
        ifc(q.isNull, Null,
          Struct(Seq("value" -> q.getField("value").cast("decimal(28,8)"), "unit" -> fhirQuantityUnit(q))))
      case FunctionRef("ToConcept", Some("FHIRHelpers"), Seq(arg)) => go(arg)
      case FunctionRef("ToCode", Some("FHIRHelpers"), Seq(arg))    => Struct(Seq("coding" -> Call("array", Seq(go(arg)))))
      // ToValue on a NON-choice FHIR primitive is an identity unwrap in the bare-primitive model (as the
      // other FHIRHelpers primitive conversions are). The value[x] CHOICE forms are resolved upstream:
      // alias-rooted by `SqlQueryLowering.rewriteChoiceValueXStatic` (relation and Array query paths), and
      // any other statically typed base (a `let` chain, or a define reference in a scalar define body) by
      // `SqlQueryLowering.rewriteTypedChoiceValueX`. So any ToValue reaching here is monomorphic.
      case FunctionRef("ToValue", Some("FHIRHelpers"), Seq(arg))   => go(arg)
      case FunctionRef(fn, Some("FHIRHelpers"), Seq(arg)) if FhirPrimitiveConversions.contains(fn) => go(arg)

      // --- Conditionals (ARCH-CONDITIONAL). ---
      case If(cond, t, e0) => ifc(go(cond), go(t), go(e0))
      case CqlCase(comparand, items, elseExpr) =>
        val pairs = items.map { it =>
          val cond = comparand match { case Some(c) => go(c) === go(it.when); case None => go(it.when) }
          cond -> go(it.thenExpr)
        }
        if (pairs.isEmpty) go(elseExpr) else whenChain(pairs, go(elseExpr))

      // --- Scalar lists (LANG-LIST). ---
      case ListExpr(elems)  => Call("array", elems.map(go))
      case Exists(o)        => Call("size", Seq(go(o))) > int(0)
      case First(o)         => Call("get", Seq(go(o), int(0)))
      case Last(o)          => val a = go(o); Call("get", Seq(a, Call("size", Seq(a)) - int(1)))
      // CQL Count is the number of non-null elements, 0 for a null list (CQL Reference, Count).
      case Count(o) =>
        val a = listOperand(o)
        ifc(a.isNull, int(0), Call("size", Seq(nonNull(a, quantity = isQuantityLikeType(elementOfList(o))))))
      case AggregateFunction(op, src) => aggregate(op, src)
      case Split(s, sep) =>
        val pattern = sep match {
          case Literal(_, Some(v)) => java.util.regex.Pattern.quote(v)
          case _ => throw UnsupportedNode("Split", "only a constant string separator is supported")
        }
        Call("split", Seq(go(s), str(pattern)))
      case Indexer(list, ix) => Call("get", Seq(go(list), go(ix)))
      case In(point, iv) if isInterval(iv) =>
        val dm = ivIsDate(iv)
        val (il0, ih0) = ivOperand(iv)
        val pcc = constTemporal(point)
        val dayPoint = dm && pcc.isEmpty && point.isInstanceOf[Property]
        val pcRaw = if (dayPoint) fhirTemporal(go(point), DateTimePrecision.Day.rank) else go(point)
        val pc = if (dm && pcc.isEmpty) hoist(pcRaw) else pcRaw
        // Point truncated to day -> truncate the bounds to day too, so the compare is at ONE precision.
        val (il, ih) = if (dayPoint) (truncBndDay(il0), truncBndDay(ih0)) else (il0, ih0)
        cmpPoints(il.col, il.const, pc, pcc, dm)(Le, il.inf, None) &&
          cmpPoints(pc, pcc, ih.col, ih.const, dm)(Le, None, ih.inf)
      // List membership uses EQUALITY, not equivalence (LANG-EQUIV) — "the in operator for lists
      // returns true if the given element is in the given list using equality semantics" — so
      // `'diabetes' in { 'Diabetes' }` is FALSE. `array_contains` is exactly that.
      case In(elem, list)   => Call("array_contains", Seq(go(list), go(elem)))
      // ELM `Distinct` — `distinct X`, likewise EQUALITY-based ("duplicates eliminated using equality
      // semantics"), so Spark's `array_distinct` is the faithful lowering. This node previously did not
      // decode at all and failed the Normalize verifier as `UnknownExpr(Distinct)`.
      case Distinct(o)      => Call("array_distinct", Seq(go(o)))
      case SingletonFrom(o) => val a = go(o); ifc(Call("size", Seq(a)) === int(1), Call("get", Seq(a, int(0))), Null)
      case ToList(o)        =>
        val c = go(o); ifc(c.isNull, Cast(Call("array", Nil), s"array<${sparkType(typeOf(o))}>"), Call("array", Seq(c)))
      case Union(a, b)     => Call("array_union", Seq(go(a), go(b)))
      case Intersect(a, b) => Call("array_intersect", Seq(go(a), go(b)))
      case Except(a, b)    => Call("array_except", Seq(go(a), go(b)))

      case TupleExpr(elems) => Struct(elems.map(el => el.name -> go(el.value)))

      // Array-encoded query -> HOF SqlExpr (the Relation encoding is lowered at the frame level).
      case q: Query => SqlQueryLowering.array(q, resolveRef, resolveAlias, typeOf, temporalUdf)

      // --- Intervals (LANG-INTERVAL). ---
      case iv: IntervalExpr => intervalStruct(iv, go, typeOf, hoist, hoistT, temporalUdf)
      case Start(o)  => lo(o)
      case End(o)    => hi(o)
      case Width(o)  => hi(o) - lo(o)
      case DateFrom(o) => go(o).substr(int(1), int(10))
      case ToDateTimeExpr(o) => go(o)
      case Message(o)        => go(o)
      case CalculateAgeAt(birth, asOf, prec) =>
        if (!prec.contains("Year"))
          throw UnsupportedNode("CalculateAgeAt", s"precision '${prec.getOrElse("?")}' not yet lowered (only Year)")
        def asTemporal(e0: Expr): SqlExpr = e0 match {
          case _: Property => fhirTemporal(go(e0), DateTimePrecision.Day.rank)
          case _           => go(e0)
        }
        // EMIT-SQL-TEMPORAL-UDF (opaque): one call folds the tsOf/year/month/day age tower.
        if (temporalUdf) Call(SqlNames.AgeAt, Seq(asTemporal(birth), asTemporal(asOf)))
        else {
        val bs = hoist(asTemporal(birth)); val as_ = hoist(asTemporal(asOf))
        val bt = hoist(tsOf(bs)); val at = hoist(tsOf(as_))
        val notYet = (Call("month", Seq(at)) < Call("month", Seq(bt))) ||
          (Call("month", Seq(at)) === Call("month", Seq(bt)) && Call("dayofmonth", Seq(at)) < Call("dayofmonth", Seq(bt)))
        ifc(bs.isNull || as_.isNull, Cast(Null, "INT"),
          (Call("year", Seq(at)) - Call("year", Seq(bt)) - ifc(notYet, int(1), int(0))).cast("int"))
        }

      // --- Allen interval relations (LANG-INTERVAL). ---
      case Before(a, b) if isInterval(a) => ivGuard(a, b){ val (_, ah) = ivOperand(a); val (bl, _) = ivOperand(b); cmpB(ah, bl, ivIsDate(a))(Lt) }
      case After(a, b)  if isInterval(a) => ivGuard(a, b){ val (al, _) = ivOperand(a); val (_, bh) = ivOperand(b); cmpB(al, bh, ivIsDate(a))(Gt) }
      case Before(a, b) => cmpB(pointBnd(a), pointBnd(b), isDate(a))(Lt)
      case After(a, b)  => cmpB(pointBnd(a), pointBnd(b), isDate(a))(Gt)
      case SameOrBefore(a, b) if isInterval(a) => ivGuard(a, b){ val (_, ah) = ivOperand(a); val (bl, _) = ivOperand(b); cmpB(ah, bl, ivIsDate(a))(Le) }
      case SameOrAfter(a, b)  if isInterval(a) => ivGuard(a, b){ val (al, _) = ivOperand(a); val (_, bh) = ivOperand(b); cmpB(al, bh, ivIsDate(a))(Ge) }
      case SameOrBefore(a, b) => cmpB(pointBnd(a), pointBnd(b), isDate(a))(Le)
      case SameOrAfter(a, b)  => cmpB(pointBnd(a), pointBnd(b), isDate(a))(Ge)
      // LANG-INTERVAL-PRECISION: `atPrecision` truncates BOTH operands when the relation names one
      // (`overlaps day of`, `during day of`); a no-op when it does not.
      case Overlaps(a, b, p) => ivGuard(a, b){ val dm = ivIsDate(a)
        val (al, ah) = atPrecision(p)(ivOperand(a)); val (bl, bh) = atPrecision(p)(ivOperand(b))
        cmpB(al, bh, dm)(Le) && cmpB(bl, ah, dm)(Le) }
      case Includes(a, b)  => ivGuard(a, b){ val dm = ivIsDate(a); val (al, ah) = ivOperand(a); val (bl, bh) = ivOperand(b); cmpB(al, bl, dm)(Le) && cmpB(bh, ah, dm)(Le) }
      case IncludedIn(a, b, p) => ivGuard(a, b){ val dm = ivIsDate(a)
        val (al, ah) = atPrecision(p)(ivOperand(a)); val (bl, bh) = atPrecision(p)(ivOperand(b))
        cmpB(bl, al, dm)(Le) && cmpB(ah, bh, dm)(Le) }
      case Starts(a, b)    => ivGuard(a, b){ val dm = ivIsDate(a); val (al, ah) = ivOperand(a); val (bl, bh) = ivOperand(b); cmpB(al, bl, dm)(Eq) && cmpB(ah, bh, dm)(Le) }
      case Ends(a, b)      => ivGuard(a, b){ val dm = ivIsDate(a); val (al, ah) = ivOperand(a); val (bl, bh) = ivOperand(b); cmpB(ah, bh, dm)(Eq) && cmpB(al, bl, dm)(Ge) }

      case other =>
        val detail = other match { case FunctionRef(n, lib, _) => s" ('${lib.map(_ + ".").getOrElse("")}$n')"; case _ => "" }
        throw UnsupportedNode(other.elmType, s"SqlColumnLowering does not yet handle this node$detail")
    }
    go(expr)
  }

  // =============================================================================================
  // Companion helpers (copied from ColumnLowering, re-targeted to SqlExpr).
  // =============================================================================================

  private[lower] def choiceTag(t: CqlType): String = t match {
    case _: QuantityType => "Quantity"
    case _: DateType     => "Date"
    case _: DateTimeType => "DateTime"
    case _: TimeType     => "Time"
    // A FHIR resource member's tag is CANONICALIZED to its local type name (`FHIR.Encounter`), so the
    // define-build side (union arms typed by their `{url}Type` retrieve) and the consume side (a Choice
    // member typed by the short name) agree on the discriminated slot — a het-union navigation dispatches
    // to `asFHIR_Encounter` regardless of which type-name form each side carries (LANG-CHOICE-RESOURCE).
    case NamedType(n)    => "FHIR." + FhirModel.localName(n)
    case other           => other.show
  }
  private[lower] def choiceSlot(t: CqlType): String = "as" + choiceTag(t).replaceAll("[^A-Za-z0-9]", "_")

  /** Collapse a DEGENERATE `Choice` whose members all share ONE [[choiceTag]] to its representative member
    * (LANG-CHOICE-RESOURCE). This is the `Choice<Condition, Condition>` case: a union of two
    * QI-Core PROFILES of the SAME FHIR resource (`ConditionProblemsHealthConcerns` union
    * `ConditionEncounterDiagnosis`) is typed as a two-member Choice by the profile NAMES, but both localize
    * to `Condition` — so at the physical FHIR-resource grain the union is HOMOGENEOUS and must
    * realize/navigate as the plain resource, NOT a discriminated struct (whose slots would both be
    * `asFHIR_Condition` — a duplicate-field struct, and whose `__choice` tag `Choice<Condition, Condition>`
    * matches no member slot). A genuine multi-tag Choice / any other type is returned unchanged. */
  private[lower] def collapseChoice(t: CqlType): CqlType = t match {
    case ChoiceType(ms) if ms.map(choiceTag).distinct.sizeIs == 1 => ms.head
    case other                                                    => other
  }

  /** The distinct resource/choice members BY [[choiceTag]] (each degenerate member first collapsed), so two
    * profiles of one FHIR resource count as ONE member — the shared home for every het-union member-count
    * and slot-layout decision ([[SqlQueryLowering]]/[[SqlFrameLowering]]). Preserves first-seen order. */
  private[lower] def dedupByTag(ms: Seq[CqlType]): Seq[CqlType] =
    ms.map(collapseChoice).distinctBy(choiceTag)

  /** The SQL slot type a scalar Choice member is cast to (the package-visible face of [[sparkType]]) —
    * so the resource-aware relation tagger ([[SqlFrameLowering.choiceTagArm]]) casts scalar member slots
    * identically to [[choiceStructOf]]. */
  private[lower] def slotType(t: CqlType): String = sparkType(t)

  /** Wrap an arm's realized element `armVal` into the discriminated Choice struct `{__choice, as<M1>, …}`
    * (LANG-CHOICE-RESOURCE), resource-aware: the active member's slot is a resource BODY → the
    * demand-pruned HANDLE ([[handleBody]]); a tuple → the struct as-is; a scalar → cast to its slot type.
    * Every other slot is NULL — a scalar null is cast (a single arm stays well-typed), a resource/tuple
    * null is bare (SQL UNION ALL coerces it to the sibling arm's type, so no model Spark DataType is
    * spelled — the Spark-free encoding). Shared by the union-define, conditional-union, and inline
    * het-union-source paths (PROC-DRY). */
  private[lower] def choiceTagArm(armVal: SqlExpr, member: CqlType, members: Seq[CqlType],
                                  demand: Set[String], flat: Boolean = false): SqlExpr = {
    val tag = choiceTag(member)
    if (flat && members.forall(_.isInstanceOf[NamedType])) {
      // ONE resource-union carrier builder ([[choiceCarrierArm]]) — the demand-pruned native flat carrier
      // (EMIT-SQL-FLAT-CHOICE), the sole het-Choice encoding.
      choiceCarrierArm(armVal, member, members, demand)
    }
    else Struct(("__choice" -> str(tag)) +: members.map { m =>
      val active = choiceTag(m) == tag
      val v: SqlExpr = m match {
        case NamedType(_) => if (active) handleBody(armVal, m, demand) else Null
        case TupleType(_) => if (active) armVal else Null
        case scalar       => if (active) Cast(armVal, sparkType(scalar)) else Cast(Null, sparkType(scalar))
      }
      choiceSlot(m) -> v
    })
  }

  /** The physical slot(s) a demanded top-level field `d` expands to on FHIR resource `rt`: a `value[x]`
    * base to its typed slots (`onset` -> `onsetDateTime`/`onsetPeriod`/…), a plain element to itself, a
    * field this resource lacks to nothing. Shared by [[handleBody]] and the flat construction so the
    * demand-pruned slot set is computed ONE way (and matches the navigation's `vxSlot`/plain-path slots). */
  private[lower] def physSlots(rt: String, d: String): Seq[String] = {
    val slots = FhirModel.choiceSlots(rt, d)
    if (slots.nonEmpty)                          slots
    else if (FhirModel.navType(rt, d).isDefined) Seq(d)
    else                                         Seq.empty
  }

  /** The ONE het-Choice resource-union carrier builder (EMIT-SQL-FLAT-CHOICE): `struct(__choice,
    * __resource_type, __resource_id, <demand-pruned physical slots>)`. Every arm emits the SAME field
    * set/order (the UNION of demand slots across all members), filling only the slots THIS member carries
    * and nulling the rest — so UNION-ALL aligns by position and a navigation reads a shallow top-level
    * `alias.<slot>` (one extraction per field, no per-arm nesting), collapsing the `NestedColumnAliasing`
    * O(N²). The demand slots are NATIVE top-level fields (the flat encoding is the sole het-Choice carrier;
    * the former VARIANT payload alternative was retired — see EMIT-SQL-VARIANT-CHOICE RETIRED). */
  private[lower] def choiceCarrierArm(armVal: SqlExpr, member: CqlType, members: Seq[CqlType],
                                      demand: Set[String]): SqlExpr = {
    def rtOf(m: CqlType): Option[String] = m match { case NamedType(n) => Some(FhirModel.localName(n)); case _ => None }
    val ds = demand.toSeq.sorted
    // The global slot set — deterministic (sorted demand, member order), distinct — so every arm's row has
    // an identical schema (required for UNION ALL positional alignment).
    val globalSlots = ds.flatMap(d => members.flatMap(m => rtOf(m).toSeq.flatMap(rt => physSlots(rt, d)))).distinct
    val thisSlots   = rtOf(member).toSeq.flatMap(rt => ds.flatMap(d => physSlots(rt, d))).toSet
    val identity = Seq(
      "__choice"                 -> str(choiceTag(member)),
      SystemColumns.ResourceType -> Field(armVal, SystemColumns.ResourceType),
      SystemColumns.ResourceId   -> Field(armVal, SystemColumns.ResourceId))
    val slots = globalSlots.map(s => s -> (if (thisSlots(s)) Field(armVal, s) else Null))
    Struct(identity ++ slots)
  }

  /** The demand-pruned resource HANDLE (ARCH-RESOURCE-HANDLE): `{__resource_type, __resource_id, <demand
    * slots>}` off the arm's resource `__val` body. Identity is promoted so a whole export renders by
    * identity (`JsonNormalize.isResourceStruct`); each demanded top-level field expands to its physical
    * slot(s) (a value[x] base to its typed slots, a plain element to itself); a field this member lacks
    * contributes nothing. The SQL twin of `ColumnLowering.choiceHandleOf` (no model Spark DataType — SQL
    * UNION ALL coerces the sibling arms' null slots). */
  private[lower] def handleBody(body: SqlExpr, member: CqlType, demand: Set[String]): SqlExpr = {
    val rt = member match { case NamedType(n) => FhirModel.localName(n); case _ => "" }
    val identity = Seq(
      SystemColumns.ResourceType -> Field(body, SystemColumns.ResourceType),
      SystemColumns.ResourceId   -> Field(body, SystemColumns.ResourceId))
    val fields = demand.toSeq.sorted.flatMap(d => physSlots(rt, d).map(s => s -> Field(body, s)))
    Struct(identity ++ fields)
  }

  private[lower] def choiceStructOf(value: SqlExpr, tag: String, members: Seq[CqlType]): SqlExpr =
    Struct(("__choice" -> str(tag)) +: members.map { m =>
      val slot = if (choiceTag(m) == tag) value else Null
      choiceSlot(m) -> slot.cast(sparkType(m))
    })

  private def intervalStruct(iv: IntervalExpr, go: Expr => SqlExpr, typeOf: Expr => CqlType,
                             hoist: Hoister, hoistT: Hoister, temporalUdf: Boolean = false): SqlExpr = {
    val point = typeOf(iv) match { case IntervalType(p) => p; case _ => AnyType }
    def s(fields: (String, SqlExpr)*): SqlExpr = Struct(fields)
    // EMIT-SQL-TEMPORAL-UDF (opaque): close an OPEN temporal bound with the step UDF (successor for a low
    // bound, predecessor for a high one) rather than the inline strStep tower; `low` picks the direction
    // (closeTemporal steps `minus = !low`).
    def closeBound(c: SqlExpr, low: Boolean): SqlExpr =
      if (temporalUdf) hoistT(Call(SqlNames.TemporalStep, Seq(c, bool(!low))))
      else hoistT(closeTemporal(c, low, hoistT))
    // ARCH-SQL-CSE: hoist each interval BOUND value to a `__h<N>` column (SqlHoist). A closed bound was
    // previously inlined verbatim into BOTH slots, so a POINT interval `Interval[x, x]` — the QICore
    // `onsetSpan` DateTime case — spelled its choice-dispatch tower `x` twice; the
    // structural dedup collapses the two identical bounds (and a bound shared across sibling intervals) to
    // ONE column, so `Start`/`End`/`.low`/`.high` reads become a scalar column ref instead of re-inlining
    // (and re-exploding, via Catalyst `GetStructField` pushdown through the nested `CASE`) the whole tower.
    // A no-op inside `array` HOF lambdas (where `hoist` is `noHoist`). The OR-of-temporal-`exists` exclusion
    // blowup (SqlHoist doc). `boundH` hoists the bound FIRST, then closes it if the bound is open.
    // Hoist each interval BOUND via `hoistT` (the fine-temporal-tower hoister), NOT `hoist`. In the normal
    // define-body path `hoist eq hoistT`, so this is byte-identical. It matters ONLY for the `such that`
    // two-sided carrier (EMIT-SQL-LET-CARRIER, [[SqlQueryLowering.combined]]): a bound is a date/scalar
    // tower that may sit inside an onset/abatement choice `CASE` arm (a birthDate+age arm whose
    // `CAST(… AS DECIMAL)` errors on a Period's date string), so it must stay INLINE-inside-its-guard when
    // two-sided — `hoistT` leaves it inline there, while the WHOLE interval struct is bound once by the
    // outer `ivOperand`/`ivGuard` `hoist`. Hoisting a bound with `hoist` would lift the guarded arm to the
    // eager carrier struct and evaluate it UNCONDITIONALLY (CORE-NO-SILENT-MISLOWER).
    def boundH(o: Option[Expr], low: Boolean, closed: Boolean, wrap: SqlExpr => SqlExpr): SqlExpr =
      o.map(e => hoistT(go(e))).map(c => if (closed) c else wrap(c)).getOrElse(Null)
    point match {
      case IntegerType | LongType | AnyType =>
        val low  = boundH(iv.low,  low = true,  iv.lowClosed,  c => Infix("+", c, int(1)))
        val high = boundH(iv.high, low = false, iv.highClosed, c => Infix("-", c, int(1)))
        s("low" -> low, "high" -> high)
      case _: DateType | _: DateTimeType =>
        val low  = boundH(iv.low,  low = true,  iv.lowClosed,  c => closeBound(c, low = true))
        val high = boundH(iv.high, low = false, iv.highClosed, c => closeBound(c, low = false))
        s("low" -> low, "high" -> high)
      case _: QuantityType =>
        s("low" -> iv.low.map(e => hoistT(go(e))).getOrElse(Null), "high" -> iv.high.map(e => hoistT(go(e))).getOrElse(Null))
      case ChoiceType(ms) if ms.exists { case _: DateType | _: DateTimeType => true; case _ => false } =>
        val tName = ms.collectFirst { case _: DateTimeType => "DateTime"; case _: DateType => "Date" }.get
        def bound(o: Option[Expr], low: Boolean, closed: Boolean): SqlExpr =
          o.map(e => hoistT(go(As(e, tName, strict = false, Nil))))
            .map(c => if (closed) c else closeBound(c, low)).getOrElse(Null)
        s("low" -> bound(iv.low, low = true, iv.lowClosed), "high" -> bound(iv.high, low = false, iv.highClosed))
      case other => throw UnsupportedNode("Interval",
        s"interval over ${other.show} points (continuous boundary algebra) arrives in a later phase")
    }
  }

  private val PrecLen = Vector(4, 7, 10, 13, 16, 19, 23)
  private def lenOfRank(r: Int): Int = if (r >= 6) 23 else PrecLen(r)
  private val FullTemplate = "0000-01-01T00:00:00.000"

  private def rankOfLen(len: SqlExpr): SqlExpr =
    whenChain(Seq(
      Infix("<=", len, int(4))  -> int(0), Infix("<=", len, int(7))  -> int(1),
      Infix("<=", len, int(10)) -> int(2), Infix("<=", len, int(13)) -> int(3),
      Infix("<=", len, int(16)) -> int(4), Infix("<=", len, int(19)) -> int(5)),
      int(6))

  private def lenOfRankCol(rank: SqlExpr): SqlExpr =
    (0 to 6).foldRight(int(23): SqlExpr) { (r, acc) => ifc(Infix("=", rank, int(r)), int(lenOfRank(r)), acc) }

  private def tsOf(s: SqlExpr): SqlExpr =
    Cast(Call("concat", Seq(s, Call("substr", Seq(str(FullTemplate), Infix("+", Call("length", Seq(s)), int(1)), int(23))))), "TIMESTAMP_NTZ")

  // Format a timestamp back to the canonical ISO string, then truncate to the operand's precision.
  // The pattern carries NO quote-escaped literal (EMIT-SQL-EXECUTE): a `date_format` mask of
  // `yyyy-MM-dd'T'HH:mm:ss.SSS` renders as the SQL literal `'yyyy-MM-dd''T''HH:mm:ss.SSS'`, whose doubled
  // quotes are collapsed to a bare `T` when Spark re-parses the statement's stored text (temp-view text is
  // re-analyzed on reference) — the pattern then reaches `date_format` as the invalid `yyyy-MM-ddTHH:mm:ss.SSS`
  // (`INVALID_DATETIME_PATTERN`). So format with a SPACE separator (a valid UNQUOTED literal that needs no
  // escaping) and `replace` the sole space with `T` — byte-identical output, no fragile `''` in the emitted SQL.
  private def fmtTrunc(ts: SqlExpr, len: SqlExpr): SqlExpr =
    Call("substr", Seq(
      Call("replace", Seq(Call("date_format", Seq(ts, str("yyyy-MM-dd HH:mm:ss.SSS"))), str(" "), str("T"))),
      int(1), len))

  // ARCH-TEMPORAL-CSE: add/subtract ONE calendar unit at the operand's own precision. The old shape was a
  // 7-way `CASE WHEN rankCol = <r> THEN CAST(ts + make_interval(…)) …` — every arm re-inlined `ts` (a
  // `concat`/`substr`/`CAST` tower) AND re-inlined `rankCol` (itself a 6-way `rankOfLen` CASE when the
  // caller does not pass a hoisted column), so one `closeTemporal` carried 7 copies of `ts` and 7 copies
  // of the precision ladder. Build instead a SINGLE `make_interval` whose components each pick the ±1 at
  // their own slot: `ts` and the interval add appear exactly ONCE, and `rankCol` is referenced per slot
  // (cheap when the caller hoists it to a `__h<N>` column). The `-0.001s` millisecond fallback moves into
  // the seconds slot's own CASE. Byte-different SQL, identical arithmetic.
  private def addUnit(ts: SqlExpr, rankCol: SqlExpr, minus: Boolean): SqlExpr = {
    import CqlType.DateTimePrecision._
    val one = int(if (minus) -1 else 1); val z = int(0)
    val ms = decimal(BigDecimal(if (minus) "-0.001" else "0.001"))
    def slot(r: Int): SqlExpr = ifc(Infix("=", rankCol, int(r)), one, z)
    val secs = whenChain(Seq(
      Infix("=", rankCol, int(Second.rank)) -> one,
      Infix("<", rankCol, int(Second.rank)) -> z),
      ms) // rank > Second.rank == Millisecond: step the sub-second slot
    val iv = Call("make_interval", Seq(
      slot(Year.rank), slot(Month.rank), z, slot(Day.rank), slot(Hour.rank), slot(Minute.rank), secs))
    Cast(Infix("+", ts, iv), "TIMESTAMP_NTZ")
  }

  // ARCH-SQL-CSE: `hoist` (defaulted to `noHoist`) lifts the precision ladder `rankOfLen(length(s))` to a
  // single `__h<N>` column so `addUnit` references it by name instead of re-expanding the 6-way CASE at
  // each interval slot. `s` and its `length` are already alias-rooted (a prior `__h<N>` or a struct
  // column), so `tsOf`/`fmtTrunc` need no further hoisting.
  private def strStep(s: SqlExpr, rankCol: SqlExpr, minus: Boolean, hoist: Hoister = noHoist): SqlExpr =
    fmtTrunc(addUnit(tsOf(s), hoist(rankCol), minus), Call("length", Seq(s)))

  private def closeTemporal(b: SqlExpr, low: Boolean, hoist: Hoister = noHoist): SqlExpr =
    strStep(b, rankOfLen(Call("length", Seq(b))), minus = !low, hoist)

  private def fhirTemporal(s: SqlExpr, nominalRank: Int): SqlExpr =
    Call("substr", Seq(s, int(1), int(lenOfRank(nominalRank))))

  private def dateStruct(parts: Seq[Expr], isDateTime: Boolean, go: Expr => SqlExpr): SqlExpr =
    literalIso(parts, isDateTime) match { case Some(s) => str(s); case None => dateStructDynamic(parts, isDateTime, go) }

  private def literalIso(parts: Seq[Expr], isDateTime: Boolean): Option[String] = {
    val opts = parts.map { case Literal(_, Some(raw)) => raw.toIntOption; case _ => None }
    if (opts.isEmpty || opts.exists(_.isEmpty)) None
    else {
      val v = opts.map(_.get)
      def at(i: Int, d: Int): Int = v.lift(i).getOrElse(d)
      val full = f"${v.head}%04d-${at(1, 1)}%02d-${at(2, 1)}%02dT${at(3, 0)}%02d:${at(4, 0)}%02d:${at(5, 0)}%02d.${at(6, 0)}%03d"
      val rank = if (!isDateTime) math.min(v.size - 1, 2) else math.min(v.size - 1, 6)
      Some(full.substring(0, lenOfRank(rank)))
    }
  }

  private def dateStructDynamic(parts: Seq[Expr], isDateTime: Boolean, go: Expr => SqlExpr): SqlExpr = {
    val cols = parts.map(go)
    def part(i: Int, default: Int, width: Int): SqlExpr =
      Call("lpad", Seq(Cast(Call("coalesce", Seq(cols.lift(i).getOrElse(int(default)), int(default))), "STRING"), int(width), str("0")))
    val full = Call("concat", Seq(part(0, 1, 4), str("-"), part(1, 1, 2), str("-"), part(2, 1, 2),
      str("T"), part(3, 0, 2), str(":"), part(4, 0, 2), str(":"), part(5, 0, 2), str("."), part(6, 0, 3)))
    val rankRaw = (1 until cols.size).foldRight(int(cols.size - 1): SqlExpr) { (i, acc) => ifc(cols(i).isNull, int(i - 1), acc) }
    val rank = if (isDateTime) rankRaw else Call("least", Seq(rankRaw, int(2)))
    val yearNull = cols.headOption.map(_.isNull).getOrElse(bool(false))
    ifc(yearNull, Cast(Null, "STRING"), Call("substr", Seq(full, int(1), lenOfRankCol(rank))))
  }

  private def timeStruct(parts: Seq[Expr], go: Expr => SqlExpr): SqlExpr = {
    val cols = parts.map(go)
    def part(i: Int, default: Int, width: Int): SqlExpr =
      Call("lpad", Seq(Cast(Call("coalesce", Seq(cols.lift(i).getOrElse(int(default)), int(default))), "STRING"), int(width), str("0")))
    val full = Call("concat", Seq(str("1970-01-01T"), part(0, 0, 2), str(":"), part(1, 0, 2), str(":"), part(2, 0, 2), str("."), part(3, 0, 3)))
    val rank = math.min(parts.size + 2, CqlType.DateTimePrecision.Millisecond.rank)
    Call("substr", Seq(full, int(1), int(lenOfRank(rank))))
  }

  private def castScalar(col: SqlExpr, cqlType: String): SqlExpr = {
    val ty = cqlType.substring(cqlType.lastIndexOf('}') + 1)
    ty match {
      case "Boolean"  => col.cast("boolean")
      case "Integer"  => col.cast("int")
      case "Long"     => col.cast("long")
      case "Decimal"  => col.cast("decimal(28,8)")
      case "String"   => col.cast("string")
      case "Quantity" => Struct(Seq("value" -> col.cast("decimal(28,8)"), "unit" -> str("1")))
      case _          => col
    }
  }

  private def literal(l: Literal): SqlExpr = l.value match {
    case None => Null
    case Some(raw) =>
      val ty = l.valueType.substring(l.valueType.lastIndexOf('}') + 1)
      ty match {
        case "Boolean" => bool(raw.toBoolean)
        case "Integer" =>
          raw.toIntOption.map(i => int(i)).getOrElse(
            raw.toLongOption.map(n => int(n)).getOrElse(
              throw UnsupportedNode("Literal[Integer]", s"integer literal '$raw' exceeds the 64-bit range")))
        case "Long"    => int(raw.toLong)
        case "Decimal" => decimal(BigDecimal(raw))
        case "String"  => str(raw)
        case _ => throw UnsupportedNode(s"Literal[$ty]", s"scalar literal type not yet lowered: ${l.valueType}")
      }
  }

  private def precRank(name: String): Int =
    CqlType.DateTimePrecision.ordered.find(_.toString == name).map(_.rank).getOrElse(
      throw UnsupportedNode("SameAs", s"unsupported temporal precision '$name'"))

  private def sparkType(t: CqlType): String = t match {
    case CqlType.IntegerType => "int"
    case CqlType.LongType    => "long"
    case CqlType.DecimalType => "decimal(28,8)"
    case CqlType.BooleanType => "boolean"
    case CqlType.StringType  => "string"
    case _: CqlType.DateType | _: CqlType.DateTimeType | _: CqlType.TimeType => "string"
    case _: CqlType.QuantityType => "struct<value:decimal(28,8),unit:string>"
    case CqlType.IntervalType(p) => s"struct<low:${sparkType(p)},high:${sparkType(p)}>"
    case _                   => "string"
  }
}
