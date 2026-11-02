package com.databricks.cql.load

import com.databricks.cql.error.ElmLoadError
import com.databricks.cql.ir._

/** Load (ELM JSON -> sealed-ADT IR) and its inverse Emit (IR -> ELM JSON).
  *
  * Load is the engine's first pass (ARCH-PIPELINE): lift ELM JSON straight into the IR
  * so every later pass enjoys pattern matching and exhaustiveness (ARCH-IR). Load is
  * TOTAL — any expression `type` we do not model is lifted into an explicit `UnknownExpr`
  * carrying the verbatim ELM JSON, never silently dropped (CORE-NO-SILENT-MISLOWER).
  *
  * Emit is the inverse used only for the ELM->IR->ELM round-trip:
  * its only consumer is our own Load, so it need not satisfy the JS reference. Together,
  * `Load(Emit(Load(elm))) == Load(elm)` is a fixed point — the round-trip invariant.
  *
  * NOTE on fidelity: Load deliberately drops non-semantic ELM annotations (`localId`,
  * `locator`, `annotation`, `resultType*`). Those are re-derivable (types are recomputed
  * in the Type/Encoding pass) and carry no kernel meaning, so dropping them keeps the IR
  * clean while preserving every semantic field.
  */
object ElmCodec {

  // ---- small ujson accessors -------------------------------------------------

  private def field(o: ujson.Value, k: String): Option[ujson.Value] =
    o.objOpt.flatMap(_.get(k))
  private def str(o: ujson.Value, k: String): Option[String] =
    field(o, k).flatMap(_.strOpt)
  private def boolField(o: ujson.Value, k: String, default: Boolean): Boolean =
    field(o, k).flatMap(_.boolOpt).getOrElse(default)
  private def arrField(o: ujson.Value, k: String): Seq[ujson.Value] =
    field(o, k).flatMap(_.arrOpt).map(_.toSeq).getOrElse(Nil)
  private def defs(o: ujson.Value, k: String): Seq[ujson.Value] =
    field(o, k).flatMap(ds => field(ds, "def")).flatMap(_.arrOpt).map(_.toSeq).getOrElse(Nil)

  /** Render a type specifier object to the IR's type-name string. A `NamedTypeSpecifier` is its
    * `name`; a composite `Interval`/`List` specifier renders to `Interval<…>`/`List<…>` (the form
    * `CqlType.parse` reads back) — the translator carries the point/element type ONLY in the nested
    * specifier (no `name`), so without this an `as/is Interval<DateTime>` would load untyped. A
    * `ChoiceTypeSpecifier` renders to "" (its members are carried separately, in `As.choiceTypes`). */
  private def typeSpecName(sp: ujson.Value): String = str(sp, "type") match {
    case Some("IntervalTypeSpecifier") =>
      "Interval<" + field(sp, "pointType").map(typeSpecName).getOrElse("") + ">"
    case Some("ListTypeSpecifier") =>
      "List<" + field(sp, "elementType").map(typeSpecName).getOrElse("") + ">"
    case _ => str(sp, "name").getOrElse("") // NamedTypeSpecifier (or a ChoiceTypeSpecifier → "")
  }

  // ============================ LOAD =========================================

  /** Lift a full ELM JSON document (`{ "library": { ... } }`) into a `Library` IR. */
  def load(elm: ujson.Value): Library = {
    val lib = field(elm, "library").getOrElse(
      throw ElmLoadError("ELM JSON has no top-level 'library' object"))
    loadLibrary(lib)
  }

  def loadString(json: String): Library =
    try load(ujson.read(json))
    catch { case e: ElmLoadError => throw e; case e: Throwable => throw ElmLoadError("malformed ELM JSON", e) }

  private def loadLibrary(lib: ujson.Value): Library = {
    val identifier = field(lib, "identifier")
    val id = identifier.flatMap(i => str(i, "id")).getOrElse("")
    val version = identifier.flatMap(i => str(i, "version"))

    Library(
      id = id,
      version = version,
      usings = defs(lib, "usings").map(loadUsing),
      includes = defs(lib, "includes").map(loadInclude),
      parameters = defs(lib, "parameters").map(loadParameter),
      codeSystems = defs(lib, "codeSystems").map(loadCodeSystem),
      valueSets = defs(lib, "valueSets").map(loadValueSet),
      codes = defs(lib, "codes").map(loadCode),
      concepts = defs(lib, "concepts").map(loadConcept),
      contexts = defs(lib, "contexts").map(d => ContextDef(str(d, "name").getOrElse(""))),
      statements = defs(lib, "statements").map(loadStatement)
    )
  }

  private def loadUsing(d: ujson.Value): UsingDef =
    UsingDef(str(d, "localIdentifier"), str(d, "uri").getOrElse(""), str(d, "version"))
  private def loadInclude(d: ujson.Value): IncludeDef =
    IncludeDef(str(d, "localIdentifier").getOrElse(""), str(d, "path").getOrElse(""), str(d, "version"))
  private def loadParameter(d: ujson.Value): ParameterDef = {
    val typeName = field(d, "parameterTypeSpecifier").flatMap(s => str(s, "name"))
      .orElse(str(d, "parameterType"))
    ParameterDef(str(d, "name").getOrElse(""), typeName, field(d, "default").map(loadExpr))
  }
  private def loadCodeSystem(d: ujson.Value): CodeSystemDef =
    CodeSystemDef(str(d, "name").getOrElse(""), str(d, "id").getOrElse(""), str(d, "version"))
  private def loadValueSet(d: ujson.Value): ValueSetDef =
    ValueSetDef(str(d, "name").getOrElse(""), str(d, "id").getOrElse(""), str(d, "version"))
  private def loadCode(d: ujson.Value): CodeDef =
    CodeDef(str(d, "name").getOrElse(""), str(d, "id").getOrElse(""),
      field(d, "codeSystem").flatMap(cs => str(cs, "name")))
  private def loadConcept(d: ujson.Value): ConceptDef =
    ConceptDef(str(d, "name").getOrElse(""),
      arrField(d, "code").flatMap(c => str(c, "name")), str(d, "display"))

  private def loadStatement(d: ujson.Value): Statement = {
    val t = str(d, "type")
    t match {
      case Some("FunctionDef") =>
        FunctionDef(
          name = str(d, "name").getOrElse(""),
          context = str(d, "context"),
          operands = arrField(d, "operand").map(o =>
            OperandDef(str(o, "name").getOrElse(""),
              field(o, "operandTypeSpecifier").flatMap(s => str(s, "name")))),
          body = field(d, "expression").map(loadExpr).getOrElse(NullExpr()),
          fluent = boolField(d, "fluent", default = false),
          external = boolField(d, "external", default = false))
      case Some("ExpressionDef") | None if field(d, "expression").isDefined =>
        ExpressionDef(str(d, "name").getOrElse(""), str(d, "context"),
          field(d, "expression").map(loadExpr).getOrElse(NullExpr()))
      case other =>
        UnknownStatement(other.getOrElse("UnknownStatement"), str(d, "name"), ujson.write(d))
    }
  }

  // ---- expressions ----------------------------------------------------------

  private val binaryBuilders: Map[String, (Expr, Expr) => Expr] = Map(
    "And" -> (And(_, _)), "Or" -> (Or(_, _)), "Xor" -> (Xor(_, _)), "Implies" -> (Implies(_, _)),
    "Equal" -> (Equal(_, _)), "Equivalent" -> (Equivalent(_, _)), "NotEqual" -> (NotEqual(_, _)),
    "Less" -> (Less(_, _)), "Greater" -> (Greater(_, _)),
    "LessOrEqual" -> (LessOrEqual(_, _)), "GreaterOrEqual" -> (GreaterOrEqual(_, _)),
    "Add" -> (Add(_, _)), "Subtract" -> (Subtract(_, _)), "Multiply" -> (Multiply(_, _)),
    "Divide" -> (Divide(_, _)), "TruncatedDivide" -> (TruncatedDivide(_, _)),
    "Modulo" -> (Modulo(_, _)), "Power" -> (Power(_, _)), "Log" -> (Log(_, _)),
    "Indexer" -> (Indexer(_, _)),
    // List set operators (LANG-LIST) — binary, `operand` array of two lists.
    "Union" -> (Union(_, _)), "Intersect" -> (Intersect(_, _)), "Except" -> (Except(_, _)),
    // Interval (Allen-relation) operators (LANG-INTERVAL) — binary, `operand` array of two.
    // `Overlaps`/`IncludedIn` additionally carry an optional `precision` NAME and so are decoded
    // explicitly below (LANG-INTERVAL-PRECISION), not through this arity-only table.
    "Before" -> (Before(_, _)), "After" -> (After(_, _)),
    "Includes" -> (Includes(_, _)),
    "Starts" -> (Starts(_, _)), "Ends" -> (Ends(_, _)),
    // Inclusive point-comparison siblings of Before/After (LANG-INTERVAL); precision modifier, when
    // present, is dropped at lowering as Before/After already do.
    "SameOrBefore" -> (SameOrBefore(_, _)), "SameOrAfter" -> (SameOrAfter(_, _)),
    // Boundary operators (LANG-INTERVAL) — binary, `operand` array of [value, precision].
    "LowBoundary" -> (LowBoundary(_, _)), "HighBoundary" -> (HighBoundary(_, _))
  )

  // NB: ELM `First`/`Last` carry their list in a `source` field, not `operand`, so they
  // are handled explicitly below rather than via the generic unary `operand` path.
  private val unaryBuilders: Map[String, Expr => Expr] = Map(
    "Not" -> (Not(_)), "Exists" -> (Exists(_)), "IsNull" -> (IsNull(_)),
    "IsTrue" -> (IsTrue(_)), "IsFalse" -> (IsFalse(_)),
    "SingletonFrom" -> (SingletonFrom(_)), "ToList" -> (ToList(_)), "Negate" -> (Negate(_)),
    // `distinct X` — dedupe by EQUALITY (LANG-EQUIV); the query-clause `return distinct` is decoded
    // on `Query` instead.
    "Distinct" -> (Distinct(_)),
    // Unary numeric functions — single `operand`. Round is n-ary (optional
    // `precision`), so it is handled explicitly below, not here.
    "Abs" -> (Abs(_)), "Floor" -> (Floor(_)), "Ceiling" -> (Ceiling(_)), "Truncate" -> (Truncate(_)),
    "Predecessor" -> (Predecessor(_)), "Successor" -> (Successor(_)), "Ln" -> (Ln(_)), "Exp" -> (Exp(_)),
    "Precision" -> (Precision(_)),
    // Interval accessors (LANG-INTERVAL) — unary, `operand` carries the interval.
    "Start" -> (Start(_)), "End" -> (End(_)), "Width" -> (Width(_)),
    // Temporal conversions (LANG-INTERVAL) — unary, single `operand`.
    "DateFrom" -> (DateFrom(_)), "ToDateTime" -> (ToDateTimeExpr(_)),
    // Code -> Concept promotion; resolved to a literal ConceptValue at Normalize (LANG-TERMINOLOGY).
    "ToConcept" -> (ToConcept(_))
  )

  // Implicit conversion operators the translator inserts; all unary, all lower to a cast.
  // Mapped onto the single `Convert` IR node carrying the target CQL system type.
  private val conversionOps: Map[String, String] = Map(
    "ToBoolean" -> "Boolean", "ToInteger" -> "Integer", "ToLong" -> "Long",
    "ToDecimal" -> "Decimal", "ToString" -> "String", "ToQuantity" -> "Quantity"
  )

  def loadExpr(v: ujson.Value): Expr = {
    val t = str(v, "type").getOrElse(
      throw ElmLoadError(s"expression node missing 'type': ${ujson.write(v)}"))

    def binary: Expr = {
      val ops = arrField(v, "operand")
      if (ops.size != 2) throw ElmLoadError(s"binary '$t' expects 2 operands, got ${ops.size}")
      binaryBuilders(t)(loadExpr(ops.head), loadExpr(ops(1)))
    }
    def unary: Expr =
      unaryBuilders(t)(field(v, "operand").map(loadExpr).getOrElse(
        throw ElmLoadError(s"unary '$t' missing 'operand'")))

    t match {
      case _ if binaryBuilders.contains(t) => binary
      case _ if unaryBuilders.contains(t)  => unary

      // minimum/maximum <T> carry the target type in `valueType`, no operand.
      case "MinValue" =>
        MinValue(str(v, "valueType").getOrElse(throw ElmLoadError("MinValue missing 'valueType'")))
      case "MaxValue" =>
        MaxValue(str(v, "valueType").getOrElse(throw ElmLoadError("MaxValue missing 'valueType'")))

      // Round: `operand` plus an optional `precision` (decimal places).
      case "Round" =>
        Round(
          field(v, "operand").map(loadExpr).getOrElse(throw ElmLoadError("Round missing 'operand'")),
          field(v, "precision").map(loadExpr))

      // SameAs: two `operand`s plus an optional `precision` NAME (Year…Millisecond), distinct from
      // Round's numeric precision. Precision-aware temporal equality (LANG-INTERVAL).
      case "SameAs" =>
        val ops = arrField(v, "operand")
        if (ops.size != 2) throw ElmLoadError(s"binary 'SameAs' expects 2 operands, got ${ops.size}")
        SameAs(loadExpr(ops.head), loadExpr(ops(1)), str(v, "precision"))

      // Overlaps / IncludedIn: two `operand`s plus an optional `precision` NAME — `X during day of Y`
      // (LANG-INTERVAL-PRECISION). The name is a property of the COMPARISON: lowering truncates BOTH
      // operands to it, so operands at different precisions cannot tie into an UNCERTAIN null. Dropping
      // it here (as the arity-only table above would) silently reintroduces that defect.
      case t @ ("Overlaps" | "IncludedIn") =>
        val ops = arrField(v, "operand")
        if (ops.size != 2) throw ElmLoadError(s"binary '$t' expects 2 operands, got ${ops.size}")
        val (l, r, p) = (loadExpr(ops.head), loadExpr(ops(1)), str(v, "precision"))
        if (t == "Overlaps") Overlaps(l, r, p) else IncludedIn(l, r, p)

      // In: two `operand`s plus an optional `precision` NAME. `point during day of X` translates to
      // `In(point, X, Day)` (LANG-INTERVAL-PRECISION), and dropping the name (as the arity-only table above
      // would) compares at full precision, so a day-precision point on the interval's first day reads as
      // uncertain. A point in an interval at a precision is that point's unit interval included in it at the
      // precision (CQL spec, Interval Operators: In), so it loads as `IncludedIn`, with a null point kept null.
      // `In` with a precision over a list is not modeled: fail loudly rather than drop the precision.
      case "In" =>
        val ops = arrField(v, "operand")
        if (ops.size != 2) throw ElmLoadError(s"binary 'In' expects 2 operands, got ${ops.size}")
        val (l, r) = (loadExpr(ops.head), loadExpr(ops(1)))
        str(v, "precision") match {
          case None => In(l, r)
          case Some(p) if field(ops(1), "resultTypeSpecifier").flatMap(t => str(t, "type")).contains("IntervalTypeSpecifier") =>
            If(IsNull(l), NullExpr(), IncludedIn(IntervalExpr(Some(l), Some(l), lowClosed = true, highClosed = true), r, Some(p)))
          case Some(p) =>
            throw ElmLoadError(s"'In' with precision '$p' over a non-interval operand is not modeled")
        }

      // CalculateAgeAt: two `operand`s (birthDate, asOf) plus a calendar `precision` NAME. LANG-INTERVAL.
      case "CalculateAgeAt" =>
        val ops = arrField(v, "operand")
        if (ops.size != 2) throw ElmLoadError(s"'CalculateAgeAt' expects 2 operands, got ${ops.size}")
        CalculateAgeAt(loadExpr(ops.head), loadExpr(ops(1)), str(v, "precision"))

      // Message: returns its `source`; condition/code/severity/message are logging-only (dropped).
      case "Message" =>
        Message(field(v, "source").map(loadExpr).getOrElse(throw ElmLoadError("Message missing 'source'")))

      case "First" =>
        First(field(v, "source").map(loadExpr).getOrElse(throw ElmLoadError("First missing 'source'")))
      case "Last" =>
        Last(field(v, "source").map(loadExpr).getOrElse(throw ElmLoadError("Last missing 'source'")))
      case "Count" =>
        Count(field(v, "source").map(loadExpr).getOrElse(throw ElmLoadError("Count missing 'source'")))
      // LANG-AGGREGATE-FUNCTIONS: the list is in `source`. An ELM `path` (aggregate a property of each element)
      // is never emitted for CQL source, so it is refused rather than dropped.
      case _ if AggregateOp.byElmType.contains(t) =>
        if (field(v, "path").isDefined) throw ElmLoadError(s"'$t' with a 'path' is not modeled")
        AggregateFunction(AggregateOp.byElmType(t),
          field(v, "source").map(loadExpr).getOrElse(throw ElmLoadError(s"$t missing 'source'")))
      case "Split" =>
        Split(field(v, "stringToSplit").map(loadExpr).getOrElse(throw ElmLoadError("Split missing 'stringToSplit'")),
              field(v, "separator").map(loadExpr).getOrElse(throw ElmLoadError("Split missing 'separator'")))

      case "Null"      => NullExpr()
      case "Literal"   => Literal(str(v, "valueType").getOrElse(""), str(v, "value"))
      case "Quantity"  =>
        val value = field(v, "value").flatMap(_.numOpt).map(BigDecimal(_)).getOrElse(BigDecimal(0))
        QuantityExpr(value, str(v, "unit"))
      // Date/DateTime components are named fields in significance order; precision is their count.
      case "Date" =>
        DateExpr(Seq("year", "month", "day").flatMap(k => field(v, k).map(loadExpr)))
      case "DateTime" =>
        DateTimeExpr(Seq("year", "month", "day", "hour", "minute", "second", "millisecond")
          .flatMap(k => field(v, k).map(loadExpr)))
      case "Time" =>
        TimeExpr(Seq("hour", "minute", "second", "millisecond").flatMap(k => field(v, k).map(loadExpr)))
      // ELM `Ratio` carries `numerator`/`denominator` as bare quantity objects ({value, unit}) with
      // NO `type` discriminator, so they are read directly as quantities (not via loadExpr).
      case "Ratio" =>
        def quantityOf(k: String): Expr = {
          val q = field(v, k).getOrElse(throw ElmLoadError(s"Ratio missing '$k'"))
          val value = q.objOpt.flatMap(_.get("value")).flatMap(_.numOpt).map(BigDecimal(_)).getOrElse(BigDecimal(0))
          QuantityExpr(value, q.objOpt.flatMap(_.get("unit")).flatMap(_.strOpt))
        }
        RatioExpr(quantityOf("numerator"), quantityOf("denominator"))

      case "ExpressionRef" => ExpressionRef(str(v, "name").getOrElse(""), str(v, "libraryName"))
      case "ParameterRef"  => ParameterRef(str(v, "name").getOrElse(""), str(v, "libraryName"))
      case "OperandRef"    => OperandRef(str(v, "name").getOrElse(""))
      case "AliasRef"      => AliasRef(str(v, "name").getOrElse(""))
      case "QueryLetRef"   => QueryLetRef(str(v, "name").getOrElse(""))
      case "ValueSetRef"   => ValueSetRef(str(v, "name").getOrElse(""), str(v, "libraryName"))
      case "CodeSystemRef" => CodeSystemRef(str(v, "name").getOrElse(""), str(v, "libraryName"))
      case "CodeRef"       => CodeRef(str(v, "name").getOrElse(""), str(v, "libraryName"))
      case "ConceptRef"    => ConceptRef(str(v, "name").getOrElse(""), str(v, "libraryName"))

      case "List"     => ListExpr(arrField(v, "element").map(loadExpr))
      case "Coalesce" => Coalesce(arrField(v, "operand").map(loadExpr))
      case "Interval" =>
        val iv = IntervalExpr(
          low = field(v, "low").map(loadExpr),
          high = field(v, "high").map(loadExpr),
          lowClosed = boolField(v, "lowClosed", default = true),
          highClosed = boolField(v, "highClosed", default = true))
        // CQL Interval selector with DYNAMIC closedness (`lowClosedExpression`/`highClosedExpression`
        // instead of the static `lowClosed`/`highClosed`): the CQL→ELM translator emits this form to
        // PROMOTE an interval (e.g. `Interval<Date>` → `Interval<DateTime>` for a mixed-precision
        // `overlaps`) — `low`/`high`/closedness all read the SOURCE interval `X` via `Property`
        // (`ToDateTime(X.low)`, `X.lowClosed`, …). A promotion of a NULL source must stay NULL: otherwise a
        // null source promotes to `Interval[null, null]`, which the Allen algebra reads as fully UNBOUNDED
        // (`[-inf, +inf]`) so `overlaps`/`includes` wrongly fire (a medication-request period
        // `overlaps …` over a dosage-less MedicationRequest — LANG-INTERVAL). Guard the interval on the
        // SOURCE being null (`X.lowClosed`'s `source` is `X`); our interval representation carries no
        // `lowClosed`/`highClosed` field, so we must NOT read the closedness itself — only detect the null
        // source. The non-null branch keeps the static closed flags (promotions are of CLOSED `[,]`
        // intervals; the IR carries no dynamic closedness).
        val closedSrcs = Seq("lowClosedExpression", "highClosedExpression").flatMap(k => field(v, k)).map(loadExpr)
          .collect { case Property(Some(src), _, p) if p == "lowClosed" || p == "highClosed" => src }
        if (closedSrcs.isEmpty) iv
        else If(closedSrcs.distinct.map(s => IsNull(s): Expr).reduce(Or(_, _)), NullExpr(), iv)

      case "Query" =>
        val ret = field(v, "return")
        Query(
          sources = arrField(v, "source").map(s =>
            QuerySource(str(s, "alias").getOrElse(""),
              field(s, "expression").map(loadExpr).getOrElse(NullExpr()))),
          where = field(v, "where").map(loadExpr),
          returnExpr = ret.flatMap(r => field(r, "expression")).map(loadExpr),
          // CQL `return` defaults to DISTINCT (ELM ReturnClause.distinct defaults true when omitted);
          // `return all` sets it false. (Verified by the property gate — a plain return dedups.)
          returnDistinct = ret.exists(r => boolField(r, "distinct", default = true)),
          // sort `by` items: ByDirection (element), ByColumn (a `path` field), ByExpression (a key).
          sort = field(v, "sort").map(s => arrField(s, "by").map(b =>
            SortItem(str(b, "direction").getOrElse("asc"),
              field(b, "expression").map(loadExpr), str(b, "path")))).getOrElse(Nil),
          aggregate = field(v, "aggregate").map(a => AggregateClause(
            identifier = str(a, "identifier").getOrElse("$this"),
            starting = field(a, "starting").map(loadExpr).getOrElse(NullExpr()),
            body = field(a, "expression").map(loadExpr).getOrElse(NullExpr()),
            distinct = boolField(a, "distinct", default = false))),
          // relationship clauses: With (semi-join) / Without (anti-join), with a `suchThat` predicate.
          relationships = arrField(v, "relationship").map(r => RelationshipClause(
            alias = str(r, "alias").getOrElse(""),
            expr = field(r, "expression").map(loadExpr).getOrElse(NullExpr()),
            suchThat = field(r, "suchThat").map(loadExpr).getOrElse(NullExpr()),
            negated = str(r, "type").contains("Without"))),
          // `let` bindings (LANG-QUERY-LET): query-iteration-scoped named values, in dependency order.
          lets = arrField(v, "let").map(l => LetClause(
            identifier = str(l, "identifier").getOrElse(""),
            expr = field(l, "expression").map(loadExpr).getOrElse(NullExpr()))))

      case "If" =>
        If(field(v, "condition").map(loadExpr).getOrElse(NullExpr()),
          field(v, "then").map(loadExpr).getOrElse(NullExpr()),
          field(v, "else").map(loadExpr).getOrElse(NullExpr()))
      case "Case" =>
        Case(field(v, "comparand").map(loadExpr),
          arrField(v, "caseItem").map(ci =>
            CaseItem(field(ci, "when").map(loadExpr).getOrElse(NullExpr()),
              field(ci, "then").map(loadExpr).getOrElse(NullExpr()))),
          field(v, "else").map(loadExpr).getOrElse(NullExpr()))

      case "FunctionRef" =>
        FunctionRef(str(v, "name").getOrElse(""), str(v, "libraryName"),
          arrField(v, "operand").map(loadExpr))

      case "As" =>
        val spec = field(v, "asTypeSpecifier")
        // A choice-constructing cast carries its member types in a `ChoiceTypeSpecifier` — either
        // directly (scalar choice) or as the `elementType` of a `ListTypeSpecifier` (List<Choice>,
        // e.g. `union` of differently-typed lists). The list-vs-scalar case is recovered at
        // typing/lowering from the operand's own type, so only the member names are kept here.
        // Each choice member is rendered by `typeSpecName`, so Interval/List members (which carry no
        // `name`) are captured as `Interval<…>`/`List<…>` rather than dropped — the choice the FHIR
        // value[x] dispatch constructs (`Choice<DateTime, Quantity, Interval<DateTime>, …>`) needs
        // every member to tag/slot correctly.
        def members(sp: ujson.Value): Seq[String] = arrField(sp, "choice").map(typeSpecName)
        val choiceTypes = spec match {
          case Some(sp) if str(sp, "type").contains("ChoiceTypeSpecifier") => members(sp)
          case Some(sp) if str(sp, "type").contains("ListTypeSpecifier") =>
            field(sp, "elementType").filter(et => str(et, "type").contains("ChoiceTypeSpecifier"))
              .map(members).getOrElse(Nil)
          case _ => Nil
        }
        // A choice-CONSTRUCTING cast (choiceTypes non-empty) carries its semantics ENTIRELY in
        // `choiceTypes`; downstream always matches `As(_, _, _, cts) if cts.nonEmpty` and never reads
        // `asType` (LANG-CHOICE). So leave `asType` empty here rather than the degenerate name
        // `typeSpecName` yields for a `List<Choice>` specifier ("List<>", inner choice → ""). This also
        // restores the round-trip fixed point: Emit drops `asType` when choiceTypes is non-empty, so a
        // non-empty `asType` on such a node would reload as "" (which would make ELM->IR->ELM->IR unstable).
        val asType =
          if (choiceTypes.nonEmpty) ""
          else str(v, "asType").orElse(spec.map(typeSpecName).filter(_.nonEmpty)).getOrElse("")
        As(field(v, "operand").map(loadExpr).getOrElse(NullExpr()),
          asType, boolField(v, "strict", default = false), choiceTypes)
      case "Is" =>
        Is(field(v, "operand").map(loadExpr).getOrElse(NullExpr()),
          str(v, "isType").orElse(field(v, "isTypeSpecifier").map(typeSpecName).filter(_.nonEmpty)).getOrElse(""))

      case "Convert" =>
        Convert(field(v, "operand").map(loadExpr).getOrElse(NullExpr()),
          str(v, "toType").orElse(field(v, "toTypeSpecifier").flatMap(s => str(s, "name"))).getOrElse(""))
      case t2 if conversionOps.contains(t2) =>
        Convert(field(v, "operand").map(loadExpr).getOrElse(NullExpr()), conversionOps(t2))

      case "ConvertQuantity" =>
        // operand[0] = the quantity; operand[1] = a String Literal naming the target UCUM unit.
        val ops = arrField(v, "operand")
        ConvertQuantity(ops.headOption.map(loadExpr).getOrElse(NullExpr()),
          ops.lift(1).flatMap(u => str(u, "value")).getOrElse(""))

      case "Tuple" =>
        TupleExpr(arrField(v, "element").map(el =>
          TupleElement(str(el, "name").getOrElse(""),
            field(el, "value").map(loadExpr).getOrElse(NullExpr()))))

      case "Instance" =>
        InstanceExpr(str(v, "classType").getOrElse(""), arrField(v, "element").map(el =>
          TupleElement(str(el, "name").getOrElse(""),
            field(el, "value").map(loadExpr).getOrElse(NullExpr()))))

      case "IdentifierRef" => IdentifierRef(str(v, "name").getOrElse(""))

      case "Property" =>
        Property(field(v, "source").map(loadExpr), str(v, "scope"), str(v, "path").getOrElse(""))

      case "InValueSet" =>
        // The value set is a `valuesetExpression` (e.g. an OperandRef when a value set is passed as
        // a function argument — LANG-FUNCTION-TERMINOLOGY) OR a `valueset` reference. In a
        // `where`-position membership the `valueset` is an UNTYPED reference object
        // `{name, libraryName?}` (no ELM `type` tag, unlike a Retrieve's `codes` ValueSetRef), so
        // load it as a `ValueSetRef` by name rather than as a typed expression.
        val vs = field(v, "valuesetExpression").map(loadExpr)
          .orElse(field(v, "valueset").map {
            case s: ujson.Str                  => ValueSetRef(s.value, None)
            case o if str(o, "type").isDefined => loadExpr(o) // a TYPED expr (OperandRef, ValueSetRef, …)
            case o                             => ValueSetRef(str(o, "name").getOrElse(""), str(o, "libraryName"))
          })
          .getOrElse(ValueSetRef("", None))
        InValueSet(field(v, "code").map(loadExpr).getOrElse(NullExpr()), vs)

      case "AnyInValueSet" =>
        // List-valued value-set membership: the QI-Core `<listProp> in "VS"` shape over a
        // `List<CodeableConcept>` (e.g. `HighInterventionsOrdered.reasonCode in "Overweight or
        // Obese"`). The translator wraps the list property in a `Query{ source: <listProp>,
        // return: FHIRHelpers.ToConcept($this) }` and tests whether ANY element is in the set.
        // Desugar to a list-valued [[InValueSet]] over the underlying property: the retrieve
        // membership lowering already explodes a list-valued code path (`FhirModel.isListElement`),
        // reading `.coding` off each CodeableConcept, so "any coding in the set" holds identically —
        // reusing the whole terminology-pushdown path (LANG-TERMINOLOGY). Only the recognized
        // single-source, no-`where` codes shape is supported; any other is rejected loudly
        // (UnknownExpr → Lower-time failure, CORE-NO-SILENT-MISLOWER).
        val vs = field(v, "valuesetExpression").map(loadExpr)
          .orElse(field(v, "valueset").map {
            case s: ujson.Str                  => ValueSetRef(s.value, None)
            case o if str(o, "type").isDefined => loadExpr(o)
            case o                             => ValueSetRef(str(o, "name").getOrElse(""), str(o, "libraryName"))
          })
          .getOrElse(ValueSetRef("", None))
        val codes = field(v, "codes").getOrElse(ujson.Null)
        val srcs  = arrField(codes, "source")
        if (str(codes, "type").contains("Query") && srcs.size == 1 && field(codes, "where").isEmpty)
          InValueSet(field(srcs.head, "expression").map(loadExpr).getOrElse(NullExpr()), vs)
        else UnknownExpr("AnyInValueSet", ujson.write(v))

      case "Retrieve" =>
        // `datePointConverter` is a pushdown artifact (set by Optimize), never present in source ELM.
        Retrieve(str(v, "dataType").getOrElse(""), str(v, "templateId"),
          str(v, "codeProperty"), field(v, "codes").map(loadExpr),
          str(v, "dateProperty"), field(v, "dateRange").map(loadExpr))

      // Not yet modeled — preserved verbatim, rejected loudly at Lower-time.
      case other => UnknownExpr(other, ujson.write(v))
    }
  }

  // ============================ EMIT =========================================

  /** Emit a `Library` IR back to an ELM JSON document. Exhaustive over the sealed IR;
    * the compiler enforces totality (ARCH-ROBUSTNESS pillar 1). */
  def emit(lib: Library): ujson.Value = ujson.Obj("library" -> emitLibrary(lib))

  def emitString(lib: Library): String = ujson.write(emit(lib), indent = 2)

  private def defObj(items: Seq[ujson.Value]): ujson.Value = ujson.Obj("def" -> ujson.Arr(items: _*))

  private def emitLibrary(lib: Library): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap.empty[String, ujson.Value]
    val ident = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("id" -> ujson.Str(lib.id))
    lib.version.foreach(v => ident("version") = ujson.Str(v))
    o("identifier") = ujson.Obj.from(ident)
    if (lib.usings.nonEmpty)      o("usings")      = defObj(lib.usings.map(emitUsing))
    if (lib.includes.nonEmpty)    o("includes")    = defObj(lib.includes.map(emitInclude))
    if (lib.parameters.nonEmpty)  o("parameters")  = defObj(lib.parameters.map(emitParameter))
    if (lib.codeSystems.nonEmpty) o("codeSystems") = defObj(lib.codeSystems.map(emitCodeSystem))
    if (lib.valueSets.nonEmpty)   o("valueSets")   = defObj(lib.valueSets.map(emitValueSet))
    if (lib.codes.nonEmpty)       o("codes")       = defObj(lib.codes.map(emitCode))
    if (lib.concepts.nonEmpty)    o("concepts")    = defObj(lib.concepts.map(emitConcept))
    if (lib.contexts.nonEmpty)    o("contexts")    = defObj(lib.contexts.map(c => ujson.Obj("name" -> c.name)))
    if (lib.statements.nonEmpty)  o("statements")  = defObj(lib.statements.map(emitStatement))
    ujson.Obj.from(o)
  }

  private def optStr(o: scala.collection.mutable.LinkedHashMap[String, ujson.Value], k: String, v: Option[String]): Unit =
    v.foreach(s => o(k) = ujson.Str(s))

  private def emitUsing(u: UsingDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("uri" -> ujson.Str(u.uri))
    optStr(o, "localIdentifier", u.localIdentifier); optStr(o, "version", u.version)
    ujson.Obj.from(o)
  }
  private def emitInclude(i: IncludeDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
      "localIdentifier" -> ujson.Str(i.localIdentifier), "path" -> ujson.Str(i.path))
    optStr(o, "version", i.version); ujson.Obj.from(o)
  }
  private def emitParameter(p: ParameterDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("name" -> ujson.Str(p.name))
    p.parameterTypeName.foreach(t =>
      o("parameterTypeSpecifier") = ujson.Obj("type" -> ujson.Str("NamedTypeSpecifier"), "name" -> ujson.Str(t)))
    p.default.foreach(d => o("default") = emitExpr(d))
    ujson.Obj.from(o)
  }
  private def emitCodeSystem(c: CodeSystemDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
      "name" -> ujson.Str(c.name), "id" -> ujson.Str(c.id))
    optStr(o, "version", c.version); ujson.Obj.from(o)
  }
  private def emitValueSet(c: ValueSetDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
      "name" -> ujson.Str(c.name), "id" -> ujson.Str(c.id))
    optStr(o, "version", c.version); ujson.Obj.from(o)
  }
  private def emitCode(c: CodeDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
      "name" -> ujson.Str(c.name), "id" -> ujson.Str(c.id))
    c.codeSystem.foreach(cs => o("codeSystem") = ujson.Obj("name" -> ujson.Str(cs)))
    ujson.Obj.from(o)
  }
  private def emitConcept(c: ConceptDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("name" -> ujson.Str(c.name))
    if (c.codeNames.nonEmpty) o("code") = ujson.Arr(c.codeNames.map(n => ujson.Obj("name" -> ujson.Str(n)): ujson.Value): _*)
    optStr(o, "display", c.display); ujson.Obj.from(o)
  }

  private def emitStatement(s: Statement): ujson.Value = s match {
    case e: ExpressionDef =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("ExpressionDef"), "name" -> ujson.Str(e.name))
      optStr(o, "context", e.context)
      o("expression") = emitExpr(e.expression)
      ujson.Obj.from(o)
    case f: FunctionDef =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("FunctionDef"), "name" -> ujson.Str(f.name))
      optStr(o, "context", f.context)
      if (f.fluent) o("fluent") = ujson.Bool(true)
      if (f.external) o("external") = ujson.Bool(true)
      if (f.operands.nonEmpty) o("operand") = ujson.Arr(f.operands.map(emitOperand): _*)
      o("expression") = emitExpr(f.body)
      ujson.Obj.from(o)
    case u: UnknownStatement => ujson.read(u.rawJson)
  }

  private def emitOperand(op: OperandDef): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("name" -> ujson.Str(op.name))
    op.operandTypeName.foreach(t =>
      o("operandTypeSpecifier") = ujson.Obj("type" -> ujson.Str("NamedTypeSpecifier"), "name" -> ujson.Str(t)))
    ujson.Obj.from(o)
  }

  private def named(t: String, name: String, lib: Option[String]): ujson.Value = {
    val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
      "type" -> ujson.Str(t), "name" -> ujson.Str(name))
    lib.foreach(l => o("libraryName") = ujson.Str(l))
    ujson.Obj.from(o)
  }

  def emitExpr(e: Expr): ujson.Value = e match {
    // Specific unary nodes with extra fields MUST precede the generic unary case.
    case As(op, asType, strict, choiceTypes) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("As"), "operand" -> emitExpr(op))
      if (choiceTypes.nonEmpty)
        o("asTypeSpecifier") = ujson.Obj("type" -> ujson.Str("ChoiceTypeSpecifier"),
          "choice" -> ujson.Arr(choiceTypes.map(t =>
            ujson.Obj("type" -> ujson.Str("NamedTypeSpecifier"), "name" -> ujson.Str(t)): ujson.Value): _*))
      else o("asType") = ujson.Str(asType)
      if (strict) o("strict") = ujson.Bool(true)
      ujson.Obj.from(o)
    case Is(op, isType) =>
      ujson.Obj("type" -> ujson.Str("Is"), "operand" -> emitExpr(op), "isType" -> ujson.Str(isType))
    case Convert(op, toType) =>
      ujson.Obj("type" -> ujson.Str("Convert"), "operand" -> emitExpr(op), "toType" -> ujson.Str(toType))
    case ConvertQuantity(op, toUnit) =>
      ujson.Obj("type" -> ujson.Str("ConvertQuantity"), "operand" -> ujson.Arr(emitExpr(op),
        ujson.Obj("type" -> ujson.Str("Literal"),
          "valueType" -> ujson.Str("{urn:hl7-org:elm-types:r1}String"), "value" -> ujson.Str(toUnit))))

    // First/Last/Count and the aggregate functions carry their list in `source`, not `operand`.
    case First(source) => ujson.Obj("type" -> ujson.Str("First"), "source" -> emitExpr(source))
    case Last(source)  => ujson.Obj("type" -> ujson.Str("Last"), "source" -> emitExpr(source))
    case Count(source) => ujson.Obj("type" -> ujson.Str("Count"), "source" -> emitExpr(source))
    case AggregateFunction(op, source) => ujson.Obj("type" -> ujson.Str(op.elmType), "source" -> emitExpr(source))

    // SameAs carries an optional `precision` name; emit it before the generic binary case.
    case SameAs(left, right, precision) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("SameAs"), "operand" -> ujson.Arr(emitExpr(left), emitExpr(right)))
      precision.foreach(p => o("precision") = ujson.Str(p))
      ujson.Obj.from(o)

    // Overlaps/IncludedIn mirror SameAs (two operands + an optional precision name) —
    // LANG-INTERVAL-PRECISION. Emit before the generic binary case so the name survives a round trip;
    // an emit that dropped it would make `during day of` and bare `during` indistinguishable on reload.
    case Overlaps(left, right, precision) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("Overlaps"), "operand" -> ujson.Arr(emitExpr(left), emitExpr(right)))
      precision.foreach(p => o("precision") = ujson.Str(p))
      ujson.Obj.from(o)
    case IncludedIn(left, right, precision) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("IncludedIn"), "operand" -> ujson.Arr(emitExpr(left), emitExpr(right)))
      precision.foreach(p => o("precision") = ujson.Str(p))
      ujson.Obj.from(o)

    // CalculateAgeAt mirrors SameAs (two operands + a precision name); emit before generic binary.
    case CalculateAgeAt(left, right, precision) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("CalculateAgeAt"), "operand" -> ujson.Arr(emitExpr(left), emitExpr(right)))
      precision.foreach(p => o("precision") = ujson.Str(p))
      ujson.Obj.from(o)

    // Message carries its value in `source` (not `operand`); emit before the generic unary case.
    case Message(source) =>
      ujson.Obj("type" -> ujson.Str("Message"), "source" -> emitExpr(source))

    // Split carries NAMED operands (`stringToSplit`/`separator`), not the positional `operand[]` the
    // generic binary case emits — so `loadExpr` reads them back symmetrically. Emit before generic binary.
    case Split(stringToSplit, separator) =>
      ujson.Obj("type" -> ujson.Str("Split"),
        "stringToSplit" -> emitExpr(stringToSplit), "separator" -> emitExpr(separator))

    case u: UnaryExpr  => ujson.Obj("type" -> ujson.Str(u.elmType), "operand" -> emitExpr(u.operand))
    case b: BinaryExpr =>
      ujson.Obj("type" -> ujson.Str(b.elmType),
        "operand" -> ujson.Arr(emitExpr(b.left), emitExpr(b.right)))

    case ListExpr(elements) =>
      ujson.Obj("type" -> ujson.Str("List"), "element" -> ujson.Arr(elements.map(emitExpr): _*))
    case Coalesce(operands) =>
      ujson.Obj("type" -> ujson.Str("Coalesce"), "operand" -> ujson.Arr(operands.map(emitExpr): _*))
    case Round(operand, precision) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("Round"), "operand" -> emitExpr(operand))
      precision.foreach(p => o("precision") = emitExpr(p))
      ujson.Obj.from(o)
    case MinValue(valueType) => ujson.Obj("type" -> ujson.Str("MinValue"), "valueType" -> ujson.Str(valueType))
    case MaxValue(valueType) => ujson.Obj("type" -> ujson.Str("MaxValue"), "valueType" -> ujson.Str(valueType))
    case FunctionRef(name, libraryName, operands) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("FunctionRef"), "name" -> ujson.Str(name))
      libraryName.foreach(l => o("libraryName") = ujson.Str(l))
      o("operand") = ujson.Arr(operands.map(emitExpr): _*)
      ujson.Obj.from(o)

    case NullExpr()              => ujson.Obj("type" -> ujson.Str("Null"))
    case Literal(valueType, value) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("Literal"), "valueType" -> ujson.Str(valueType))
      value.foreach(v => o("value") = ujson.Str(v))
      ujson.Obj.from(o)
    case QuantityExpr(value, unit) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("Quantity"), "value" -> ujson.Num(value.toDouble))
      unit.foreach(u => o("unit") = ujson.Str(u))
      ujson.Obj.from(o)
    case DateExpr(parts) =>
      val keys = Seq("year", "month", "day")
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("type" -> ujson.Str("Date"))
      parts.zip(keys).foreach { case (p, k) => o(k) = emitExpr(p) }
      ujson.Obj.from(o)
    case DateTimeExpr(parts) =>
      val keys = Seq("year", "month", "day", "hour", "minute", "second", "millisecond")
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("type" -> ujson.Str("DateTime"))
      parts.zip(keys).foreach { case (p, k) => o(k) = emitExpr(p) }
      ujson.Obj.from(o)
    case TimeExpr(parts) =>
      val keys = Seq("hour", "minute", "second", "millisecond")
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("type" -> ujson.Str("Time"))
      parts.zip(keys).foreach { case (p, k) => o(k) = emitExpr(p) }
      ujson.Obj.from(o)
    case RatioExpr(numerator, denominator) =>
      ujson.Obj("type" -> ujson.Str("Ratio"),
        "numerator" -> emitExpr(numerator), "denominator" -> emitExpr(denominator))

    case ExpressionRef(n, l) => named("ExpressionRef", n, l)
    case ParameterRef(n, l)  => named("ParameterRef", n, l)
    case OperandRef(n)       => ujson.Obj("type" -> ujson.Str("OperandRef"), "name" -> ujson.Str(n))
    case AliasRef(n)         => ujson.Obj("type" -> ujson.Str("AliasRef"), "name" -> ujson.Str(n))
    case QueryLetRef(n)      => ujson.Obj("type" -> ujson.Str("QueryLetRef"), "name" -> ujson.Str(n))
    case ValueSetRef(n, l)   => named("ValueSetRef", n, l)
    case CodeSystemRef(n, l) => named("CodeSystemRef", n, l)
    case CodeRef(n, l)       => named("CodeRef", n, l)
    case ConceptRef(n, l)    => named("ConceptRef", n, l)

    case IntervalExpr(low, high, lowClosed, highClosed) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("type" -> ujson.Str("Interval"))
      low.foreach(l => o("low") = emitExpr(l))
      high.foreach(h => o("high") = emitExpr(h))
      o("lowClosed") = ujson.Bool(lowClosed)
      o("highClosed") = ujson.Bool(highClosed)
      ujson.Obj.from(o)

    case Query(sources, where, returnExpr, returnDistinct, sort, aggregate, relationships, lets) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("type" -> ujson.Str("Query"))
      o("source") = ujson.Arr(sources.map(s =>
        ujson.Obj("alias" -> ujson.Str(s.alias), "expression" -> emitExpr(s.expr)): ujson.Value): _*)
      if (lets.nonEmpty)
        o("let") = ujson.Arr(lets.map(l =>
          ujson.Obj("identifier" -> ujson.Str(l.identifier), "expression" -> emitExpr(l.expr)): ujson.Value): _*)
      if (relationships.nonEmpty)
        o("relationship") = ujson.Arr(relationships.map(r => ujson.Obj(
          "type" -> ujson.Str(if (r.negated) "Without" else "With"),
          "alias" -> ujson.Str(r.alias),
          "expression" -> emitExpr(r.expr),
          "suchThat" -> emitExpr(r.suchThat)): ujson.Value): _*)
      where.foreach(w => o("where") = emitExpr(w))
      if (returnExpr.isDefined || returnDistinct) {
        val r = scala.collection.mutable.LinkedHashMap.empty[String, ujson.Value]
        // Always emit `distinct` explicitly: Load defaults an ABSENT flag to true (CQL's
        // "return defaults to distinct"), so `return all` (distinct=false) only round-trips if
        // the false is written out — otherwise it reloads as true.
        r("distinct") = ujson.Bool(returnDistinct)
        returnExpr.foreach(e => r("expression") = emitExpr(e))
        o("return") = ujson.Obj.from(r)
      }
      if (sort.nonEmpty)
        o("sort") = ujson.Obj("by" -> ujson.Arr(sort.map { s =>
          val kind = if (s.path.isDefined) "ByColumn" else if (s.expr.isDefined) "ByExpression" else "ByDirection"
          val b = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
            "type" -> ujson.Str(kind), "direction" -> ujson.Str(s.direction))
          s.expr.foreach(e => b("expression") = emitExpr(e))
          s.path.foreach(p => b("path") = ujson.Str(p))
          ujson.Obj.from(b): ujson.Value
        }: _*))
      aggregate.foreach { a =>
        val ag = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
          "identifier" -> ujson.Str(a.identifier), "starting" -> emitExpr(a.starting),
          "expression" -> emitExpr(a.body))
        if (a.distinct) ag("distinct") = ujson.Bool(true)
        o("aggregate") = ujson.Obj.from(ag)
      }
      ujson.Obj.from(o)

    case If(condition, thenExpr, elseExpr) =>
      ujson.Obj("type" -> ujson.Str("If"), "condition" -> emitExpr(condition),
        "then" -> emitExpr(thenExpr), "else" -> emitExpr(elseExpr))
    case Case(comparand, items, elseExpr) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value]("type" -> ujson.Str("Case"))
      comparand.foreach(c => o("comparand") = emitExpr(c))
      o("caseItem") = ujson.Arr(items.map(i =>
        ujson.Obj("when" -> emitExpr(i.when), "then" -> emitExpr(i.thenExpr)): ujson.Value): _*)
      o("else") = emitExpr(elseExpr)
      ujson.Obj.from(o)

    case TupleExpr(elements) =>
      ujson.Obj("type" -> ujson.Str("Tuple"), "element" -> ujson.Arr(elements.map(e =>
        ujson.Obj("name" -> ujson.Str(e.name), "value" -> emitExpr(e.value)): ujson.Value): _*))

    case InstanceExpr(classType, elements) =>
      ujson.Obj("type" -> ujson.Str("Instance"), "classType" -> ujson.Str(classType),
        "element" -> ujson.Arr(elements.map(e =>
          ujson.Obj("name" -> ujson.Str(e.name), "value" -> emitExpr(e.value)): ujson.Value): _*))

    case IdentifierRef(name) =>
      ujson.Obj("type" -> ujson.Str("IdentifierRef"), "name" -> ujson.Str(name))

    case Property(source, scope, path) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("Property"), "path" -> ujson.Str(path))
      scope.foreach(s => o("scope") = ujson.Str(s))
      source.foreach(s => o("source") = emitExpr(s))
      ujson.Obj.from(o)

    case InValueSet(code, valueset) =>
      ujson.Obj("type" -> ujson.Str("InValueSet"), "code" -> emitExpr(code), "valueset" -> emitExpr(valueset))

    case Retrieve(dataType, templateId, codeProperty, codes, dateProperty, dateRange, datePointConverter, _) =>
      val o = scala.collection.mutable.LinkedHashMap[String, ujson.Value](
        "type" -> ujson.Str("Retrieve"), "dataType" -> ujson.Str(dataType))
      templateId.foreach(t => o("templateId") = ujson.Str(t))
      codeProperty.foreach(c => o("codeProperty") = ujson.Str(c))
      codes.foreach(c => o("codes") = emitExpr(c))
      dateProperty.foreach(d => o("dateProperty") = ujson.Str(d))
      dateRange.foreach(d => o("dateRange") = emitExpr(d))
      datePointConverter.foreach(c => o("datePointConverter") = ujson.Str(c))
      ujson.Obj.from(o)

    // Synthesized at Normalize (ConceptResolver), never present in loaded ELM; emitted only so
    // emitExpr stays total for post-normalize IR dumps. Not a real ELM node type.
    case ConceptValue(codes) =>
      ujson.Obj("type" -> ujson.Str("ConceptValue"),
        "codes" -> ujson.Arr(codes.map { case (sys, code) =>
          ujson.Obj("system" -> ujson.Str(sys), "code" -> ujson.Str(code)): ujson.Value }: _*))

    case UnknownExpr(_, rawJson) => ujson.read(rawJson)
  }
}
